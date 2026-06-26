"""
Smart Parking Dashboard — FastAPI Application
Real-time parking occupancy detection using RF-DETR 6-segment grid + GPT chat
"""
import asyncio
import base64
from collections import deque
import json
import logging
import math
import os
import re
import time
import uuid
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from fastapi import FastAPI, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, FileResponse, StreamingResponse


from app.models.schemas import (
    ChatRequest,
    ChatResponse,
    ForecastRequest,
    ForecastResponse,
    GPTEstimateRequest,
    GPTEstimateResponse,
    GridConfig,
    HealthResponse,
    HistoryEntry,
    OccupancyResponse,
    PublishLocationRequest,
    PublishLocationResponse,
    TuneParametersRequest,
    ZoneSpotConfig,
)
from app.services.chat_service import ChatService
from app.services.detection_service import DetectionService
from app.services.parking_service import ParkingService
from app.services.stream_service import StreamService
from app.services.wizard_service import WizardService
from app.yolo_tool import init_db as _yolo_init_db
from app.yolo_tool.router import router as yolo_router
from app.yolotracker_entry_exit import websocket_live_entry_exit_counts_new
from app.run_job import run_job
from app.video_process_count import VideoProcessCount
from app.video_process_count import build_regions

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# Root of the repo — used to locate training/runs/
_PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_RUNS_DIR = os.path.join(_PROJECT_ROOT, "training", "runs")

# ── Global services ──────────────────────────────────────────────────
parking_service: ParkingService = None
stream_service: StreamService = None
chat_service: ChatService = None
connected_clients: List[WebSocket] = []

# Per-location pipeline pool.
# Key "default" = shared pipeline for all demo/hardcoded locations (RF-DETR base + default video).
# Key <location_id> = dedicated pipeline for that published location.
# Switching locations just reassigns parking_service/stream_service — no video swapping or
# model reloading mid-cycle.
_pipeline_pool: Dict[str, tuple] = {}

# Default video path — restored when user switches to a demo location
_DEFAULT_VIDEO_PATH: str = ""

# ── Separate model state per persona ─────────────────────────────────
# Developer wizard: stores which model the developer selected for preview.
# This NEVER touches parking_service.detection_service.
_dev_model_path: str = ""

# Cache entry/exit detectors to avoid reloading model weights on every request.
_entry_exit_detector_cache: Dict[Tuple[str, float], DetectionService] = {}
_entry_exit_detector_cache_lock = asyncio.Lock()

# Simple in-process settings store (survives the process lifetime only).
_settings: dict = {}


def _settings_set(key: str, value) -> None:
    _settings[key] = value


def _settings_get(key: str, default=None):
    return _settings.get(key, default)


def _resolve_startup_media_path() -> str:
    """Resolve startup media path for both Docker and local runs.

    Priority:
    1) Explicit VIDEO_PATH (if it exists)
    2) Known project-relative defaults for Parking_Lot_Video.mp4
    3) Most recently uploaded media in data/uploads
    4) Docker default path (last resort)
    """
    configured = os.getenv("VIDEO_PATH", "").strip()
    docker_default = "/app/video/Parking_Lot_Video.mp4"
    default_name = "Parking_Lot_Video.mp4"

    def _existing(paths: List[str]) -> Optional[str]:
        for p in paths:
            if p and os.path.exists(p):
                return os.path.normpath(p)
        return None

    if configured and os.path.exists(configured):
        return os.path.normpath(configured)

    # If a Docker-style path is configured during local Windows run,
    # remap it to the project-local backend/app/video folder.
    if configured.startswith("/app/video/"):
        local_from_docker = os.path.join(_PROJECT_ROOT, "backend", "app", "video", os.path.basename(configured))
        if os.path.exists(local_from_docker):
            local_from_docker = os.path.normpath(local_from_docker)
            logger.info(f"Mapped Docker VIDEO_PATH to local file: {local_from_docker}")
            return local_from_docker

    candidate_defaults = [
        os.path.join(_PROJECT_ROOT, "video", default_name),
        os.path.join(_PROJECT_ROOT, "backend", "video", default_name),
        os.path.join(_PROJECT_ROOT, "backend", "app", "video", default_name),
        os.path.join(_PROJECT_ROOT, "data", "uploads", default_name),
        os.path.join(_PROJECT_ROOT, "backend", "data", "uploads", default_name),
    ]
    found_default = _existing(candidate_defaults)
    if found_default:
        logger.info(f"Using startup media file: {found_default}")
        return found_default

    media_exts = (".mp4", ".avi", ".mkv", ".webm", ".mov", ".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".tiff")
    upload_dirs = [
        os.path.join(_PROJECT_ROOT, "data", "uploads"),
        os.path.join(_PROJECT_ROOT, "backend", "data", "uploads"),
    ]
    uploaded_files: List[str] = []
    for upload_dir in upload_dirs:
        if not os.path.isdir(upload_dir):
            continue
        for name in os.listdir(upload_dir):
            path = os.path.join(upload_dir, name)
            if os.path.isfile(path) and name.lower().endswith(media_exts):
                uploaded_files.append(path)

    if uploaded_files:
        latest = max(uploaded_files, key=os.path.getmtime)
        latest = os.path.normpath(latest)
        logger.info(f"Using latest uploaded media as startup source: {latest}")
        return latest

    if configured and not os.path.exists(configured):
        logger.warning(f"VIDEO_PATH is set but not found: {configured}. Falling back to {docker_default}")
    else:
        logger.warning(
            "No local startup media found. "
            f"Set VIDEO_PATH or place media at one of: {candidate_defaults}"
        )
    fallback = configured or docker_default
    if fallback and os.path.exists(fallback):
        return os.path.normpath(fallback)
    return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    global parking_service, stream_service, chat_service, _DEFAULT_VIDEO_PATH

    logger.info("Starting Smart Parking Backend…")

    # Initialise dataset tool SQLite DB
    _yolo_init_db()

    # Stream service — local video file
    video_path = _resolve_startup_media_path()
    _DEFAULT_VIDEO_PATH = video_path
    stream_service = StreamService(video_path=video_path)

    parking_service = ParkingService(
        stream_service=stream_service,
        total_spots=int(os.getenv("PARKING_TOTAL_SPOTS", "168")),
        zone_config={
            "TL": int(os.getenv("PARKING_ZONE_TL_SPOTS", "96")),
            "TR": int(os.getenv("PARKING_ZONE_TR_SPOTS", "94")),
            "ML": int(os.getenv("PARKING_ZONE_ML_SPOTS", "88")),
            "MR": int(os.getenv("PARKING_ZONE_MR_SPOTS", "90")),
            "BL": int(os.getenv("PARKING_ZONE_BL_SPOTS", "52")),
            "BR": int(os.getenv("PARKING_ZONE_BR_SPOTS", "54")),
        },
    )

    # Load detector model
    await parking_service.load_detection_model()

    # Register default pipeline — all demo/hardcoded locations share this
    _pipeline_pool["default"] = (stream_service, parking_service)

    # Chat service (GPT via pydantic-ai)
    chat_service = ChatService()

    # Background detection loop (only when startup stream is available)
    if stream_service and stream_service.is_active:
        update_interval = int(os.getenv("UPDATE_INTERVAL_SECONDS", "10"))
        asyncio.create_task(detection_loop(update_interval))
    else:
        logger.warning("Skipping background detection loop: startup stream is not active")

    logger.info("Backend initialised successfully")
    yield

    # Cleanup
    logger.info("Shutting down…")
    for _loc_id, (_ss, _ps) in list(_pipeline_pool.items()):
        try:
            _ss.release()
        except Exception:
            pass
    _pipeline_pool.clear()


async def detection_loop(interval: int):
    """Background loop for continuous detection."""
    while True:
        try:
            if parking_service:
                occupancy = await parking_service.run_detection()
                await broadcast_occupancy(occupancy)
        except Exception as e:
            logger.error(f"Detection loop error: {e}")
        await asyncio.sleep(interval)


async def broadcast_occupancy(occupancy: OccupancyResponse):
    """Broadcast occupancy to all connected WebSocket clients."""
    if not connected_clients:
        return
    message = occupancy.model_dump_json()
    disconnected = []
    for client in connected_clients:
        try:
            await client.send_text(message)
        except Exception:
            disconnected.append(client)
    for client in disconnected:
        connected_clients.remove(client)


# ── FastAPI app ──────────────────────────────────────────────────────
app = FastAPI(
    title="Smart Parking API",
    description="Real-time parking occupancy detection using RF-DETR 6-segment grid",
    version="3.0.0",
    lifespan=lifespan,
)

app.include_router(yolo_router)

_DEFAULT_ORIGINS = "https://parking.theaidaccelerators.com,http://localhost:8002,http://127.0.0.1:8002,http://10.0.1.68:8002,http://localhost:5001,http://127.0.0.1:5001,http://localhost:5002,http://127.0.0.1:5002,http://localhost:5003,http://127.0.0.1:5003"
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", _DEFAULT_ORIGINS).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def compat_v1_rewrite(request: Request, call_next):
    """Support proxies/clients that send /v1/* instead of /api/v1/*."""
    if request.url.path.startswith("/v1/"):
        request.scope["path"] = f"/api{request.url.path}"
    return await call_next(request)


# ── Endpoints ────────────────────────────────────────────────────────
# @app.get("/api/v1/health", response_model=HealthResponse)
# async def health_check():
#     return HealthResponse(
#         status="healthy",
#         version="3.0.0",
#         sam_loaded=parking_service.detection_loaded if parking_service else False,
#         stream_active=stream_service.is_active if stream_service else False,
#         openai_configured=chat_service.configured if chat_service else False,
    # )


# @app.get("/api/v1/occupancy", response_model=OccupancyResponse)
# async def get_occupancy():
#     if not parking_service:
#         return JSONResponse(status_code=503, content={"error": "Service not ready"})
#     return await parking_service.get_current_occupancy()


# @app.get("/api/v1/history", response_model=List[HistoryEntry])
# async def get_history(hours: int = 24):
#     if not parking_service:
#         return JSONResponse(status_code=503, content={"error": "Service not ready"})
#     return await parking_service.get_history(hours)


# @app.post("/api/v1/forecast", response_model=ForecastResponse)
# async def get_forecast(request: ForecastRequest):
#     if not parking_service:
#         return JSONResponse(status_code=503, content={"error": "Service not ready"})
#     return await parking_service.get_forecast(request.target_hour)


# @app.get("/api/v1/forecasts", response_model=List[ForecastResponse])
# async def get_all_forecasts():
#     """Return forecasts for all hours (0-23) in one call."""
#     if not parking_service:
#         return JSONResponse(status_code=503, content={"error": "Service not ready"})
#     results = []
#     for h in range(24):
#         f = await parking_service.get_forecast(h)
#         if f.based_on_samples > 0:
#             results.append(f)
#     return results


# @app.get("/api/v1/stats")
# async def get_stats():
#     if not parking_service:
#         return JSONResponse(status_code=503, content={"error": "Service not ready"})
#     return await parking_service.get_stats()


# @app.get("/api/v1/video/frame")
# async def get_video_frame():
#     """Return the latest annotated video frame as JPEG."""
#     if not stream_service:
#         return JSONResponse(status_code=503, content={"error": "Stream not ready"})
#     jpeg = stream_service.get_annotated_jpeg()
#     if jpeg is None:
#         return Response(status_code=204)
#     return Response(content=jpeg, media_type="image/jpeg")


# @app.get("/api/v1/video/raw")
# async def get_raw_frame():
#     """Return the latest raw (un-annotated) video frame as JPEG."""
#     if not stream_service:
#         return JSONResponse(status_code=503, content={"error": "Stream not ready"})
#     jpeg = stream_service.get_raw_jpeg()
#     if jpeg is None:
#         return Response(status_code=204)
#     return Response(content=jpeg, media_type="image/jpeg")


# @app.get("/api/v1/video/stream")
# async def get_video_file():
#     """Serve the MP4 video file for HTML5 <video> playback."""
#     if not stream_service:
#         return JSONResponse(status_code=503, content={"error": "Stream not ready"})
#     video_path = stream_service.video_path
#     if not os.path.isfile(video_path):
#         return JSONResponse(status_code=404, content={"error": "Video file not found"})
#     return FileResponse(
#         path=video_path,
#         media_type="video/mp4",
#         filename="parking_lot.mp4",
    # )


# @app.post("/api/v1/chat", response_model=ChatResponse)
# async def chat(request: ChatRequest):
#     """Chat with the GPT-powered parking assistant."""
#     if not chat_service:
#         return JSONResponse(status_code=503, content={"error": "Chat not ready"})
#     if not parking_service:
#         return JSONResponse(status_code=503, content={"error": "Parking service not ready"})

#     context = await parking_service.build_chat_context()
#     return await chat_service.chat(request, context)


# ── Developer Wizard Endpoints ───────────────────────────────────────

# Directory for uploaded videos
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "data", "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Default directory for saved entry/exit debug images used by backend endpoints.
ENTRY_EXIT_IMAGES_DIR = os.path.join(_PROJECT_ROOT, "blazor-frontend", "data", "entry_exit_images")

# Allowed video and image MIME types
_ALLOWED_VIDEO_TYPES = {
    "video/mp4", "video/mpeg", "video/x-msvideo", "video/quicktime",
    "video/x-matroska", "video/webm", "video/avi",
    "image/jpeg", "image/jpg", "image/png", "image/webp",
    "image/avif", "image/bmp", "image/tiff",
}


@app.post("/api/v1/developer/upload-video")
async def upload_video(file: UploadFile):
    """Upload a video file for the developer wizard.

    Saves the file to disk only — does NOT touch the live detection feed.
    The video will be applied to the live feed only when the developer
    publishes the location and a user activates it.
    """
    if not file.filename:
        return JSONResponse(status_code=400, content={"error": "No file provided"})

    # Validate content type
    ct = (file.content_type or "").lower()
    if ct not in _ALLOWED_VIDEO_TYPES:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported file type: {ct}. Upload a video (MP4, AVI, MKV, WebM) or image (JPG, PNG, AVIF, WebP)."},
        )

    # Sanitise filename — keep only alphanumeric, dash, underscore, dot
    import re
    safe_name = re.sub(r"[^\w.\-]", "_", file.filename)
    dest = os.path.join(UPLOAD_DIR, safe_name)

    # Stream file to disk
    try:
        with open(dest, "wb") as f:
            while chunk := await file.read(1024 * 1024):  # 1 MB chunks
                f.write(chunk)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Upload failed: {e}"})

    # Verify file is readable by OpenCV (video or image)
    import cv2 as cv
    is_image = ct.startswith("image/")
    if is_image:
        frame = cv.imread(dest)
        if frame is None:
            os.remove(dest)
            return JSONResponse(status_code=400, content={"error": "File is not a valid image or cannot be decoded"})
    else:
        test_cap = cv.VideoCapture(dest)
        ok = test_cap.isOpened()
        ret, _ = test_cap.read() if ok else (False, None)
        test_cap.release()
        if not ok or not ret:
            os.remove(dest)
            return JSONResponse(status_code=400, content={"error": "File is not a valid video or cannot be decoded"})

    logger.info(f"Developer source saved (live feed unchanged): {dest}")
    return {"message": "File uploaded successfully", "filename": safe_name}


