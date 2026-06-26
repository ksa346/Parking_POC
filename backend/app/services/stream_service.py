"""
Local Video Stream Service — loops an MP4 file continuously.

Replaces the old YouTube-based stream capture.  Reads the video with
OpenCV, advances one frame per ``capture_frame()`` call, and loops
back to the start when the video ends.  Also exposes JPEG-encoded
frames for the frontend video-feed endpoint.
"""
import asyncio
import logging
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    import cv2

    CV_AVAILABLE = True
except ImportError:
    CV_AVAILABLE = False
    logger.error("OpenCV not available — stream service will not function")


class StreamService:
    """Read frames from a local MP4 file in an infinite loop."""

    def __init__(self, video_path: str):
        self.video_path = video_path
        self.is_active = False
        self._cap: Optional[cv2.VideoCapture] = None
        self._lock = asyncio.Lock()

        # Video metadata (populated on first open)
        self.fps: float = 0.0
        self.frame_count: int = 0
        self.width: int = 0
        self.height: int = 0
        self.duration: float = 0.0

        # Frame cache
        self._last_frame: Optional[np.ndarray] = None
        self._last_frame_ts: float = 0.0

        # Annotated frame (set by detection service for frontend display)
        self._annotated_jpeg: Optional[bytes] = None

        self._open()

    # ── Video lifecycle ──────────────────────────────────────────────
    def _open(self) -> bool:
        """Open (or re-open) the video file."""
        if not CV_AVAILABLE:
            return False
        try:
            if self._cap is not None:
                self._cap.release()
            self._cap = cv2.VideoCapture(self.video_path)
            if not self._cap.isOpened():
                logger.error(f"Cannot open video: {self.video_path}")
                self.is_active = False
                return False

            self.fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
            self.frame_count = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self.duration = self.frame_count / self.fps if self.fps else 0

            self.is_active = True
            logger.info(
                f"Video opened: {self.width}x{self.height} @ {self.fps:.1f}fps, "
                f"{self.frame_count} frames, {self.duration:.1f}s"
            )
            return True
        except Exception as e:
            logger.error(f"Error opening video: {e}")
            self.is_active = False
            return False

    # ── Frame capture ────────────────────────────────────────────────
    async def capture_frame(self) -> Optional[np.ndarray]:
        """Return the next frame.  Loops the video when it ends."""
        async with self._lock:
            return self._read_next_frame()

    def _read_next_frame(self) -> Optional[np.ndarray]:
        if self._cap is None or not self._cap.isOpened():
            if not self._open():
                return self._last_frame

        ret, frame = self._cap.read()
        if not ret:
            # End of video → loop
            logger.info("Video ended — looping back to start")
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = self._cap.read()
            if not ret:
                logger.warning("Failed to read after loop reset")
                return self._last_frame

        self._last_frame = frame
        self._last_frame_ts = time.time()
        self.is_active = True
        return frame

    # ── Annotated JPEG for frontend ──────────────────────────────────
    def set_annotated_frame(self, jpeg_bytes: bytes):
        """Store the latest annotated JPEG (called by detection pipeline)."""
        self._annotated_jpeg = jpeg_bytes

    def get_annotated_jpeg(self) -> Optional[bytes]:
        """Return the latest annotated JPEG for the video-feed endpoint."""
        return self._annotated_jpeg

    # ── Raw JPEG for live-feed tab (no annotations) ─────────────────
    def get_raw_jpeg(self) -> Optional[bytes]:
        """Return the latest raw (un-annotated) frame as JPEG."""
        if self._last_frame is None:
            return None
        try:
            _, buf = cv2.imencode(".jpg", self._last_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return buf.tobytes()
        except Exception as e:
            logger.error(f"Error encoding raw JPEG: {e}")
            return None

    # ── Swap to a new video file ─────────────────────────────────────
    def swap_video(self, new_path: str) -> bool:
        """Replace the current video source with a new file and restart."""
        old = self.video_path
        self.video_path = new_path
        ok = self._open()
        if not ok:
            # Revert on failure
            self.video_path = old
            self._open()
        return ok

    # ── Cleanup ──────────────────────────────────────────────────────
    def release(self):
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self.is_active = False
