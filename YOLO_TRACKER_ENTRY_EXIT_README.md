# YOLO Entry/Exit Tracker - Code Explanation

This README explains the script `yolotracker_entry_exit.py` in two ways:
1. Function-level purpose and behavior.
2. Line-by-line walkthrough (grouped by contiguous line ranges).

## What this script does

This script runs a live vehicle counting pipeline using two streams:
- Entry camera stream
- Exit camera stream

It uses YOLO tracking to detect vehicles inside configured ROIs (regions of interest), then:
- Increments `in_count` for new tracks seen on entry stream.
- Increments `out_count` for new tracks seen on exit stream.
- Saves evidence images for counted events.
- Sends live count updates to a WebSocket client.

## Runtime architecture

- Main async function: `websocket_live_entry_exit_counts_new(...)`
- Two worker processes: one for entry, one for exit, each running `process_stream_worker(...)`
- Shared state via `multiprocessing.Manager()`:
  - `in_count`
  - `out_count`
  - lock
- Result queues (`entry_queue`, `exit_queue`) report worker health/count snapshots.

## Function-by-function purpose

### 1) `process_stream_worker(...)`

Purpose:
- Runs in a separate process.
- Opens one video stream.
- Applies ROI masking.
- Runs YOLO tracking on selected frames.
- Prevents duplicate counting using track-id cache + minimum time interval.
- Updates shared entry/exit counters safely using a lock.
- Saves evidence frames when a new vehicle is counted.
- Reports status to parent process through queue.

Key behavior:
- Uses `OrderedDict` as bounded cache (`MAX_CACHE_LENGTH`) of already-counted track IDs.
- Counts only when:
  - Track ID is new (not in cache), and
  - Minimum interval since last count has elapsed.
- Only top-confidence detection is considered per processed frame.

### 2) `websocket_live_entry_exit_counts_new(websocket, stream_type)`

Purpose:
- Handles one WebSocket session.
- Accepts initial JSON config.
- Validates/normalizes stream inputs.
- Creates run output folders.
- Starts both worker processes.
- Periodically emits count updates to WebSocket client.
- Sends warning/error events on worker or validation failures.
- Terminates child processes on exit.

Key behavior:
- Uses default RTSP URLs and per-stream confidence thresholds when `USE_DEFAULTS=True`.
- Emits `count` event approximately every second.
- Includes evidence folder paths in each count event.

### 3) Local helper parsers inside websocket function

- `_parse_float(...)`: safe float parsing with optional clamping.
- `_parse_int(...)`: safe int parsing with optional lower bound.

Note: `_parse_int` is currently defined but not used.

## Event payloads sent to WebSocket client

- `started`: sent after worker processes start.
- `warning`: sent when a worker reports a frame-read/stream error.
- `count`: periodic cumulative counts with metadata.
- `error`: sent on fatal config/model/runtime exceptions.

## Line-by-line walkthrough (grouped by line ranges)

### Imports, setup, and constants

- Lines 1-7: Import libraries (OpenCV, Torch, JSON, async, multiprocessing, YOLO, datetime, etc.).
- Line 8: Forces multiprocessing start method to `spawn`.
- Lines 9-10: Configures logger and module logger.
- Lines 11-13: Computes project root and default output base directory.
- Line 14: Enables default runtime values when `True`.
- Lines 15-18: Default stream URLs and confidence thresholds.
- Lines 19-21: Model path and object class IDs.
- Line 22: Restricts detection to cars (`CAR_CLASS_ID`).
- Lines 23-29: Frame and runtime controls (resolution, skip-rate, cache size, labels).
- Line 30: Evidence subfolder name.
- Lines 31-35: Entry-side direction + ROI polygon variants.
- Lines 36-41: Exit-side direction + ROI polygon variants.

### `process_stream_worker(...)` detailed flow

