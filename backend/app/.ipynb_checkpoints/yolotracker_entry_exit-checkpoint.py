import cv2, torch, json, os, time, uuid, asyncio, logging
import multiprocessing as mp
import numpy as np
from multiprocessing import Queue, Manager
from ultralytics import YOLO
from collections import OrderedDict
from datetime import datetime
mp.set_start_method("spawn", force=True)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
# ── Constants ────────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
ENTRY_EXIT_IMAGES_DIR = os.path.join(_PROJECT_ROOT, "blazor-frontend", "data", "entry_exit_images")
# ENTRY_EXIT_IMAGES_DIR = "/mnt/batch/tasks/shared/LS_root/mounts/clusters/ci-divc223/code/Users/divc223/ui_changes/demo_ui/NC-Parking-Lot-POC/blazor-frontend/data/entry_exit_images"
USE_DEFAULTS = True
DEFAULT_ENTRY_URL = "rtsp://ukcamviz:qUoTuRNytxe6EH@10.29.18.175/0/profile2/media.smp"
DEFAULT_EXIT_URL = "rtsp://ukcamviz:qUoTuRNytxe6EH@10.29.18.171/axis-media/media.amp"
# DEFAULT_ENTRY_URL = "rtsp://10.182.55.21:8554/entry"
# DEFAULT_EXIT_URL = "rtsp://10.182.55.21:8554/exit"
ENTRY_MIN_COUNT_INTERVAL_MS = 1000
EXIT_MIN_COUNT_INTERVAL_MS = 2000
DEFAULT_CONFIDENCE_THRESHOLD = 0.6
DEFAULT_ENTRY_CONFIDENCE_THRESHOLD = 0.6
DEFAULT_EXIT_CONFIDENCE_THRESHOLD = 0.4
MODEL_PATH = "yolov8m.pt"
CAR_CLASS_ID = 2
BUS_CLASS_ID = 6
TRUCK_CLASS_ID = 8
DETECTION_CLASSES = [CAR_CLASS_ID]
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
SAVE_ANNOTATED_FRAMES = False
SKIP_RATE = 2
MAX_CACHE_LENGTH = 20
ENTRY_STRING = "entry"
EXIT_STRING = "exit"
RTSP_STRING = "rtsp"
VIDEO_STRING = "video"
EVIDENCE_DIR = 'evidence'
# ── Camera-specific counting regions and directions ─────────────────
# Entry camera (camera_id: 1) - any new track detected inside the region = entered
ENTRY_DIRECTION = "in"  # Allowed values: "in" or "out"
# ENTRY_REGION = [(156, 162), (0, 443), (635, 464), (527, 164)]
ENTRY_REGION = [(193, 137), (491, 137), (635, 401), (8, 387)]
# ENTRY_REGION = [(196, 138), (0, 443), (635, 464), (494, 138)]
# Exit camera (camera_id: 2) - any new track detected inside the region = exited
EXIT_DIRECTION = "out"  # Allowed values: "in" or "out"
# EXIT_REGION = [(8, 472), (217, 88), (389, 91), (616, 475)]
# EXIT_REGION = [(8, 472), (195, 127), (409, 126), (616, 475)]
EXIT_REGION = [(194, 129), (407, 127), (595, 444), (45, 431)] 
#EXIT_REGION = [(177, 162), (426, 162), (595, 444), (45, 431)]
 
