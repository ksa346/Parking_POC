#!/usr/bin/env python3
"""Download and train RF-DETR using a local COCO dataset.

This script supports the COCO layout created by training/scripts/yolo_zip_to_coco.py:

  dataset/
    images/{train,val,test}/*
    annotations/instances_{train,val,test}.json

RF-DETR expects COCO data in this structure:

  dataset/
    train/_annotations.coco.json
    train/<image files>
    valid/_annotations.coco.json
    valid/<image files>
    test/_annotations.coco.json
    test/<image files>

The script can auto-build the RF-DETR layout in a working directory and then run training.
how to run  - 
python rfdetr_train.py train --model base --dataset "C:/path/to/rfdetr_coco_dataset" --output-dir "training/rfdetr_output" --epochs 50 --batch-size 4 --grad-accum-steps 4 --lr 1e-4
Optional:

Add --device cuda:0 to force GPU
Add --resolution 560 (or another valid size)
Add --resume "path/to/checkpoint.ckpt" to continue training

For resume training - 
python training/scripts/rfdetr_train.py train `
  --model base `
  --dataset "C:/path/to/rfdetr_coco_dataset" `
  --output-dir "training/rfdetr_output" `
  --epochs 50 `
  --resume "training/rfdetr_output/last.ckpt"
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path


DEFAULT_OUTPUT_DIR = "training/rfdetr_output"
DEFAULT_WORK_DIR = "training/rfdetr_dataset"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
HEARTBEAT_INTERVAL_SEC = int(os.environ.get("RFDETR_HEARTBEAT_INTERVAL_SEC", "60"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download base RF-DETR weights and train on a COCO dataset."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    download_cmd = sub.add_parser("download", help="Download RF-DETR pretrained weights.")
    add_common_model_args(download_cmd)

    train_cmd = sub.add_parser("train", help="Train RF-DETR on a dataset.")
    add_common_model_args(train_cmd)
    train_cmd.add_argument(
        "--dataset",
        required=True,
        help=(
            "Path to converted COCO dataset root. Supports either: "
            "(1) images/ + annotations/ layout, or (2) RF-DETR train/valid/test layout."
        ),
    )
    train_cmd.add_argument(
        "--work-dir",
        default=DEFAULT_WORK_DIR,
        help=(
            "Directory where RF-DETR-compatible train/valid/test layout is prepared "
            "when input dataset is in images/ + annotations/ layout."
        ),
    )
    train_cmd.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Training output directory.")
    train_cmd.add_argument("--epochs", type=int, default=50, help="Number of training epochs.")
    train_cmd.add_argument("--batch-size", type=int, default=4, help="Per-step batch size.")
    train_cmd.add_argument("--grad-accum-steps", type=int, default=1, help="Gradient accumulation steps.")
    train_cmd.add_argument("--lr", type=float, default=1e-4, help="Learning rate.")
    train_cmd.add_argument(
        "--resolution",
        type=int,
        default=None,
        help="Optional input resolution override (must match RF-DETR constraints).",
    )
    train_cmd.add_argument(
        "--device",
        default=None,
        help='Optional training device string (examples: "cpu", "cuda", "cuda:0").',
    )
    train_cmd.add_argument(
        "--resume",
        default=None,
        help="Optional checkpoint path to resume training.",
    )

    install_cmd = sub.add_parser("install", help="Install RF-DETR package with training extras.")
    install_cmd.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable to use for pip install.",
    )

    return parser.parse_args()


def add_common_model_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        default="base",
        choices=["base", "small", "medium", "large", "nano"],
        help="RF-DETR model size.",
    )


def install_rfdetr() -> None:
    cmd = [sys.executable, "-m", "pip", "install", 'rfdetr[train,loggers]']
    print("Installing RF-DETR package:", " ".join(cmd))
    subprocess.check_call(cmd)


def _import_model_class(model_size: str):
    try:
        import rfdetr  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "rfdetr is not installed. Run: python training/scripts/rfdetr_train.py install"
        ) from exc

    # RFDETRBase is deprecated in newer versions but still usable where available.
    class_candidates = {
        "base": ["RFDETRBase", "RFDETRMedium"],
        "nano": ["RFDETRNano"],
        "small": ["RFDETRSmall"],
        "medium": ["RFDETRMedium"],
        "large": ["RFDETRLarge"],
    }

    import importlib

    module = importlib.import_module("rfdetr")
    for name in class_candidates[model_size]:
        cls = getattr(module, name, None)
        if cls is not None:
            if model_size == "base" and name != "RFDETRBase":
                print("RFDETRBase not available in installed version; falling back to RFDETRMedium.")
            return cls

    tried = ", ".join(class_candidates[model_size])
    raise RuntimeError(f"Could not find model class for '{model_size}'. Tried: {tried}")


def download_pretrained(model_size: str) -> None:
    model_cls = _import_model_class(model_size)
    print(f"Initializing {model_cls.__name__} to download pretrained weights...")
    _ = model_cls()
    print("Model initialized. Pretrained weights are available in local cache.")


def _looks_like_converter_output(dataset_root: Path) -> bool:
    return (dataset_root / "images").exists() and (dataset_root / "annotations").exists()


def _looks_like_rfdetr_coco(dataset_root: Path) -> bool:
    return (
        (dataset_root / "train" / "_annotations.coco.json").exists()
        and (dataset_root / "valid" / "_annotations.coco.json").exists()
    )


def _rewrite_coco_file_names(annotation_path: Path, split: str) -> dict:
    payload = json.loads(annotation_path.read_text(encoding="utf-8"))
    for image in payload.get("images", []):
        original = str(image.get("file_name", ""))
        filename = Path(original).name
        image["file_name"] = filename
    return payload


def prepare_rfdetr_dataset(dataset_root: Path, work_dir: Path) -> Path:
    """Prepare RF-DETR dataset structure and return final dataset root path."""
    if _looks_like_rfdetr_coco(dataset_root):
        print("Dataset already matches RF-DETR COCO layout. Using as-is.")
        return dataset_root

    if not _looks_like_converter_output(dataset_root):
        raise FileNotFoundError(
            "Unsupported dataset layout. Expected either:\n"
            "1) images/ + annotations/ (from yolo_zip_to_coco.py), or\n"
            "2) train/valid with _annotations.coco.json files."
        )

    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    split_map = {"train": "train", "val": "valid", "test": "test"}

    for src_split, dst_split in split_map.items():
        src_images = dataset_root / "images" / src_split
        src_ann = dataset_root / "annotations" / f"instances_{src_split}.json"
        if not src_images.exists() or not src_ann.exists():
            continue

        dst_dir = work_dir / dst_split
        dst_dir.mkdir(parents=True, exist_ok=True)

        for img_path in sorted(src_images.iterdir()):
            if img_path.is_file():
                shutil.copy2(img_path, dst_dir / img_path.name)

        coco = _rewrite_coco_file_names(src_ann, dst_split)
        (dst_dir / "_annotations.coco.json").write_text(
            json.dumps(coco, indent=2),
            encoding="utf-8",
        )

    if not (work_dir / "train" / "_annotations.coco.json").exists():
        raise FileNotFoundError("Prepared dataset is missing train/_annotations.coco.json")
    if not (work_dir / "valid" / "_annotations.coco.json").exists():
        raise FileNotFoundError("Prepared dataset is missing valid/_annotations.coco.json")

    print(f"Prepared RF-DETR dataset at: {work_dir}")
    return work_dir


def _count_train_images(training_dataset: Path) -> int:
    train_dir = training_dataset / "train"
    if not train_dir.exists():
        return 0
    return sum(1 for p in train_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def _is_cpu_device(device: str | None) -> bool:
    if not device:
        return False
    return str(device).strip().lower().startswith("cpu")


def _apply_cpu_safety_settings(train_kwargs: dict, train_image_count: int) -> None:
    batch = int(train_kwargs.get("batch_size", 1))
    grad = int(train_kwargs.get("grad_accum_steps", 1))

    # On small Windows CPU datasets, high effective batch can trigger native crashes.
    if train_image_count > 0 and train_image_count < 24 and batch > 2:
        train_kwargs["batch_size"] = 2
        print(f"[safety] Small train split ({train_image_count} images): batch_size capped to 2")
    elif train_image_count > 0 and train_image_count < 64 and batch > 4:
        train_kwargs["batch_size"] = 4
        print(f"[safety] Medium-small train split ({train_image_count} images): batch_size capped to 4")

    if grad > 1:
        train_kwargs["grad_accum_steps"] = 1
        print("[safety] CPU mode: grad_accum_steps reduced to 1")

    # Reduce OpenMP thread contention that can cause instability on Windows CPU runs.
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")


def train_model(args: argparse.Namespace) -> None:
    dataset_root = Path(args.dataset).resolve()
    if not dataset_root.exists():
        raise FileNotFoundError(f"Dataset path does not exist: {dataset_root}")

    work_dir = Path(args.work_dir).resolve()
    training_dataset = prepare_rfdetr_dataset(dataset_root, work_dir)
    train_image_count = _count_train_images(training_dataset)
    if train_image_count:
        print(f"Train images detected: {train_image_count}")

    model_cls = _import_model_class(args.model)
    print(f"Starting training with {model_cls.__name__}...")
    model = model_cls()

    train_kwargs = {
        "dataset_dir": str(training_dataset),
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "grad_accum_steps": args.grad_accum_steps,
        "lr": args.lr,
        "output_dir": str(Path(args.output_dir).resolve()),
    }
    if args.resolution is not None:
        train_kwargs["resolution"] = args.resolution
    if args.device:
        train_kwargs["device"] = args.device
    if args.resume:
        train_kwargs["resume"] = str(Path(args.resume).resolve())

    if _is_cpu_device(args.device):
        _apply_cpu_safety_settings(train_kwargs, train_image_count)

    stop_heartbeat = threading.Event()

    def _heartbeat() -> None:
        while not stop_heartbeat.wait(HEARTBEAT_INTERVAL_SEC):
            print(
                f"[heartbeat] Training in progress for {int(HEARTBEAT_INTERVAL_SEC)}s intervals...",
                flush=True,
            )

    heartbeat_thread = threading.Thread(target=_heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
        model.train(**train_kwargs)
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=2.0)

    print("Training completed.")


def main() -> None:
    args = parse_args()

    if args.command == "install":
        install_rfdetr()
        return

    if args.command == "download":
        download_pretrained(args.model)
        return

    if args.command == "train":
        train_model(args)
        return

    raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