@app.post("/api/v1/video/upload-and-apply")
async def upload_and_apply_video(file: UploadFile):
    """Upload a video and immediately switch the active stream to it.

    Compatibility endpoint used by the car-detection UI.
    """
    global _DEFAULT_VIDEO_PATH

    if not stream_service:
        return JSONResponse(status_code=503, content={"error": "Stream not ready"})
    if not file.filename:
        return JSONResponse(status_code=400, content={"error": "No file provided"})

    ct = (file.content_type or "").lower()
    if not ct.startswith("video/"):
        return JSONResponse(status_code=400, content={"error": f"Unsupported file type: {ct}. Upload a video file."})

    safe_name = re.sub(r"[^\w.\-]", "_", file.filename)
    dest = os.path.join(UPLOAD_DIR, f"live_{uuid.uuid4().hex[:8]}_{safe_name}")

    try:
        with open(dest, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Upload failed: {e}"})

    # Ensure uploaded media is a readable video before switching source.
    import cv2 as cv
    test_cap = cv.VideoCapture(dest)
    ok = test_cap.isOpened()
    ret, _ = test_cap.read() if ok else (False, None)
    test_cap.release()
    if not ok or not ret:
        try:
            os.remove(dest)
        except OSError:
            pass
        return JSONResponse(status_code=400, content={"error": "File is not a valid video or cannot be decoded"})

    if not stream_service.swap_video(dest):
        return JSONResponse(status_code=400, content={"error": f"Cannot open video: {dest}"})

    _DEFAULT_VIDEO_PATH = dest
    logger.info(f"Live stream source updated via upload-and-apply: {dest}")
    return {"message": "Video uploaded and applied", "video_path": dest, "filename": safe_name}


@app.post("/api/v1/video/set-stream-url")
async def set_stream_url(payload: dict):
    """Switch active stream source to a provided path/URL.

    Compatibility endpoint used by the car-detection UI.
    """
    global _DEFAULT_VIDEO_PATH

    if not stream_service:
        return JSONResponse(status_code=503, content={"error": "Stream not ready"})

    raw_url = str(payload.get("url") or "").strip()
    if not raw_url:
        return JSONResponse(status_code=400, content={"error": "url is required"})

    source = raw_url
    if raw_url.lower().startswith("file://"):
        source = urllib.parse.unquote(raw_url[7:])

    # If this looks like a local path, require it to exist.
    is_network = bool(re.match(r"^(rtsp|rtmp|http|https)://", source, flags=re.IGNORECASE))
    if not is_network and not os.path.isfile(source):
        return JSONResponse(status_code=400, content={"error": f"Video file not found: {source}"})

    if not stream_service.swap_video(source):
        return JSONResponse(status_code=400, content={"error": f"Cannot open video: {source}"})

    _DEFAULT_VIDEO_PATH = source
    logger.info(f"Live stream source updated via set-stream-url: {source}")
    return {"message": "Stream source updated", "video_path": source}


# @app.post("/api/v1/developer/video-counts")
# async def get_video_counts(payload: dict):
#     """Count detected vehicles from one or more videos at regular intervals.

#     Request payload:
#     - video_path: str  (single video; required if video_paths not given)
#     - video_paths: list[str]  (batch mode; required if video_path not given)
#     - model_path: ignored for this endpoint (always uses "checkpoint_best_total.pth")
#     - interval_seconds: int (optional) sampling interval, default 10
#     - confidence_threshold: float (optional) minimum confidence (0.0-1.0)
#     - save_annotated_frames: bool (optional) save sampled frames with drawn boxes
#     - save_output_dir: str (optional) directory for saved frames

#     Single-video response:
#     - If duration >= interval: {"counts": [{"second": 10, "count": 20}, ...]}
#     - If duration < interval:  {"count": 14}

#     Batch-video response (when video_paths list is supplied):
#     - {"batch": true, "total": N, "results": [...per-video results...]}
#       Each element in results follows the same single-video schema, or contains
#       an "error" key if that specific video could not be processed.
#     """
#     import cv2 as cv
#     import threading

#     # ── Determine single vs batch mode ───────────────────────────────
#     video_path = str(payload.get("video_path") or "").strip()
#     video_paths_raw = payload.get("video_paths")
#     is_batch = isinstance(video_paths_raw, list) and len(video_paths_raw) > 0

#     if is_batch:
#         video_paths_list = [str(p).strip() for p in video_paths_raw if str(p).strip()]
#         if not video_paths_list:
#             return JSONResponse(status_code=400, content={"error": "video_paths list is empty"})
#     elif video_path:
#         video_paths_list = [video_path]
#     else:
#         return JSONResponse(status_code=400, content={"error": "video_path or video_paths is required"})

#     # ── Parse shared parameters ───────────────────────────────────────
#     # Pin this endpoint to the project checkpoint so UI changes cannot alter
#     # the model used by backend vehicle counts.
#     model_path = "./checkpoint_best_total.pth"
#     resolved_model_path = os.path.normpath(
#         os.path.join(os.path.dirname(__file__), "..", "..", model_path)
#     )
#     if not os.path.isfile(resolved_model_path):
#         return JSONResponse(status_code=400, content={"error": f"Model not found: {resolved_model_path}"})

#     try:
#         interval_seconds = int(payload.get("interval_seconds", 10))
#     except (TypeError, ValueError):
#         interval_seconds = 10
#     interval_seconds = max(1, interval_seconds)

#     # Pin confidence threshold for consistent vehicle counts regardless of UI payload.
#     confidence_threshold = 0.85

#     save_annotated_frames = bool(payload.get("save_annotated_frames", False))
#     save_output_dir = str(payload.get("save_output_dir") or "").strip()

#     # ── Load / retrieve cached detector ──────────────────────────────
#     cache_key = (model_path, round(confidence_threshold, 4))
#     async with _entry_exit_detector_cache_lock:
#         detector = _entry_exit_detector_cache.get(cache_key)
#         if detector is None:
#             detector = DetectionService(model_path=model_path, confidence=confidence_threshold)
#             if not detector.load_model():
#                 return JSONResponse(
#                     status_code=400,
#                     content={"error": f"Failed to load model: {detector.last_error or model_path}"},
#                 )
#         # detector1 = _entry_exit_detector_cache.get(cache_key)
#         # if detector1 is None:
#         #     detector1 = DetectionService(model_path=model_path, confidence=confidence_threshold)
#         #     if not detector1.load_model():
#         #         return JSONResponse(
#         #             status_code=400,
#         #             content={"error": f"Failed to load model: {detector1.last_error or model_path}"},
#         #         )
#             _entry_exit_detector_cache[cache_key] = detector
#             logger.info(
#                 "Entry/exit detector cached for model=%s confidence=%.4f",
#                 model_path,
#                 confidence_threshold,
#             )

#     # Serialize model inference when multiple videos are processed concurrently.
#     _infer_lock = threading.Lock()

#     def _process_single_video(vpath: str) -> dict:
#         """Process one video file synchronously (runs in a thread-pool worker)."""
#         run_output_dir_v: Optional[str] = None
#         saved_frames_v: List[dict] = []

#         if save_annotated_frames:
#             if save_output_dir and len(video_paths_list) > 1:
#                 # Give each video its own sub-directory to avoid filename collisions.
#                 safe_vname = re.sub(r"[^\w.\-]", "_", os.path.basename(vpath))
#                 run_output_dir_v = os.path.join(os.path.normpath(save_output_dir), safe_vname)
#             elif save_output_dir:
#                 run_output_dir_v = os.path.normpath(save_output_dir)
#             else:
#                 run_output_dir_v = os.path.join(
#                     UPLOAD_DIR,
#                     "video_counts_debug",
#                     f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
#                 )
#             try:
#                 os.makedirs(run_output_dir_v, exist_ok=True)
#             except Exception as e:
#                 return {"video_path": vpath, "error": f"Could not create save_output_dir: {e}"}

#         if not os.path.isfile(vpath):
#             return {"video_path": vpath, "error": f"Video file not found: {vpath}"}

#         cap = cv.VideoCapture(vpath)
#         if not cap.isOpened():
#             cap.release()
#             return {"video_path": vpath, "error": "Could not open video file"}

#         fps = float(cap.get(cv.CAP_PROP_FPS) or 0.0)
#         frame_count = int(cap.get(cv.CAP_PROP_FRAME_COUNT) or 0)
#         duration_seconds = (frame_count / fps) if fps > 0 and frame_count > 0 else 0.0

#         def _save_annotated_frame(frame: np.ndarray, detections: list, second_mark: int, count: int) -> None:
#             if not run_output_dir_v:
#                 return
#             annotated = frame.copy()
#             for (bx1, by1, bx2, by2), conf, name in detections:
#                 x1 = int(round(max(0.0, bx1)))
#                 y1 = int(round(max(0.0, by1)))
#                 x2 = int(round(max(0.0, bx2)))
#                 y2 = int(round(max(0.0, by2)))
#                 cv.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 2)
#                 cv.putText(
#                     annotated,
#                     f"{name} {conf:.2f}",
#                     (x1, max(16, y1 - 6)),
#                     cv.FONT_HERSHEY_SIMPLEX,
#                     0.5,
#                     (0, 0, 255),
#                     1,
#                     cv.LINE_AA,
#                 )
#             cv.putText(
#                 annotated,
#                 f"second={second_mark} count={count}",
#                 (10, 24),
#                 cv.FONT_HERSHEY_SIMPLEX,
#                 0.7,
#                 (255, 255, 255),
#                 2,
#                 cv.LINE_AA,
#             )
#             out_path = os.path.join(run_output_dir_v, f"second_{int(second_mark):04d}.jpg")
#             cv.imwrite(out_path, annotated)
#             saved_frames_v.append({"second": int(second_mark), "path": out_path, "count": int(count)})

#         def _count_for_frame(frame: np.ndarray, second_mark: int) -> int:
#             with _infer_lock:
#                 count, bboxes, scores, class_names, _proc_ms = detector.detect_vehicles(frame)
#             if save_annotated_frames and run_output_dir_v:
#                 detections = [
#                     ((float(x1), float(y1), float(x2), float(y2)), float(conf), str(name))
#                     for (x1, y1, x2, y2), conf, name in zip(bboxes, scores, class_names)
#                 ]
#                 _save_annotated_frame(frame, detections, second_mark, count)
#             return int(count)

#         def _read_frame_at_second(sec: float) -> Optional[np.ndarray]:
#             targets = [float(sec), max(0.0, float(sec) - 0.2), max(0.0, float(sec) - 0.5), max(0.0, float(sec) - 1.0)]
#             for t in targets:
#                 cap.set(cv.CAP_PROP_POS_MSEC, t * 1000.0)
#                 ok, frame = cap.read()
#                 if ok and frame is not None:
#                     return frame
#                 if fps > 0:
#                     cap.set(cv.CAP_PROP_POS_FRAMES, int(max(0.0, t) * fps))
#                     ok, frame = cap.read()
#                     if ok and frame is not None:
#                         return frame
#             return None

#         try:
#             if duration_seconds <= 0 or duration_seconds < interval_seconds:
#                 frame = _read_frame_at_second(0.0)
#                 if frame is None:
#                     return {"video_path": vpath, "error": "Could not decode a frame from video"}
#                 count = _count_for_frame(frame, 0)
#                 result: dict = {
#                     "video_path": vpath,
#                     "model_path": model_path,
#                     "duration_seconds": round(float(duration_seconds), 3),
#                     "confidence_threshold": confidence_threshold,
#                     "count": count,
#                 }
#                 if run_output_dir_v:
#                     result["saved_output_dir"] = run_output_dir_v
#                     result["saved_frames"] = saved_frames_v
#                 return result

#             marks: List[int] = list(range(interval_seconds, int(duration_seconds) + 1, interval_seconds))
#             if not marks:
#                 marks = [interval_seconds]

#             counts: List[dict] = []
#             for sec_mark in marks:
#                 seek_second = min(float(sec_mark), max(0.0, float(duration_seconds) - 0.05))
#                 frame = _read_frame_at_second(seek_second)
#                 if frame is None:
#                     continue
#                 counts.append({
#                     "second": int(sec_mark),
#                     "count": _count_for_frame(frame, int(sec_mark)),
#                 })

#             if not counts:
#                 return {"video_path": vpath, "error": "Could not decode sampled frames from video"}

#             result = {
#                 "video_path": vpath,
#                 "model_path": model_path,
#                 "duration_seconds": round(float(duration_seconds), 3),
#                 "interval_seconds": interval_seconds,
#                 "confidence_threshold": confidence_threshold,
#                 "counts": counts,
#             }
#             if run_output_dir_v:
#                 result["saved_output_dir"] = run_output_dir_v
#                 result["saved_frames"] = saved_frames_v
#             return result
#         finally:
#             cap.release()

#     if is_batch:
#         # Process all videos concurrently in the thread pool.
#         # _infer_lock inside _process_single_video serializes model inference
#         # while allowing video I/O and decoding to overlap across threads.
#         tasks = [asyncio.to_thread(_process_single_video, vp) for vp in video_paths_list]
#         raw_results = await asyncio.gather(*tasks, return_exceptions=True)
#         processed_results = []
#         for vp, res in zip(video_paths_list, raw_results):
#             if isinstance(res, Exception):
#                 processed_results.append({"video_path": vp, "error": str(res)})
#             else:
#                 processed_results.append(res)
#         return {
#             "batch": True,
#             "total": len(processed_results),
#             "model_path": model_path,
#             "interval_seconds": interval_seconds,
#             "confidence_threshold": confidence_threshold,
#             "results": processed_results,
#         }

#     # Single video mode — backward-compatible response format.
#     result = await asyncio.to_thread(_process_single_video, video_paths_list[0])
#     if isinstance(result, dict) and "error" in result:
#         return JSONResponse(status_code=400, content={"error": result["error"]})
#     return result

# @app.post("/api/v1/developer/video-counts")
@app.post("/api/v1/developer/video-counts")
async def get_video_counts(payload: dict):
    """
    Request payload:
    - video_path: str
    - video_paths: list[str]
    - model_path: str (optional, default yolov8m.pt)
    - interval_seconds: int (optional, default 1)
    - confidence_threshold: float (optional, default 0.25)
    - iou_threshold: float (optional, default 0.45)
    - regions: dict (optional)
    - save_annotated_frames: bool (optional)
    - save_output_dir: str (optional)
 
    Response shape matches the previous endpoint:
    - short single video: {"count": ...}
    - long single video: {"counts": [{"second": 1, "count": 4}, ...]}
    - batch: {"batch": true, "total": N, "results": [...]}
    """
    video_path = str(payload.get("video_path") or "").strip()
    raw_video_paths = payload.get("video_paths")
    is_batch = isinstance(raw_video_paths, list) and len(raw_video_paths) > 0
 
    if is_batch:
        video_paths = [str(path).strip() for path in raw_video_paths if str(path).strip()]
        if not video_paths:
            return JSONResponse(status_code=400, content={"error": "video_paths list is empty"})
    elif video_path:
        video_paths = [video_path]
    else:
        return JSONResponse(status_code=400, content={"error": "video_path or video_paths is required"})
 
    model_path = "yolov8m.pt"
 
    try:
        interval_seconds = max(1, int(payload.get("interval_seconds", 1)))
    except (TypeError, ValueError):
        interval_seconds = 1
 
    try:
        confidence_threshold = float(payload.get("confidence_threshold", 0.15))
    except (TypeError, ValueError):
        confidence_threshold = 0.15
 
    try:
        iou_threshold = float(payload.get("iou_threshold", 0.45))
    except (TypeError, ValueError):
        iou_threshold = 0.45
 
    if not os.path.isfile(model_path):
        return JSONResponse(status_code=400, content={"error": f"Model not found: {model_path}"})
 
    # regions = payload.get("regions") if isinstance(payload.get("regions"), dict) else None
    regions = {     "region_left": [[0, 1053], [769, 349], [561, 243], [0, 464]],     "region_right": [[1920, 972], [1100, 369], [1323, 160], [1920, 356]], }
    save_annotated_frames = True
    save_output_dir = r"C:\Users\divc223\Documents\demo_ui\completed_demo\NC-Parking-Lot-POC\data"  # str(payload.get("save_output_dir") or "").strip() or None
 
    if is_batch:
        result = await asyncio.to_thread(
            run_job,
            video_paths,
            model_path,
            interval_seconds,
            confidence_threshold,
            iou_threshold,
            regions,
            save_annotated_frames,
            save_output_dir,
        )
        return result
 
    processor = VideoProcessCount(
        model_path=model_path,
        conf_threshold=confidence_threshold,
        iou_threshold=iou_threshold,
    )
 
    result = await asyncio.to_thread(
        processor.process_video,
        video_paths[0],
        interval_seconds,
        regions,
        save_annotated_frames,
        save_output_dir,
    )
    print("Result from process_video:", result)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=400, content={"error": result["error"]})
    return result