def process_stream_worker(url, model_path, confidence_threshold, run_output_dir, ws_id, result_queue, shared_state,
                          stream_name, roi_polygon, direction, evidence_save_dir, min_count_interval_ms):
    """Worker process for reading and processing video stream"""
    try:
        cap = cv2.VideoCapture(url)
    except Exception as e:
        err = f"Failed to open url: {url}. Error: {e}"
        logger.info(err)
        raise err
    yolo_model = None
    try:
        yolo_model = YOLO(model_path)
    except  Exception as e:
        err = "Model load error: {e}"
        logger.info(err)
        raise err
    logger.info(f"Model loaded")
    # Build a black-out mask once: 255 inside the region, 0 outside.
    region_mask = np.zeros((FRAME_HEIGHT, FRAME_WIDTH), dtype=np.uint8)
    if roi_polygon is not None:
        cv2.fillPoly(region_mask, [roi_polygon], 255)
    # seen_ids = set()
    seen_ids = OrderedDict()
    last_count_epoch = 0
    idx = 0
    frame_save_dir = os.path.join(run_output_dir, stream_name)
    os.makedirs(frame_save_dir, exist_ok=True)
    while True:
        try:
            if not cap.isOpened():
                result_queue.put((stream_name, False, None, None))
                continue

            frame_read_start_time = time.time()
            ok, frame = cap.read()
            logger.info(f"Buffer size: {cap.get(cv2.CAP_PROP_BUFFERSIZE)}, "
                        f"Frame read time: {((time.time() - frame_read_start_time) * 1000):.1f},{datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]}")     
            # ok, frame = cap.read()
            source_fps = cap.get(cv2.CAP_PROP_FPS)
            frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))
            # Draw ROI polygon
            if roi_polygon is not None:
                cv2.polylines(frame, [roi_polygon], isClosed=True, color=(0, 255, 255), thickness=2)
            idx += 1
            current_epoch = round(time.time() * 1000)
            frame_start_time = time.time()
            if not ok or frame is None:
                result_queue.put((stream_name, False, None, None))
                continue
            if idx % SKIP_RATE != 0:  # Skip frames until nth frame
                continue
            logger.info(f"f.fps frame capture {source_fps},job_id:{ws_id}")  
            device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(f"Loading model on device: {device}")
            start_time = time.time()
            # Black out everything outside the region before detection
            masked_frame = cv2.bitwise_and(frame, frame, mask=region_mask)
            logger.info(f"Masked frame time for stream: {stream_name}, idx: {idx}, epoch {current_epoch}: "
                        f"{((time.time() - start_time) * 1000):.1f} ms")
            # cv2.imwrite(os.path.join(frame_save_dir, f"masked_frame_{current_epoch}_{idx}.jpg"), masked_frame)
            results = yolo_model.track(masked_frame, classes=DETECTION_CLASSES,
                                       conf=confidence_threshold, persist=True, device=device)
            logger.info(f"Model detection time for stream: {stream_name}, idx: {idx}, epoch {current_epoch}: "
                        f"{((time.time() - start_time) * 1000):.1f} ms")
            processing_start_time = time.time()
            boxes = results[0].boxes
            if boxes is not None and boxes.id is not None:
                ids = boxes.id.int().tolist()
                xyxys = boxes.xyxy.cpu().numpy()
                confs = boxes.conf.cpu().numpy()
                best_idx = int(confs.argmax())
                ids = [ids[best_idx]]
                xyxys = xyxys[best_idx:best_idx+1]
                for track_id, box in zip(ids, xyxys):
                    x1, y1, x2, y2 = box
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                    cv2.putText(
                        frame,
                        f"ID: {track_id}",
                        (int(x1), int(y1) - 10),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (255, 0, 0),
                        2,
                    )
                    
                    if track_id not in seen_ids and (current_epoch - last_count_epoch) >= min_count_interval_ms:
                        last_count_epoch = current_epoch
                        # seen_ids.add(track_id)
                        seen_ids[track_id] = None
                        if len(seen_ids) > MAX_CACHE_LENGTH:
                            seen_ids.popitem(last=False)
                        logger.info(f"Current seen ids for frame {idx}: {seen_ids}")
                        if direction == ENTRY_DIRECTION:
                            with shared_state['lock']:
                                shared_state['in_count'] += 1
                                in_count = shared_state['in_count']
                            file_save_start_time = time.time()
                            cv2.imwrite(os.path.join(evidence_save_dir, f"{stream_name}_{current_epoch}_{idx}.jpg"), frame)
                            logger.info(f"[{stream_name}] Car {track_id} is new. Bbox: {box}. "
                                        f"Epoch: {current_epoch}. Total in: {in_count}."
                                        f" File save time taken: {((time.time() - file_save_start_time) * 1000):.1f} ms")
                        else:
                            with shared_state['lock']:
                                shared_state['out_count'] += 1
                                out_count = shared_state['out_count']
                            file_save_start_time = time.time()
                            cv2.imwrite(os.path.join(evidence_save_dir, f"{stream_name}_{current_epoch}_{idx}.jpg"), frame)
                            logger.info(f"[{stream_name}] Car {track_id} is new. Bbox: {box}. "
                                        f"Epoch: {current_epoch}. Total out: {out_count}. "
                                        f"File save time taken: {((time.time() - file_save_start_time) * 1000):.1f} ms")
            logger.info(f"Logic processing time for stream: {stream_name}, idx: {idx}, epoch {current_epoch}: "
                        f" Logic processing time: {((time.time() - processing_start_time) * 1000):.1f} ms")
            if SAVE_ANNOTATED_FRAMES:
                cv2.imwrite(os.path.join(frame_save_dir, f"frame_{current_epoch}_{idx}.jpg"), frame)
            with shared_state['lock']:
                result_queue.put((stream_name, True, shared_state['in_count'], shared_state['out_count']))
            logger.info(f"[{stream_name}] counts after frame {idx}: in_count - {shared_state['in_count']}, "
                        f"out_count - {shared_state['out_count']}")
            logger.info(f"Frame {idx} execution time for stream: {stream_name}, epoch {current_epoch}: "
                        f"{((time.time() - frame_start_time) * 1000):.1f} ms,{datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]}")
        except Exception as e:
            logger.info(f"[{stream_name}]. [{ws_id}] Worker error: {e}")
            result_queue.put((stream_name, False, None, None))
            time.sleep(0.1)
 
