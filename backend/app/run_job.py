
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from app.video_process_count import VideoProcessCount


def run_job(
    video_paths: list,
    model_path: str = "yolov8m.pt",
    interval_seconds: int = 1,
    confidence_threshold: float = 0.25,
    iou_threshold: float = 0.45,
    regions: Optional[dict] = None,
    save_annotated_frames: bool = False,
    save_output_dir: Optional[str] = None,
    max_workers: int = 4,
) -> dict:
    processor = VideoProcessCount(
        model_path=model_path,
        conf_threshold=confidence_threshold,
        iou_threshold=iou_threshold,
    )

    results = [None] * len(video_paths)

    def worker(index: int, video_path: str):
        result = processor.process_video(
            video_path=video_path,
            interval_seconds=interval_seconds,
            regions=regions,
            save_annotated_frames=save_annotated_frames,
            save_output_dir=save_output_dir,
        )
        return index, result

    worker_count = max(1, min(max_workers, len(video_paths)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(worker, idx, path) for idx, path in enumerate(video_paths)]
        for future in as_completed(futures):
            try:
                index, result = future.result()
                results[index] = result
            except Exception as exc:
                results.append({"error": str(exc)})

    return {
        "batch": True,
        "total": len(video_paths),
        "model_path": model_path,
        "interval_seconds": interval_seconds,
        "confidence_threshold": confidence_threshold,
        "results": results,
    }