@app.post("/api/v1/developer/entry-exit-counts")
async def get_entry_exit_counts(payload: dict):
    """Count entry/exit vehicles from two recorded videos using tripwire crossing.

    Request payload:
    - entry_video_path: str (required)
    - exit_video_path: str (required)
    - model_path: ignored for this endpoint (always uses "yolo_trained.pt")
    - confidence_threshold: float (optional, default 0.5)
    - frame_stride: int (optional, default 1)
    - max_match_distance_px: float (optional, default 90)
    - max_stale_frames: int (optional, default 20)
    - line_deadband_px: float (optional, default 6)
    - allow_track_recount: bool (optional, default False)
    - save_annotated_frames: bool (optional)
    - save_output_dir: str (optional)

    Optional tripwire overrides per stream:
    - entry_line: {"x1":..., "y1":..., "x2":..., "y2":...}
    - exit_line:  {"x1":..., "y1":..., "x2":..., "y2":...}
    If omitted, each video uses a horizontal middle-line.
    """
    import cv2 as cv

    entry_video_path = str(payload.get("entry_video_path") or "").strip()
    exit_video_path = str(payload.get("exit_video_path") or "").strip()
    # This endpoint is pinned to project-relative YOLO weights for portability.
    model_path = "./yolo_trained.pt"
    resolved_model_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", model_path)
    )
    if not os.path.isfile(resolved_model_path):
        return JSONResponse(status_code=400, content={"error": f"YOLO model not found: {resolved_model_path}"})

    try:
        confidence_threshold = float(payload.get("confidence_threshold", 0.5))
    except (TypeError, ValueError):
        confidence_threshold = 0.5
    confidence_threshold = max(0.0, min(1.0, confidence_threshold))

    try:
        frame_stride = int(payload.get("frame_stride", 1))
    except (TypeError, ValueError):
        frame_stride = 1
    frame_stride = max(1, frame_stride)

    try:
        max_match_distance_px = float(payload.get("max_match_distance_px", 90.0))
    except (TypeError, ValueError):
        max_match_distance_px = 90.0
    max_match_distance_px = max(5.0, max_match_distance_px)

    try:
        max_stale_frames = int(payload.get("max_stale_frames", 20))
    except (TypeError, ValueError):
        max_stale_frames = 20
    max_stale_frames = max(1, max_stale_frames)

    try:
        line_deadband_px = float(payload.get("line_deadband_px", 6.0))
    except (TypeError, ValueError):
        line_deadband_px = 6.0
    line_deadband_px = max(0.0, line_deadband_px)

    allow_track_recount = bool(payload.get("allow_track_recount", False))
    save_annotated_frames = bool(payload.get("save_annotated_frames", True))
    save_output_dir = str(payload.get("save_output_dir") or "").strip()
    generate_processed_videos = bool(payload.get("generate_processed_videos", True))
    processed_output_dir = str(payload.get("processed_output_dir") or "").strip()

    # Both paths are optional — omit either to skip that analysis (count = 0)
    analyze_entry = bool(entry_video_path)
    analyze_exit = bool(exit_video_path)
    if not analyze_entry and not analyze_exit:
        return JSONResponse(status_code=400, content={"error": "At least one of entry_video_path or exit_video_path is required"})
    if analyze_entry and not os.path.isfile(entry_video_path):
        return JSONResponse(status_code=400, content={"error": f"Entry video not found: {entry_video_path}"})
    if analyze_exit and not os.path.isfile(exit_video_path):
        return JSONResponse(status_code=400, content={"error": f"Exit video not found: {exit_video_path}"})

    output_dir: Optional[str] = None
    if save_annotated_frames:
        output_dir = (
            os.path.normpath(save_output_dir)
            if save_output_dir
            else os.path.normpath(ENTRY_EXIT_IMAGES_DIR)
        )
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            return JSONResponse(status_code=400, content={"error": f"Could not create save_output_dir: {e}"})

    processed_dir: Optional[str] = None
    if generate_processed_videos:
        processed_dir = (
            os.path.normpath(processed_output_dir)
            if processed_output_dir
            else (output_dir if output_dir else os.path.normpath(ENTRY_EXIT_IMAGES_DIR))
        )
        try:
            os.makedirs(processed_dir, exist_ok=True)
        except Exception as e:
            return JSONResponse(status_code=400, content={"error": f"Could not create processed_output_dir: {e}"})

    detector = DetectionService(model_path=model_path, confidence=confidence_threshold)
    if not detector.load_model():
        return JSONResponse(
            status_code=400,
            content={"error": f"Failed to load model: {detector.last_error or model_path}"},
        )
    detector1 = DetectionService(model_path=model_path, confidence=confidence_threshold)
    if not detector1.load_model():
        return JSONResponse(
            status_code=400,
            content={"error": f"Failed to load model: {detector1.last_error or model_path}"},
        )

    def _parse_line_cfg(cfg: Any, width: int, height: int) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        # Default tripwire: vertical line at 63% from left, top quarter of frame.
        # Cars entering from the right gate cross this line moving left.
        default = ((width * 0.63, 0.0), (width * 0.63, height * 0.25))
        if not isinstance(cfg, dict):
            return default
        try:
            x1 = float(cfg.get("x1"))
            y1 = float(cfg.get("y1"))
            x2 = float(cfg.get("x2"))
            y2 = float(cfg.get("y2"))
        except (TypeError, ValueError):
            return default
        if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
            return default
        return (x1, y1), (x2, y2)

    def _signed_side(px: float, py: float, p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
        # Sign of cross product (line vector x point vector).
        return ((p2[0] - p1[0]) * (py - p1[1])) - ((p2[1] - p1[1]) * (px - p1[0]))

    def _to_entry_exit_static_url(path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        try:
            abs_path = os.path.normpath(path)
            static_root = os.path.normpath(ENTRY_EXIT_IMAGES_DIR)
            rel = os.path.relpath(abs_path, static_root)
            if rel.startswith(".."):
                return None
            web_rel = rel.replace("\\", "/")
            return "/entry-exit-images/" + urllib.parse.quote(web_rel)
        except Exception:
            return None

    def _annotate_processed_frame(
        frame: np.ndarray,
        detections: List[Tuple[Tuple[float, float, float, float], float, str]],
        p1: Tuple[float, float],
        p2: Tuple[float, float],
        crossing_count: int,
        stream_label: str,
    ) -> np.ndarray:
        annotated = frame.copy()
        cv.line(
            annotated,
            (int(round(p1[0])), int(round(p1[1]))),
            (int(round(p2[0])), int(round(p2[1]))),
            (255, 255, 0),
            2,
        )
        for (bx1, by1, bx2, by2), conf, name in detections:
            x1 = int(round(max(0.0, bx1)))
            y1 = int(round(max(0.0, by1)))
            x2 = int(round(max(0.0, bx2)))
            y2 = int(round(max(0.0, by2)))
            cv.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 2)
            cv.putText(
                annotated,
                f"{name} {conf:.2f}",
                (x1, max(16, y1 - 6)),
                cv.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 0, 255),
                1,
                cv.LINE_AA,
            )
        cv.putText(
            annotated,
            f"{stream_label} count={int(crossing_count)}",
            (10, 24),
            cv.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
            cv.LINE_AA,
        )
        return annotated

    def _create_processed_video_writer(
        output_root: str,
        stream_label: str,
        fps_value: float,
        width: int,
        height: int,
    ) -> Tuple[Optional[cv.VideoWriter], Optional[str], Optional[str]]:
        """Create a browser-friendly writer, preferring H.264/AVC codecs."""
        video_stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        write_fps = fps_value if fps_value > 0 else 30.0
        codec_candidates: List[Tuple[str, str]] = [
            ("avc1", ".mp4"),
            ("H264", ".mp4"),
            ("X264", ".mp4"),
            ("mp4v", ".mp4"),
            ("MJPG", ".avi"),
        ]

        for codec_name, ext in codec_candidates:
            out_path = os.path.join(output_root, f"{stream_label}_processed_{video_stamp}{ext}")
            fourcc = cv.VideoWriter_fourcc(*codec_name)
            candidate = cv.VideoWriter(out_path, fourcc, write_fps, (width, height))
            if candidate is not None and candidate.isOpened():
                logger.info("Processed video writer selected codec=%s path=%s", codec_name, out_path)
                return candidate, out_path, _to_entry_exit_static_url(out_path)
            try:
                if candidate is not None:
                    candidate.release()
            except Exception:
                pass

        logger.warning("Could not open processed video writer for %s", stream_label)
        return None, None, None

    def _analyze_video(video_path: str, line_cfg: Any, stream_label: str) -> dict:
        cap = cv.VideoCapture(video_path)
        if not cap.isOpened():
            return {"error": f"Could not open video: {video_path}"}

        fps = float(cap.get(cv.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv.CAP_PROP_FRAME_COUNT) or 0)
        duration_seconds = (frame_count / fps) if fps > 0 and frame_count > 0 else None

        # Probe first readable frame to infer dimensions for default line.
        ok, first_frame = cap.read()
        if not ok or first_frame is None:
            cap.release()
            return {"error": f"Could not decode frames from: {video_path}"}
        h, w = first_frame.shape[:2]
        p1, p2 = _parse_line_cfg(line_cfg, w, h)

        # Reset stream after probing frame.
        cap.set(cv.CAP_PROP_POS_FRAMES, 0)

        processed_video_path: Optional[str] = None
        processed_video_url: Optional[str] = None
        writer: Optional[cv.VideoWriter] = None
        if processed_dir:
            writer, processed_video_path, processed_video_url = _create_processed_video_writer(
                processed_dir,
                stream_label,
                fps,
                w,
                h,
            )

        next_track_id = 1
        tracks: Dict[int, dict] = {}
        crossings: List[dict] = []
        saved_frames: List[dict] = []
        crossing_count = 0
        frame_idx = -1

        while True:
            
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            current_epoch = round(time.time()*1000)
            frame_idx += 1
            if frame_idx % frame_stride != 0:
                if writer is not None:
                    writer.write(frame)
                continue

            detections = detector._predict_detections(frame)
            draw_detections: List[Tuple[Tuple[float, float, float, float], float, str]] = [
                ((float(bx1), float(by1), float(bx2), float(by2)), float(conf), str(name))
                for (bx1, by1, bx2, by2), conf, name in detections
            ]
            centers: List[Tuple[float, float]] = []
            for (bx1, by1, bx2, by2), _conf, _name in detections:
                centers.append(((float(bx1) + float(bx2)) / 2.0, (float(by1) + float(by2)) / 2.0))

            # Build candidate matches: (distance, track_id, det_idx)
            candidates: List[Tuple[float, int, int]] = []
            for tid, tr in tracks.items():
                tcx, tcy = tr["center"]
                for det_idx, (dcx, dcy) in enumerate(centers):
                    dist = math.hypot(dcx - tcx, dcy - tcy)
                    if dist <= max_match_distance_px:
                        candidates.append((dist, tid, det_idx))
            candidates.sort(key=lambda x: x[0])

            matched_tracks = set()
            matched_dets = set()
            assignments: List[Tuple[int, int]] = []
            for _dist, tid, det_idx in candidates:
                if tid in matched_tracks or det_idx in matched_dets:
                    continue
                matched_tracks.add(tid)
                matched_dets.add(det_idx)
                assignments.append((tid, det_idx))

            # Update matched tracks and detect crossings.
            crossing_now = []
            for tid, det_idx in assignments:
                cx, cy = centers[det_idx]
                side = _signed_side(cx, cy, p1, p2)
                prev_side = tracks[tid].get("side")
                crossed = False

                if prev_side is not None:
                    was_far = abs(prev_side) > line_deadband_px
                    is_far = abs(side) > line_deadband_px
                    if was_far and is_far and (prev_side * side < 0):
                        if allow_track_recount or not tracks[tid].get("counted", False):
                            crossed = True
                            crossing_count += 1
                            tracks[tid]["counted"] = True
                            crossing_event = {
                                "track_id": int(tid),
                                "frame_index": int(frame_idx),
                                "time_seconds": round((frame_idx / fps), 3) if fps > 0 else None,
                                "from_side": "negative" if prev_side < 0 else "positive",
                                "to_side": "negative" if side < 0 else "positive",
                            }
                            crossings.append(crossing_event)
                            crossing_now.append(crossing_event)

                tracks[tid].update({
                    "center": (cx, cy),
                    "side": side,
                    "last_seen": frame_idx,
                })

            # Create new tracks for unmatched detections.
            for det_idx, (cx, cy) in enumerate(centers):
                if det_idx in matched_dets:
                    continue
                side = _signed_side(cx, cy, p1, p2)
                tracks[next_track_id] = {
                    "center": (cx, cy),
                    "side": side,
                    "last_seen": frame_idx,
                    "counted": False,
                }
                next_track_id += 1

            # Remove stale tracks.
            stale_ids = [
                tid for tid, tr in tracks.items()
                if frame_idx - int(tr.get("last_seen", frame_idx)) > max_stale_frames
            ]
            for tid in stale_ids:
                tracks.pop(tid, None)

            # Save raw crossing frames only.
            # Files are written directly into output_dir with pattern:
            # entry/exit_YYYYMMDD_HHMMSS_microseconds_frameIndex_trackId.jpg
            # frame_index = int(crossing["frame_index"])
            # track_id = int(crossing["track_id"])
            
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
            out_name = f"{stream_label}_{timestamp}_{frame_idx}_{current_epoch}.jpg"
            out_path = os.path.join(output_dir, out_name)
            cv.imwrite(out_path, frame)
            saved_frames.append({
                        "frame_index": frame_idx,
                        "epoch": current_epoch,
                        "time_seconds": round((frame_idx / fps), 3) if fps > 0 else None,
                        "path": out_path
                    })
            if output_dir and crossing_now:
                for crossing in crossing_now:
                    frame_index = int(crossing["frame_index"])
                    track_id = int(crossing["track_id"])
                    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
                    out_name = f"{stream_label}_{timestamp}_{frame_index}_{track_id}_{current_epoch}.jpg"
                    out_path = os.path.join(output_dir, out_name)
                    cv.imwrite(out_path, frame)
                    saved_frames.append({
                        "frame_index": frame_index,
                        "track_id": track_id,
                        "epoch": current_epoch,
                        "time_seconds": round((frame_index / fps), 3) if fps > 0 else None,
                        "path": out_path,
                    })

            if writer is not None:
                writer.write(_annotate_processed_frame(frame, draw_detections, p1, p2, crossing_count, stream_label))

        cap.release()
        if writer is not None:
            writer.release()
        return {
            "video_path": video_path,
            "duration_seconds": round(float(duration_seconds), 3) if duration_seconds is not None else None,
            "fps": round(float(fps), 3) if fps > 0 else None,
            "frame_count": frame_count,
            "frame_stride": frame_stride,
            "tripwire": {
                "x1": round(float(p1[0]), 2),
                "y1": round(float(p1[1]), 2),
                "x2": round(float(p2[0]), 2),
                "y2": round(float(p2[1]), 2),
                "deadband_px": line_deadband_px,
            },
            "count": int(crossing_count),
            "crossings": crossings,
            "saved_frames": saved_frames,
            "processed_video_path": processed_video_path,
            "processed_video_url": processed_video_url,
        }

    if analyze_entry:
        entry_result = await asyncio.to_thread(_analyze_video, entry_video_path, payload.get("entry_line"), "entry")
        if "error" in entry_result:
            return JSONResponse(status_code=400, content={"error": entry_result["error"]})
        entered_count = int(entry_result["count"])
    else:
        entry_result = {}
        entered_count = 0

    if analyze_exit:
        exit_result = await asyncio.to_thread(_analyze_video, exit_video_path, payload.get("exit_line"), "exit")
        if "error" in exit_result:
            return JSONResponse(status_code=400, content={"error": exit_result["error"]})
        exited_count = int(exit_result["count"])
    else:
        exit_result = {}
        exited_count = 0

    response = {
        "entry_video_path": entry_video_path,
        "exit_video_path": exit_video_path,
        "model_path": model_path,
        "confidence_threshold": confidence_threshold,
        "frame_stride": frame_stride,
        "entered_count": entered_count,
        "exited_count": exited_count,
        "entry": entry_result,
        "exit": exit_result,
        "processed_entry_video_path": entry_result.get("processed_video_path"),
        "processed_exit_video_path": exit_result.get("processed_video_path"),
        "processed_entry_video_url": entry_result.get("processed_video_url"),
        "processed_exit_video_url": exit_result.get("processed_video_url"),
    }
    if output_dir:
        response["saved_output_dir"] = output_dir
    if processed_dir:
        response["processed_output_dir"] = processed_dir
    return response


