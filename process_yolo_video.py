from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import cv2
from ultralytics import YOLO


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a YOLO checkpoint on a video and save an annotated copy."
    )
    parser.add_argument(
        "video",
        help="Path to the input video file.",
    )
    parser.add_argument(
        "model",
        help="Path to the YOLO .pt checkpoint.",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Path for the annotated output video. Defaults to <input>_processed.mp4.",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=0.25,
        help="Confidence threshold used during prediction.",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
        help="Inference image size passed to YOLO.",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Optional Ultralytics device argument, for example cpu or 0.",
    )
    parser.add_argument(
        "--fourcc",
        default="mp4v",
        help="Video codec for the output file.",
    )
    return parser.parse_args()


def resolve_paths(video_arg: str, model_arg: str, output_arg: Optional[str]) -> tuple[Path, Path, Path]:
    video_path = Path(video_arg).expanduser().resolve()
    model_path = Path(model_arg).expanduser().resolve()

    if output_arg:
        output_path = Path(output_arg).expanduser().resolve()
    else:
        output_path = video_path.with_name(f"{video_path.stem}_processed.mp4")

    return video_path, model_path, output_path


def annotate_frame(frame, result):
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return frame

    annotated = frame.copy()
    for box in boxes:
        xyxy = box.xyxy[0].tolist()
        conf = float(box.conf[0])
        label = "car"

        x1, y1, x2, y2 = map(int, xyxy)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
        text = f"{label} {conf:.2f}"

        (text_w, text_h), baseline = cv2.getTextSize(
            text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2
        )
        text_y = max(0, y1 - text_h - baseline - 6)
        cv2.rectangle(
            annotated,
            (x1, text_y),
            (x1 + text_w + 8, text_y + text_h + baseline + 6),
            (0, 255, 0),
            -1,
        )
        cv2.putText(
            annotated,
            text,
            (x1 + 4, text_y + text_h + 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 0, 0),
            2,
            cv2.LINE_AA,
        )

    return annotated


def build_writer(output_path: Path, fps: float, width: int, height: int, fourcc: str) -> cv2.VideoWriter:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*fourcc),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError(f"Could not open video writer for: {output_path}")
    return writer


def main() -> int:
    args = parse_args()
    video_path, model_path, output_path = resolve_paths(args.video, args.model, args.output)

    if not video_path.exists():
        raise FileNotFoundError(f"Input video not found: {video_path}")
    if not model_path.exists():
        raise FileNotFoundError(f"YOLO checkpoint not found: {model_path}")

    print(f"Loading YOLO model: {model_path}")
    model = YOLO(str(model_path))

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open input video: {video_path}")

    fps = capture.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 30.0

    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width <= 0 or height <= 0:
        raise RuntimeError("Could not read input video dimensions.")

    writer = build_writer(output_path, fps, width, height, args.fourcc)

    frame_count = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break

            results = model.predict(
                source=frame,
                conf=args.conf,
                imgsz=args.imgsz,
                device=args.device,
                verbose=False,
            )
            annotated = annotate_frame(frame, results[0] if results else None)
            writer.write(annotated)
            frame_count += 1

            if frame_count % 25 == 0:
                print(f"Processed {frame_count} frames...")
    finally:
        capture.release()
        writer.release()

    print(f"Done. Wrote {frame_count} frames to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())