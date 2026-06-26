"""
WizardService — completely isolated from the live detection pipeline.

Every method here is stateless with respect to the global ParkingService /
DetectionService / StreamService.  The wizard can run concurrently with live
detection without ever touching shared state.

Architecture
────────────
Live pipeline  ←→  ParkingService / DetectionService / StreamService
Wizard         ←→  WizardService  (this file)

The only intentional cross-over is activate_location() in main.py, which
deliberately switches the live pipeline to a published location's config.
"""
import logging
import math
import os
import threading
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

import numpy as np
from app.services.detection_service import DetectionService

logger = logging.getLogger(__name__)

try:
    import cv2
    CV_AVAILABLE = True
except ImportError:
    CV_AVAILABLE = False

# Classes that RF-DETR/COCO models often fire on for aerial vehicle footage
_AERIAL_CLASSES = {
    "car", "truck", "bus", "motorcycle",
    "cell phone", "backpack", "suitcase", "bottle",
    "handbag", "laptop", "remote", "book",
    "keyboard", "mouse", "tv", "refrigerator",
    "skateboard", "surfboard",
}


# ── LRU model cache (wizard-only, never shared with live detection) ──────────

class _WizardModelCache:
    """Thread-safe LRU cache retained for wizard compatibility."""

    def __init__(self, max_size: int = 2):
        self._cache: OrderedDict[str, object] = OrderedDict()
        self._lock = threading.Lock()
        self._max_size = max_size

    def get_or_load(self, model_path: str) -> object:
        """No-op cache getter retained to avoid changing external references."""
        with self._lock:
            return self._cache.get(model_path)

    def invalidate(self, model_path: str):
        with self._lock:
            self._cache.pop(model_path, None)


_model_cache = _WizardModelCache(max_size=2)


# ── WizardService ────────────────────────────────────────────────────────────