# @app.post("/api/v1/developer/capture-frame")
# async def capture_frame_from_url(payload: dict):
#     """Capture a single frame for the wizard.

#     Priority order:
#     1. use_existing=True  → read from the most recently wizard-uploaded file on disk
#     2. video_url provided → open that URL/stream directly (never touches live feed)
#     3. Fallback           → read one frame from the live stream (read-only, no mutation)
#     """
#     import cv2 as cv

#     video_url = payload.get("video_url", "")
#     use_existing = payload.get("use_existing", False)
#     timestamp_seconds = payload.get("timestamp_seconds", 0)

#     try:
#         timestamp_seconds = max(0.0, float(timestamp_seconds or 0.0))
#     except (TypeError, ValueError):
#         timestamp_seconds = 0.0

#     def _read_frame_at_timestamp(source: str):
#         cap = cv.VideoCapture(source)
#         if not cap.isOpened():
#             cap.release()
#             return None, None, None

#         fps = float(cap.get(cv.CAP_PROP_FPS) or 0.0)
#         frame_count = int(cap.get(cv.CAP_PROP_FRAME_COUNT) or 0)
#         duration = (frame_count / fps) if fps > 0 and frame_count > 0 else None

#         target_ts = timestamp_seconds
#         if duration is not None:
#             target_ts = min(target_ts, max(0.0, duration))

#         if target_ts > 0:
#             target_frame = int(round(target_ts * fps)) if fps > 0 else 0
#             if target_frame > 0:
#                 cap.set(cv.CAP_PROP_POS_FRAMES, target_frame)
#             else:
#                 cap.set(cv.CAP_PROP_POS_MSEC, target_ts * 1000.0)

#         ret, frm = cap.read()
#         actual_ts = float(cap.get(cv.CAP_PROP_POS_MSEC) or (target_ts * 1000.0)) / 1000.0
#         cap.release()
#         return (frm if ret else None), duration, actual_ts

#     frame = None
#     duration_seconds = None
#     captured_at_seconds = None

#     if use_existing:
#         # Find the most recently modified file in UPLOAD_DIR
#         def _latest_upload():
#             if not os.path.isdir(UPLOAD_DIR):
#                 return None
#             files = [
#                 os.path.join(UPLOAD_DIR, f)
#                 for f in os.listdir(UPLOAD_DIR)
#                 if f.lower().endswith((".mp4", ".avi", ".mkv", ".webm", ".mov", ".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".tiff"))
#             ]
#             if not files:
#                 return None
#             latest = max(files, key=os.path.getmtime)
#             # Try as video first; if that fails, try as image
#             frm, dur, actual_ts = _read_frame_at_timestamp(latest)
#             if frm is not None:
#                 return frm, dur, actual_ts
#             # Fallback to image
#             img = cv.imread(latest)
#             return (img, None, 0.0) if img is not None else (None, None, None)

#         frame, duration_seconds, captured_at_seconds = await asyncio.to_thread(_latest_upload)

#     elif video_url:
#         if not video_url.startswith(("http://", "https://", "rtsp://")):
#             return JSONResponse(status_code=400, content={"error": "Invalid URL scheme"})

#         def _read_url(url):
#             return _read_frame_at_timestamp(url)

#         frame, duration_seconds, captured_at_seconds = await asyncio.to_thread(_read_url, video_url)
#         if frame is None:
#             return JSONResponse(status_code=400, content={"error": "Could not capture frame from URL"})

#     else:
#         # Fallback: read one frame from live stream (read-only — no swap)
#         if not stream_service:
#             return JSONResponse(status_code=503, content={"error": "Stream not ready"})
#         frame = await stream_service.capture_frame()

#     if frame is None:
#         return JSONResponse(status_code=500, content={"error": "Frame capture failed"})

#     _, buf = cv.imencode(".jpg", frame, [cv.IMWRITE_JPEG_QUALITY, 90])
#     b64 = base64.b64encode(buf.tobytes()).decode("ascii")

#     return {
#         "frame_base64": b64,
#         "width": int(frame.shape[1]),
#         "height": int(frame.shape[0]),
#         "captured_at_seconds": round(float(captured_at_seconds or 0.0), 3),
#         "duration_seconds": round(float(duration_seconds), 3) if duration_seconds is not None else None,
#     }


@app.post("/api/v1/developer/estimate-spots", response_model=GPTEstimateResponse)
async def estimate_spots(request: GPTEstimateRequest):
    """Use GPT Vision to estimate parking spots per zone from user-drawn grid."""
    import cv2 as cv

    grid = request.grid_config
    h_lines = sorted(grid.horizontal_lines)
    v_lines = sorted(grid.vertical_lines)
    h_line_angles = [float(v) for v in (getattr(grid, "horizontal_line_angles", []) or [])]
    v_line_angles = [float(v) for v in (getattr(grid, "vertical_line_angles", []) or [])]
    h_angle_deg = float(getattr(grid, "horizontal_angle_deg", 0.0) or 0.0)
    v_angle_deg = float(getattr(grid, "vertical_angle_deg", 0.0) or 0.0)

    # Build zone labels
    rows = len(h_lines) + 1
    cols = len(v_lines) + 1
    row_labels = _row_labels(rows)
    col_labels = _col_labels(cols)

    # Decode the full frame
    frame = None
    if request.frame_base64:
        frame_bytes = base64.b64decode(request.frame_base64)
        nparr = np.frombuffer(frame_bytes, np.uint8)
        frame = cv.imdecode(nparr, cv.IMREAD_COLOR)

    if frame is None:
        return JSONResponse(status_code=400, content={"error": "frame_base64 is required for accurate estimation"})

    img_h, img_w = frame.shape[:2]

    # Compute border offsets in pixels
    bt = int(img_h * grid.border.get("top", 0) / 100)
    br = int(img_w * grid.border.get("right", 0) / 100)
    bb = int(img_h * grid.border.get("bottom", 0) / 100)
    bl = int(img_w * grid.border.get("left", 0) / 100)

    # Active region boundaries
    ax1, ay1 = bl, bt
    ax2, ay2 = img_w - br, img_h - bb
    aw = max(1.0, float(ax2 - ax1))
    ah = max(1.0, float(ay2 - ay1))
    cx_mid = (ax1 + ax2) / 2.0
    cy_mid = (ay1 + ay2) / 2.0
    h_tan = math.tan(math.radians(h_angle_deg))
    v_tan = math.tan(math.radians(v_angle_deg))

    # Compute row/col boundaries in pixel coords
    h_bounds_frac = [0.0] + h_lines + [1.0]
    v_bounds_frac = [0.0] + v_lines + [1.0]

    # Angles for interior boundaries; outer frame edges stay axis-aligned (0°)
    if len(h_line_angles) < len(h_lines):
        h_line_angles = h_line_angles + [h_angle_deg] * (len(h_lines) - len(h_line_angles))
    if len(v_line_angles) < len(v_lines):
        v_line_angles = v_line_angles + [v_angle_deg] * (len(v_lines) - len(v_line_angles))
    h_boundary_angles = [0.0] + h_line_angles[:len(h_lines)] + [0.0]
    v_boundary_angles = [0.0] + v_line_angles[:len(v_lines)] + [0.0]

    def _intersect_hv(hf: float, vf: float, h_boundary_angle: float, v_boundary_angle: float) -> Tuple[float, float]:
        y0 = ay1 + ah * hf
        x0 = ax1 + aw * vf
        h_tan = math.tan(math.radians(h_boundary_angle))
        v_tan = math.tan(math.radians(v_boundary_angle))
        # y = y0 + h_tan * (x - cx_mid)
        # x = x0 + v_tan * (y - cy_mid)
        a1 = -h_tan
        b1 = 1.0
        c1 = y0 - h_tan * cx_mid
        a2 = 1.0
        b2 = -v_tan
        c2 = x0 - v_tan * cy_mid
        det = a1 * b2 - a2 * b1
        if abs(det) < 1e-6:
            return x0, y0
        x = (c1 * b2 - c2 * b1) / det
        y = (a1 * c2 - a2 * c1) / det
        return x, y

    zone_configs = []
    total = 0

    for r in range(rows):
        for c in range(cols):
            zone_id = f"{row_labels[r]}{col_labels[c]}"

            # Build tilted cell polygon and masked crop
            p_tl = _intersect_hv(h_bounds_frac[r], v_bounds_frac[c], h_boundary_angles[r], v_boundary_angles[c])
            p_tr = _intersect_hv(h_bounds_frac[r], v_bounds_frac[c + 1], h_boundary_angles[r], v_boundary_angles[c + 1])
            p_br = _intersect_hv(h_bounds_frac[r + 1], v_bounds_frac[c + 1], h_boundary_angles[r + 1], v_boundary_angles[c + 1])
            p_bl = _intersect_hv(h_bounds_frac[r + 1], v_bounds_frac[c], h_boundary_angles[r + 1], v_boundary_angles[c])
            poly = np.array([p_tl, p_tr, p_br, p_bl], dtype=np.float32)

            x1 = int(max(0, math.floor(float(np.min(poly[:, 0])))))
            y1 = int(max(0, math.floor(float(np.min(poly[:, 1])))))
            x2 = int(min(img_w, math.ceil(float(np.max(poly[:, 0])))))
            y2 = int(min(img_h, math.ceil(float(np.max(poly[:, 1])))))

            if x2 <= x1 or y2 <= y1:
                crop = np.zeros((8, 8, 3), dtype=np.uint8)
            else:
                rect_crop = frame[y1:y2, x1:x2].copy()
                mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
                poly_local = np.array([
                    [int(round(p_tl[0] - x1)), int(round(p_tl[1] - y1))],
                    [int(round(p_tr[0] - x1)), int(round(p_tr[1] - y1))],
                    [int(round(p_br[0] - x1)), int(round(p_br[1] - y1))],
                    [int(round(p_bl[0] - x1)), int(round(p_bl[1] - y1))],
                ], dtype=np.int32)
                cv.fillConvexPoly(mask, poly_local, 255)
                crop = cv.bitwise_and(rect_crop, rect_crop, mask=mask)

            _, crop_buf = cv.imencode(".jpg", crop, [cv.IMWRITE_JPEG_QUALITY, 85])
            crop_b64 = base64.b64encode(crop_buf.tobytes()).decode("ascii")

            zone_configs.append({
                "zone_id": zone_id,
                "crop_b64": crop_b64,
                "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            })

    # ── GPT Vision estimation per zone ──────────────────────────────
    try:
        import openai

        api_key = os.getenv("OPENAI_API_KEY", "")
        if api_key and len(api_key) > 10:
            client = openai.AsyncOpenAI(api_key=api_key)

            # Send full frame + grid overlay description, plus individual crops
            vision_messages = []
            zone_image_parts = []
            for zc in zone_configs:
                zone_image_parts.append({
                    "type": "text",
                    "text": f"Zone {zc['zone_id']} (pixel region {zc['x1']},{zc['y1']} to {zc['x2']},{zc['y2']}):"
                })
                zone_image_parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{zc['crop_b64']}",
                        "detail": "high",
                    },
                })

            vision_messages = [
                {
                    "role": "system",
                    "content": (
                        "You are an expert parking lot analyst with computer vision expertise. "
                        "You will be shown cropped aerial images of individual zones within a "
                        "parking lot. For EACH zone, carefully count or estimate the number of "
                        "INDIVIDUAL PARKING SPACES (not vehicles) visible. Look for:\n"
                        "- Painted lane markings / lines separating spaces\n"
                        "- The width patterns that indicate individual bays\n"
                        "- Vehicles already parked (each occupies one space)\n"
                        "- Empty gaps that could fit one vehicle\n"
                        "Be precise. A small zone might have 3-8 spots; a large one might have 15-40.\n"
                        "Return ONLY a JSON array: [{\"zone_id\":\"XX\",\"spots\":N}, ...] — no explanation."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"This parking lot frame has been segmented into a {rows}x{cols} grid "
                                f"({len(zone_configs)} zones). The full frame is {img_w}x{img_h}px with "
                                f"horizontal angle={h_angle_deg}°, vertical angle={v_angle_deg}°, "
                                f"border insets: top={grid.border.get('top',0)}%, "
                                f"right={grid.border.get('right',0)}%, "
                                f"bottom={grid.border.get('bottom',0)}%, "
                                f"left={grid.border.get('left',0)}%.\n\n"
                                f"Below are cropped images of each zone. Count the parking SPACES "
                                f"(not vehicles) in each zone image carefully."
                            ),
                        },
                        *zone_image_parts,
                    ],
                },
            ]

            resp = await client.chat.completions.create(
                model="gpt-4.1",
                messages=vision_messages,
                max_tokens=1000,
                temperature=0.2,
            )
            raw = resp.choices[0].message.content.strip()

            # Parse JSON from GPT
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            parsed = json.loads(raw)

            # Build a lookup from GPT results
            gpt_map = {item["zone_id"]: int(item.get("spots", 0)) for item in parsed}

            result_zones = []
            total = 0
            for zc in zone_configs:
                spots = gpt_map.get(zc["zone_id"], 10)
                result_zones.append(ZoneSpotConfig(
                    zone_id=zc["zone_id"],
                    estimated_spots=spots,
                    user_spots=spots,
                    zone_image_base64=zc["crop_b64"],
                ))
                total += spots

            return GPTEstimateResponse(zones=result_zones, total_spots=total, model="gpt-4.1-vision")
    except Exception as e:
        logger.warning(f"GPT Vision estimation failed, using heuristic: {e}")

    # ── Heuristic fallback: estimate based on zone pixel area ───────
    total_active_area = (ax2 - ax1) * (ay2 - ay1)
    result_zones = []
    total = 0
    for zc in zone_configs:
        zone_area = (zc["x2"] - zc["x1"]) * (zc["y2"] - zc["y1"])
        fraction = zone_area / total_active_area if total_active_area > 0 else 1 / len(zone_configs)
        # Assume ~120 total spots in a typical lot, distribute by area
        spots = max(2, round(120 * fraction))
        result_zones.append(ZoneSpotConfig(
            zone_id=zc["zone_id"],
            estimated_spots=spots,
            user_spots=spots,
            zone_image_base64=zc["crop_b64"],
        ))
        total += spots
    return GPTEstimateResponse(zones=result_zones, total_spots=total, model="heuristic")


# @app.post("/api/v1/developer/preview")
# async def preview_detection(payload: dict):
#     """Run detection on a wizard frame using WizardService.

#     Completely isolated from the live detection pipeline.  No global service
#     (ParkingService / DetectionService / StreamService) is mutated here.
#     """
#     import binascii
#     import cv2 as cv

#     frame_b64 = payload.get("frame_base64", "")
#     params = payload.get("parameters") or {}
#     grid_cfg = payload.get("grid_config") or {}

#     if not frame_b64:
#         return JSONResponse(status_code=400, content={"error": "frame_base64 required"})

#     try:
#         frame_bytes = base64.b64decode(frame_b64)
#     except (binascii.Error, ValueError, TypeError):
#         return JSONResponse(status_code=400, content={"error": "Invalid frame_base64 payload"})

#     nparr = np.frombuffer(frame_bytes, np.uint8)
#     frame = cv.imdecode(nparr, cv.IMREAD_COLOR)
#     if frame is None:
#         return JSONResponse(status_code=400, content={"error": "Invalid frame data"})

#     # Parse all numeric parameters with safe defaults
#     def _f(key, default, lo=None, hi=None):
#         try:
#             v = float(params.get(key, default))
#             if lo is not None: v = max(lo, v)
#             if hi is not None: v = min(hi, v)
#             return v
#         except (TypeError, ValueError):
#             return float(default)

#     def _i(key, default, lo=None, hi=None):
#         try:
#             v = int(float(params.get(key, default)))
#             if lo is not None: v = max(lo, v)
#             if hi is not None: v = min(hi, v)
#             return v
#         except (TypeError, ValueError):
#             return int(default)

