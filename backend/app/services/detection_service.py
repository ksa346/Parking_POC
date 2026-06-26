"""
RF-DETR Detection Service.

Uses a 6-segment grid (2 columns × 3 rows) strategy that mirrors the
physical layout of the parking lot. Each segment is run through RF-DETR
independently, then remapped to full-frame coordinates and deduplicated.
"""
import logging
import importlib
import os
import tempfile
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

try:
    import cv2

    CV_AVAILABLE = True
except ImportError:
    CV_AVAILABLE = False

try:
    import rfdetr  # noqa: F401

    RFDETR_AVAILABLE = True
except ImportError:
    RFDETR_AVAILABLE = False
    logger.warning("rfdetr not installed — RF-DETR detection unavailable")

try:
    from ultralytics import YOLO

    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False
    YOLO = None
    logger.warning("ultralytics not installed — YOLO detection unavailable")

# COCO class IDs for vehicles
VEHICLE_CLASSES = {"car"}

# In aerial/top-down parking-lot footage, COCO-pretrained detectors
# COCO images) systematically mis-classifies cars as everyday rectangular
# objects.  We accept ALL classes that the model commonly fires on cars
# and rely on bounding-box geometry (size + aspect ratio) to filter.
AERIAL_ACCEPT_CLASSES = {"car"}

# Pixel-area bounds for a vehicle in 3840x2160 footage.
# A typical car is roughly 40-120 px wide × 60-160 px tall.
MIN_VEHICLE_AREA = 800    # < this is noise / artefacts
MAX_VEHICLE_AREA = 80000  # > this is a building / road segment
MIN_VEHICLE_DIM  = 15     # minimum width or height in px
MAX_ASPECT_RATIO = 5.0    # reject very elongated slivers

# Relaxed thresholds for vehicles near frame edges (partially visible)
EDGE_MIN_VEHICLE_AREA = 300   # partial vehicles can be smaller
EDGE_MIN_VEHICLE_DIM  = 10    # partial vehicles may be narrower
EDGE_MAX_ASPECT_RATIO = 6.0   # partial crops may be more elongated
EDGE_MARGIN_PX = 40           # pixels from frame edge to use relaxed rules

# 6-segment grid: 2 columns × 3 rows with overlap at boundaries
SEGMENT_COLS = 2
SEGMENT_ROWS = 3
SEGMENT_OVERLAP = 0.12    # 12 % overlap on each boundary edge
NMS_IOU_THRESHOLD = 0.25  # IoU threshold for cross-segment NMS (lower = more aggressive dedup)


