# Backend Quick Handoff (Parking_POC)

This is a rapid handoff for the entire backend codebase under backend/app.
Use this to understand ownership, runtime flow, key APIs, and where to change behavior safely.

## 1) Backend At a Glance

Primary backend entrypoint:
- [backend/app/main.py](backend/app/main.py)

Core architecture:
1. FastAPI app with lifecycle startup/shutdown and CORS.
2. Background occupancy loop for parking analytics.
3. HTTP + WebSocket endpoints for live and recorded counting.
4. Separate YOLO dataset/training tool mounted via router.
5. Service layer for stream capture, detection, parking aggregation, chat, and wizard preview.

## 2) Directory Ownership Map

### API orchestration
- [backend/app/main.py](backend/app/main.py): route wiring, lifespan initialization, global runtime state, WebSocket handlers, and bridge logic into services.

### Detection/counting helpers
- [backend/app/video_process_count.py](backend/app/video_process_count.py): YOLO frame-level counting, region filtering, per-video sampling.
- [backend/app/run_job.py](backend/app/run_job.py): batch wrapper for multi-video processing.
- [backend/app/yolotracker_entry_exit.py](backend/app/yolotracker_entry_exit.py): multiprocessing entry/exit tracking pipeline.

### Service layer
- [backend/app/services/detection_service.py](backend/app/services/detection_service.py): RF-DETR/YOLO model loading + inference + filtering.
- [backend/app/services/parking_service.py](backend/app/services/parking_service.py): occupancy orchestration, zone assignment, DB writes/reads.
- [backend/app/services/stream_service.py](backend/app/services/stream_service.py): frame capture from local video and stream swapping.
- [backend/app/services/chat_service.py](backend/app/services/chat_service.py): GPT/fallback parking assistant responses.
- [backend/app/services/wizard_service.py](backend/app/services/wizard_service.py): isolated preview/tuning path (does not mutate live pipeline by default).

### Schemas
- [backend/app/models/schemas.py](backend/app/models/schemas.py): Pydantic request/response contracts for occupancy, chat, tuning, publishing.

### YOLO dataset/training subsystem
- [backend/app/yolo_tool/router.py](backend/app/yolo_tool/router.py): routes under /api/v1/yolo for dataset CRUD, annotations, export, train jobs, auto-annotate.
- [backend/app/yolo_tool/models.py](backend/app/yolo_tool/models.py): SQLAlchemy tables for projects/images/classes/annotations/groups.
- [backend/app/yolo_tool/schemas.py](backend/app/yolo_tool/schemas.py): dataset-tool request/response models.
- [backend/app/yolo_tool/db.py](backend/app/yolo_tool/db.py): database session and engine setup.
- [backend/app/yolo_tool/storage.py](backend/app/yolo_tool/storage.py): upload path and file saving helpers.
- [backend/app/yolo_tool/importer.py](backend/app/yolo_tool/importer.py): YOLO txt label import.
- [backend/app/yolo_tool/exporter.py](backend/app/yolo_tool/exporter.py): YOLO zip export builder.
- [backend/app/yolo_tool/__init__.py](backend/app/yolo_tool/__init__.py): DB init + lightweight migrations.

## 3) Startup and Runtime Lifecycle