#     conf            = _f("confidence_threshold", 0.15, 0.01, 1.0)
#     nms_iou         = _f("nms_iou_threshold",    0.4,  0.1,  1.0)
#     seg_overlap     = _f("segment_overlap",       0.12, 0.0,  0.5)
#     min_area        = _i("min_vehicle_area",       800, 100)
#     max_area        = _i("max_vehicle_area",     80000, 1000)
#     if min_area > max_area:
#         min_area, max_area = max_area, min_area

#     model_path = (params.get("model_path") or "base").strip()

#     border  = grid_cfg.get("border") or {}
#     h_lines = [float(v) for v in (grid_cfg.get("horizontal_lines") or [])]
#     v_lines_list = [float(v) for v in (grid_cfg.get("vertical_lines") or [])]
#     h_line_angles = [float(v) for v in (grid_cfg.get("horizontal_line_angles") or [])]
#     v_line_angles = [float(v) for v in (grid_cfg.get("vertical_line_angles") or [])]
#     exclude_regions = grid_cfg.get("exclude_regions") or []
#     h_angle_deg = float(grid_cfg.get("horizontal_angle_deg", 0.0) or 0.0)
#     v_angle_deg = float(grid_cfg.get("vertical_angle_deg", 0.0) or 0.0)

#     # Delegate entirely to WizardService — never touches global state
#     annotated, vehicle_count, elapsed_ms = await asyncio.to_thread(
#         WizardService.run_preview,
#         frame,
#         model_path,
#         _PROJECT_ROOT,
#         conf,
#         nms_iou,
#         seg_overlap,
#         min_area,
#         max_area,
#         h_lines,
#         v_lines_list,
#         h_line_angles,
#         v_line_angles,
#         exclude_regions,
#         h_angle_deg,
#         v_angle_deg,
#         border,
#     )

#     _, buf = cv.imencode(".jpg", annotated, [cv.IMWRITE_JPEG_QUALITY, 85])
#     return {
#         "annotated_frame_base64": base64.b64encode(buf.tobytes()).decode("ascii"),
#         "vehicle_count": vehicle_count,
#         "processing_ms": round(elapsed_ms, 1),
#     }


# def _geocode_from_google_maps_url(url: str) -> Tuple[Optional[float], Optional[float]]:
#     """Extract lat/lon from a Google Maps URL. Tries embedded coordinates first,
#     then falls back to Nominatim geocoding for address-based URLs."""
#     if not url:
#         return None, None

#     # Pattern 1: @lat,lon embedded in URL
#     m = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
#     if m:
#         return float(m.group(1)), float(m.group(2))

#     # Pattern 2: q=lat,lon or ll=lat,lon
#     m = re.search(r'[?&](?:q|ll)=(-?\d+\.\d+),(-?\d+\.\d+)', url)
#     if m:
#         return float(m.group(1)), float(m.group(2))

#     # Pattern 3: /place/.../@lat,lon
#     m = re.search(r'/place/[^/]+/@(-?\d+\.\d+),(-?\d+\.\d+)', url)
#     if m:
#         return float(m.group(1)), float(m.group(2))

#     # No raw coordinates — extract an address and geocode via Nominatim
#     address = None
#     # daddr=address (directions destination)
#     m = re.search(r'[?&]daddr=([^&]+)', url)
#     if m:
#         address = urllib.parse.unquote_plus(m.group(1))
#     # q=address (non-numeric value)
#     if not address:
#         m = re.search(r'[?&]q=([^&]+)', url)
#         if m:
#             val = urllib.parse.unquote_plus(m.group(1))
#             if not re.match(r'^-?\d+\.\d+,-?\d+\.\d+$', val):
#                 address = val
#     # /place/address/@
#     if not address:
#         m = re.search(r'/place/([^/@]+)', url)
#         if m:
#             address = urllib.parse.unquote_plus(m.group(1)).replace('+', ' ')

#     if not address:
#         return None, None

#     try:
#         encoded = urllib.parse.quote(address)
#         api_url = f"https://nominatim.openstreetmap.org/search?format=json&limit=1&q={encoded}"
#         req = urllib.request.Request(api_url, headers={"User-Agent": "SmartParkingDashboard/1.0"})
#         with urllib.request.urlopen(req, timeout=10) as resp:
#             data = json.loads(resp.read().decode())
#             if data:
#                 return float(data[0]["lat"]), float(data[0]["lon"])
#     except Exception as e:
#         logger.warning(f"Nominatim geocode failed for '{address}': {e}")

#     return None, None


# @app.post("/api/v1/developer/publish", response_model=PublishLocationResponse)
# async def publish_location(request: PublishLocationRequest):
#     """Publish a new parking location configuration (persisted to PostgreSQL)."""
#     loc_id = str(uuid.uuid4())[:8]
#     lat, lon = _geocode_from_google_maps_url(request.google_maps_url)
#     try:
#         conn = parking_service._pg_connect()
#         cur = conn.cursor()
#         cur.execute(
#             """
#             INSERT INTO public.published_locations
#                 (id, name, google_maps_url, video_url, grid_config, zones, total_spots, parameters, lat, lon, published_at)
#             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
#             """,
#             (
#                 loc_id,
#                 request.name,
#                 request.google_maps_url,
#                 request.video_url,
#                 json.dumps(request.grid_config.model_dump()),
#                 json.dumps([z.model_dump() for z in request.zones]),
#                 request.total_spots,
#                 json.dumps(request.parameters.model_dump()),
#                 lat,
#                 lon,
#             ),
#         )
#         conn.commit()
#         cur.close()
#         conn.close()
#     except Exception as e:
#         logger.error(f"Failed to persist location: {e}")
#         return JSONResponse(status_code=500, content={"error": "Failed to save location"})

#     logger.info(f"Published location: {request.name} ({loc_id}) — {request.total_spots} spots, coords=({lat},{lon})")
#     return PublishLocationResponse(
#         id=loc_id,
#         name=request.name,
#         total_spots=request.total_spots,
#         zone_count=len(request.zones),
#     )


# @app.get("/api/v1/developer/locations")
# async def list_published_locations():
#     """List all published parking locations from the database."""
#     try:
#         conn = parking_service._pg_connect()
#         cur = conn.cursor()
#         cur.execute(
#             "SELECT id, name, google_maps_url, video_url, grid_config, zones, "
#             "total_spots, parameters, published_at, lat, lon "
#             "FROM public.published_locations ORDER BY published_at DESC"
#         )
#         rows = cur.fetchall()
#         cur.close()
#         conn.close()
#         results = []
#         for r in rows:
#             row_lat, row_lon = None, None
#             # Try stored coordinates first
#             if len(r) > 9 and r[9] is not None:
#                 row_lat = r[9]
#             if len(r) > 10 and r[10] is not None:
#                 row_lon = r[10]
#             # Fallback: geocode from URL if lat/lon not stored
#             if row_lat is None and r[2]:
#                 row_lat, row_lon = _geocode_from_google_maps_url(r[2])
#             results.append({
#                 "id": r[0],
#                 "name": r[1],
#                 "google_maps_url": r[2],
#                 "video_url": r[3],
#                 "grid_config": r[4],
#                 "zones": r[5],
#                 "total_spots": r[6],
#                 "parameters": r[7],
#                 "published_at": r[8].isoformat() if r[8] else None,
#                 "lat": row_lat,
#                 "lon": row_lon,
#             })
#         return results
#     except Exception as e:
#         logger.error(f"Failed to list locations: {e}")
#         return []


# @app.delete("/api/v1/developer/locations/{location_id}")
# async def delete_published_location(location_id: str):
#     """Hard-delete a published location from the database."""
#     try:
#         conn = parking_service._pg_connect()
#         cur = conn.cursor()
#         cur.execute("DELETE FROM public.published_locations WHERE id = %s", (location_id,))
#         deleted = cur.rowcount
#         conn.commit()
#         cur.close()
#         conn.close()
#         if deleted == 0:
#             return JSONResponse(status_code=404, content={"error": "Location not found"})
#         logger.info(f"Deleted location: {location_id}")
#         return {"status": "deleted", "id": location_id}
#     except Exception as e:
#         logger.error(f"Failed to delete location {location_id}: {e}")
#         return JSONResponse(status_code=500, content={"error": "Failed to delete location"})


# ── Trained-model management ─────────────────────────────────────────

# @app.get("/api/v1/developer/models")
# async def list_trained_models(wizard: int = 0):
#     """List trained RF-DETR checkpoints under training/runs/ plus base model options.

#     ?wizard=1  — wizard context: no active flag, only base models that exist on disk.
#     (default)  — dashboard context: marks the currently loaded model as active.
#     """
#     results = []

#     if os.path.isdir(_RUNS_DIR):
#         for project in sorted(os.listdir(_RUNS_DIR)):
#             proj_dir = os.path.join(_RUNS_DIR, project)
#             if not os.path.isdir(proj_dir):
#                 continue
#             for run in sorted(os.listdir(proj_dir)):
#                 run_dir = os.path.join(proj_dir, run)
#                 checkpoint_names = sorted(
#                     fname
#                     for fname in os.listdir(run_dir)
#                     if os.path.isfile(os.path.join(run_dir, fname))
#                     and fname.lower().endswith((".pth", ".ckpt"))
#                 )
#                 for fname in checkpoint_names:
#                     cpath = os.path.join(run_dir, fname)
#                     rel = os.path.relpath(cpath, _PROJECT_ROOT)
#                     results.append({
#                         "label": f"{project} / {run} ({fname})",
#                         "project": project,
#                         "run": run,
#                         "weight_file": fname,
#                         "path": rel,
#                         "active": False,
#                     })

#     # Base model variants available through RF-DETR package
#     for base in ("nano", "small", "base", "medium", "large"):
#         results.append({
#             "label": f"Base model - {base}",
#             "project": "",
#             "run": "",
#             "weight_file": base,
#             "path": base,
#             "active": False,
#         })

#     # Also expose local checkpoint at repo root when present
#     for root_ckpt in ("rf-detr-base.pth",):
#         candidate = os.path.join(_PROJECT_ROOT, root_ckpt)
#         if os.path.isfile(candidate):
#             results.append({
#                 "label": f"Base checkpoint - {root_ckpt}",
#                 "project": "",
#                 "run": "",
#                 "weight_file": root_ckpt,
#                 "path": root_ckpt,
#                 "active": False,
#             })

#     if not wizard:
#         # Mark the currently loaded model as active (dashboard context)
#         active = parking_service.detection_service.model_path if parking_service else "base"
#         for r in results:
#             r["active"] = (
#                 r["path"] == active
#                 or r["weight_file"] == active
#                 or r["path"] == os.path.basename(active)
#             )

#     return results


# @app.post("/api/v1/developer/models/activate")
# async def activate_model(payload: dict):
#     """Store the developer's chosen model for wizard previews.

#     This endpoint is scoped to the Developer Persona only.
#     It does NOT touch the live detection pipeline used by the User Persona.
#     The wizard preview endpoint already receives model_path in each payload;
#     this endpoint simply persists the developer's last selection server-side.

#     To promote a model to the live detection pipeline, use:
#       POST /api/v1/live/model/activate
#     """
#     global _dev_model_path

#     raw_path: str = (payload.get("path") or "").strip()
#     if not raw_path:
#         return JSONResponse(status_code=400, content={"error": "path is required"})

#     # Security: reject absolute paths or path traversal
#     if os.path.isabs(raw_path) or ".." in raw_path.split(os.sep):
#         return JSONResponse(status_code=400, content={"error": "Invalid model path"})

#     # Validate that the file actually exists (for non-bare paths)
#     if os.sep in raw_path or "/" in raw_path:
#         full_path = os.path.normpath(os.path.join(_PROJECT_ROOT, raw_path))
#         if not os.path.isfile(full_path):
#             return JSONResponse(status_code=404, content={"error": f"Weight file not found: {raw_path}"})

#     _dev_model_path = raw_path
#     logger.info(f"Developer model preference saved: {raw_path} (live pipeline unchanged)")
#     return {"status": "saved", "path": raw_path, "label": raw_path, "scope": "developer_only"}


# @app.post("/api/v1/live/model/activate")
# async def activate_live_model(payload: dict):
#     """Promote a trained model to the LIVE detection pipeline (User Persona feed).

#     This is a deliberate, explicit action separate from the developer wizard.
#     The live detection loop keeps running on the current model while the new
#     one loads; the swap is atomic so there is no detection gap.

#     Expects: {"path": "training/runs/MyProject/run1/checkpoint_best_total.pth"}
#     """
#     if not parking_service:
#         return JSONResponse(status_code=503, content={"error": "Service not ready"})

#     raw_path: str = (payload.get("path") or "").strip()
#     if not raw_path:
#         return JSONResponse(status_code=400, content={"error": "path is required"})

#     if os.path.isabs(raw_path) or ".." in raw_path.split(os.sep):
#         return JSONResponse(status_code=400, content={"error": "Invalid model path"})

#     if os.sep in raw_path or "/" in raw_path:
#         full_path = os.path.normpath(os.path.join(_PROJECT_ROOT, raw_path))
#         if not os.path.isfile(full_path):
#             return JSONResponse(status_code=404, content={"error": f"Weight file not found: {raw_path}"})
#         load_path = full_path
#     else:
#         load_path = raw_path

#     # Load entirely in background — live model keeps running during this
#     from app.services.detection_service import DetectionService as _DS
#     tmp = _DS(model_path=load_path, confidence=parking_service.detection_service.confidence)
#     success = await asyncio.get_event_loop().run_in_executor(None, tmp.load_model)
#     if not success:
#         return JSONResponse(status_code=500, content={"error": "Failed to load model — check server logs"})

#     # Atomic swap — single pointer reassignment, no gap in detection
#     parking_service.detection_service = tmp
#     logger.info(f"Live detection model promoted to: {load_path}")
#     return {"status": "activated", "path": raw_path, "label": raw_path, "scope": "live"}


# async def _activate_location_internal(location_id: str) -> dict:
#     """Switch the live pipeline to a published location's dedicated StreamService + ParkingService.

#     On first activation the pipeline is built (video opened, model loaded) and cached in
#     _pipeline_pool[location_id].  Subsequent activations of the same location just swap the
#     global pointers — no teardown or reload needed.
#     """
#     global parking_service, stream_service

#     # ── Fast path: pipeline already built ───────────────────────────
#     if location_id in _pipeline_pool:
#         stream_service, parking_service = _pipeline_pool[location_id]
#         logger.info(f"activate_location: switched to cached pipeline for {location_id}")
#         # Fetch name for the response
#         name = location_id
#         try:
#             conn = parking_service._pg_connect()
#             cur = conn.cursor()
#             cur.execute("SELECT name FROM public.published_locations WHERE id = %s", (location_id,))
#             r = cur.fetchone()
#             cur.close(); conn.close()
#             if r:
#                 name = r[0]
#         except Exception:
#             pass
#         return {"status": "activated", "id": location_id, "name": name}

#     # ── Slow path: build a new pipeline ─────────────────────────────
#     try:
#         conn = parking_service._pg_connect()
#         cur = conn.cursor()
#         cur.execute(
#             "SELECT name, video_url, grid_config, zones, total_spots, parameters "
#             "FROM public.published_locations WHERE id = %s",
#             (location_id,),
#         )
#         row = cur.fetchone()
#         cur.close()
#         conn.close()
#     except Exception as e:
#         logger.error(f"activate_location DB error: {e}")
#         return {"error": "Database error"}

#     if not row:
#         return {"error": f"Location {location_id} not found"}

#     name, video_url, grid_config_raw, zones_raw, total_spots, parameters_raw = row