class DetectionService:
    """Vehicle detection using YOLOv8 or RF-DETR with 6-segment grid inference."""

    def __init__(
        self,
        model_path: Optional[str] = None,
        confidence: float = 0.15,
    ):
        self.model_path = model_path or os.getenv("RFDETR_MODEL_PATH", "base")
        self.confidence = confidence

        self._model: Optional[object] = None
        self._backend: str = ""
        self._class_names: List[str] = []
        self.last_error: str = ""
        self.loaded = False
        self.exclude_regions: List[List[Tuple[float, float]]] = []
        self.exclude_border: Dict[str, float] = {"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0}

    @staticmethod
    def _is_yolo_model_spec(model_spec: str) -> bool:
        spec = (model_spec or "").strip().lower()
        return spec.startswith("yolo") or "yolo" in os.path.basename(spec)

    @staticmethod
    def _resolve_model_spec_path(model_spec: str) -> str:
        spec = (model_spec or "").strip()
        if os.path.isabs(spec):
            return spec
        if os.path.sep in spec or "/" in spec:
            project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
            return os.path.normpath(os.path.join(project_root, spec))
        return spec

    def _load_yolo_model(self, model_spec: str) -> object:
        if not YOLO_AVAILABLE:
            raise RuntimeError("ultralytics not installed")
        spec = self._resolve_model_spec_path(model_spec or "yolov8n.pt")
        return YOLO(spec)

    def set_exclude_regions(
        self,
        regions: Optional[List[List[List[float]]]],
        border: Optional[Dict[str, float]] = None,
    ) -> None:
        """Set polygon exclusion regions in normalized active-region coordinates.

        Each region is a list of points ``[x_frac, y_frac]`` where each
        coordinate is in ``[0,1]`` within the active region (after border inset).
        """
        cleaned: List[List[Tuple[float, float]]] = []
        for region in regions or []:
            pts: List[Tuple[float, float]] = []
            if not isinstance(region, list):
                continue
            for pt in region:
                if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                    continue
                try:
                    x = float(pt[0])
                    y = float(pt[1])
                except (TypeError, ValueError):
                    continue
                pts.append((min(1.0, max(0.0, x)), min(1.0, max(0.0, y))))
            if len(pts) >= 3:
                cleaned.append(pts)
        self.exclude_regions = cleaned
        if border:
            self.exclude_border = {
                "top": float(border.get("top", 0.0)),
                "right": float(border.get("right", 0.0)),
                "bottom": float(border.get("bottom", 0.0)),
                "left": float(border.get("left", 0.0)),
            }

    @staticmethod
    def _get_rfdetr_class(name: str):
        module = importlib.import_module("rfdetr")
        cls = getattr(module, name, None)
        if cls is not None:
            return cls
        for module_name in ("rfdetr.variants", "rfdetr.detr"):
            try:
                submod = importlib.import_module(module_name)
                cls = getattr(submod, name, None)
                if cls is not None:
                    return cls
            except Exception:
                continue
        return None

    def _load_rfdetr_model(self, model_spec: str) -> object:
        spec = (model_spec or "base").strip()
        if spec.endswith((".pth", ".ckpt")) or os.path.sep in spec or "/" in spec:
            resolved = spec
            if not os.path.isabs(resolved):
                project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
                resolved = os.path.normpath(os.path.join(project_root, resolved))
            root_cls = self._get_rfdetr_class("RFDETR")
            if root_cls is not None and hasattr(root_cls, "from_checkpoint") and os.path.exists(resolved):
                try:
                    return root_cls.from_checkpoint(resolved)
                except Exception:
                    pass
            for cls_name in ("RFDETRBase", "RFDETRMedium", "RFDETRSmall", "RFDETRLarge", "RFDETRNano"):
                cls = self._get_rfdetr_class(cls_name)
                if cls is None:
                    continue
                if hasattr(cls, "from_checkpoint") and os.path.exists(resolved):
                    try:
                        return cls.from_checkpoint(resolved)
                    except Exception:
                        pass
                if os.path.exists(resolved):
                    for kw in ("pretrain_weights", "checkpoint", "weights"):
                        try:
                            return cls(**{kw: resolved})
                        except TypeError:
                            continue
                        except Exception:
                            pass
            raise RuntimeError(f"Unable to load RF-DETR checkpoint: {resolved}")

        size_alias = {
            "base": "RFDETRBase",
            "nano": "RFDETRNano",
            "small": "RFDETRSmall",
            "medium": "RFDETRMedium",
            "large": "RFDETRLarge",
            "rf-detr-base": "RFDETRBase",
        }
        size_key = spec.lower().replace(".pth", "")
        cls_name = size_alias.get(size_key, "RFDETRBase")
        cls = self._get_rfdetr_class(cls_name)
        if cls is None and cls_name == "RFDETRBase":
            cls = self._get_rfdetr_class("RFDETRMedium")
        if cls is None:
            raise RuntimeError(f"Unable to resolve RF-DETR model class for: {spec}")
        return cls()

    # ── Model loading ────────────────────────────────────────────────
    def load_model(self) -> bool:
        """Load the configured model backend. Returns True on success."""
        self.last_error = ""
        try:
            use_yolo = self._is_yolo_model_spec(self.model_path)
            if use_yolo:
                logger.info(f"Loading YOLO model: {self.model_path}")
                self._model = self._load_yolo_model(self.model_path)
                self._backend = "yolo"
                self._class_names = []
            else:
                if not RFDETR_AVAILABLE:
                    self.last_error = "rfdetr not installed"
                    logger.error(self.last_error)
                    return False
                logger.info(f"Loading RF-DETR model: {self.model_path}")
                self._model = self._load_rfdetr_model(self.model_path)
                self._backend = "rfdetr"
                self._class_names = [str(v) for v in (getattr(self._model, "class_names", []) or [])]
            self.loaded = True
            logger.info(f"Model loaded successfully (backend={self._backend})")
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Failed to load model: {e}")
            return False

    def _predict_detections(self, image: np.ndarray) -> List[Tuple[Tuple[float, float, float, float], float, str]]:
        if self._model is None:
            return []
        if image is None or image.size == 0:
            return []

        if self._backend == "yolo":
            try:
                results = self._model.predict(source=image, conf=self.confidence, verbose=False)
            except Exception:
                return []
            if not results:
                return []
            result = results[0]
            boxes_obj = getattr(result, "boxes", None)
            names_map = getattr(result, "names", {}) or {}
            if boxes_obj is None or len(boxes_obj) == 0:
                return []

            parsed: List[Tuple[Tuple[float, float, float, float], float, str]] = []
            for box in boxes_obj:
                xyxy = box.xyxy[0].tolist()
                conf = float(box.conf[0])
                cid = int(box.cls[0])
                cname = str(names_map.get(cid, cid))
                parsed.append(((float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])), conf, cname))
            return parsed

        # RF-DETR Python API in this project accepts a file path; write a temp image.
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            # OpenCV frames are BGR; convert to RGB PIL for consistent export.
            img_rgb = image[:, :, ::-1]
            Image.fromarray(img_rgb).save(tmp_path, format="JPEG", quality=95)
            detections = self._model.predict(tmp_path, threshold=self.confidence)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        if detections is None or len(detections) == 0:
            return []

        boxes = detections.xyxy.tolist()
        class_ids = detections.class_id.tolist()
        confidences = detections.confidence.tolist()
        parsed: List[Tuple[Tuple[float, float, float, float], float, str]] = []
        for box, cid, conf in zip(boxes, class_ids, confidences):
            cname = str(int(cid))
            if self._class_names and 0 <= int(cid) < len(self._class_names):
                cname = self._class_names[int(cid)]
            parsed.append(((float(box[0]), float(box[1]), float(box[2]), float(box[3])), float(conf), cname))
        return parsed

    # ── Public detection API ─────────────────────────────────────────
    def detect_vehicles(
        self, frame: np.ndarray
    ) -> Tuple[int, List[Tuple[int, int, int, int]], List[float], List[str], float]:
        """Detect vehicles in *frame*.

        Returns ``(count, bounding_boxes_xyxy, confidence_scores, class_names, processing_ms)``.
        Each bounding box is ``(x1, y1, x2, y2)`` in pixel coordinates.
        """
        start = time.time()

        if not self.loaded:
            elapsed = (time.time() - start) * 1000
            return 0, [], [], [], elapsed

        h, w = frame.shape[:2]
        logger.info(f"Frame size: {w}x{h}")

        # Apply exclusion mask before inference, if configured.
        masked = self.apply_exclusion_mask(frame, self.exclude_regions, self.exclude_border)

        # Primary strategy: 6-segment grid (2 cols × 3 rows) for high
        # accuracy on aerial parking-lot imagery.
        count, bboxes, scores, class_names = self._detect_segmented(masked)

        elapsed = (time.time() - start) * 1000
        logger.info(f"Detected {count} vehicles in {elapsed:.0f} ms")
        return count, bboxes, scores, class_names, elapsed

    def annotate_frame(
        self,
        frame: np.ndarray,
        bboxes: List[Tuple[int, int, int, int]],
        scores: List[float],
        double_parked_indices: Optional[set] = None,
        border: Optional[Dict[str, float]] = None,
        exclude_regions: Optional[List[List[Tuple[float, float]]]] = None,
    ) -> np.ndarray:
        """Draw bounding boxes and segment grid on *frame*."""
        if not CV_AVAILABLE:
            return frame

        annotated = frame.copy()
        h, w = annotated.shape[:2]
        dp_set = double_parked_indices or set()

        # Scale annotation sizes to the actual frame resolution.
        # Constants calibrated at 1280×720 (the resized output resolution).
        _ref_diag = (1280**2 + 720**2) ** 0.5
        _diag     = (w**2 + h**2) ** 0.5
        _scale    = _diag / _ref_diag          # 1.0 at 1280×720, ~3.5 at 4K
        box_thick_normal = max(1, round(2 * _scale))
        box_thick_dp     = max(2, round(3 * _scale))
        text_scale       = max(0.4, 0.6 * _scale)
        text_thick       = max(1, round(1 * _scale))
        overlay_scale    = max(0.5, 0.8 * _scale)
        overlay_thick    = max(1, round(2 * _scale))

        # Draw segment grid lines (cyan) to visualise the 6 segments
        col_w = w // SEGMENT_COLS
        row_h = h // SEGMENT_ROWS
        for c in range(1, SEGMENT_COLS):
            x = c * col_w
            cv2.line(annotated, (x, 0), (x, h), (255, 255, 0), 2)
        for r in range(1, SEGMENT_ROWS):
            y = r * row_h
            cv2.line(annotated, (0, y), (w, y), (255, 255, 0), 2)

        # Draw exclusion regions (semi-transparent red)
        if exclude_regions:
            b = border or {"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0}
            bt = h * float(b.get("top", 0.0)) / 100.0
            br = w * float(b.get("right", 0.0)) / 100.0
            bb = h * float(b.get("bottom", 0.0)) / 100.0
            bl = w * float(b.get("left", 0.0)) / 100.0
            ax1, ay1 = bl, bt
            ax2, ay2 = w - br, h - bb
            aw = max(1.0, ax2 - ax1)
            ah = max(1.0, ay2 - ay1)

            overlay = annotated.copy()
            for region in exclude_regions:
                pts = []
                for fx, fy in region:
                    px = int(round(ax1 + aw * float(fx)))
                    py = int(round(ay1 + ah * float(fy)))
                    pts.append([px, py])
                if len(pts) >= 3:
                    poly = np.array(pts, dtype=np.int32)
                    cv2.fillPoly(overlay, [poly], (0, 0, 255))
                    cv2.polylines(annotated, [poly], True, (0, 0, 255), max(1, round(2 * _scale)))
            cv2.addWeighted(overlay, 0.2, annotated, 0.8, 0, annotated)

        for i, ((x1, y1, x2, y2), score) in enumerate(zip(bboxes, scores)):
            if i in dp_set:
                color = (255, 0, 255)  # magenta — double-parked
                thickness = box_thick_dp
            elif score >= 0.5:
                color = (0, 255, 0)   # green — high confidence
                thickness = box_thick_normal
            elif score >= 0.25:
                color = (0, 200, 255)  # orange — medium
                thickness = max(1, box_thick_normal - 1)
            else:
                color = (0, 255, 255)  # yellow — low
                thickness = max(1, box_thick_normal - 1)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, thickness)

        # Count overlay
        dp_count = len(dp_set)
        text = f"Vehicles: {len(bboxes)}"
        if dp_count > 0:
            text += f" | DP: {dp_count}"
        cy = max(20, int(30 * _scale))
        cv2.putText(annotated, text, (10, cy), cv2.FONT_HERSHEY_SIMPLEX, overlay_scale, (0, 0, 255), overlay_thick)
        return annotated

    @staticmethod
    def apply_exclusion_mask(
        frame: np.ndarray,
        exclude_regions: Optional[List[List[Tuple[float, float]]]] = None,
        border: Optional[Dict[str, float]] = None,
    ) -> np.ndarray:
        """Mask out exclusion polygons (black pixels) before detection."""
        if not CV_AVAILABLE or frame is None or not exclude_regions:
            return frame

        h, w = frame.shape[:2]
        b = border or {"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0}
        bt = h * float(b.get("top", 0.0)) / 100.0
        br = w * float(b.get("right", 0.0)) / 100.0
        bb = h * float(b.get("bottom", 0.0)) / 100.0
        bl = w * float(b.get("left", 0.0)) / 100.0
        ax1, ay1 = bl, bt
        ax2, ay2 = w - br, h - bb
        aw = max(1.0, ax2 - ax1)
        ah = max(1.0, ay2 - ay1)

        out = frame.copy()
        for region in exclude_regions:
            pts = []
            for fx, fy in region:
                px = int(round(ax1 + aw * float(fx)))
                py = int(round(ay1 + ah * float(fy)))
                pts.append([px, py])
            if len(pts) >= 3:
                poly = np.array(pts, dtype=np.int32)
                cv2.fillPoly(out, [poly], (0, 0, 0))
        return out

    # ── 6-Segment grid detection (primary strategy) ──────────────────
    @staticmethod
    def _compute_segments(
        frame_w: int, frame_h: int
    ) -> List[Tuple[int, int, int, int]]:
        """Return a list of (x1, y1, x2, y2) crops for the 6-segment grid.

        Each segment is *expanded* by ``SEGMENT_OVERLAP`` of the segment
        size on every interior edge so vehicles straddling a boundary
        appear fully in at least one segment.
        """
        col_w = frame_w / SEGMENT_COLS
        row_h = frame_h / SEGMENT_ROWS
        pad_x = int(col_w * SEGMENT_OVERLAP)
        pad_y = int(row_h * SEGMENT_OVERLAP)

        segments: List[Tuple[int, int, int, int]] = []
        for r in range(SEGMENT_ROWS):
            for c in range(SEGMENT_COLS):
                x1 = int(c * col_w) - (pad_x if c > 0 else 0)
                y1 = int(r * row_h) - (pad_y if r > 0 else 0)
                x2 = int((c + 1) * col_w) + (pad_x if c < SEGMENT_COLS - 1 else 0)
                y2 = int((r + 1) * row_h) + (pad_y if r < SEGMENT_ROWS - 1 else 0)
                # Clamp to frame bounds
                segments.append((
                    max(x1, 0),
                    max(y1, 0),
                    min(x2, frame_w),
                    min(y2, frame_h),
                ))
        return segments

    @staticmethod
    def _nms(
        bboxes: List[Tuple[int, int, int, int]],
        scores: List[float],
        iou_thresh: float = NMS_IOU_THRESHOLD,
        class_names: Optional[List[str]] = None,
    ) -> Tuple[List[Tuple[int, int, int, int]], List[float], List[str]]:
        """Non-Maximum Suppression to deduplicate cross-segment detections."""
        if not bboxes:
            return [], [], []

        boxes_arr = np.array(bboxes, dtype=np.float32)
        scores_arr = np.array(scores, dtype=np.float32)

        x1 = boxes_arr[:, 0]
        y1 = boxes_arr[:, 1]
        x2 = boxes_arr[:, 2]
        y2 = boxes_arr[:, 3]
        areas = (x2 - x1) * (y2 - y1)

        order = scores_arr.argsort()[::-1]
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

            remaining = np.where(iou <= iou_thresh)[0]
            order = order[remaining + 1]

        kept_names = [class_names[k] for k in keep] if class_names else ["unknown"] * len(keep)
        return [bboxes[k] for k in keep], [scores[k] for k in keep], kept_names

    def _detect_segmented(
        self, frame: np.ndarray
    ) -> Tuple[int, List[Tuple[int, int, int, int]], List[float]]:
        """Run RF-DETR on each segment, remap and merge results."""
        h, w = frame.shape[:2]
        segments = self._compute_segments(w, h)
        logger.info(
            f"Running 6-segment detection ({SEGMENT_COLS}×{SEGMENT_ROWS}), "
            f"overlap={SEGMENT_OVERLAP:.0%}"
        )

        # Scale area/dimension thresholds proportionally to the actual frame size.
        # All constants are calibrated for 3840×2160 (4K); scale linearly.
        _REF_AREA = 3840 * 2160
        frame_scale = (w * h) / _REF_AREA
        scaled_min_area      = max(30,  int(MIN_VEHICLE_AREA      * frame_scale))
        scaled_max_area      = max(1000, int(MAX_VEHICLE_AREA      * frame_scale))
        scaled_min_dim       = max(4,   int(MIN_VEHICLE_DIM        * (min(w, h) / 2160)))
        scaled_edge_min_area = max(15,  int(EDGE_MIN_VEHICLE_AREA  * frame_scale))
        scaled_edge_min_dim  = max(2,   int(EDGE_MIN_VEHICLE_DIM   * (min(w, h) / 2160)))
        logger.info(
            f"Frame {w}x{h} (scale={frame_scale:.4f}): "
            f"min_area={scaled_min_area} max_area={scaled_max_area} "
            f"min_dim={scaled_min_dim}"
        )

        all_bboxes: List[Tuple[int, int, int, int]] = []
        all_scores: List[float] = []
        all_names: List[str] = []

        for idx, (sx1, sy1, sx2, sy2) in enumerate(segments):
            crop = frame[sy1:sy2, sx1:sx2]
            try:
                detections = self._predict_detections(crop)
            except Exception as e:
                logger.error(f"RF-DETR segment {idx} error: {e}")
                continue

            seg_count = 0
            rejected_class = 0
            rejected_geom = 0

            for (bx1, by1, bx2, by2), conf, name in detections:

                    # Class filter — keep cars only.
                    if str(name).strip().lower() not in AERIAL_ACCEPT_CLASSES:
                        rejected_class += 1
                        continue

                    bw = bx2 - bx1
                    bh = by2 - by1
                    area = bw * bh
                    aspect = max(bw, bh) / max(min(bw, bh), 1)

                    # Remap to full-frame coordinates first
                    fx1 = int(bx1 + sx1)
                    fy1 = int(by1 + sy1)
                    fx2 = int(bx2 + sx1)
                    fy2 = int(by2 + sy1)

                    # Use relaxed thresholds for vehicles near frame edges
                    # (partially visible vehicles are smaller / cropped)
                    near_edge = (
                        fx1 < EDGE_MARGIN_PX
                        or fy1 < EDGE_MARGIN_PX
                        or fx2 > w - EDGE_MARGIN_PX
                        or fy2 > h - EDGE_MARGIN_PX
                    )
                    min_area = scaled_edge_min_area if near_edge else scaled_min_area
                    min_dim  = scaled_edge_min_dim  if near_edge else scaled_min_dim
                    max_ar   = EDGE_MAX_ASPECT_RATIO if near_edge else MAX_ASPECT_RATIO

                    if (area < min_area or area > scaled_max_area
                            or min(bw, bh) < min_dim
                            or aspect > max_ar):
                        rejected_geom += 1
                        continue
                    all_bboxes.append((fx1, fy1, fx2, fy2))
                    all_scores.append(round(float(conf), 3))
                    all_names.append(name)
                    seg_count += 1

            logger.info(
                f"Segment {idx} [{sx1},{sy1} → {sx2},{sy2}]: "
                f"{seg_count} vehicles "
                f"(rejected: {rejected_class} class, {rejected_geom} geom)"
            )

        # Deduplicate detections from overlapping boundaries
        before_nms = len(all_bboxes)
        all_bboxes, all_scores, all_names = self._nms(
            all_bboxes, all_scores, class_names=all_names,
        )
        logger.info(
            f"6-segment totals: {before_nms} raw → "
            f"{len(all_bboxes)} after NMS (removed {before_nms - len(all_bboxes)} dupes)"
        )

        return len(all_bboxes), all_bboxes, all_scores, all_names

    # ── Direct RF-DETR inference (fallback) ──────────────────────────
    def _detect_direct(
        self, frame: np.ndarray
    ) -> Tuple[int, List[Tuple[int, int, int, int]], List[float], List[str]]:
        """Standard single-pass RF-DETR inference (fallback)."""
        try:
            detections = self._predict_detections(frame)
            bboxes: List[Tuple[int, int, int, int]] = []
            scores: List[float] = []
            names: List[str] = []
            for (x1, y1, x2, y2), conf, name in detections:
                if str(name).strip().lower() in VEHICLE_CLASSES:
                    bboxes.append((int(x1), int(y1), int(x2), int(y2)))
                    scores.append(round(float(conf), 3))
                    names.append(name)
            return len(bboxes), bboxes, scores, names
        except Exception as e:
            logger.error(f"Direct RF-DETR detection error: {e}")
            return 0, [], [], []
