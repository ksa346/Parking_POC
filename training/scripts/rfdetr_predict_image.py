#!/usr/bin/env python3
"""Run RF-DETR inference on a single image and save an annotated output.

How to run:
  # With a trained checkpoint
  python training/scripts/rfdetr_predict_image.py \
      --checkpoint training/rfdetr_output/checkpoint_best_total.pth \
      --image      path/to/input.jpg \
      --output     training/rfdetr_predictions/input_pred.jpg
      --confidence-threshold 0.9

  # Without a checkpoint (uses pretrained COCO base weights)
  python training/scripts/rfdetr_predict_image.py \
      --model  base \
      --image  path/to/input.jpg \
      --output training/rfdetr_predictions/input_pred.jpg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


CAR_CLASS_NAME = "car"


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RF-DETR inference on a single image and save bounding boxes."
    )
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
        "--image",
        required=True,
        help="Path to input image.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to output image. If omitted, writes next to input as <name>_pred<ext>.",
    )
    parser.add_argument(
        "--threshold",
        "--confidence-threshold",
        "--confidence_threshold",
        dest="threshold",
        type=float,
        default=0.3,
        help="Confidence threshold for predictions (0.0-1.0).",
    )
    parser.add_argument(
        "--device",
        default=None,
        help='Inference device, e.g. "cpu", "cuda", "cuda:0".',
    )
    return parser.parse_args()


# -----------------------------------------------------------------------------
# Model helpers
# -----------------------------------------------------------------------------

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
        cls = getattr(_rfdetr_module, name, None)
        if cls is not None:
            return cls

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

                if hasattr(cls, "from_checkpoint"):
                    try:
                        model = cls.from_checkpoint(str(ckpt_path))
                        print(f"Loaded checkpoint using {name}.from_checkpoint()")
                        break
                    except Exception as exc:
                        load_errors.append(f"{name}.from_checkpoint failed: {exc}")

                ctor_kwargs_candidates = [
                    {"pretrain_weights": str(ckpt_path)},
                    {"checkpoint": str(ckpt_path)},
                    {"weights": str(ckpt_path)},
                ]
                for ctor_kwargs in ctor_kwargs_candidates:
                    try:
                        model = cls(**ctor_kwargs)
                        print(
                            f"Loaded checkpoint using {name}({list(ctor_kwargs.keys())[0]}=...)"
                        )
                        break
                    except TypeError:
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


# -----------------------------------------------------------------------------
# Visualization
# -----------------------------------------------------------------------------

_PALETTE = [
    (220, 20, 60),
    (30, 144, 255),
    (50, 205, 50),
    (255, 165, 0),
    (138, 43, 226),
    (0, 206, 209),
    (255, 20, 147),
    (255, 215, 0),
    (0, 128, 128),
    (255, 69, 0),
]


def _class_color(cat_id: int) -> tuple[int, int, int]:
    return _PALETTE[cat_id % len(_PALETTE)]


def draw_predictions(
    image_path: Path,
    boxes_xyxy: list[list[float]],
    class_ids: list[int],
    confidences: list[float],
    class_names: list[str],
    output_path: Path,
) -> None:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", size=14)
        small_font = font
    except Exception:
        font = ImageFont.load_default()
        small_font = font

    for box, cid, conf in zip(boxes_xyxy, class_ids, confidences):
        x1, y1, x2, y2 = box
        label_name = class_names[cid] if 0 <= cid < len(class_names) else str(cid)
        label = f"{label_name} {conf:.2f}"

        color = _class_color(cid)
        color_hex = "#{:02x}{:02x}{:02x}".format(*color)

        draw.rectangle([x1, y1, x2, y2], outline=color_hex, width=2)

        try:
            bbox_text = draw.textbbox((x1, y1 - 16), label, font=small_font)
        except AttributeError:
            tw, th = draw.textsize(label, font=small_font)
            bbox_text = (x1, y1 - th - 2, x1 + tw, y1)

        draw.rectangle(bbox_text, fill=color_hex)
        draw.text((bbox_text[0], bbox_text[1]), label, fill="white", font=small_font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), quality=95)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    image_path = Path(args.image).resolve()
    if not image_path.exists() or not image_path.is_file():
        print(f"Input image not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    output_path = (
        Path(args.output).resolve()
        if args.output
        else image_path.with_name(f"{image_path.stem}_pred{image_path.suffix}")
    )

    model = load_model(
        checkpoint=args.checkpoint,
        model_size=args.model if not args.checkpoint else None,
        device=args.device,
    )

    print(f"Running inference on: {image_path}")
    detections = model.predict(str(image_path), threshold=args.threshold)

    boxes_xyxy: list[list[float]] = []
    class_ids: list[int] = []
    confidences: list[float] = []

    if detections is not None and len(detections) > 0:
        boxes_all = detections.xyxy.tolist()
        class_ids_all = [int(v) for v in detections.class_id.tolist()]
        confidences_all = [float(v) for v in detections.confidence.tolist()]

        # Keep only detections classified as car.
        for box, cid, conf in zip(boxes_all, class_ids_all, confidences_all):
            label_name = ""
            try:
                label_name = str(model.class_names[cid]).strip().lower()
            except Exception:
                label_name = ""
            if label_name == CAR_CLASS_NAME:
                boxes_xyxy.append(box)
                class_ids.append(cid)
                confidences.append(conf)

    class_names = []
    try:
        class_names = list(model.class_names or [])
    except Exception:
        class_names = []

    draw_predictions(
        image_path=image_path,
        boxes_xyxy=boxes_xyxy,
        class_ids=class_ids,
        confidences=confidences,
        class_names=class_names,
        output_path=output_path,
    )

    print(f"Detections: {len(boxes_xyxy)}")
    print(f"Saved annotated image: {output_path}")


if __name__ == "__main__":
    main()