async def websocket_live_entry_exit_counts_new(websocket, stream_type):
    await websocket.accept()
    ws_id = uuid.uuid4().hex[:8]
    entry_process = None
    exit_process = None
    logger.info(f"[live-entry-exit:{ws_id}] WebSocket accepted")
    logger.info(f"Stream_type: {stream_type}.")
    try:
        raw = await websocket.receive_text()
        try:
            cfg = json.loads(raw)
        except Exception as e:
            logger.warning(f"[live-entry-exit:{ws_id}] Invalid first payload: {e}")
            await websocket.send_json({"event": "error", "message": "First message must be a valid JSON config"})
            await websocket.close(code=1003)
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
        # ── Parse required fields ─────────────────────────────────────
        entry_url = str(cfg.get("entry_url") or "").strip()
        exit_url = str(cfg.get("exit_url") or "").strip()
        confidence_threshold = _parse_float("confidence_threshold", 0.5, 0.0, 1.0)
        save_output_dir = str(cfg.get("save_output_dir") or "").strip()
        if USE_DEFAULTS:
            entry_url = DEFAULT_ENTRY_URL
            exit_url = DEFAULT_EXIT_URL
            entry_confidence_threshold = DEFAULT_ENTRY_CONFIDENCE_THRESHOLD
            exit_confidence_threshold = DEFAULT_EXIT_CONFIDENCE_THRESHOLD
        logger.info(f"Stream_type: {stream_type}. Received URLs - Entry: {entry_url}, Exit: {exit_url}")
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
        if str(stream_type).lower() == RTSP_STRING:
            for url_val, label in ((entry_url, "entry_url"), (exit_url, "exit_url")):
                if not url_val.startswith(("rtsp://", "http://", "https://")):
                    logger.warning(f"[live-entry-exit:{ws_id}] Invalid URL scheme for {label}: {url_val}")
                    await websocket.send_json(
                        {"event": "error", "message": f"{label} must start with rtsp://, http:// or https://"})
                    await websocket.close(code=1003)
                    return
        # ── Parse optional fields ─────────────────────────────────────
        if not os.path.isfile(MODEL_PATH):
            logger.error(f"[live-entry-exit:{ws_id}] YOLO model missing: {MODEL_PATH}")
            await websocket.send_json({
                "event": "error",
                "message": f"YOLO model not found: {MODEL_PATH}",
            })
            await websocket.close(code=1011)
            return
        # Build ROI polygons as numpy arrays (required by cv2.fillPoly for masking)
        entry_roi_polygon = np.array(ENTRY_REGION, dtype=np.int32)
        exit_roi_polygon = np.array(EXIT_REGION, dtype=np.int32)
        logger.info(
            f"[live-entry-exit:{ws_id}] Config parsed: "
            f"entry_url={entry_url}, exit_url={exit_url}"
            f"confidence={confidence_threshold}, output_dir={save_output_dir}"
        )
        run_output_dir = (
            os.path.normpath(save_output_dir)
            if save_output_dir
            else os.path.join(
                ENTRY_EXIT_IMAGES_DIR,
                stream_type,
                f"{stream_type}_{ws_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            )
        )
        os.makedirs(run_output_dir, exist_ok=True)
        entry_evidence_dir = os.path.join(run_output_dir, EVIDENCE_DIR, ENTRY_STRING)
        os.makedirs(entry_evidence_dir, exist_ok=True)
        exit_evidence_dir = os.path.join(run_output_dir, EVIDENCE_DIR, EXIT_STRING)
        os.makedirs(exit_evidence_dir, exist_ok=True)
        # ── Setup multiprocessing ─────────────────────────────────────
        manager = Manager()
        shared_state = manager.dict()
        shared_state['in_count'] = 0
        shared_state['out_count'] = 0
        shared_state['lock'] = manager.Lock()
        entry_queue = Queue()
        exit_queue = Queue()
        # Start worker processes
        entry_process = mp.Process(
            target=process_stream_worker,
            args=(entry_url, MODEL_PATH, entry_confidence_threshold, run_output_dir, ws_id, entry_queue, shared_state,
                  ENTRY_STRING, entry_roi_polygon, ENTRY_DIRECTION, entry_evidence_dir,ENTRY_MIN_COUNT_INTERVAL_MS)
        )
        exit_process = mp.Process(
            target=process_stream_worker,
            args=(exit_url, MODEL_PATH, exit_confidence_threshold, run_output_dir, ws_id, exit_queue, shared_state,
                  EXIT_STRING, exit_roi_polygon, EXIT_DIRECTION, exit_evidence_dir,EXIT_MIN_COUNT_INTERVAL_MS)
        )
        logger.info(f"Starting processes")
        entry_process.start()
        exit_process.start()
        # ── Notify client that we're live ─────────────────────────────
        await websocket.send_json({
            "event": "started",
            "confidence_threshold": confidence_threshold
        })
        logger.info(f"[live-entry-exit:{ws_id}] start event sent")
        # ── Main loop ─────────────────────────────────────────────────
        seq = 0
        last_emit = time.monotonic()
        interval_seconds = 1.0  # Define interval
        while True:
            tick_start = time.monotonic()
            # Get results from processes (non-blocking with timeout)
            entry_ok = True
            exit_ok = True
            try:
                entry_result = await asyncio.wait_for(asyncio.to_thread(entry_queue.get, timeout=0.1), timeout=0.2)
                stream_name, entry_ok, entry_in, entry_out = entry_result
            except:
                pass
            try:
                exit_result = await asyncio.wait_for(asyncio.to_thread(exit_queue.get, timeout=0.1), timeout=0.2)
                stream_name, exit_ok, exit_in, exit_out = exit_result
            except:
                pass
            if not entry_ok:
                await websocket.send_json({"event": "warning", "stream": ENTRY_STRING,
                                           "message": "Some error occurred. Could not read frame from entry stream"})
            if not exit_ok:
                await websocket.send_json({"event": "warning", "stream": EXIT_STRING,
                                           "message": "Some error occurred. Could not read frame from exit stream"})
            # Emit cumulative counts on schedule
            now = time.monotonic()
            if now - last_emit >= interval_seconds:
                with shared_state['lock']:
                    in_count = shared_state['in_count']
                    out_count = shared_state['out_count']
                await websocket.send_json({
                    "event": "count",
                    "seq": seq,
                    "timestamp": datetime.now().isoformat() + "Z",
                    "entered_count": int(in_count),
                    "exited_count": int(out_count),
                    "confidence_threshold": confidence_threshold,
                    "save_output_dir": run_output_dir,
                    "entry_evidence_dir": entry_evidence_dir,
                    "exit_evidence_dir": exit_evidence_dir,
                })
                logger.info(
                    f"[live-entry-exit:{ws_id}] count emitted seq={seq} "
                    f"entered={int(in_count)} exited={int(out_count)} "
                    f"entry_ok={entry_ok} exit_ok={exit_ok}"
                )
                seq += 1
                last_emit = now
    except Exception as e:
        logger.error(f"[live-entry-exit:{ws_id}] Error: {e}")
        await websocket.send_json({"event": "error", "message": str(e)})
    finally:
        # Terminate worker processes
        if entry_process and entry_process.is_alive():
            entry_process.terminate()
            entry_process.join(timeout=2)
        if exit_process and exit_process.is_alive():
            exit_process.terminate()
            exit_process.join(timeout=2)
        logger.info(f"[live-entry-exit:{ws_id}] WebSocket closed")