class WizardService:
    """Stateless helper methods for the developer wizard.

    Methods are @staticmethod so callers never instantiate this class.
    No instance state — and absolutely no reference to the global
    ParkingService/DetectionService/StreamService.
    """

    # ── Frame capture ────────────────────────────────────────────────

    @staticmethod
    def capture_from_upload_dir(upload_dir: str) -> Optional[np.ndarray]:
        """Return a frame from the most recently modified video in *upload_dir*."""
        if not CV_AVAILABLE:
            return None
        if not os.path.isdir(upload_dir):
            return None

        files = [
            os.path.join(upload_dir, f)
            for f in os.listdir(upload_dir)
            if f.lower().endswith((".mp4", ".avi", ".mkv", ".webm", ".mov"))
        ]
        if not files:
            return None

        latest = max(files, key=os.path.getmtime)
        cap = cv2.VideoCapture(latest)
        ret, frame = cap.read()
        cap.release()
        return frame if ret else None

    @staticmethod
    def capture_from_url(url: str) -> Optional[np.ndarray]:
        """Read one frame from an HTTP/RTSP stream URL."""
        if not CV_AVAILABLE:
            return None
        cap = cv2.VideoCapture(url)
        ret, frame = cap.read()
        cap.release()
        return frame if ret else None

    # ── Preview detection ────────────────────────────────────────────

    @staticmethod
    def run_preview(
        frame: np.ndarray,
        model_path: str,
        project_root: str,
        conf: float = 0.15,
        nms_iou: float = 0.4,
        seg_overlap: float = 0.12,
        min_vehicle_area: int = 800,
        max_vehicle_area: int = 80000,
        h_lines: Optional[List[float]] = None,
        v_lines: Optional[List[float]] = None,
        h_line_angles: Optional[List[float]] = None,
        v_line_angles: Optional[List[float]] = None,
        exclude_regions: Optional[List[List[List[float]]]] = None,
        h_angle_deg: float = 0.0,
        v_angle_deg: float = 0.0,
        border: Optional[Dict[str, float]] = None,
    ) -> Tuple[np.ndarray, int, float]:
        """Run RF-DETR preview entirely in isolation.

        Returns ``(annotated_frame, vehicle_count, processing_ms)``.
        Never touches global services. Model is fetched from the
        wizard-local LRU cache.
        """
        if not CV_AVAILABLE:
            return frame, 0, 0.0

        h_lines = sorted(h_lines or [])
        v_lines = sorted(v_lines or [])
        h_line_angles = [float(v) for v in (h_line_angles or [])]
        v_line_angles = [float(v) for v in (v_line_angles or [])]
        exclude_regions = exclude_regions or []
        border = border or {"top": 0, "right": 0, "bottom": 0, "left": 0}

        # Resolve model path
        resolved = _resolve_model_path(model_path, project_root)

        try:
            preview_detector = DetectionService(model_path=resolved, confidence=conf)
            if not preview_detector.load_model():
                raise RuntimeError("Model failed to load")
        except Exception as e:
            logger.error(f"WizardService: failed to load RF-DETR model {resolved}: {e}")
            return frame, 0, 0.0

        img_h, img_w = frame.shape[:2]

        # ── Auto-scale area thresholds to match the actual frame resolution ──
        # min_vehicle_area / max_vehicle_area user values are calibrated at
        # 4K (3840×2160).  The wizard sends a 1280-px-wide frame (downscaled
        # by the browser), so pixel areas are ~9× smaller.  We rescale here
        # so the slider values stay meaningful regardless of input resolution.
        _REF_PIXELS = 3840 * 2160  # calibration reference (4K)
        _frame_pixels = img_w * img_h
        _area_scale = _frame_pixels / _REF_PIXELS
        scaled_min_area = max(30, int(min_vehicle_area * _area_scale))
        scaled_max_area = max(500, int(max_vehicle_area * _area_scale))
        # Edge-relaxed thresholds for partially-visible vehicles near segment borders
        edge_min_area = max(15, int(scaled_min_area * 0.4))
        edge_min_dim  = max(5,  int(15 * (_frame_pixels / _REF_PIXELS) ** 0.5))
        edge_margin   = max(20, int(40 * (_frame_pixels / _REF_PIXELS) ** 0.5))
        std_min_dim   = max(8,  int(15 * (_frame_pixels / _REF_PIXELS) ** 0.5))
        logger.debug(
            f"WizardService: frame={img_w}×{img_h} area_scale={_area_scale:.3f} "
            f"min_area={scaled_min_area} max_area={scaled_max_area}"
        )

        # Border insets
        bt = int(img_h * border.get("top", 0) / 100)
        br = int(img_w * border.get("right", 0) / 100)
        bb = int(img_h * border.get("bottom", 0) / 100)
        bl = int(img_w * border.get("left", 0) / 100)
        ax1, ay1 = bl, bt
        ax2, ay2 = img_w - br, img_h - bb
        aw = max(1.0, float(ax2 - ax1))
        ah = max(1.0, float(ay2 - ay1))
        cx_mid = (ax1 + ax2) / 2.0
        cy_mid = (ay1 + ay2) / 2.0
        h_tan = math.tan(math.radians(float(h_angle_deg or 0.0)))
        v_tan = math.tan(math.radians(float(v_angle_deg or 0.0)))

        # Apply exclusion mask before inference
        working_frame = DetectionService.apply_exclusion_mask(frame, exclude_regions, border)

        # Build segment list from user's grid
        h_bounds = [0.0] + h_lines + [1.0]
        v_bounds = [0.0] + v_lines + [1.0]
        rows = len(h_bounds) - 1
        cols_n = len(v_bounds) - 1
        if len(h_line_angles) < len(h_lines):
            h_line_angles = h_line_angles + [float(h_angle_deg or 0.0)] * (len(h_lines) - len(h_line_angles))
        if len(v_line_angles) < len(v_lines):
            v_line_angles = v_line_angles + [float(v_angle_deg or 0.0)] * (len(v_lines) - len(v_line_angles))
        h_boundary_angles = [0.0] + h_line_angles[:len(h_lines)] + [0.0]
        v_boundary_angles = [0.0] + v_line_angles[:len(v_lines)] + [0.0]

        segments = []

        def _intersect_hv(hf: float, vf: float, h_boundary_angle: float, v_boundary_angle: float) -> Tuple[float, float]:
            y0 = ay1 + ah * hf
            x0 = ax1 + aw * vf
            h_tan_b = math.tan(math.radians(float(h_boundary_angle or 0.0)))
            v_tan_b = math.tan(math.radians(float(v_boundary_angle or 0.0)))
            a1 = -h_tan_b
            b1 = 1.0
            c1 = y0 - h_tan_b * cx_mid
            a2 = 1.0
            b2 = -v_tan_b
            c2 = x0 - v_tan_b * cy_mid
            det = a1 * b2 - a2 * b1
            if abs(det) < 1e-6:
                return x0, y0
            x = (c1 * b2 - c2 * b1) / det
            y = (a1 * c2 - a2 * c1) / det
            return x, y

        for r in range(rows):
            for c in range(cols_n):
                p_tl = _intersect_hv(h_bounds[r], v_bounds[c], h_boundary_angles[r], v_boundary_angles[c])
                p_tr = _intersect_hv(h_bounds[r], v_bounds[c + 1], h_boundary_angles[r], v_boundary_angles[c + 1])
                p_br = _intersect_hv(h_bounds[r + 1], v_bounds[c + 1], h_boundary_angles[r + 1], v_boundary_angles[c + 1])
                p_bl = _intersect_hv(h_bounds[r + 1], v_bounds[c], h_boundary_angles[r + 1], v_boundary_angles[c])

                sx1 = int(max(0, math.floor(min(p_tl[0], p_tr[0], p_br[0], p_bl[0]))))
                sy1 = int(max(0, math.floor(min(p_tl[1], p_tr[1], p_br[1], p_bl[1]))))
                sx2 = int(min(img_w, math.ceil(max(p_tl[0], p_tr[0], p_br[0], p_bl[0]))))
                sy2 = int(min(img_h, math.ceil(max(p_tl[1], p_tr[1], p_br[1], p_bl[1]))))
                pad_x = int((sx2 - sx1) * seg_overlap)
                pad_y = int((sy2 - sy1) * seg_overlap)
                segments.append((
                    max(sx1 - (pad_x if c > 0 else 0), 0),
                    max(sy1 - (pad_y if r > 0 else 0), 0),
                    min(sx2 + (pad_x if c < cols_n - 1 else 0), img_w),
                    min(sy2 + (pad_y if r < rows - 1 else 0), img_h),
                ))

        # Run inference on each segment
        start = time.time()
        all_bboxes: List[Tuple[int, int, int, int]] = []
        all_scores: List[float] = []

        for sx1, sy1, sx2, sy2 in segments:
            crop = working_frame[sy1:sy2, sx1:sx2]
            if crop.size == 0:
                continue
            try:
                detections = preview_detector._predict_detections(crop)
            except Exception as e:
                logger.warning(f"WizardService: RF-DETR error on segment: {e}")
                continue

            for (bx1, by1, bx2, by2), box_conf, name in detections:
                if name not in _AERIAL_CLASSES and name not in {"vehicle", "cars", "car"}:
                    continue
                bw, bh = bx2 - bx1, by2 - by1
                area = bw * bh

                # Full-frame coordinates (used for edge check)
                fx1 = int(bx1 + sx1)
                fy1 = int(by1 + sy1)
                fx2 = int(bx2 + sx1)
                fy2 = int(by2 + sy1)

                # Edge-relaxed thresholds for partially-visible vehicles
                near_edge = (
                    fx1 < edge_margin or fy1 < edge_margin
                    or fx2 > img_w - edge_margin
                    or fy2 > img_h - edge_margin
                )
                min_area = edge_min_area if near_edge else scaled_min_area
                min_dim  = edge_min_dim  if near_edge else std_min_dim
                max_ar   = 6.0           if near_edge else 5.0

                if area < min_area or area > scaled_max_area:
                    continue
                if min(bw, bh) < min_dim or max(bw, bh) / max(min(bw, bh), 1) > max_ar:
                    continue
                all_bboxes.append((fx1, fy1, fx2, fy2))
                all_scores.append(float(box_conf))

        # NMS dedup
        all_bboxes, all_scores = _nms(all_bboxes, all_scores, iou_thresh=nms_iou)
        elapsed_ms = (time.time() - start) * 1000

        # Annotate
        annotated = _annotate(
            frame,
            all_bboxes,
            all_scores,
            h_lines,
            v_lines,
            h_line_angles,
            v_line_angles,
            exclude_regions,
            ax1,
            ay1,
            ax2,
            ay2,
        )
        return annotated, len(all_bboxes), elapsed_ms


