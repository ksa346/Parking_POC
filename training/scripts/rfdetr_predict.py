#!/usr/bin/env python3
"""Run RF-DETR inference on a dataset and produce an evaluation report.

How to run:
  # With a trained checkpoint
  python training/scripts/rfdetr_predict.py \\
      --checkpoint training/rfdetr_output/checkpoint_best_total.pth \\
      --dataset    path/to/rfdetr_coco_dataset \\
      --output     training/rfdetr_predictions \\
      --split      val

  # Without a checkpoint (uses pretrained COCO base weights)
  python training/scripts/rfdetr_predict.py \\
      --model   base \\
      --dataset path/to/rfdetr_coco_dataset \\
      --output  training/rfdetr_predictions \\
      --split   val

Output folder:
  <output>/
    accuracy.txt          — overall mAP + per-class precision/recall
    predicted_image_1.jpg
    ...
    predicted_image_5.jpg — five sample images with coloured bounding boxes

Supported dataset layouts:
  1. images/<split>/* + annotations/instances_<split>.json   (yolo_zip_to_coco.py output)
  2. <split>/_annotations.coco.json + images in same folder  (RF-DETR layout)

With a trained checkpoint:
python training/scripts/rfdetr_predict.py `
    --checkpoint training/rfdetr_output/checkpoint_best_total.pth `
    --dataset    path/to/rfdetr_coco_dataset `
    --output     training/rfdetr_predictions `
    --split      val

With pretrained weights only:
python training/scripts/rfdetr_predict.py `
    --model   base `
    --dataset path/to/rfdetr_coco_dataset `
    --output  training/rfdetr_predictions `
    --split   val
  
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RF-DETR inference + evaluation on a dataset.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--checkpoint",
        help="Path to a trained RF-DETR checkpoint file (.pth / .ckpt).",
    )
    group.add_argument(
        "--model",
        choices=["nano", "small", "base", "medium", "large"],
        help="Use a pretrained RF-DETR model (no custom checkpoint).",
    )
    parser.add_argument(
        "--dataset",
        required=True,
        help="Dataset root folder (supports yolo_zip_to_coco.py or RF-DETR COCO layout).",
    )
    parser.add_argument(
        "--split",
        default="val",
        choices=["train", "val", "valid", "test"],
        help="Dataset split to evaluate. 'val' and 'valid' are treated identically.",
    )
    parser.add_argument(
        "--output",
        default="training/rfdetr_predictions",
        help="Folder where accuracy.txt and annotated images are written.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.3,
        help="Confidence threshold for predictions.",
    )
    parser.add_argument(
        "--num-samples",
        type=int,
        default=5,
        help="Number of annotated sample images to save.",
    )
    parser.add_argument(
        "--device",
        default=None,
        help='Inference device, e.g. "cpu", "cuda", "cuda:0".',
    )
    return parser.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# Dataset helpers
# ─────────────────────────────────────────────────────────────────────────────

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

_SPLIT_ALIAS = {"val": "val", "valid": "val", "train": "train", "test": "test"}


def _locate_coco_annotation_and_images(dataset_root: Path, split: str) -> tuple[Path, Path]:
    """Return (annotations_json, images_dir) for the requested split.

    Supports:
      Layout A  dataset/images/<split>/*  +  dataset/annotations/instances_<split>.json
      Layout B  dataset/<split>/_annotations.coco.json  +  dataset/<split>/<images>
      Layout C  (val is stored as 'valid') dataset/valid/...
    """
    split_key = _SPLIT_ALIAS.get(split, split)
    rfdetr_split = "valid" if split_key == "val" else split_key

    # Layout B / C  — RF-DETR native
    for candidate in (split_key, rfdetr_split):
        ann_b = dataset_root / candidate / "_annotations.coco.json"
        if ann_b.exists():
            return ann_b, dataset_root / candidate

    # Layout A  — yolo_zip_to_coco output
    ann_a = dataset_root / "annotations" / f"instances_{split_key}.json"
    images_a = dataset_root / "images" / split_key
    if ann_a.exists() and images_a.exists():
        return ann_a, images_a

    raise FileNotFoundError(
        f"Could not locate '{split}' annotations in {dataset_root}.\n"
        "Tried:\n"
        f"  {dataset_root / split_key / '_annotations.coco.json'}\n"
        f"  {dataset_root / rfdetr_split / '_annotations.coco.json'}\n"
        f"  {dataset_root / 'annotations' / f'instances_{split_key}.json'}\n"
    )


def load_coco(ann_path: Path) -> dict:
    return json.loads(ann_path.read_text(encoding="utf-8"))


def build_gt_index(coco: dict, images_dir: Path) -> tuple[dict, dict, dict]:
    """Return (image_id → image_path, image_id → [gt_box], cat_id → name)."""
    id_to_path: dict[int, Path] = {}
    for img in coco.get("images", []):
        fname = Path(img["file_name"]).name
        candidate = images_dir / fname
        if candidate.exists():
            id_to_path[img["id"]] = candidate

    id_to_gts: dict[int, list[dict]] = defaultdict(list)
    for ann in coco.get("annotations", []):
        id_to_gts[ann["image_id"]].append(ann)

    cat_names: dict[int, str] = {
        c["id"]: c["name"] for c in coco.get("categories", [])
    }
    return id_to_path, id_to_gts, cat_names


# ─────────────────────────────────────────────────────────────────────────────
# Model helpers
# ─────────────────────────────────────────────────────────────────────────────

_MODEL_CLASSES = {
    "nano": ["RFDETRNano"],
    "small": ["RFDETRSmall"],
    "base": ["RFDETRBase", "RFDETRMedium"],
    "medium": ["RFDETRMedium"],
    "large": ["RFDETRLarge"],
}


def load_model(checkpoint: str | None, model_size: str | None, device: str | None):
    try:
        import rfdetr as _rfdetr_module
    except ImportError as exc:
        raise ImportError(
            "rfdetr is not installed. Run:\n"
            "  python training/scripts/rfdetr_train.py install"
        ) from exc

    def _get_class(name: str):
        """Resolve RF-DETR classes across package versions/layouts."""
        cls = getattr(_rfdetr_module, name, None)
        if cls is not None:
            return cls

        # Fallback for builds that do not re-export classes from rfdetr.__init__
        module_candidates = ("rfdetr.variants", "rfdetr.detr")
        for module_name in module_candidates:
            try:
                import importlib

                mod = importlib.import_module(module_name)
                cls = getattr(mod, name, None)
                if cls is not None:
                    return cls
            except Exception:
                continue
        return None

    if checkpoint:
        ckpt_path = Path(checkpoint).resolve()
        if not ckpt_path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {ckpt_path}")
        print(f"Loading RF-DETR from checkpoint: {ckpt_path}")
        # Newer/older rfdetr builds differ in what is exported at top-level.
        # Prefer RFDETR.from_checkpoint when available; otherwise try variant classes.
        root_cls = _get_class("RFDETR")
        if root_cls is not None and hasattr(root_cls, "from_checkpoint"):
            model = root_cls.from_checkpoint(str(ckpt_path))
        else:
            model = None
            tried = []
            load_errors = []
            candidate_names = [
                "RFDETRBase",
                "RFDETRMedium",
                "RFDETRSmall",
                "RFDETRLarge",
                "RFDETRNano",
            ]
            for name in candidate_names:
                cls = _get_class(name)
                if cls is None:
                    continue
                tried.append(name)

                # Preferred path for newer RF-DETR versions
                if hasattr(cls, "from_checkpoint"):
                    try:
                        model = cls.from_checkpoint(str(ckpt_path))
                        print(f"Loaded checkpoint using {name}.from_checkpoint()")
                        break
                    except Exception as exc:
                        load_errors.append(f"{name}.from_checkpoint failed: {exc}")

                # Compatibility path for versions where constructor accepts checkpoint arg.
                # Try common kwargs used across releases.
                ctor_kwargs_candidates = [
                    {"pretrain_weights": str(ckpt_path)},
                    {"checkpoint": str(ckpt_path)},
                    {"weights": str(ckpt_path)},
                ]
                for ctor_kwargs in ctor_kwargs_candidates:
                    try:
                        model = cls(**ctor_kwargs)
                        print(f"Loaded checkpoint using {name}({list(ctor_kwargs.keys())[0]}=...) ")
                        break
                    except TypeError:
                        # Kwarg not supported by this version/class.
                        continue
                    except Exception as exc:
                        load_errors.append(
                            f"{name}({list(ctor_kwargs.keys())[0]}=...) failed: {exc}"
                        )
                if model is not None:
                    break

            if model is None:
                tried_str = ", ".join(tried) if tried else "no compatible classes exported"
                details = ""
                if load_errors:
                    details = "\nDetails: " + " | ".join(load_errors[:3])
                raise RuntimeError(
                    "Could not load checkpoint with installed rfdetr package. "
                    f"Tried classes: {tried_str}. Upgrade rfdetr or pass --model for pretrained inference."
                    f"{details}"
                )
    else:
        candidates = _MODEL_CLASSES[model_size]
        cls = None
        for name in candidates:
            cls = _get_class(name)
            if cls is not None:
                break
        if cls is None:
            raise RuntimeError(f"Could not find model class for '{model_size}'.")
        print(f"Loading pretrained RF-DETR: {cls.__name__}")
        model = cls()

    if device:
        model.model_config.device = device

    return model


# ─────────────────────────────────────────────────────────────────────────────
# IoU and mAP
# ─────────────────────────────────────────────────────────────────────────────

def _iou(boxA: list[float], boxB: list[float]) -> float:
    """IoU between two COCO boxes [x, y, w, h]."""
    ax1, ay1, aw, ah = boxA
    bx1, by1, bw, bh = boxB
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_area = max(0.0, inter_x2 - inter_x1) * max(0.0, inter_y2 - inter_y1)
    union_area = aw * ah + bw * bh - inter_area
    return inter_area / union_area if union_area > 0 else 0.0


def _xyxy_to_coco(box_xyxy: list[float]) -> list[float]:
    x1, y1, x2, y2 = box_xyxy
    return [x1, y1, x2 - x1, y2 - y1]


def compute_ap(precisions: list[float], recalls: list[float]) -> float:
    """Compute AP using 11-point interpolation (VOC style)."""
    ap = 0.0
    for thr in [i / 10.0 for i in range(11)]:
        prec_at_thr = [p for p, r in zip(precisions, recalls) if r >= thr]
        ap += max(prec_at_thr) if prec_at_thr else 0.0
    return ap / 11.0


def evaluate(
    image_ids: list[int],
    id_to_gts: dict[int, list[dict]],
    all_preds: dict[int, list[dict]],
    cat_names: dict[int, str],
    iou_threshold: float = 0.5,
) -> tuple[dict[int, dict], float]:
    """Compute per-class AP and mean AP.

    Returns (per_class_results, mAP) where per_class_results maps category_id →
    {name, ap, tp, fp, n_gt}.
    """
    cat_ids = sorted(set(cat_names.keys()))

    per_class: dict[int, dict] = {}

    for cat_id in cat_ids:
        gt_list: list[dict] = []  # {image_id, box, matched}
        pred_list: list[dict] = []  # {image_id, box, score}

        for img_id in image_ids:
            for ann in id_to_gts.get(img_id, []):
                if ann["category_id"] == cat_id:
                    gt_list.append({"image_id": img_id, "box": ann["bbox"], "matched": False})
            for pred in all_preds.get(img_id, []):
                if pred["category_id"] == cat_id:
                    pred_list.append({"image_id": img_id, "box": pred["bbox"], "score": pred["score"]})

        if not gt_list:
            per_class[cat_id] = {
                "name": cat_names[cat_id],
                "ap": float("nan"),
                "tp": 0,
                "fp": 0,
                "n_gt": 0,
            }
            continue

        # Sort predictions by descending score
        pred_list.sort(key=lambda x: -x["score"])

        tp_arr, fp_arr = [], []
        gt_by_image: dict[int, list[dict]] = defaultdict(list)
        for g in gt_list:
            gt_by_image[g["image_id"]].append(g)

        for pred in pred_list:
            img_id = pred["image_id"]
            gts = gt_by_image.get(img_id, [])
            best_iou, best_gt = 0.0, None
            for gt in gts:
                if gt["matched"]:
                    continue
                iou = _iou(pred["box"], gt["box"])
                if iou > best_iou:
                    best_iou, best_gt = iou, gt
            if best_iou >= iou_threshold and best_gt is not None:
                best_gt["matched"] = True
                tp_arr.append(1)
                fp_arr.append(0)
            else:
                tp_arr.append(0)
                fp_arr.append(1)

        cum_tp = list(_cumsum(tp_arr))
        cum_fp = list(_cumsum(fp_arr))
        n_gt = len(gt_list)
        precisions = [tp / (tp + fp) if (tp + fp) > 0 else 0.0 for tp, fp in zip(cum_tp, cum_fp)]
        recalls = [tp / n_gt for tp in cum_tp]
        ap = compute_ap(precisions, recalls)

        per_class[cat_id] = {
            "name": cat_names[cat_id],
            "ap": ap,
            "tp": sum(tp_arr),
            "fp": sum(fp_arr),
            "n_gt": n_gt,
        }

    valid_aps = [v["ap"] for v in per_class.values() if not math.isnan(v["ap"])]
    mean_ap = sum(valid_aps) / len(valid_aps) if valid_aps else 0.0
    return per_class, mean_ap


def _cumsum(lst: list[int]) -> list[int]:
    s, result = 0, []
    for v in lst:
        s += v
        result.append(s)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Visualization
# ─────────────────────────────────────────────────────────────────────────────

# Distinct colours (BGR) for up to 20 classes
_PALETTE = [
    (220,  20,  60), ( 30, 144, 255), ( 50, 205,  50), (255, 165,   0),
    (138,  43, 226), (  0, 206, 209), (255,  20, 147), (255, 215,   0),
    (  0, 128, 128), (255,  69,   0), (124, 252,   0), (218, 112, 214),
    (100, 149, 237), (255, 140,   0), (  0, 191, 255), (173, 255,  47),
    (219, 112, 147), ( 64, 224, 208), (255, 127,  80), (144, 238, 144),
]


def _class_color(cat_id: int) -> tuple[int, int, int]:
    return _PALETTE[cat_id % len(_PALETTE)]


def draw_predictions(
    image_path: Path,
    predictions: list[dict],
    cat_names: dict[int, str],
    output_path: Path,
) -> None:
    """Draw bounding boxes on image and save to output_path using PIL."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", size=14)
        small_font = font
    except Exception:
        font = ImageFont.load_default()
        small_font = font

    for pred in predictions:
        x, y, w, h = pred["bbox"]
        x2, y2 = x + w, y + h
        cat_id = pred["category_id"]
        score = pred["score"]
        label = f"{cat_names.get(cat_id, str(cat_id))} {score:.2f}"
        color = _class_color(cat_id)
        color_hex = "#{:02x}{:02x}{:02x}".format(*color)

        # Box
        draw.rectangle([x, y, x2, y2], outline=color_hex, width=2)

        # Label background
        try:
            bbox_text = draw.textbbox((x, y - 16), label, font=small_font)
        except AttributeError:
            tw, th = draw.textsize(label, font=small_font)
            bbox_text = (x, y - th - 2, x + tw, y)
        draw.rectangle(bbox_text, fill=color_hex)
        draw.text((bbox_text[0], bbox_text[1]), label, fill="white", font=small_font)

    img.save(str(output_path), quality=95)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    dataset_root = Path(args.dataset).resolve()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    split = args.split
    ann_path, images_dir = _locate_coco_annotation_and_images(dataset_root, split)
    print(f"Annotations : {ann_path}")
    print(f"Images dir  : {images_dir}")

    coco = load_coco(ann_path)
    id_to_path, id_to_gts, cat_names = build_gt_index(coco, images_dir)

    if not id_to_path:
        print("No images found matching the annotation file. Check your dataset path.", file=sys.stderr)
        sys.exit(1)

    model = load_model(
        checkpoint=args.checkpoint,
        model_size=args.model if not args.checkpoint else None,
        device=args.device,
    )

    # Override cat_names from model when running with a trained checkpoint
    try:
        model_class_names = model.class_names
        if model_class_names and len(model_class_names) > 0:
            # model returns 0-based list; COCO cat ids are 1-based
            cat_names = {i + 1: name for i, name in enumerate(model_class_names)}
    except Exception:
        pass  # keep cat_names from the annotation file

    image_ids = sorted(id_to_path.keys())
    all_preds: dict[int, list[dict]] = {}

    print(f"\nRunning inference on {len(image_ids)} images (split={split})...")
    for idx, img_id in enumerate(image_ids, start=1):
        img_path = id_to_path[img_id]
        detections = model.predict(str(img_path), threshold=args.threshold)

        preds = []
        if detections is not None and len(detections) > 0:
            boxes_xyxy = detections.xyxy.tolist()
            class_ids = detections.class_id.tolist()
            confidences = detections.confidence.tolist()
            for box, cid, conf in zip(boxes_xyxy, class_ids, confidences):
                preds.append(
                    {
                        "image_id": img_id,
                        # RF-DETR is 0-based; COCO categories are 1-based
                        "category_id": int(cid) + 1,
                        "bbox": _xyxy_to_coco(box),
                        "score": float(conf),
                    }
                )
        all_preds[img_id] = preds

        if idx % 20 == 0 or idx == len(image_ids):
            print(f"  {idx}/{len(image_ids)} done")

    # ── Evaluation ────────────────────────────────────────────────────────────
    print("\nComputing mAP@0.5 ...")
    per_class, mean_ap = evaluate(image_ids, id_to_gts, all_preds, cat_names, iou_threshold=0.5)

    total_gt = sum(v["n_gt"] for v in per_class.values())
    total_tp = sum(v["tp"] for v in per_class.values())
    total_pred = sum(len(p) for p in all_preds.values())

    # ── Write accuracy report ─────────────────────────────────────────────────
    report_lines = [
        "=" * 60,
        "RF-DETR Evaluation Report",
        "=" * 60,
        f"Split          : {split}",
        f"Images         : {len(image_ids)}",
        f"Total GT boxes : {total_gt}",
        f"Total predicted: {total_pred}",
        f"Total TP (IoU≥0.5): {total_tp}",
        "",
        f"mAP@0.50       : {mean_ap:.4f}  ({mean_ap * 100:.2f}%)",
        "",
        "─" * 60,
        f"{'Category':<25} {'AP@0.50':>8}  {'TP':>6}  {'FP':>6}  {'GT':>6}",
        "─" * 60,
    ]
    for cat_id in sorted(per_class):
        info = per_class[cat_id]
        ap_str = f"{info['ap']:.4f}" if not math.isnan(info["ap"]) else "  n/a "
        report_lines.append(
            f"{info['name']:<25} {ap_str:>8}  {info['tp']:>6}  {info['fp']:>6}  {info['n_gt']:>6}"
        )
    report_lines += ["─" * 60, ""]

    report_text = "\n".join(report_lines)
    print("\n" + report_text)

    acc_file = output_dir / "accuracy.txt"
    acc_file.write_text(report_text, encoding="utf-8")
    print(f"Accuracy report saved to: {acc_file}")

    # ── Sample annotated images ───────────────────────────────────────────────
    # Pick images that have at least one prediction; fall back to any images
    ids_with_preds = [i for i in image_ids if all_preds.get(i)]
    sample_pool = ids_with_preds if ids_with_preds else image_ids
    num_samples = min(args.num_samples, len(sample_pool))
    step = max(1, len(sample_pool) // num_samples)
    sample_ids = [sample_pool[i * step] for i in range(num_samples)]

    print(f"\nSaving {num_samples} annotated sample image(s)...")
    for i, img_id in enumerate(sample_ids, start=1):
        img_path = id_to_path[img_id]
        out_img = output_dir / f"predicted_image_{i}{img_path.suffix}"
        try:
            draw_predictions(img_path, all_preds.get(img_id, []), cat_names, out_img)
            print(f"  [{i}] {out_img.name}  ({len(all_preds.get(img_id, []))} boxes)")
        except Exception as exc:
            print(f"  [{i}] Failed to draw {img_path.name}: {exc}", file=sys.stderr)

    print(f"\nAll outputs written to: {output_dir}")


if __name__ == "__main__":
    main()