- Lines 53-55: Function signature + docstring.
- Lines 56-61: Tries to open stream using `cv2.VideoCapture`; logs and raises on failure.
- Line 62: Initializes model variable placeholder.
- Lines 63-68: Loads YOLO model; logs and raises on failure.
- Line 69: Logs model load success.
- Lines 70-73: Builds ROI mask (`255` inside ROI, `0` outside).
- Lines 74-77: Initializes dedupe cache, last-count timestamp, frame index.
- Lines 78-79: Creates stream frame-save directory.
- Line 80: Infinite processing loop.
- Lines 81-84: If capture is closed, reports failure status and continues.
- Lines 86-89: Reads one frame and logs read timing/buffer metrics.
- Line 91: Reads source FPS.
- Line 92: Resizes frame to configured dimensions.
- Lines 93-95: Draws ROI polygon overlay on frame.
- Lines 96-98: Increments frame index, records current epoch, captures frame-start time.
- Lines 99-101: If frame invalid, reports failure and continues.
- Lines 102-103: Applies frame skipping using `SKIP_RATE`.
- Lines 104-106: Logs FPS and selects compute device (`cuda` or `cpu`).
- Line 107: Starts timing for mask+detect stage.
- Lines 108-111: Applies ROI mask and logs masking time.
- Lines 113-114: Runs YOLO tracking on masked frame with class filter and confidence threshold.
- Lines 115-116: Logs model detection time.
- Lines 117-118: Starts logic-processing timer, extracts boxes.
- Lines 119-122: Extracts tracked IDs, boxes, and confidences when IDs are available.
- Lines 123-125: Chooses only highest-confidence detection in frame.
- Lines 126-137: Draws rectangle and track-id text on annotated frame.
- Lines 139-145: Dedup + min-interval gate; updates bounded `seen_ids` cache.
- Lines 146-154: Entry path: increments `in_count` under lock, saves evidence frame, logs details.
- Lines 155-163: Exit path: increments `out_count` under lock, saves evidence frame, logs details.
- Lines 164-165: Logs logic processing time.
- Lines 166-167: Optionally saves all annotated frames.
- Lines 168-169: Pushes success status + cumulative counts to result queue.
- Lines 170-173: Logs updated counts and per-frame execution timing.
- Lines 174-177: On any worker exception, logs error, queues failure, brief sleep, then loop continues.

### `websocket_live_entry_exit_counts_new(...)` detailed flow

- Lines 179-185: Accepts WebSocket, creates short session ID, initializes process handles, logs start.
- Lines 186-194: Waits for first client message and parses JSON config; sends error and closes on invalid JSON.
- Lines 195-202: `_parse_float` helper for safe float parse + clamping.
- Lines 203-209: `_parse_int` helper for safe int parse + lower-bound clamp.
- Lines 211-214: Reads input URLs, global confidence threshold, and optional output directory.
- Lines 215-219: If defaults enabled, overrides URLs and uses per-stream confidence thresholds.
- Line 220: Logs effective stream inputs.
- Lines 221-230: Validates required URLs; sends validation errors and closes if missing.
- Lines 231-238: For RTSP mode, validates URL schemes.
- Lines 240-247: Verifies YOLO model file exists; sends fatal error and closes if missing.
- Lines 249-250: Converts entry/exit ROI coordinates to NumPy arrays.
- Lines 251-255: Logs final parsed config.
- Lines 256-264: Resolves output folder (provided path or autogenerated timestamped folder).
- Lines 265-269: Creates run, entry evidence, and exit evidence directories.
- Lines 271-277: Initializes manager-backed shared state and inter-process queues.
- Lines 279-288: Constructs entry and exit worker processes with stream-specific args.
- Lines 289-291: Starts worker processes.
- Lines 293-297: Sends `started` event to client.
- Lines 299-301: Initializes count sequence and emit timing controls.
- Lines 302-316: Main loop polling worker queues with async timeouts.
- Lines 317-322: Emits warning events if entry or exit worker reports failure.
- Lines 324-346: Every interval, reads shared counts under lock and emits `count` event.
- Lines 347-349: On exception, logs and sends `error` event.
- Lines 350-358: `finally` block terminates and joins worker processes; logs socket closure.

## Important implementation notes

- The code currently sets `USE_DEFAULTS=True`, which overrides client-provided URLs and per-stream confidence values.
- `DEFAULT_CONFIDENCE_THRESHOLD` and `_parse_int` are currently unused in effective processing logic.
- `tick_start` is assigned but not used.
- `frame = cv2.resize(...)` runs before `if not ok or frame is None`; if `cap.read()` fails and returns `frame=None`, this can raise before the guard.
- In model-load exception block, `err = "Model load error: {e}"` is a plain string and does not interpolate the exception.

## Suggested quick improvements

1. Move frame validity check before `cv2.resize(frame, ...)`.
2. Change model-load error to f-string: `f"Model load error: {e}"`.
3. Remove or use unused variables (`tick_start`, `_parse_int`, `DEFAULT_CONFIDENCE_THRESHOLD`).
4. Consider queue read strategy and backpressure handling for long-running sessions.
5. Add graceful stop signal support instead of only terminate/join.