# ── Private helpers ───────────────────────────────────────────────────────────

def _resolve_model_path(model_path: str, project_root: str) -> str:
    """Turn a relative training run path into an absolute path."""
    if not model_path:
        return "base"
    if os.path.isabs(model_path):
        return model_path
    if "/" in model_path or os.sep in model_path:
        full = os.path.normpath(os.path.join(project_root, model_path))
        return full if os.path.isfile(full) else model_path
    return model_path  # bare name like "base"


def _nms(
    bboxes: List[Tuple[int, int, int, int]],
    scores: List[float],
    iou_thresh: float = 0.4,
) -> Tuple[List[Tuple[int, int, int, int]], List[float]]:
    """Non-Maximum Suppression."""
    if not bboxes:
        return [], []
    boxes = np.array(bboxes, dtype=np.float32)
    sc = np.array(scores, dtype=np.float32)
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = sc.argsort()[::-1]
    keep: List[int] = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
        order = order[np.where(iou <= iou_thresh)[0] + 1]
    return [bboxes[k] for k in keep], [scores[k] for k in keep]


def _annotate(
    frame: np.ndarray,
    bboxes: List[Tuple[int, int, int, int]],
    scores: List[float],
    h_lines: List[float],
    v_lines: List[float],
    h_line_angles: List[float],
    v_line_angles: List[float],
    exclude_regions: List[List[List[float]]],
    ax1: int, ay1: int, ax2: int, ay2: int,
) -> np.ndarray:
    if not CV_AVAILABLE:
        return frame
    out = frame.copy()
    img_h, img_w = out.shape[:2]

    # Scale annotation sizes to actual frame resolution (calibrated at 1280×720)
    _ref_diag = (1280**2 + 720**2) ** 0.5
    _diag     = (img_w**2 + img_h**2) ** 0.5
    _scale    = _diag / _ref_diag
    _box_thick  = max(1, round(2 * _scale))
    _ovl_scale  = max(0.5, 0.8 * _scale)
    _ovl_thick  = max(1, round(2 * _scale))

    for (bx1, by1, bx2, by2), score in zip(bboxes, scores):
        color = (0, 255, 0) if score >= 0.5 else (0, 200, 255) if score >= 0.25 else (0, 255, 255)
        cv2.rectangle(out, (bx1, by1), (bx2, by2), color, _box_thick)

    _grid_thick = max(1, round(2 * _scale))
    aw = max(1.0, float(ax2 - ax1))
    ah = max(1.0, float(ay2 - ay1))
    cx_mid = (ax1 + ax2) / 2.0
    cy_mid = (ay1 + ay2) / 2.0

    for idx, frac in enumerate(h_lines):
        h_tan = math.tan(math.radians(float(h_line_angles[idx] if idx < len(h_line_angles) else 0.0)))
        y0 = ay1 + ah * frac
        y1 = y0 + h_tan * (ax1 - cx_mid)
        y2 = y0 + h_tan * (ax2 - cx_mid)
        cv2.line(out, (int(ax1), int(round(y1))), (int(ax2), int(round(y2))), (255, 255, 0), _grid_thick)

    for idx, frac in enumerate(v_lines):
        v_tan = math.tan(math.radians(float(v_line_angles[idx] if idx < len(v_line_angles) else 0.0)))
        x0 = ax1 + aw * frac
        x1 = x0 + v_tan * (ay1 - cy_mid)
        x2 = x0 + v_tan * (ay2 - cy_mid)
        cv2.line(out, (int(round(x1)), int(ay1)), (int(round(x2)), int(ay2)), (255, 0, 255), _grid_thick)

    if exclude_regions:
        overlay = out.copy()
        for region in exclude_regions:
            pts = []
            for pt in region:
                if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                    continue
                px = int(round(ax1 + aw * float(pt[0])))
                py = int(round(ay1 + ah * float(pt[1])))
                pts.append([px, py])
            if len(pts) >= 3:
                poly = np.array(pts, dtype=np.int32)
                cv2.fillPoly(overlay, [poly], (0, 0, 255))
                cv2.polylines(out, [poly], True, (0, 0, 255), _grid_thick)
        cv2.addWeighted(overlay, 0.2, out, 0.8, 0, out)

    cv2.rectangle(out, (ax1, ay1), (ax2, ay2), (0, 0, 255), _grid_thick)
    cy = max(20, int(30 * _scale))
    cv2.putText(out, f"Vehicles: {len(bboxes)}", (10, cy),
                cv2.FONT_HERSHEY_SIMPLEX, _ovl_scale, (0, 0, 255), _ovl_thick)
    return out
