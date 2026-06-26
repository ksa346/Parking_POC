#!/usr/bin/env python3
r"""Convert a YOLO dataset ZIP export to COCO annotations for RF-DETR.

Expected YOLO ZIP structure (as exported by this project):
  <dataset_name>/
    dataset.yaml
    images/{train,val,test}/*
    labels/{train,val,test}/*.txt

Output structure:
  <output_dir>/
    images/{train,val,test}/*
    annotations/instances_{train,val,test}.json

Notes:
- YOLO labels use normalized values (cx, cy, w, h) in range [0, 1].
- COCO boxes are pixel values in format [x, y, width, height].
- YOLO category index is 0-based; COCO category_id in this script is 1-based.

command  - 
python training/scripts/yolo_zip_to_coco.py --zip "C:\Users\tusharssharma\Desktop\UKHC\output\training1_yolo.zip" --output "C:\Users\tusharssharma\Desktop\UKHC\output\rfdetr_coco_dataset"
"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from PIL import Image

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
DEFAULT_SPLITS = ("train", "val", "test")


@dataclass
class ParsedLabel:
    class_index: int
    x: float
    y: float
    w: float
    h: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert YOLO export ZIP to RF-DETR-ready COCO annotations."
    )
    parser.add_argument(
        "--zip",
        dest="zip_path",
        required=True,
        help="Path to YOLO export ZIP file.",
    )
    parser.add_argument(
        "--output",
        dest="output_dir",
        required=True,
        help="Directory where COCO dataset will be written.",
    )
    parser.add_argument(
        "--splits",
        nargs="+",
        default=list(DEFAULT_SPLITS),
        help="Dataset splits to convert. Default: train val test",
    )
    parser.add_argument(
        "--segmentation-to-bbox",
        action="store_true",
        default=False,
        help=(
            "If YOLO polygon segmentation lines are found, convert them to tight bboxes. "
            "By default, non-bbox lines are skipped."
        ),
    )
    return parser.parse_args()


def find_dataset_root(extracted_dir: Path) -> Path:
    """Locate dataset root inside extracted ZIP contents."""
    if (extracted_dir / "dataset.yaml").exists() and (extracted_dir / "images").exists():
        return extracted_dir

    candidates = []
    for child in extracted_dir.iterdir():
        if child.is_dir() and (child / "images").exists() and (child / "labels").exists():
            candidates.append(child)

    if len(candidates) == 1:
        return candidates[0]

    for candidate in extracted_dir.rglob("dataset.yaml"):
        root = candidate.parent
        if (root / "images").exists() and (root / "labels").exists():
            return root

    raise FileNotFoundError(
        "Could not locate dataset root in zip. Expected folder containing images/ and labels/."
    )


def parse_category_names(dataset_root: Path) -> list[str] | None:
    """Parse class names from dataset.yaml if present.

    The project exporter writes dataset.yaml line like:
      names: ['car', 'person']
    """
    yaml_path = dataset_root / "dataset.yaml"
    if not yaml_path.exists():
        return None

    for line in yaml_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("names:"):
            continue

        value = stripped.split(":", 1)[1].strip()
        if not value:
            return None

        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return None

        if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
            return parsed

    return None


def list_images(images_dir: Path) -> list[Path]:
    return sorted(
        [p for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS],
        key=lambda p: p.name.lower(),
    )


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def parse_label_line(line: str, segmentation_to_bbox: bool) -> ParsedLabel | None:
    parts = line.strip().split()
    if len(parts) < 5:
        return None

    try:
        class_index = int(parts[0])
        coords = [float(v) for v in parts[1:]]
    except ValueError:
        return None

    if len(coords) == 4:
        cx, cy, bw, bh = coords
        return ParsedLabel(class_index=class_index, x=cx, y=cy, w=bw, h=bh)

    if segmentation_to_bbox and len(coords) >= 6 and len(coords) % 2 == 0:
        xs = coords[0::2]
        ys = coords[1::2]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        cx = (min_x + max_x) / 2.0
        cy = (min_y + max_y) / 2.0
        bw = max_x - min_x
        bh = max_y - min_y
        return ParsedLabel(class_index=class_index, x=cx, y=cy, w=bw, h=bh)

    return None


def convert_yolo_to_coco_bbox(label: ParsedLabel, width: int, height: int) -> list[float] | None:
    """Convert YOLO normalized bbox to COCO pixel bbox [x, y, w, h]."""
    x_center = label.x * width
    y_center = label.y * height
    box_w = label.w * width
    box_h = label.h * height

    x_min = x_center - box_w / 2.0
    y_min = y_center - box_h / 2.0

    x_min = clamp(x_min, 0.0, float(width))
    y_min = clamp(y_min, 0.0, float(height))
    box_w = clamp(box_w, 0.0, float(width) - x_min)
    box_h = clamp(box_h, 0.0, float(height) - y_min)

    if box_w <= 0.0 or box_h <= 0.0:
        return None

    return [round(x_min, 4), round(y_min, 4), round(box_w, 4), round(box_h, 4)]


def infer_class_names_from_labels(label_files: Iterable[Path]) -> list[str]:
    max_idx = -1
    for label_file in label_files:
        for line in label_file.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split()
            if not parts:
                continue
            try:
                idx = int(parts[0])
            except ValueError:
                continue
            if idx > max_idx:
                max_idx = idx

    if max_idx < 0:
        return []

    return [f"class_{i}" for i in range(max_idx + 1)]


def convert_split(
    split: str,
    dataset_root: Path,
    output_dir: Path,
    category_names: list[str],
    segmentation_to_bbox: bool,
) -> tuple[int, int]:
    images_src_dir = dataset_root / "images" / split
    labels_src_dir = dataset_root / "labels" / split
    if not images_src_dir.exists():
        return (0, 0)

    images_out_dir = output_dir / "images" / split
    ann_out_dir = output_dir / "annotations"
    images_out_dir.mkdir(parents=True, exist_ok=True)
    ann_out_dir.mkdir(parents=True, exist_ok=True)

    coco_images = []
    coco_annotations = []
    ann_id = 1

    for image_id, img_path in enumerate(list_images(images_src_dir), start=1):
        out_img_path = images_out_dir / img_path.name
        shutil.copy2(img_path, out_img_path)

        with Image.open(img_path) as img:
            width, height = img.size

        coco_images.append(
            {
                "id": image_id,
                "file_name": str(Path("images") / split / img_path.name).replace("\\", "/"),
                "width": width,
                "height": height,
            }
        )

        label_path = labels_src_dir / f"{img_path.stem}.txt"
        if not label_path.exists():
            continue

        for line in label_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            parsed = parse_label_line(line, segmentation_to_bbox=segmentation_to_bbox)
            if not parsed:
                continue

            bbox = convert_yolo_to_coco_bbox(parsed, width, height)
            if not bbox:
                continue

            coco_annotations.append(
                {
                    "id": ann_id,
                    "image_id": image_id,
                    "category_id": parsed.class_index + 1,
                    "bbox": bbox,
                    "area": round(bbox[2] * bbox[3], 4),
                    "iscrowd": 0,
                }
            )
            ann_id += 1

    categories = [
        {
            "id": idx + 1,
            "name": name,
            "supercategory": "object",
        }
        for idx, name in enumerate(category_names)
    ]

    coco = {
        "info": {
            "description": "Converted from YOLO ZIP export",
            "version": "1.0",
            "year": datetime.now(tz=timezone.utc).year,
            "date_created": datetime.now(tz=timezone.utc).isoformat(),
        },
        "licenses": [],
        "images": coco_images,
        "annotations": coco_annotations,
        "categories": categories,
    }

    out_json = ann_out_dir / f"instances_{split}.json"
    out_json.write_text(json.dumps(coco, indent=2), encoding="utf-8")
    return (len(coco_images), len(coco_annotations))


def main() -> None:
    args = parse_args()
    zip_path = Path(args.zip_path).resolve()
    output_dir = Path(args.output_dir).resolve()

    if not zip_path.exists() or not zip_path.is_file():
        raise FileNotFoundError(f"ZIP file not found: {zip_path}")

    with tempfile.TemporaryDirectory(prefix="yolo_zip_extract_") as tmp:
        tmp_dir = Path(tmp)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_dir)

        dataset_root = find_dataset_root(tmp_dir)

        names = parse_category_names(dataset_root)
        if names is None:
            label_files = list((dataset_root / "labels").rglob("*.txt"))
            names = infer_class_names_from_labels(label_files)

        output_dir.mkdir(parents=True, exist_ok=True)

        total_images = 0
        total_annotations = 0
        for split in args.splits:
            img_count, ann_count = convert_split(
                split=split,
                dataset_root=dataset_root,
                output_dir=output_dir,
                category_names=names,
                segmentation_to_bbox=args.segmentation_to_bbox,
            )
            total_images += img_count
            total_annotations += ann_count
            print(
                f"[{split}] images={img_count} annotations={ann_count} "
                f"json={output_dir / 'annotations' / f'instances_{split}.json'}"
            )

        print(f"Done. Total images={total_images}, annotations={total_annotations}")
        print(f"COCO dataset written to: {output_dir}")


if __name__ == "__main__":
    main()