#     # ── Parse zones ──────────────────────────────────────────────────
#     zones_list: list = []
#     zone_config: Dict[str, int] = {}
#     if zones_raw:
#         try:
#             zones_list = zones_raw if isinstance(zones_raw, list) else json.loads(zones_raw)
#             zone_config = {
#                 z["zone_id"]: int(z.get("user_spots") or z.get("estimated_spots") or 0)
#                 for z in zones_list
#             }
#         except Exception as e:
#             logger.warning(f"activate_location: could not parse zones: {e}")
#     if not zone_config:
#         zone_config = {
#             "TL": int(os.getenv("PARKING_ZONE_TL_SPOTS", "96")),
#             "TR": int(os.getenv("PARKING_ZONE_TR_SPOTS", "94")),
#             "ML": int(os.getenv("PARKING_ZONE_ML_SPOTS", "88")),
#             "MR": int(os.getenv("PARKING_ZONE_MR_SPOTS", "90")),
#             "BL": int(os.getenv("PARKING_ZONE_BL_SPOTS", "52")),
#             "BR": int(os.getenv("PARKING_ZONE_BR_SPOTS", "54")),
#         }

#     # ── Parse parameters ─────────────────────────────────────────────
#     params: dict = {}
#     if parameters_raw:
#         try:
#             params = parameters_raw if isinstance(parameters_raw, dict) else json.loads(parameters_raw)
#         except Exception:
#             pass
#     conf = float(params.get("confidence_threshold", 0.15))
#     model_path_raw = (params.get("model_path") or "").strip()

#     # ── Resolve video path ───────────────────────────────────────────
#     resolved_video = _DEFAULT_VIDEO_PATH
#     if video_url:
#         if not video_url.startswith(("http://", "https://", "rtsp://")):
#             candidate = os.path.normpath(
#                 os.path.join(os.path.dirname(__file__), "..", "..", "data", video_url)
#             )
#             if os.path.isfile(candidate):
#                 resolved_video = candidate
#             else:
#                 logger.warning(f"activate_location: video not found at {candidate}, using default")
#         else:
#             resolved_video = video_url

#     # ── Resolve model path ───────────────────────────────────────────
#     # Empty model_path → use RF-DETR base.
#     if model_path_raw:
#         if os.sep in model_path_raw or "/" in model_path_raw:
#             full_mp = os.path.normpath(os.path.join(_PROJECT_ROOT, model_path_raw))
#         else:
#             full_mp = model_path_raw
#     else:
#         full_mp = os.getenv("RFDETR_MODEL_PATH", "base")

#     # ── Build zone grid ──────────────────────────────────────────────
#     zone_grid: list = [["TL", "TR"], ["ML", "MR"], ["BL", "BR"]]
#     grid_h_lines: List[float] = [1.0 / 3.0, 2.0 / 3.0]
#     grid_v_lines: List[float] = [0.5]
#     grid_h_line_angles: List[float] = [0.0, 0.0]
#     grid_v_line_angles: List[float] = [0.0]
#     grid_exclude_regions: List[List[List[float]]] = []
#     grid_border: Dict[str, float] = {"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0}
#     grid_h_angle_deg: float = 0.0
#     grid_v_angle_deg: float = 0.0
#     if zones_list and grid_config_raw:
#         try:
#             gc = grid_config_raw if isinstance(grid_config_raw, dict) else json.loads(grid_config_raw)
#             h_lines = gc.get("horizontal_lines", [])
#             v_lines = gc.get("vertical_lines", [])
#             grid_h_lines = [float(v) for v in h_lines]
#             grid_v_lines = [float(v) for v in v_lines]
#             grid_h_line_angles = [float(v) for v in (gc.get("horizontal_line_angles") or [])]
#             grid_v_line_angles = [float(v) for v in (gc.get("vertical_line_angles") or [])]
#             grid_exclude_regions = gc.get("exclude_regions") or []
#             b = gc.get("border") or {}
#             grid_border = {
#                 "top": float(b.get("top", 0.0)),
#                 "right": float(b.get("right", 0.0)),
#                 "bottom": float(b.get("bottom", 0.0)),
#                 "left": float(b.get("left", 0.0)),
#             }
#             grid_h_angle_deg = float(gc.get("horizontal_angle_deg", 0.0) or 0.0)
#             grid_v_angle_deg = float(gc.get("vertical_angle_deg", 0.0) or 0.0)
#             if len(grid_h_line_angles) < len(grid_h_lines):
#                 grid_h_line_angles = grid_h_line_angles + [grid_h_angle_deg] * (len(grid_h_lines) - len(grid_h_line_angles))
#             if len(grid_v_line_angles) < len(grid_v_lines):
#                 grid_v_line_angles = grid_v_line_angles + [grid_v_angle_deg] * (len(grid_v_lines) - len(grid_v_line_angles))
#             grid_rows = len(h_lines) + 1
#             grid_cols = len(v_lines) + 1
#             zone_ids = [z["zone_id"] for z in zones_list]
#             g: list = []
#             for r in range(grid_rows):
#                 row_: list = []
#                 for c in range(grid_cols):
#                     idx = r * grid_cols + c
#                     if idx < len(zone_ids):
#                         row_.append(zone_ids[idx])
#                 if row_:
#                     g.append(row_)
#             if g:
#                 zone_grid = g
#         except Exception as e:
#             logger.warning(f"activate_location: could not build zone_grid: {e}")

#     # ── Create dedicated StreamService ──────────────────────────────
#     new_ss = StreamService(video_path=resolved_video)
#     logger.info(f"activate_location: StreamService → {resolved_video}")

#     # ── Create dedicated DetectionService and load model ────────────
#     new_ds = DetectionService(model_path=full_mp, confidence=conf)
#     ok = await asyncio.get_event_loop().run_in_executor(None, new_ds.load_model)
#     if not ok:
#         logger.warning(f"activate_location: model load failed ({full_mp}), reusing current")
#         new_ds = parking_service.detection_service

#     # ── Create dedicated ParkingService ─────────────────────────────
#     new_ps = ParkingService(
#         stream_service=new_ss,
#         total_spots=int(total_spots or sum(zone_config.values())),
#         zone_config=zone_config,
#     )
#     # Inject pre-loaded detection service (skip default load)
#     new_ps.detection_service = new_ds
#     new_ps.detection_loaded = new_ds.loaded
#     new_ps._zone_grid = zone_grid
#     new_ps._grid_h_lines = grid_h_lines
#     new_ps._grid_v_lines = grid_v_lines
#     new_ps._grid_h_line_angles = grid_h_line_angles
#     new_ps._grid_v_line_angles = grid_v_line_angles
#     new_ps._grid_border = grid_border
#     new_ps._grid_exclude_regions = grid_exclude_regions
#     new_ps._grid_h_angle_deg = grid_h_angle_deg
#     new_ps._grid_v_angle_deg = grid_v_angle_deg

#     # ── Store and activate ───────────────────────────────────────────
#     _pipeline_pool[location_id] = (new_ss, new_ps)
#     stream_service = new_ss
#     parking_service = new_ps
#     logger.info(f"activate_location: pipeline ready for '{name}' ({location_id})")
#     return {"status": "activated", "id": location_id, "name": name}


# @app.post("/api/v1/location/restore-default")
# async def restore_default_location():
#     """Switch the live pipeline back to the default (demo) pipeline.

#     Called when a user selects a demo/hardcoded location so the feed reverts
#     to the default video and base detector model.
#     """
#     global parking_service, stream_service

#     default = _pipeline_pool.get("default")
#     if not default:
#         return JSONResponse(status_code=503, content={"error": "Default pipeline not ready"})

#     stream_service, parking_service = default
#     # Ensure zone grid is the default 2×3 layout
#     parking_service._zone_grid = [["TL", "TR"], ["ML", "MR"], ["BL", "BR"]]
#     logger.info("restore_default: switched to default pipeline (RF-DETR base + default video)")
#     return {"status": "restored"}


# @app.post("/api/v1/location/activate/{location_id}")
# async def activate_location(location_id: str):
#     """Load a published location's config into the live detection service."""
#     if not parking_service:
#         return JSONResponse(status_code=503, content={"error": "Service not ready"})

#     result = await _activate_location_internal(location_id)

#     if "error" in result:
#         status_code = 404 if "not found" in result["error"] else 500
#         return JSONResponse(status_code=status_code, content=result)

#     # Persist so backend restores this location on next restart
#     _settings_set("active_location_id", location_id)
#     return result



# def _row_labels(count: int) -> list:
#     """Generate row labels: T, M, B for 3 rows, or T, UM, M, LM, B for more."""
#     if count == 1:
#         return [""]
#     if count == 2:
#         return ["T", "B"]
#     if count == 3:
#         return ["T", "M", "B"]
#     labels = ["T"]
#     for i in range(1, count - 1):
#         labels.append(f"M{i}")
#     labels.append("B")
#     return labels


# def _col_labels(count: int) -> list:
#     """Generate column labels: L, R for 2 cols, or L, C, R for more."""
#     if count == 1:
#         return [""]
#     if count == 2:
#         return ["L", "R"]
#     if count == 3:
#         return ["L", "C", "R"]
#     labels = ["L"]
#     for i in range(1, count - 1):
#         labels.append(f"C{i}")
#     labels.append("R")
#     return labels


# @app.websocket("/ws/occupancy")
# async def websocket_occupancy(websocket: WebSocket):
#     """WebSocket for real-time occupancy updates."""
#     await websocket.accept()
#     connected_clients.append(websocket)
#     logger.info(f"WS client connected. Total: {len(connected_clients)}")

#     try:
#         if parking_service:
#             occupancy = await parking_service.get_current_occupancy()
#             await websocket.send_text(occupancy.model_dump_json())
#         while True:
#             await websocket.receive_text()
#     except WebSocketDisconnect:
#         pass
#     finally:
#         if websocket in connected_clients:
#             connected_clients.remove(websocket)
#         logger.info(f"WS client disconnected. Total: {len(connected_clients)}")


# @app.websocket("/api/ws/occupancy")
# async def websocket_occupancy_api(websocket: WebSocket):
#     """WebSocket alias for deployments that prefix routes with /api."""
#     await websocket_occupancy(websocket)