Main lifecycle functions:
- [_resolve_startup_media_path](backend/app/main.py#L99)
- [lifespan](backend/app/main.py#L176)
- [detection_loop](backend/app/main.py#L232)
- [broadcast_occupancy](backend/app/main.py#L244)

Startup sequence:
1. App boots and runs lifespan startup.
2. YOLO tool DB initialized.
3. StreamService is created with resolved startup media.
4. ParkingService is created and detection model is loaded.
5. Default pipeline registered in in-memory pool.
6. ChatService initialized.
7. Background detection loop starts if stream is active.

Shutdown sequence:
1. Release all stream resources in pipeline pool.
2. Clear in-memory pool state.

## 4) Main API Surface (What To Hit)

### Core API in main.py

1. Upload/swap input media:
- [upload_video](backend/app/main.py#L410)
- [upload_and_apply_video](backend/app/main.py#L463)
- [set_stream_url](backend/app/main.py#L511)

2. Recorded counting:
- [get_video_counts](backend/app/main.py#L806)
- [get_entry_exit_counts](backend/app/main.py#L897)

3. Spot estimation:
- [estimate_spots](backend/app/main.py#L1463)

4. Live counting:
- [websocket_live_counts](backend/app/main.py#L2391)
- [live_counts](backend/app/main.py#L2572)

5. Stream relay:
- [stream_mjpeg](backend/app/main.py#L2643)

6. Entry/exit live websockets:
- [websocket_live_entry_exit_counts](backend/app/main.py#L2781)
- [websocket_live_entry_exit_counts_api](backend/app/main.py#L3485)

Compatibility middleware:
- [compat_v1_rewrite](backend/app/main.py#L282) rewrites /v1/* to /api/v1/*.

### YOLO dataset tool routes

Router root:
- [backend/app/yolo_tool/router.py](backend/app/yolo_tool/router.py) with prefix /api/v1/yolo

Route groups:
1. Projects/classes/images CRUD.
2. Annotation CRUD + import labels.
3. Frame extraction from video.
4. Export project as YOLO zip.
5. Start/list/get training jobs.
6. Auto-annotate project images.
7. Group creation, grid overrides, and image assignment.

## 5) Critical Runtime Flows

### A) Background occupancy pipeline

Path:
1. [detection_loop](backend/app/main.py#L232)
2. [ParkingService.run_detection](backend/app/services/parking_service.py)
3. [DetectionService.detect_vehicles](backend/app/services/detection_service.py)
4. Zone assignment + double-park logic in ParkingService
5. Persist occupancy history
6. [broadcast_occupancy](backend/app/main.py#L244) to connected WebSocket clients

### B) Recorded video counting

Path:
1. [get_video_counts](backend/app/main.py#L806)
2. single mode -> VideoProcessCount.process_video
3. batch mode -> [run_job](backend/app/run_job.py#L8) -> thread pool -> process_video per video
4. optional annotated frame output

### C) Live spot detection snapshot API

Path:
1. [live_counts](backend/app/main.py#L2572)
2. reads one frame per stream source
3. build_regions + _count_frame from video_process_count
4. returns count, region_counts, and frame path

### D) Live continuous WebSocket count stream

Path:
1. client connects to [websocket_live_counts](backend/app/main.py#L2391)
2. sends config JSON
3. backend loads detector, loops frame reads, emits periodic count events

### E) Entry/exit tracking

Path:
1. WebSocket handlers in main.py
2. delegates heavy stream tracking to [backend/app/yolotracker_entry_exit.py](backend/app/yolotracker_entry_exit.py)
3. multiprocessing workers run YOLO tracking and direction-based counting

## 6) State and Storage

In-memory process state:
1. Connected WebSocket clients list in main.py.
2. Pipeline pool for default and per-location pipelines.
3. Dev model selection and detector caches.
4. YOLO training in-memory job store in yolo_tool router.

Persistent stores:
1. Parking history and published locations via ParkingService DB backend.
2. YOLO tool SQLite/SQLAlchemy tables for dataset projects and annotations.
3. Uploaded/generated media under data/upload-like folders and spot_detection_output.

## 7) Configuration and Environment

Common runtime settings used by backend modules:
1. VIDEO_PATH: startup media path resolver input.
2. UPDATE_INTERVAL_SECONDS: detection loop interval.
3. PARKING_TOTAL_SPOTS and zone-specific env vars.
4. CORS_ORIGINS for frontend access.
5. DB_BACKEND and DB connection env vars for parking data.
6. OPENAI_API_KEY and OPENAI_MODEL for chat service.
7. RFDETR_MODEL_PATH and model path inputs for detection.
8. YOLO training env knobs in yolo_tool router (training image, executor, timeouts).

## 8) What Is Safe To Change vs Risky

Usually safe:
1. UI-facing response formatting.
2. Threshold defaults in endpoint payload handling.
3. Additional logs/metrics.
4. New routes that do not mutate shared global state.

Higher risk:
1. Global variables and shared service instances in main.py.
2. DetectionService filtering and geometry thresholds.
3. ParkingService zone mapping math and DB writes.
4. WebSocket loops and reconnect logic.
5. yolo_tool training subprocess management and job lifecycle.

## 9) Known Implementation Notes

1. Some legacy/commented routes still exist in main.py; active developer and live routes are the primary interface.
2. In live_counts path, processor thresholds are reassigned per stream iteration.
3. Model loading and inference behavior differs between DetectionService and VideoProcessCount paths.
4. chat_service docstring mentions GPT-5.2, while default model env fallback in code points to gpt-4.1 when not overridden.
5. models/schemas.py contains duplicated SAMDetectionResult class definition.

## 10) First-Day Handoff Checklist

1. Run backend locally and verify app startup path in [backend/app/main.py](backend/app/main.py).
2. Test these routes first:
- /api/v1/developer/video-counts
- /live-counts
- /api/v1/stream/mjpeg
3. Confirm DB backend mode and write/read path in ParkingService.
4. Validate one YOLO tool flow end-to-end:
- create project -> upload image -> annotate -> export.
5. Validate one training job start/list/get flow in yolo_tool.
6. Validate one WebSocket live count flow.

## 11) Fast File Lookup

If you only remember one file per concern:
1. App behavior and route wiring -> [backend/app/main.py](backend/app/main.py)
2. Detection internals -> [backend/app/services/detection_service.py](backend/app/services/detection_service.py)
3. Occupancy and storage logic -> [backend/app/services/parking_service.py](backend/app/services/parking_service.py)
4. Recorded video count endpoint behavior -> [backend/app/video_process_count.py](backend/app/video_process_count.py)
5. Dataset and training APIs -> [backend/app/yolo_tool/router.py](backend/app/yolo_tool/router.py)
