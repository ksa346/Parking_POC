"""Export a YOLO project to a standards-compliant zip archive."""
import io
import os
import random
import shutil
import tempfile
import zipfile
from typing import Optional

from sqlalchemy.orm import Session

from app.yolo_tool.models import YoloImage, YoloAnnotation, YoloClass, YoloProject
from app.yolo_tool.schemas import ExportConfig
from app.yolo_tool.storage import upload_dir


def build_yolo_zip(project_id: int, cfg: ExportConfig, db: Session) -> io.BytesIO:
    project: Optional[YoloProject] = db.query(YoloProject).filter(YoloProject.id == project_id).first()
    if not project:
        raise ValueError("Project not found")

    classes: list[YoloClass] = sorted(project.classes, key=lambda c: c.class_index)
    class_names = [c.name for c in classes]
    class_id_to_index = {c.id: c.class_index for c in classes}

    images: list[YoloImage] = project.images

    # Only export images that are ready for training (annotated/reviewed/approved)
    # and have at least one annotation so labels are not empty.
    READY_STATUSES = {"annotated", "reviewed", "approved"}
    annotated = [img for img in images if img.status in READY_STATUSES and img.annotations]
    if not annotated:
        raise ValueError(
            "No images are ready for training. "
            "Mark images as 'annotated', 'reviewed', or 'approved' and ensure they have bounding-box annotations."
        )

    if cfg.shuffle:
        random.shuffle(annotated)

    n = len(annotated)
    n_train = max(1, round(n * cfg.train_ratio))
    n_val = max(0, round(n * cfg.val_ratio))
    # When val rounds to 0 but a spare image exists, borrow one from train
    # so YOLO always finds a non-empty images/val directory.
    if n_val == 0 and n_train > 1:
        n_train -= 1
        n_val = 1
    n_test = n - n_train - n_val

    splits = {
        "train": annotated[:n_train],
        "val": annotated[n_train: n_train + n_val],
        "test": annotated[n_train + n_val:],
    }

    # When there is still no val split (only 1 image total), point val at
    # images/train so YOLO always has a non-empty validation set.
    val_images_path = "images/train" if not splits["val"] else "images/val"

    tmpdir = tempfile.mkdtemp()
    try:
        dataset_name = project.name.replace(" ", "_")

        # Create split directories
        for split in ("train", "val", "test"):
            os.makedirs(os.path.join(tmpdir, "images", split), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "labels", split), exist_ok=True)

        # Write images + label files
        for split, split_images in splits.items():
            for img in split_images:
                src = os.path.join(upload_dir(project_id), img.filename)
                if not os.path.isfile(src):
                    continue

                # Copy image
                dest_img = os.path.join(tmpdir, "images", split, img.original_filename)
                shutil.copy2(src, dest_img)

                # Write label file
                label_stem = os.path.splitext(img.original_filename)[0]
                label_path = os.path.join(tmpdir, "labels", split, f"{label_stem}.txt")
                lines = _annotation_lines(img.annotations, class_id_to_index, cfg.fmt)
                with open(label_path, "w") as f:
                    f.write("\n".join(lines))

        # dataset.yaml — use /workspace/dataset as the path so the zip works
        # directly with the Docker training container without manual editing
        yaml_lines = [
            "path: /workspace/dataset",
            "train: images/train",
            f"val: {val_images_path}",
            f"test: images/test" if n_test > 0 else "",
            "",
            f"nc: {len(class_names)}",
            f"names: {class_names!r}",
        ]
        with open(os.path.join(tmpdir, "dataset.yaml"), "w") as f:
            f.write("\n".join(l for l in yaml_lines if l or l == ""))

        # Zip everything
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Always include all split directories so the container can find them
            # even when a split is empty (ZipInfo entries ending in '/' create dirs).
            for split in ("train", "val", "test"):
                for subdir in ("images", "labels"):
                    dir_entry = zipfile.ZipInfo(f"{dataset_name}/{subdir}/{split}/")
                    zf.writestr(dir_entry, "")
            for root, _, files in os.walk(tmpdir):
                for fname in files:
                    abs_path = os.path.join(root, fname)
                    arc_path = os.path.join(dataset_name, os.path.relpath(abs_path, tmpdir))
                    zf.write(abs_path, arc_path)
        buf.seek(0)
        return buf
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _annotation_lines(
    annotations: list[YoloAnnotation],
    class_id_to_index: dict[int, int],
    fmt: str,
) -> list[str]:
    lines = []
    for ann in annotations:
        idx = class_id_to_index.get(ann.class_id)
        if idx is None:
            continue
        if fmt == "segmentation" and ann.type == "polygon":
            pts = ann.get_polygon()
            if pts:
                coords = " ".join(f"{v:.6f}" for v in pts)
                lines.append(f"{idx} {coords}")
        else:
            # Detection: YOLO centre_x cy w h
            if ann.cx is not None:
                lines.append(f"{idx} {ann.cx:.6f} {ann.cy:.6f} {ann.bw:.6f} {ann.bh:.6f}")
    return lines