@app.websocket("/ws/live-counts")
async def websocket_live_counts(websocket: WebSocket):
    """Live stream counter over WebSocket.

    Client flow:
    1) Connect to /ws/live-counts
    2) Send one JSON text message with config:
       {
         "live_url": "rtsp://..." or "http://...",
         "model_path": "base" or checkpoint path,
         "interval_seconds": 10,
         "confidence_threshold": 0.5,
         "save_annotated_frames": false,
         "save_output_dir": "optional/path"
       }
    3) Server sends a count event every interval until client disconnects.
    """
    import cv2 as cv

    await websocket.accept()
    cap = None

    try:
        raw = await websocket.receive_text()
        try:
            cfg = json.loads(raw)
        except Exception:
            await websocket.send_json({"event": "error", "message": "First message must be valid JSON config"})
            await websocket.close(code=1003)
            return

        live_url = str(cfg.get("live_url") or "").strip()
        if not live_url:
            await websocket.send_json({"event": "error", "message": "live_url is required"})
            await websocket.close(code=1003)
            return
        if not live_url.startswith(("rtsp://", "http://", "https://")):
            await websocket.send_json({"event": "error", "message": "live_url must start with rtsp://, http:// or https://"})
            await websocket.close(code=1003)
            return

        model_path = str(cfg.get("model_path") or "base").strip() or "base"
        try:
            interval_seconds = int(cfg.get("interval_seconds", 10))
        except (TypeError, ValueError):
            interval_seconds = 10
        interval_seconds = max(1, interval_seconds)

        try:
            confidence_threshold = float(cfg.get("confidence_threshold", 0.5))
        except (TypeError, ValueError):
            confidence_threshold = 0.5
        confidence_threshold = max(0.0, min(1.0, confidence_threshold))

        save_annotated_frames = bool(cfg.get("save_annotated_frames", False))
        save_output_dir = str(cfg.get("save_output_dir") or "").strip()
        run_output_dir: Optional[str] = None
        if save_annotated_frames:
            run_output_dir = (
                os.path.normpath(save_output_dir)
                if save_output_dir
                else os.path.join(
                    UPLOAD_DIR,
                    "live_counts_debug",
                    f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
                )
            )
            os.makedirs(run_output_dir, exist_ok=True)

        detector = DetectionService(model_path=model_path, confidence=confidence_threshold)
        ok = await asyncio.to_thread(detector.load_model)
        if not ok:
            await websocket.send_json({
                "event": "error",
                "message": f"Failed to load model: {detector.last_error or model_path}",
            })
            await websocket.close(code=1011)
            return

        cap = cv.VideoCapture(live_url)

        def _save_annotated(frame: np.ndarray, detections: list, seq: int, count: int) -> Optional[str]:
            if not run_output_dir:
                return None
            annotated = frame.copy()
            for (bx1, by1, bx2, by2), conf, name in detections:
                x1 = int(round(max(0.0, bx1)))
                y1 = int(round(max(0.0, by1)))
                x2 = int(round(max(0.0, bx2)))
                y2 = int(round(max(0.0, by2)))
                cv.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 2)
                cv.putText(
                    annotated,
                    f"{name} {conf:.2f}",
                    (x1, max(16, y1 - 6)),
                    cv.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 0, 255),
                    1,
                    cv.LINE_AA,
                )

            cv.putText(
                annotated,
                f"seq={seq} count={count}",
                (10, 24),
                cv.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
                cv.LINE_AA,
            )
            out_path = os.path.join(run_output_dir, f"live_{int(seq):06d}.jpg")
            cv.imwrite(out_path, annotated)
            return out_path

        await websocket.send_json(
            {
                "event": "started",
                "live_url": live_url,
                "model_path": model_path,
                "interval_seconds": interval_seconds,
                "confidence_threshold": confidence_threshold,
                "save_output_dir": run_output_dir,
            }
        )

        seq = 0
        while True:
            if not cap.isOpened():
                cap.release()
                cap = cv.VideoCapture(live_url)

            ok, frame = await asyncio.to_thread(cap.read)
            if not ok or frame is None:
                await websocket.send_json(
                    {
                        "event": "warning",
                        "message": "Could not read frame from live stream; retrying",
                        "seq": seq,
                    }
                )
                await asyncio.sleep(interval_seconds)
                continue

            detections = await asyncio.to_thread(detector._predict_detections, frame)
            count = int(len(detections))
            saved_path = None
            if save_annotated_frames and run_output_dir:
                saved_path = await asyncio.to_thread(_save_annotated, frame, detections, seq, count)

            await websocket.send_json(
                {
                    "event": "count",
                    "seq": seq,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "count": count,
                    "confidence_threshold": confidence_threshold,
                    "saved_frame_path": saved_path,
                }
            )

            seq += 1
            await asyncio.sleep(interval_seconds)

    except WebSocketDisconnect:
        logger.info("Live-count WS client disconnected")
    except Exception as e:
        logger.error(f"Live-count WS error: {e}")
        try:
            await websocket.send_json({"event": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass

# @app.websocket("/ws/live-counts")
@app.post("/live-counts")
async def live_counts(request: Request):
    payload = await request.json()
    streams = payload.get("streams", [])
    output_dir = "spot_detection_output" #payload.get("output_dir", "spot_detection_output")
    processor = VideoProcessCount(
        model_path=r"yolov8m.pt",
        conf_threshold=0.40,
        iou_threshold=0.45,
    )
    processor._get_model()
    results = []
    for stream in streams:
        cap = None
        stream_id = ""
        try:
            stream_id = stream.get("stream_id", "")
            source = stream.get("source", "")
            print(f"Processing stream_id={stream_id} source={source},datatype={type(source)}")
            conf = float(stream.get("conf", 0.25))
            iou = float(stream.get("iou", 0.45))
            regions_cfg = stream.get("regions", {})
            processor.conf_threshold = conf
            processor.iou_threshold = iou
            import cv2 as cv
            cap = cv.VideoCapture(source)
            if cap is None or not cap.isOpened():
                results.append({
                    "stream_id": stream_id,
                    "count": 0,
                    "error": "Could not open stream",
                })
                continue
            ok, frame = cap.read()
            if not ok or frame is None:
                results.append({
                    "stream_id": stream_id,
                    "count": 0,
                    "error": "Could not read frame",
                })
                continue
            height, width = frame.shape[:2]
            parsed_regions = build_regions(regions_cfg, width, height)
            count, region_counts, detections, frame_path = processor._count_frame(frame, parsed_regions , output_dir, stream_id)
            results.append({
                "stream_id": stream_id,
                "count": int(count),
                "region_counts": region_counts,
                "error": None,
                "frame_path": frame_path,
            })
        except Exception as e:
            results.append({
                "stream_id": stream_id,
                "count": 0,
                "error": str(e),
            })
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
    print(f"Live counts results: {results}")
    return {
        "event": "count",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "results": results,
    }

@app.get("/api/v1/stream/mjpeg")

async def stream_mjpeg(url: str, fps: int = 10, quality: int = 70):

    """Stream MJPEG frames from an RTSP/HTTP stream URL.
 
    Returns a multipart/x-mixed-replace MJPEG stream that can be consumed

    by an HTML <img> tag, enabling browsers to display live RTSP feeds.
 
    Query params:

      url     — RTSP or HTTP stream URL (required)

      fps     — target frame rate for the MJPEG output (default 10)

      quality — JPEG quality 1-100 (default 70)

    """

    import cv2 as cv
 
    if not url.startswith(("rtsp://", "http://", "https://")):

        return JSONResponse(status_code=400, content={"error": "url must start with rtsp://, http://, or https://"})
 
    fps = max(1, min(fps, 30))

    quality = max(1, min(quality, 100))

    frame_interval = 1.0 / fps
 
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
 
    async def generate():

        cap = cv.VideoCapture(url)

        cap.set(cv.CAP_PROP_BUFFERSIZE, 3)

        last_good_frame = None

        try:

            while True:

                ok, frame = None, None

                try:

                    ok, frame = await asyncio.to_thread(cap.read)

                except Exception as e:

                    logger.debug(f"MJPEG stream read error: {e}")

                    ok = False
 
                if not ok or frame is None or frame.size == 0:

                    # On read failure, try reconnect or use last known good frame

                    await asyncio.sleep(0.5)

                    if not cap.isOpened():

                        try:

                            cap.release()

                        except:

                            pass

                        try:

                            cap = cv.VideoCapture(url)

                            cap.set(cv.CAP_PROP_BUFFERSIZE, 3)

                            logger.info(f"Reconnected MJPEG stream")

                        except Exception as e:

                            logger.warning(f"MJPEG reconnect failed: {e}")

                    continue
 
                # Cache last good frame for fallback

                last_good_frame = frame.copy() if frame is not None else last_good_frame
 
                try:

                    _, buf = cv.imencode(".jpg", frame, [cv.IMWRITE_JPEG_QUALITY, quality])

                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"

                           + buf.tobytes() + b"\r\n")

                except Exception as e:

                    logger.debug(f"MJPEG encode error: {e}")

                    continue
 
                await asyncio.sleep(frame_interval)

        except Exception as e:

            logger.warning(f"MJPEG stream error for {url}: {e}")

        finally:

            try:

                cap.release()

            except Exception:

                pass
 
    return StreamingResponse(

        generate(),

        media_type="multipart/x-mixed-replace; boundary=frame",

    )

 

@app.websocket("/api/ws/live-counts")
async def websocket_live_counts_api(websocket: WebSocket):
    """WebSocket alias for deployments that prefix routes with /api."""
    await websocket_live_counts(websocket)


@app.websocket("/ws/live-entry-exit-counts")
# @app.get("/api/ws/live-entry-exit-counts")
async def websocket_live_entry_exit_counts(websocket: WebSocket):
    """Live entry/exit counter over WebSocket using centroid tracking + tripwire crossing.

    Client sends ONE initial JSON config message:
    {
      "entry_url":             "rtsp://..." or "http://...",  (required)
      "exit_url":              "rtsp://..." or "http://...",  (required)
    "model_path":            ignored,                        (always pinned to "./yolo_trained.pt")
      "confidence_threshold":  0.5,                           (optional)
      "interval_seconds":      10,                            (optional, how often to emit counts)
      "tick_seconds":          1,                             (optional, how often frames are sampled for tracking)
      "max_match_distance_px": 90,                            (optional)
      "max_stale_frames":      20,                            (optional)
      "line_deadband_px":      6,                             (optional)
    "min_band_frames":       1,                             (optional, stage-machine in-band frames)
    "crossing_dedupe_seconds": 0.45,                        (optional, dedupe time window in seconds)
    "crossing_dedupe_frames":  optional override,           (optional, dedupe window in processed frames)
    "crossing_dedupe_radius_px": 40,                        (optional, dedupe spatial radius)
    "use_bottom_center_for_tripwire": true,                 (optional)
    "allow_direct_flip_crossing": true,                     (optional)
      "allow_track_recount":   false,                         (optional)
      "entry_line": {"x1":..,"y1":..,"x2":..,"y2":..},       (optional, default: horizontal mid-line)
      "exit_line":  {"x1":..,"y1":..,"x2":..,"y2":..},       (optional)
      "save_annotated_frames": false,                         (optional)
      "save_output_dir":       "optional/path"                (optional)
    }

    Server emits:
      {"event": "started",  ...config echo...}
      {"event": "count",    "entered_count": N, "exited_count": M, "timestamp": "...", "seq": N}
      {"event": "warning",  "stream": "entry"|"exit", "message": "..."}
      {"event": "error",    "message": "..."}

    Notes:
    - Each tick a single frame is grabbed from each live stream and run through tracking.
    - Counts are CUMULATIVE since the WebSocket was opened.
    - Disconnect the WebSocket to stop.
    """
    import cv2 as cv
    import time as _time

    await websocket.accept()
    ws_id = uuid.uuid4().hex[:8]
    entry_cap = None
    exit_cap = None
    logger.info(f"[live-entry-exit:{ws_id}] WebSocket accepted")

    try:
        raw = await websocket.receive_text()
        try:
            cfg = json.loads(raw)
        except Exception:
            logger.warning(f"[live-entry-exit:{ws_id}] Invalid first payload: expected JSON")
            await websocket.send_json({"event": "error", "message": "First message must be valid JSON config"})
            await websocket.close(code=1003)
            return

        # ── Parse required fields ─────────────────────────────────────
        entry_url = str(cfg.get("entry_url") or "").strip()
        exit_url  = str(cfg.get("exit_url")  or "").strip()
        if not entry_url:
            logger.warning(f"[live-entry-exit:{ws_id}] Missing required entry_url")
            await websocket.send_json({"event": "error", "message": "entry_url is required"})
            await websocket.close(code=1003)
            return
        if not exit_url:
            logger.warning(f"[live-entry-exit:{ws_id}] Missing required exit_url")
            await websocket.send_json({"event": "error", "message": "exit_url is required"})
            await websocket.close(code=1003)
            return
        for url_val, label in ((entry_url, "entry_url"), (exit_url, "exit_url")):
            if not url_val.startswith(("rtsp://", "http://", "https://")):
                logger.warning(f"[live-entry-exit:{ws_id}] Invalid URL scheme for {label}: {url_val}")
                await websocket.send_json({"event": "error", "message": f"{label} must start with rtsp://, http:// or https://"})
                await websocket.close(code=1003)
                return

        # ── Parse optional fields ─────────────────────────────────────
        # Pin live entry/exit counting to project YOLO weights for consistency.
        model_path = "./yolo_trained.pt"
        resolved_model_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", model_path)
        )
        if not os.path.isfile(resolved_model_path):
            logger.error(f"[live-entry-exit:{ws_id}] YOLO model missing: {resolved_model_path}")
            await websocket.send_json({
                "event": "error",
                "message": f"YOLO model not found: {resolved_model_path}",
            })
            await websocket.close(code=1011)
            return

        def _parse_float(key, default, lo=None, hi=None):
            try:
                v = float(cfg.get(key, default))
            except (TypeError, ValueError):
                v = float(default)
            if lo is not None: v = max(lo, v)
            if hi is not None: v = min(hi, v)
            return v

        def _parse_int(key, default, lo=None):
            try:
                v = int(cfg.get(key, default))
            except (TypeError, ValueError):
                v = int(default)
            if lo is not None: v = max(lo, v)
            return v

        confidence_threshold    = _parse_float("confidence_threshold",    0.5,  0.0, 1.0)
        interval_seconds        = _parse_int(  "interval_seconds",        10,   lo=1)
        tick_seconds            = _parse_float("tick_seconds",            1.0,  0.05)
        max_match_distance_px   = _parse_float("max_match_distance_px",   90.0, 5.0)
        max_stale_frames        = _parse_int(  "max_stale_frames",        20,   lo=1)
        line_deadband_px        = _parse_float("line_deadband_px",        6.0,  0.0)
        min_band_frames         = _parse_int(  "min_band_frames",         1,    lo=1)
        crossing_dedupe_seconds = _parse_float("crossing_dedupe_seconds", 0.45, 0.0)
        raw_crossing_dedupe_frames = cfg.get("crossing_dedupe_frames", None)
        if raw_crossing_dedupe_frames is None:
            crossing_dedupe_frames = int(round(crossing_dedupe_seconds / max(0.01, tick_seconds)))
        else:
            try:
                crossing_dedupe_frames = int(raw_crossing_dedupe_frames)
            except (TypeError, ValueError):
                crossing_dedupe_frames = int(round(crossing_dedupe_seconds / max(0.01, tick_seconds)))
        crossing_dedupe_frames = max(0, crossing_dedupe_frames)
        crossing_dedupe_radius_px = _parse_float("crossing_dedupe_radius_px", 40.0, 0.0)
        use_bottom_center_for_tripwire = bool(cfg.get("use_bottom_center_for_tripwire", True))
        allow_direct_flip_crossing = bool(cfg.get("allow_direct_flip_crossing", True))
        allow_track_recount     = bool(cfg.get("allow_track_recount", False))
        save_annotated_frames   = bool(cfg.get("save_annotated_frames", False))
        save_output_dir         = str(cfg.get("save_output_dir") or "").strip()
        entry_line_cfg          = cfg.get("entry_line")
        exit_line_cfg           = cfg.get("exit_line")
        logger.info(
            f"[live-entry-exit:{ws_id}] Config parsed: "
            f"confidence={confidence_threshold}, interval={interval_seconds}s, tick={tick_seconds}s, "
            f"max_match_distance_px={max_match_distance_px}, max_stale_frames={max_stale_frames}, "
            f"line_deadband_px={line_deadband_px}, min_band_frames={min_band_frames}, "
            f"crossing_dedupe_frames={crossing_dedupe_frames}, crossing_dedupe_radius_px={crossing_dedupe_radius_px}, "
            f"use_bottom_center_for_tripwire={use_bottom_center_for_tripwire}, "
            f"allow_direct_flip_crossing={allow_direct_flip_crossing}, allow_track_recount={allow_track_recount}, "
            f"save_annotated_frames={save_annotated_frames}"
        )

        run_output_dir: Optional[str] = None
        if save_annotated_frames:
            run_output_dir = (
                os.path.normpath(save_output_dir)
                if save_output_dir
                else os.path.join(
                    ENTRY_EXIT_IMAGES_DIR,
                    "live",
                    f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
                )
            )
            os.makedirs(run_output_dir, exist_ok=True)

        # ── Load model ────────────────────────────────────────────────
        detector = DetectionService(model_path=model_path, confidence=confidence_threshold)
        ok = await asyncio.to_thread(detector.load_model)
        if not ok:
            logger.error(
                f"[live-entry-exit:{ws_id}] Model load failed: {detector.last_error or model_path}"
            )
            await websocket.send_json({
                "event": "error",
                "message": f"Failed to load model: {detector.last_error or model_path}",
            })
            await websocket.close(code=1011)
            return
        detector1 = DetectionService(model_path=model_path, confidence=confidence_threshold)
        ok = await asyncio.to_thread(detector1.load_model)
        if not ok:
            logger.error(
                f"[live-entry-exit:{ws_id}] Model load failed: {detector1.last_error or model_path}"
            )
            await websocket.send_json({
                "event": "error",
                "message": f"Failed to load model: {detector1.last_error or model_path}",
            })
            await websocket.close(code=1011)
            return
        logger.info(f"[live-entry-exit:{ws_id}] Model loaded: {resolved_model_path}")

        # ── Open streams ──────────────────────────────────────────────
        if not os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS"):
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|fflags;nobuffer|max_delay;500000|stimeout;5000000"

        entry_cap = cv.VideoCapture(entry_url)
        exit_cap  = cv.VideoCapture(exit_url)
        entry_cap.set(cv.CAP_PROP_BUFFERSIZE, 1)
        exit_cap.set(cv.CAP_PROP_BUFFERSIZE, 1)
        if hasattr(cv, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
            entry_cap.set(cv.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
            exit_cap.set(cv.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
        if hasattr(cv, "CAP_PROP_READ_TIMEOUT_MSEC"):
            entry_cap.set(cv.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
            exit_cap.set(cv.CAP_PROP_READ_TIMEOUT_MSEC, 5000)

        logger.info(
            f"[live-entry-exit:{ws_id}] Streams opened: "
            f"entry_opened={entry_cap.isOpened()}, exit_opened={exit_cap.isOpened()}"
        )

        # ── Tripwire helpers (shared with recorded endpoint) ──────────
        def _parse_line_cfg_live(line_cfg: Any, w: int, h: int):
            default = ((0.0, h / 2.0), (float(w - 1), h / 2.0))
            if not isinstance(line_cfg, dict):
                return default
            try:
                x1 = float(line_cfg.get("x1"))
                y1 = float(line_cfg.get("y1"))
                x2 = float(line_cfg.get("x2"))
                y2 = float(line_cfg.get("y2"))
            except (TypeError, ValueError):
                return default
            if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
                return default
            return (x1, y1), (x2, y2)

        def _signed_side_live(px: float, py: float, p1, p2) -> float:
            return ((p2[0] - p1[0]) * (py - p1[1])) - ((p2[1] - p1[1]) * (px - p1[0]))

        def _side_zone(side: float) -> int:
            if side > line_deadband_px:
                return 1
            if side < -line_deadband_px:
                return -1
            return 0

        # Per-stream mutable state
        def _make_stream_state() -> dict:
            return {
                "tracks": {},
                "next_id": 1,
                "crossing_count": 0,
                "p1": None,
                "p2": None,
                "frame_idx": 0,
                "out_dir": None,
                "save_seq": 0,
                "saved_verification": False,  # Track if we've saved the tripwire verification frame
                "recent_crossings": deque(),
            }

        entry_state = _make_stream_state()
        exit_state  = _make_stream_state()
        if run_output_dir:
            entry_state["out_dir"] = os.path.join(run_output_dir, "entry")
            exit_state["out_dir"]  = os.path.join(run_output_dir, "exit")
            os.makedirs(entry_state["out_dir"], exist_ok=True)
            os.makedirs(exit_state["out_dir"],  exist_ok=True)

        def _tripwire_payload(state: dict) -> Optional[dict]:
            p1 = state.get("p1")
            p2 = state.get("p2")
            if p1 is None or p2 is None:
                return None
            return {
                "x1": round(float(p1[0]), 2),
                "y1": round(float(p1[1]), 2),
                "x2": round(float(p2[0]), 2),
                "y2": round(float(p2[1]), 2),
            }

        def _save_tripwire_verification(frame: np.ndarray, state: dict, stream_label: str, current_epoch: int) -> None:
            # if not state.get("out_dir") or state.get("saved_verification"):
                # return
            p1 = state.get("p1")
            p2 = state.get("p2")
            if p1 is None or p2 is None:
                return
            dbg = frame.copy()
            cv.line(
                dbg,
                (int(round(p1[0])), int(round(p1[1]))),
                (int(round(p2[0])), int(round(p2[1]))),
                (0, 255, 255),
                3,
            )
            cv.putText(
                dbg,
                f"TRIPWIRE POSITION ({stream_label})",
                (10, 40),
                cv.FONT_HERSHEY_SIMPLEX,
                1.0,
                (0, 255, 255),
                2,
                cv.LINE_AA,
            )
            cv.imwrite(os.path.join(state["out_dir"], f"000_tripwire_position_{current_epoch}.jpg"), dbg)
            state["saved_verification"] = True

        def _initialize_tripwire_from_stream(cap: cv.VideoCapture, state: dict, line_cfg: Any,
                                            stream_url: str, stream_label: str) -> bool:
            if not cap.isOpened():
                logger.warning(
                    f"[live-entry-exit:{ws_id}] {stream_label} stream is not opened during tripwire init"
                )
                return False
            ok, frame = cap.read()
            if not ok or frame is None:
                logger.warning(
                    f"[live-entry-exit:{ws_id}] {stream_label} initial read failed; retrying stream reopen"
                )
                cap.release()
                cap.open(stream_url)
                ok, frame = cap.read()
                if not ok or frame is None:
                    logger.warning(
                        f"[live-entry-exit:{ws_id}] {stream_label} tripwire init failed after reopen"
                    )
                    return False

            h, w = frame.shape[:2]
            state["p1"], state["p2"] = _parse_line_cfg_live(line_cfg, w, h)
            logger.info(
                f"[live-entry-exit:{ws_id}] {stream_label} tripwire initialized: "
                f"p1={state['p1']}, p2={state['p2']}, frame={w}x{h}"
            )
            current_epoch = round(time.time() * 1000)
            _save_tripwire_verification(frame, state, stream_label, current_epoch)
            return True

        def _iou(a: tuple, b: tuple) -> float:
            ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
            ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
            inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
            if inter == 0.0:
                return 0.0
            area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
            area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
            union = area_a + area_b - inter
            iou   = inter / union               if union              > 0 else 0.0
            iomin = inter / min(area_a, area_b) if min(area_a, area_b) > 0 else 0.0
            return max(iou, iomin)

        def _process_frame(frame: np.ndarray, state: dict, line_cfg: Any, stream_label: str, current_epoch: int) -> int:
            """Run detection+tracking on one frame; return crossing delta this frame."""
            start_time = time.time()
            raw_detections = detector._predict_detections(frame)
            end_time = time.time()
            logging.info(f"YOLO total detection time: {end_time - start_time} seconds")
            logging.info(f"raw_detections: {raw_detections}")
            if state["frame_idx"] % 30 == 0 or len(raw_detections) > 0:
                logger.debug(f"[live-entry-exit:{ws_id}] {stream_label} raw_detections={len(raw_detections)}")
            
            raw_sorted = sorted(raw_detections, key=lambda d: d[1], reverse=True)
            detections: List[Tuple[Tuple[float, float, float, float], float, str]] = []
            for det in raw_sorted:
                if all(_iou(det[0], kept[0]) < 0.55 for kept in detections):
                    detections.append(det)
            h, w = frame.shape[:2]

            if state["p1"] is None:
                state["p1"], state["p2"] = _parse_line_cfg_live(line_cfg, w, h)

            p1 = state["p1"]
            p2 = state["p2"]
            tracks = state["tracks"]
            frame_idx = state["frame_idx"]
            state["frame_idx"] += 1
            tracks_before = len(tracks)

            recent_crossings = state["recent_crossings"]
            if crossing_dedupe_frames > 0:
                while recent_crossings and frame_idx - int(recent_crossings[0][0]) > crossing_dedupe_frames:
                    recent_crossings.popleft()

            centers = [
                (
                    (float(bx1) + float(bx2)) / 2.0,
                    float(by2) if use_bottom_center_for_tripwire else (float(by1) + float(by2)) / 2.0,
                )
                for (bx1, by1, bx2, by2), _, _ in detections
            ]

            candidates: List[Tuple[float, int, int]] = []
            for tid, tr in tracks.items():
                tcx, tcy = tr["center"]
                for det_idx, (dcx, dcy) in enumerate(centers):
                    dist = math.hypot(dcx - tcx, dcy - tcy)
                    if dist <= max_match_distance_px:
                        candidates.append((dist, tid, det_idx))
            candidates.sort(key=lambda x: x[0])

            matched_tracks: set = set()
            matched_dets:   set = set()
            assignments: List[Tuple[int, int]] = []
            for _dist, tid, det_idx in candidates:
                if tid in matched_tracks or det_idx in matched_dets:
                    continue
                matched_tracks.add(tid)
                matched_dets.add(det_idx)
                assignments.append((tid, det_idx))

            delta = 0
            crossing_now: list = []
            for tid, det_idx in assignments:
                cx, cy = centers[det_idx]
                side = _signed_side_live(cx, cy, p1, p2)
                zone = _side_zone(side)
                prev_zone = int(tracks[tid].get("zone", 0))
                phase = str(tracks[tid].get("phase", "before_band"))
                band_entry_zone = tracks[tid].get("band_entry_zone")
                in_band_frames = int(tracks[tid].get("in_band_frames", 0))
                crossed = False

                if zone == 0:
                    if prev_zone != 0:
                        phase = "in_band"
                        band_entry_zone = prev_zone
                        in_band_frames = 1
                    elif phase == "in_band":
                        in_band_frames += 1
                    else:
                        phase = "in_band"
                        in_band_frames = max(1, in_band_frames)
                else:
                    if phase == "in_band":
                        enough_band = in_band_frames >= min_band_frames
                        crossed = bool(
                            enough_band
                            and band_entry_zone in (-1, 1)
                            and int(band_entry_zone) != zone
                        )
                    elif allow_direct_flip_crossing and prev_zone in (-1, 1) and prev_zone != zone:
                        crossed = True

                    phase = "before_band"
                    band_entry_zone = zone
                    in_band_frames = 0

                if crossed and (allow_track_recount or not tracks[tid].get("counted", False)):
                    duplicate = False
                    if crossing_dedupe_frames > 0 and crossing_dedupe_radius_px > 0:
                        for ev_frame, ev_cx, ev_cy in recent_crossings:
                            if frame_idx - int(ev_frame) > crossing_dedupe_frames:
                                continue
                            if math.hypot(float(cx) - float(ev_cx), float(cy) - float(ev_cy)) <= crossing_dedupe_radius_px:
                                duplicate = True
                                break

                    if not duplicate:
                        delta += 1
                        state["crossing_count"] += 1
                        tracks[tid]["counted"] = True
                        crossing_now.append({"track_id": int(tid), "frame_idx": frame_idx})
                        recent_crossings.append((frame_idx, cx, cy))
                        logger.info(
                            f"[live-entry-exit:{ws_id}] crossing accepted stream={stream_label} "
                            f"track_id={tid} frame_idx={frame_idx} delta={delta} total={state['crossing_count']} " 
                            f"epoch = {current_epoch} centers = {cx}, {cy}"
                        )
                    else:
                        logger.debug(
                            f"[live-entry-exit:{ws_id}] crossing deduped stream={stream_label} "
                            f"track_id={tid} frame_idx={frame_idx} radius_px={crossing_dedupe_radius_px}"
                        )
                elif crossed:
                    logger.debug(
                        f"[live-entry-exit:{ws_id}] crossing suppressed by recount-guard "
                        f"stream={stream_label} track_id={tid} frame_idx={frame_idx}"
                    )

                tracks[tid].update({
                    "center": (cx, cy),
                    "side": side,
                    "zone": zone,
                    "phase": phase,
                    "band_entry_zone": band_entry_zone,
                    "in_band_frames": in_band_frames,
                    "last_seen": frame_idx,
                })

            for det_idx, (cx, cy) in enumerate(centers):
                if det_idx in matched_dets:
                    continue
                side = _signed_side_live(cx, cy, p1, p2)
                zone = _side_zone(side)
                next_id = state["next_id"]
                tracks[next_id] = {
                    "center": (cx, cy),
                    "side": side,
                    "zone": zone,
                    "phase": "in_band" if zone == 0 else "before_band",
                    "band_entry_zone": zone if zone in (-1, 1) else None,
                    "in_band_frames": 1 if zone == 0 else 0,
                    "last_seen": frame_idx,
                    "counted": False,
                }
                state["next_id"] += 1

            stale = [tid for tid, tr in tracks.items()
                     if frame_idx - int(tr.get("last_seen", frame_idx)) > max_stale_frames]
            for tid in stale:
                tracks.pop(tid, None)

            if delta > 0 or frame_idx % 30 == 0:
                logger.debug(
                    f"[live-entry-exit:{ws_id}] frame summary stream={stream_label} frame_idx={frame_idx} "
                    f"raw_det={len(raw_detections)} kept_det={len(detections)} assignments={len(assignments)} "
                    f"tracks_before={tracks_before} tracks_after={len(tracks)} stale_removed={len(stale)} "
                    f"delta={delta} total={state['crossing_count']}"
                )

            # Save verification frame (tripwire position) on first frame
            if state["out_dir"] and not state["saved_verification"]:
                # current_epoch = round(time.time() * 1000)
                _save_tripwire_verification(frame, state, stream_label, current_epoch)

            # Optional debug save on crossing frames
            if state["out_dir"] and crossing_now:
                dbg = frame.copy()
                cv.line(dbg,
                        (int(round(p1[0])), int(round(p1[1]))),
                        (int(round(p2[0])), int(round(p2[1]))),
                        (255, 255, 0), 2)
                for (bx1, by1, bx2, by2), conf, name in detections:
                    cv.rectangle(dbg,
                                 (int(round(max(0.0, bx1))), int(round(max(0.0, by1)))),
                                 (int(round(max(0.0, bx2))), int(round(max(0.0, by2)))),
                                 (0, 0, 255), 2)
                cv.putText(dbg, f"count={state['crossing_count']}", (10, 24),
                           cv.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv.LINE_AA)
                seq = state["save_seq"]
                state["save_seq"] += 1
                cv.imwrite(os.path.join(state["out_dir"], f"cross_{int(seq):06d}.jpg"), dbg)

            return delta

        def _grab_and_process(cap: cv.VideoCapture, state: dict, line_cfg: Any,
                              stream_url: str, stream_label: str) -> Tuple[bool, int]:
            """Read latest frame and run tracking. Returns (success, crossing_delta)."""
            if not cap.isOpened():
                logger.warning(f"[live-entry-exit:{ws_id}] {stream_label} capture closed; reopening")
                cap.open(stream_url)
                cap.set(cv.CAP_PROP_BUFFERSIZE, 1)
                if hasattr(cv, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
                    cap.set(cv.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
                if hasattr(cv, "CAP_PROP_READ_TIMEOUT_MSEC"):
                    cap.set(cv.CAP_PROP_READ_TIMEOUT_MSEC, 5000)

            # Keep only the freshest buffered frame to reduce missed fast crossings.
            try:
                for _ in range(2):
                    if not cap.grab():
                        break
            except Exception:
                pass

            ok, frame = cap.read()
            if not ok or frame is None:
                for attempt in range(10):
                    cap.release()
                    cap.open(stream_url)
                    cap.set(cv.CAP_PROP_BUFFERSIZE, 1)
                    if hasattr(cv, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
                        cap.set(cv.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
                    if hasattr(cv, "CAP_PROP_READ_TIMEOUT_MSEC"):
                        cap.set(cv.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
                    ok, frame = cap.read()
                    if ok and frame is not None:
                        logger.info(
                            f"[live-entry-exit:{ws_id}] {stream_label} stream recovered on retry attempt {attempt + 1}/10"
                        )
                        break
                if not ok or frame is None:
                    logger.warning(f"[live-entry-exit:{ws_id}] {stream_label} read failed after retries")
                    return False, 0
            current_epoch = round(time.time() * 1000)
            return True, _process_frame(frame, state, line_cfg, stream_label,current_epoch)

        entry_initialized = await asyncio.to_thread(
            _initialize_tripwire_from_stream,
            entry_cap,
            entry_state,
            entry_line_cfg,
            entry_url,
            "entry",
        )
        exit_initialized = await asyncio.to_thread(
            _initialize_tripwire_from_stream,
            exit_cap,
            exit_state,
            exit_line_cfg,
            exit_url,
            "exit",
        )

        if not entry_initialized:
            logger.warning(f"[live-entry-exit:{ws_id}] Entry tripwire could not initialize during startup")
            await websocket.send_json({
                "event": "warning",
                "stream": "entry",
                "message": "Could not initialize entry tripwire from stream yet",
            })
        if not exit_initialized:
            logger.warning(f"[live-entry-exit:{ws_id}] Exit tripwire could not initialize during startup")
            await websocket.send_json({
                "event": "warning",
                "stream": "exit",
                "message": "Could not initialize exit tripwire from stream yet",
            })

        # ── Notify client that we're live ─────────────────────────────
        await websocket.send_json({
            "event": "started",
            "entry_url": entry_url,
            "exit_url": exit_url,
            "model_path": model_path,
            "confidence_threshold": confidence_threshold,
            "interval_seconds": interval_seconds,
            "tick_seconds": tick_seconds,
            "min_band_frames": min_band_frames,
            "crossing_dedupe_seconds": crossing_dedupe_seconds,
            "crossing_dedupe_frames": crossing_dedupe_frames,
            "crossing_dedupe_radius_px": crossing_dedupe_radius_px,
            "use_bottom_center_for_tripwire": use_bottom_center_for_tripwire,
            "allow_direct_flip_crossing": allow_direct_flip_crossing,
            "save_output_dir": run_output_dir,
            "entry_tripwire": _tripwire_payload(entry_state),
            "exit_tripwire": _tripwire_payload(exit_state),
        })
        logger.info(
            f"[live-entry-exit:{ws_id}] started event sent: "
            f"entry_tripwire={_tripwire_payload(entry_state)}, exit_tripwire={_tripwire_payload(exit_state)}, "
            f"save_output_dir={run_output_dir}"
        )

        # ── Main loop ─────────────────────────────────────────────────
        seq = 0
        last_emit = _time.monotonic()

        while True:
            tick_start = _time.monotonic()

            # Process one frame from each stream (sequentially — simple and safe)
            entry_ok, _ = await asyncio.to_thread(
                _grab_and_process, entry_cap, entry_state, entry_line_cfg, entry_url, "entry",detector
            )
            exit_ok, _ = await asyncio.to_thread(
                _grab_and_process, exit_cap, exit_state, exit_line_cfg, exit_url, "exit",detector1
            )

            if not entry_ok:
                await websocket.send_json({"event": "warning", "stream": "entry",
                                           "message": "Could not read frame from entry stream"})
            if not exit_ok:
                await websocket.send_json({"event": "warning", "stream": "exit",
                                           "message": "Could not read frame from exit stream"})

            # Emit cumulative counts on schedule
            now = _time.monotonic()
            if now - last_emit >= interval_seconds:
                await websocket.send_json({
                    "event": "count",
                    "seq": seq,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "entered_count": int(entry_state["crossing_count"]),
                    "exited_count":  int(exit_state["crossing_count"]),
                    "confidence_threshold": confidence_threshold,
                    "save_output_dir": run_output_dir,
                    "entry_tripwire": _tripwire_payload(entry_state),
                    "exit_tripwire": _tripwire_payload(exit_state),
                })
                logger.info(
                    f"[live-entry-exit:{ws_id}] count emitted seq={seq} "
                    f"entered={int(entry_state['crossing_count'])} exited={int(exit_state['crossing_count'])} "
                    f"entry_ok={entry_ok} exit_ok={exit_ok}"
                )
                seq += 1
                last_emit = now

            # Sleep for remaining tick time
            elapsed = _time.monotonic() - tick_start
            sleep_time = max(0.0, tick_seconds - elapsed)
            await asyncio.sleep(sleep_time)

    except WebSocketDisconnect:
        logger.info(f"[live-entry-exit:{ws_id}] client disconnected")
    except Exception as e:
        logger.exception(f"[live-entry-exit:{ws_id}] WS error: {e}")
        try:
            await websocket.send_json({"event": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        try:
            if entry_cap is not None:
                entry_cap.release()
        except Exception:
            pass
        try:
            if exit_cap is not None:
                exit_cap.release()
        except Exception:
            pass
        logger.info(f"[live-entry-exit:{ws_id}] resources released")


@app.websocket("/api/ws/live-entry-exit-counts")
# @app.post("/api/ws/live-entry-exit-counts")
async def websocket_live_entry_exit_counts_api(websocket: WebSocket):
    """WebSocket alias for deployments that prefix routes with /api."""
    await websocket_live_entry_exit_counts_new(websocket,"video")
@app.websocket("/api/ws/recorded-entry-exit-counts")
async def websocket_live_entry_exit_counts_api(websocket: WebSocket):
    """WebSocket alias for deployments that prefix routes with /api."""
    await websocket_live_entry_exit_counts_new(websocket, "video")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app.main, host="0.0.0.0", port=8000)