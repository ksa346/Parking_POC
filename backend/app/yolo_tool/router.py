"""YOLO Dataset Tool — all routes under /api/v1/yolo/"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime as _dt
from pathlib import Path

import cv2
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image as PILImage
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.yolo_tool.db import get_db
from app.yolo_tool.exporter import build_yolo_zip
from app.yolo_tool.importer import import_yolo_labels
from app.yolo_tool.models import YoloAnnotation, YoloClass, YoloImage, YoloImageGroup, YoloProject
from app.yolo_tool.schemas import (
    AnnotationCreate,
    AnnotationOut,
    AnnotationUpdate,
    BBox,
    ClassCreate,
    ClassOut,
    ClassUpdate,
    ExportConfig,
    GroupAssignImages,
    GroupCreate,
    GroupGridUpdate,
    GroupOut,
    ImageOut,
    ImageStatusUpdate,
    ProjectCreate,
    ProjectGridUpdate,
    ProjectOut,
    TrainConfig,
    TrainJobOut,
    AutoAnnotateConfig,
    AutoAnnotateResult,
)
from app.yolo_tool.storage import save_upload, upload_dir

router = APIRouter(prefix="/api/v1/yolo", tags=["yolo-dataset-tool"])

# ── Training docker config ────────────────────────────────────────────────────
_TRAIN_IMAGE = os.environ.get("TRAINING_IMAGE_NAME", "parking-yolo-train")
# Project root: backend/app/yolo_tool/ → parents[3] = parking_lot_dashboard/
_PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT_DIR", str(Path(__file__).resolve().parents[3])))
_TRAINING_BUILD_CTX = _PROJECT_ROOT / "training"
_RUNS_DIR = _PROJECT_ROOT / "training" / "runs"
_TRAIN_EXECUTOR = os.environ.get("TRAIN_EXECUTOR", "local").strip().lower()
_YOLO_TO_COCO_SCRIPT = _PROJECT_ROOT / "training" / "scripts" / "yolo_zip_to_coco.py"
_RFDETR_TRAIN_SCRIPT = _PROJECT_ROOT / "training" / "scripts" / "rfdetr_train.py"
_LEGACY_RUNS_PROJECT_ENV = os.environ.get("TRAIN_LEGACY_RUNS_PROJECT", "").strip()
# If there is no subprocess log output for this long, treat it as a stall.
_TRAIN_INACTIVITY_TIMEOUT_SEC = int(os.environ.get("TRAIN_INACTIVITY_TIMEOUT_SEC", "180"))
# CPU training can be legitimately quiet for longer spans between log flushes.
_TRAIN_INACTIVITY_TIMEOUT_CPU_SEC = int(
    os.environ.get("TRAIN_INACTIVITY_TIMEOUT_CPU_SEC", str(max(_TRAIN_INACTIVITY_TIMEOUT_SEC, 900)))
)
_WINDOWS_NATIVE_CRASH_CODES = {3221225477, -1073741819}

# In-memory job store (survives for the life of the process)
_train_jobs: dict[str, dict] = {}

_ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


# ── helpers ──────────────────────────────────────────────────────────────────

def _project_or_404(project_id: int, db: Session) -> YoloProject:
    p = db.query(YoloProject).filter(YoloProject.id == project_id).first()
    if not p:
        raise HTTPException(404, "Project not found")
    return p


def _enrich_project(p: YoloProject, db: Session) -> ProjectOut:
    img_count = db.query(func.count(YoloImage.id)).filter(YoloImage.project_id == p.id).scalar() or 0
    ann_count = (
        db.query(func.count(YoloAnnotation.id))
        .join(YoloImage, YoloImage.id == YoloAnnotation.image_id)
        .filter(YoloImage.project_id == p.id)
        .scalar() or 0
    )
    out = ProjectOut.model_validate(p)
    out.image_count = img_count
    out.annotation_count = ann_count
    return out


def _enrich_image(img: YoloImage, db: Session) -> ImageOut:
    import json
    count = db.query(func.count(YoloAnnotation.id)).filter(YoloAnnotation.image_id == img.id).scalar() or 0
    out = ImageOut.model_validate(img)
    out.annotation_count = count
    # Populate group grid overrides when the image belongs to a group with a custom grid
    if img.group_id and img.group is not None:
        g = img.group
        def _parse(text):
            try:
                return json.loads(text) if text else None
            except Exception:
                return None
        out.group_id = g.id
        out.group_grid_h_lines = _parse(g.grid_h_lines)
        out.group_grid_v_lines = _parse(g.grid_v_lines)
        out.group_grid_h_line_angles = _parse(g.grid_h_line_angles)
        out.group_grid_v_line_angles = _parse(g.grid_v_line_angles)
    return out


def _ann_to_out(ann: YoloAnnotation) -> AnnotationOut:
    bbox = None
    if ann.type == "bbox" and ann.cx is not None:
        bbox = BBox(cx=ann.cx, cy=ann.cy, bw=ann.bw, bh=ann.bh)
    polygon = ann.get_polygon() if ann.type == "polygon" else None
    return AnnotationOut(
        id=ann.id,
        image_id=ann.image_id,
        class_id=ann.class_id,
        class_name=ann.cls.name if ann.cls else "",
        class_color=ann.cls.color if ann.cls else "#3B82F6",
        type=ann.type,
        bbox=bbox,
        polygon=polygon,
        created_at=ann.created_at,
    )


def _next_class_index(project_id: int, db: Session) -> int:
    existing = db.query(YoloClass).filter(YoloClass.project_id == project_id).all()
    return max((c.class_index for c in existing), default=-1) + 1


# ── Projects ─────────────────────────────────────────────────────────────────

@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    return [_enrich_project(p, db) for p in db.query(YoloProject).order_by(YoloProject.created_at.desc()).all()]


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    p = YoloProject(
        name=payload.name,
        description=payload.description,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _enrich_project(p, db)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db)):
    return _enrich_project(_project_or_404(project_id, db), db)


@router.patch("/projects/{project_id}/grid", response_model=ProjectOut)
def save_project_grid(project_id: int, payload: ProjectGridUpdate, db: Session = Depends(get_db)):
    import json
    p = _project_or_404(project_id, db)
    p.grid_h_lines = json.dumps(payload.grid_h_lines)
    p.grid_v_lines = json.dumps(payload.grid_v_lines)
    p.grid_h_line_angles = json.dumps(payload.grid_h_line_angles)
    p.grid_v_line_angles = json.dumps(payload.grid_v_line_angles)
    db.commit()
    db.refresh(p)
    return _enrich_project(p, db)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    p = _project_or_404(project_id, db)
    db.delete(p)
    db.commit()


# ── Classes ───────────────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/classes", response_model=list[ClassOut])
def list_classes(project_id: int, db: Session = Depends(get_db)):
    _project_or_404(project_id, db)
    return db.query(YoloClass).filter(YoloClass.project_id == project_id).order_by(YoloClass.class_index).all()


@router.post("/projects/{project_id}/classes", response_model=ClassOut, status_code=201)
def create_class(project_id: int, payload: ClassCreate, db: Session = Depends(get_db)):
    _project_or_404(project_id, db)
    dup = db.query(YoloClass).filter(YoloClass.project_id == project_id, YoloClass.name == payload.name).first()
    if dup:
        raise HTTPException(409, f"Class '{payload.name}' already exists")
    idx = payload.class_index if payload.class_index is not None else _next_class_index(project_id, db)
    cls = YoloClass(project_id=project_id, name=payload.name, color=payload.color, class_index=idx)
    db.add(cls)
    db.commit()
    db.refresh(cls)
    return cls


@router.put("/classes/{class_id}", response_model=ClassOut)
def update_class(class_id: int, payload: ClassUpdate, db: Session = Depends(get_db)):
    cls = db.query(YoloClass).filter(YoloClass.id == class_id).first()
    if not cls:
        raise HTTPException(404, "Class not found")
    if payload.name is not None:
        cls.name = payload.name
    if payload.color is not None:
        cls.color = payload.color
    if payload.class_index is not None:
        cls.class_index = payload.class_index
    db.commit()
    db.refresh(cls)
    return cls


@router.delete("/classes/{class_id}", status_code=204)
def delete_class(class_id: int, db: Session = Depends(get_db)):
    cls = db.query(YoloClass).filter(YoloClass.id == class_id).first()
    if not cls:
        raise HTTPException(404, "Class not found")
    db.delete(cls)
    db.commit()


# ── Images ────────────────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/images", response_model=list[ImageOut])
def list_images(project_id: int, db: Session = Depends(get_db)):
    _project_or_404(project_id, db)
    return [_enrich_image(img, db) for img in db.query(YoloImage).filter(YoloImage.project_id == project_id).order_by(YoloImage.created_at).all()]


@router.post("/projects/{project_id}/images", response_model=list[ImageOut], status_code=201)
async def upload_images(project_id: int, files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    _project_or_404(project_id, db)
    udir = upload_dir(project_id)
    created = []
    for upload in files:
        ext = os.path.splitext(upload.filename or "")[1].lower()
        if ext not in _ALLOWED_IMG_EXT:
            raise HTTPException(400, f"Unsupported file type: {ext}")
        stored = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(udir, stored)
        await save_upload(upload, path)
        with PILImage.open(path) as pil:
            w, h = pil.size
        record = YoloImage(project_id=project_id, filename=stored, original_filename=upload.filename or stored, width=w, height=h)
        db.add(record)
        db.flush()
        created.append(record)
    db.commit()
    for r in created:
        db.refresh(r)
    return [_enrich_image(img, db) for img in created]


@router.post("/projects/{project_id}/extract-frames", response_model=list[ImageOut], status_code=201)
async def extract_frames(
    project_id: int,
    video: UploadFile = File(...),
    mode: str = Query("interval", pattern="^(interval|count)$"),
    value: float = Query(2.0, gt=0),
    db: Session = Depends(get_db),
):
    """Extract frames from a video and add them as images to the project.

    mode='interval': one frame every ``value`` seconds.
    mode='count':    ``value`` frames evenly distributed across the video.
    """
    _project_or_404(project_id, db)
    udir = upload_dir(project_id)

    suffix = os.path.splitext(video.filename or "upload.mp4")[1] or ".mp4"
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        # Stream video to disk
        with os.fdopen(tmp_fd, "wb") as f:
            while chunk := await video.read(1 << 20):  # 1 MB chunks
                f.write(chunk)

        cap = cv2.VideoCapture(tmp_path)
        if not cap.isOpened():
            raise HTTPException(400, "Could not open video file — unsupported format or corrupt file")

        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            raise HTTPException(400, "Video has no readable frames")

        if mode == "interval":
            frame_interval = max(1, round(fps * value))
            target_frames = list(range(0, total_frames, frame_interval))
        else:  # count
            count = max(1, min(int(value), total_frames))
            target_frames = [round(i * (total_frames - 1) / max(count - 1, 1)) for i in range(count)]

        base_name = os.path.splitext(os.path.basename(video.filename or "frame"))[0]
        created = []
        for frame_no in target_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
            ret, frame = cap.read()
            if not ret:
                continue
            stored = f"{uuid.uuid4().hex}.jpg"
            path = os.path.join(udir, stored)
            cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
            h, w = frame.shape[:2]
            orig_name = f"{base_name}_f{frame_no:06d}.jpg"
            record = YoloImage(
                project_id=project_id,
                filename=stored,
                original_filename=orig_name,
                width=w,
                height=h,
            )
            db.add(record)
            db.flush()
            created.append(record)

        cap.release()
        db.commit()
        for r in created:
            db.refresh(r)
        return [_enrich_image(img, db) for img in created]
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.get("/images/{image_id}/file")
def serve_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(YoloImage).filter(YoloImage.id == image_id).first()
    if not img:
        raise HTTPException(404, "Image not found")
    path = os.path.join(upload_dir(img.project_id), img.filename)
    if not os.path.isfile(path):
        raise HTTPException(404, "File not on disk")
    return FileResponse(path)


@router.patch("/images/{image_id}/status", response_model=ImageOut)
def update_status(image_id: int, payload: ImageStatusUpdate, db: Session = Depends(get_db)):
    img = db.query(YoloImage).filter(YoloImage.id == image_id).first()
    if not img:
        raise HTTPException(404, "Image not found")
    img.status = payload.status
    db.commit()
    db.refresh(img)
    return _enrich_image(img, db)


@router.delete("/images/{image_id}", status_code=204)
def delete_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(YoloImage).filter(YoloImage.id == image_id).first()
    if not img:
        raise HTTPException(404, "Image not found")
    path = os.path.join(upload_dir(img.project_id), img.filename)
    db.delete(img)
    db.commit()
    if os.path.isfile(path):
        os.remove(path)


@router.post("/images/{image_id}/import-labels")
async def import_labels(image_id: int, label_file: UploadFile = File(...), db: Session = Depends(get_db)):
    img = db.query(YoloImage).filter(YoloImage.id == image_id).first()
    if not img:
        raise HTTPException(404, "Image not found")
    content = (await label_file.read()).decode("utf-8")
    return import_yolo_labels(content, image_id, img.project_id, db)


# ── Annotations ───────────────────────────────────────────────────────────────

@router.get("/images/{image_id}/annotations", response_model=list[AnnotationOut])
def list_annotations(image_id: int, db: Session = Depends(get_db)):
    img = db.query(YoloImage).filter(YoloImage.id == image_id).first()
    if not img:
        raise HTTPException(404, "Image not found")
    return [_ann_to_out(a) for a in db.query(YoloAnnotation).filter(YoloAnnotation.image_id == image_id).all()]


@router.post("/images/{image_id}/annotations", response_model=AnnotationOut, status_code=201)
def create_annotation(image_id: int, payload: AnnotationCreate, db: Session = Depends(get_db)):
    img = db.query(YoloImage).filter(YoloImage.id == image_id).first()
    if not img:
        raise HTTPException(404, "Image not found")
    cls = db.query(YoloClass).filter(YoloClass.id == payload.class_id).first()
    if not cls or cls.project_id != img.project_id:
        raise HTTPException(400, "class_id does not belong to this project")

    if payload.type == "bbox":
        if not payload.bbox:
            raise HTTPException(422, "bbox required for type='bbox'")
        ann = YoloAnnotation(image_id=image_id, class_id=payload.class_id, type="bbox",
                              cx=payload.bbox.cx, cy=payload.bbox.cy, bw=payload.bbox.bw, bh=payload.bbox.bh)
    else:
        if not payload.polygon or len(payload.polygon) < 6:
            raise HTTPException(422, "polygon needs at least 3 points (6 values)")
        ann = YoloAnnotation(image_id=image_id, class_id=payload.class_id, type="polygon")
        ann.set_polygon(payload.polygon)

    db.add(ann)
    if img.status == "unannotated":
        img.status = "in_progress"
    db.commit()
    db.refresh(ann)
    return _ann_to_out(ann)


@router.put("/annotations/{ann_id}", response_model=AnnotationOut)
def update_annotation(ann_id: int, payload: AnnotationUpdate, db: Session = Depends(get_db)):
    ann = db.query(YoloAnnotation).filter(YoloAnnotation.id == ann_id).first()
    if not ann:
        raise HTTPException(404, "Annotation not found")
    if payload.class_id is not None:
        ann.class_id = payload.class_id
    if payload.bbox and ann.type == "bbox":
        ann.cx, ann.cy, ann.bw, ann.bh = payload.bbox.cx, payload.bbox.cy, payload.bbox.bw, payload.bbox.bh
    if payload.polygon and ann.type == "polygon":
        ann.set_polygon(payload.polygon)
    db.commit()
    db.refresh(ann)
    return _ann_to_out(ann)


@router.delete("/annotations/{ann_id}", status_code=204)
def delete_annotation(ann_id: int, db: Session = Depends(get_db)):
    ann = db.query(YoloAnnotation).filter(YoloAnnotation.id == ann_id).first()
    if not ann:
        raise HTTPException(404, "Annotation not found")
    db.delete(ann)
    db.commit()


@router.delete("/images/{image_id}/annotations", status_code=204)
def clear_annotations(image_id: int, db: Session = Depends(get_db)):
    img = db.query(YoloImage).filter(YoloImage.id == image_id).first()
    if not img:
        raise HTTPException(404, "Image not found")
    db.query(YoloAnnotation).filter(YoloAnnotation.image_id == image_id).delete()
    img.status = "unannotated"
    db.commit()


# ── Export ────────────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/export")
def export_project(project_id: int, cfg: ExportConfig, db: Session = Depends(get_db)):
    _project_or_404(project_id, db)
    try:
        buf = build_yolo_zip(project_id, cfg, db)
    except ValueError as e:
        raise HTTPException(404, str(e))
    project = db.query(YoloProject).filter(YoloProject.id == project_id).first()
    fname = f"{(project.name or 'project').replace(' ', '_')}_yolo_{cfg.fmt}.zip"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ── Training ──────────────────────────────────────────────────────────────────

def _extract_zip_to(buf: "io.BytesIO", dest_dir: str, strip_prefix: str) -> None:
    """Extract zip, stripping the project-name top-level folder."""
    with zipfile.ZipFile(buf) as zf:
        for member in zf.namelist():
            rel = member[len(strip_prefix):] if member.startswith(strip_prefix) else member
            if not rel:
                continue
            dest = os.path.join(dest_dir, rel)
            if member.endswith("/"):
                os.makedirs(dest, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as dst:
                    dst.write(src.read())


def _kill_process_tree(pid: int) -> None:
    """Best-effort kill of a process and all children (Windows-first)."""
    if pid <= 0:
        return

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            pass
        return

    # POSIX fallback
    try:
        proc = subprocess.Popen(["pkill", "-TERM", "-P", str(pid)])
        proc.wait(timeout=2)
    except Exception:
        pass
    try:
        os.kill(pid, 9)
    except Exception:
        pass


def _drain_process(proc: "subprocess.Popen[bytes]", job: dict) -> int:
    """Stream subprocess logs and return promptly when the main process exits.

    On Windows, multiprocessing child workers can inherit stdout handles and keep
    the pipe open even after the main trainer process exits. We read stdout on a
    background thread, wait for the process to finish, then close our stdout end
    to ensure the reader does not block indefinitely.
    """
    assert proc.stdout is not None
    stdout = proc.stdout

    lock = threading.Lock()
    state = {
        "last_output_ts": _dt.utcnow().timestamp(),
    }

    def _reader() -> None:
        try:
            for line_bytes in iter(stdout.readline, b""):
                line_str = line_bytes.decode("utf-8", errors="replace")
                job["logs"] += line_str
                with lock:
                    state["last_output_ts"] = _dt.utcnow().timestamp()
        except Exception:
            # Ignore reader teardown errors while shutting down.
            return

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    # Wait in small intervals so we can detect and terminate hung training jobs.
    while True:
        rc = proc.poll()
        if rc is not None:
            break

        with lock:
            idle_for = _dt.utcnow().timestamp() - state["last_output_ts"]

        if idle_for > _TRAIN_INACTIVITY_TIMEOUT_SEC:
            job["logs"] += (
                f"\n[watchdog] No training log output for {int(idle_for)}s; "
                "terminating stalled process tree.\n"
            )
            _kill_process_tree(proc.pid)
            rc = proc.wait(timeout=10)
            if rc == 0:
                rc = 1
            break

        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            continue

    try:
        stdout.close()
    except Exception:
        pass

    reader.join(timeout=2.0)
    return rc


def _run_process_with_logfile(
    cmd: list[str],
    job: dict,
    env: dict | None = None,
    start_new_session: bool = False,
    inactivity_timeout_sec: int | None = None,
) -> int:
    """Run a subprocess and stream its logfile into job['logs'].

    This avoids stdout pipe inheritance issues on Windows multiprocessing where
    child workers keep inherited pipe handles open and block completion.
    """
    fd, log_path = tempfile.mkstemp(prefix="train_job_", suffix=".log")
    os.close(fd)
    pos = 0
    timeout_sec = inactivity_timeout_sec or _TRAIN_INACTIVITY_TIMEOUT_SEC
    last_output_ts = _dt.utcnow().timestamp()

    try:
        with open(log_path, "ab") as log_fp:
            proc = subprocess.Popen(
                cmd,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                text=False,
                env=env,
                close_fds=True,
                start_new_session=start_new_session,
            )

        while True:
            try:
                with open(log_path, "rb") as log_in:
                    log_in.seek(pos)
                    chunk = log_in.read()
                if chunk:
                    job["logs"] += chunk.decode("utf-8", errors="replace")
                    pos += len(chunk)
                    last_output_ts = _dt.utcnow().timestamp()
            except Exception:
                pass

            rc = proc.poll()
            if rc is not None:
                # Final flush
                try:
                    with open(log_path, "rb") as log_in:
                        log_in.seek(pos)
                        chunk = log_in.read()
                    if chunk:
                        job["logs"] += chunk.decode("utf-8", errors="replace")
                except Exception:
                    pass
                return rc

            idle_for = _dt.utcnow().timestamp() - last_output_ts
            if idle_for > timeout_sec:
                job["logs"] += (
                    f"\n[watchdog] No training log output for {int(idle_for)}s; "
                    "terminating stalled process tree.\n"
                )
                _kill_process_tree(proc.pid)
                try:
                    rc = proc.wait(timeout=10)
                except Exception:
                    rc = 1
                return rc if rc != 0 else 1

            time.sleep(1)
    finally:
        try:
            os.remove(log_path)
        except Exception:
            pass


def _resolve_model_path_for_training(model_name: str) -> str:
    """Resolve model path from common project locations, fallback to model name."""
    if os.path.isabs(model_name) and os.path.exists(model_name):
        return model_name

    candidates = [
        str(_PROJECT_ROOT / model_name),
        str(_PROJECT_ROOT / "backend" / model_name),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return model_name


def _prepare_local_dataset_yaml(dataset_dir: str) -> str:
    """Create a local dataset yaml with host path instead of /workspace path."""
    src = os.path.join(dataset_dir, "dataset.yaml")
    dst = os.path.join(dataset_dir, "dataset.local.yaml")

    with open(src, "r", encoding="utf-8") as f:
        lines = f.readlines()

    dataset_norm = os.path.normpath(dataset_dir).replace("\\", "/")
    path_line = f'path: "{dataset_norm}"\n'

    replaced = False
    new_lines: list[str] = []
    for line in lines:
        if line.strip().startswith("path:"):
            new_lines.append(path_line)
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.insert(0, path_line)

    with open(dst, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    return dst


def _run_training_local(job: dict, dataset_dir: str, cfg: TrainConfig, runs_dir: str) -> None:
    """Run RF-DETR training locally via existing conversion + training scripts."""
    os.makedirs(runs_dir, exist_ok=True)
    run_project_name = job.get("output_project_name") or job["project_name"]
    run_output_dir = os.path.join(runs_dir, run_project_name, job["run_name"])
    os.makedirs(run_output_dir, exist_ok=True)

    if not _YOLO_TO_COCO_SCRIPT.is_file():
        raise RuntimeError(f"Missing converter script: {_YOLO_TO_COCO_SCRIPT}")
    if not _RFDETR_TRAIN_SCRIPT.is_file():
        raise RuntimeError(f"Missing RF-DETR train script: {_RFDETR_TRAIN_SCRIPT}")

    job["status"] = "running"
    job["logs"] += "Starting local RF-DETR training…\n"
    job["logs"] += f"Run output directory: {run_output_dir}\n"

    dataset_zip = os.path.join(dataset_dir, "dataset.zip")
    with zipfile.ZipFile(dataset_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(dataset_dir):
            for fname in files:
                if fname == "dataset.zip":
                    continue
                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, dataset_dir)
                zf.write(abs_path, rel_path)

    coco_dir = os.path.join(dataset_dir, "coco")
    convert_cmd = [
        sys.executable,
        str(_YOLO_TO_COCO_SCRIPT),
        "--zip",
        dataset_zip,
        "--output",
        coco_dir,
        "--splits",
        "train",
        "val",
        "test",
    ]
    job["logs"] += f"Converting annotations: {' '.join(convert_cmd)}\n"
    env = os.environ.copy()
    # RF-DETR callbacks print rich unicode tables; force UTF-8 so Windows cp1252
    # consoles/pipes do not crash with UnicodeEncodeError.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # Force immediate stdout/stderr flushing to avoid false watchdog stalls.
    env["PYTHONUNBUFFERED"] = "1"

    convert_rc = _run_process_with_logfile(
        convert_cmd,
        job,
        env=env,
        start_new_session=True,
    )
    job["logs"] += f"\n[runner] COCO conversion exited with code {convert_rc}.\n"
    if convert_rc != 0:
        job["status"] = "failed"
        job["logs"] += "\nFailed to convert dataset to COCO for RF-DETR."
        return

    model_size = (cfg.model_name or "base").lower().strip()
    if "nano" in model_size:
        model_size = "nano"
    elif "small" in model_size:
        model_size = "small"
    elif "medium" in model_size:
        model_size = "medium"
    elif "large" in model_size:
        model_size = "large"
    else:
        model_size = "base"

    work_dir = os.path.join(dataset_dir, "rfdetr_dataset")
    train_cmd = [
        sys.executable,
        str(_RFDETR_TRAIN_SCRIPT),
        "train",
        "--model",
        model_size,
        "--dataset",
        coco_dir,
        "--work-dir",
        work_dir,
        "--output-dir",
        run_output_dir,
        "--epochs",
        str(cfg.epochs),
        "--batch-size",
        str(cfg.batch_size),
        "--grad-accum-steps",
        "1",
        "--lr",
        str(cfg.lr0),
    ]
    if cfg.device:
        train_cmd.extend(["--device", str(cfg.device)])

    train_inactivity_timeout_sec = _TRAIN_INACTIVITY_TIMEOUT_SEC
    if str(cfg.device).strip().lower().startswith("cpu"):
        train_inactivity_timeout_sec = _TRAIN_INACTIVITY_TIMEOUT_CPU_SEC

    job["logs"] += (
        f"[watchdog] Training inactivity timeout set to "
        f"{int(train_inactivity_timeout_sec)}s for device={cfg.device or 'auto'}.\n"
    )
    job["logs"] += f"Launching RF-DETR training: {' '.join(train_cmd)}\n"
    train_rc = _run_process_with_logfile(
        train_cmd,
        job,
        env=env,
        start_new_session=True,
        inactivity_timeout_sec=train_inactivity_timeout_sec,
    )

    if train_rc in _WINDOWS_NATIVE_CRASH_CODES and str(cfg.device).lower().startswith("cpu"):
        fallback_lr = min(float(cfg.lr0), 0.001)
        retry_cmd = [
            sys.executable,
            str(_RFDETR_TRAIN_SCRIPT),
            "train",
            "--model",
            model_size,
            "--dataset",
            coco_dir,
            "--work-dir",
            work_dir,
            "--output-dir",
            run_output_dir,
            "--epochs",
            str(cfg.epochs),
            "--batch-size",
            "1",
            "--grad-accum-steps",
            "1",
            "--lr",
            str(fallback_lr),
            "--device",
            "cpu",
        ]
        job["logs"] += (
            "\n[runner] RF-DETR hit a Windows native crash (0xC0000005). "
            "Retrying once with conservative CPU settings (batch=1, grad_accum=1, lr<=0.001).\n"
        )
        job["logs"] += f"Retrying RF-DETR training: {' '.join(retry_cmd)}\n"
        retry_rc = _run_process_with_logfile(
            retry_cmd,
            job,
            env=env,
            start_new_session=True,
            inactivity_timeout_sec=train_inactivity_timeout_sec,
        )
        job["logs"] += f"\n[runner] RF-DETR retry exited with code {retry_rc}.\n"
        train_rc = retry_rc

    job["logs"] += f"\n[runner] RF-DETR training exited with code {train_rc}.\n"
    job["status"] = "completed" if train_rc == 0 else "failed"


def _run_training_docker(job: dict, dataset_dir: str, cfg: TrainConfig, runs_dir: str) -> None:
    """Run YOLO training in Docker container."""
    docker_bin = os.environ.get("DOCKER_BIN", "").strip()
    if not docker_bin:
        docker_bin = shutil.which("docker") or ""
    if not docker_bin:
        default_windows_docker = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
        if os.path.exists(default_windows_docker):
            docker_bin = default_windows_docker
    if not docker_bin:
        raise FileNotFoundError("docker")

    # Ensure daemon is reachable before trying inspect/build/run
    daemon_check = subprocess.run(
        [docker_bin, "info"],
        capture_output=True,
        text=True,
    )
    if daemon_check.returncode != 0:
        err = (daemon_check.stderr or daemon_check.stdout or "").strip()
        raise RuntimeError(
            "Docker daemon is not running or not reachable. "
            "Start Docker Desktop and retry.\n"
            f"Details: {err}"
        )

    # Build image if it doesn't exist locally
    check = subprocess.run(
        [docker_bin, "image", "inspect", _TRAIN_IMAGE],
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        job["status"] = "building"
        job["logs"] = "Building Docker image (this may take a few minutes)…\n"
        rc = _run_process_with_logfile(
            [docker_bin, "build", "-t", _TRAIN_IMAGE, str(_TRAINING_BUILD_CTX)],
            job,
            start_new_session=True,
        )
        job["logs"] += f"\n[runner] Docker image build exited with code {rc}.\n"
        if rc != 0:
            job["status"] = "failed"
            return

    job["status"] = "running"
    job["logs"] += "Starting training container…\n"
    os.makedirs(runs_dir, exist_ok=True)

    cmd = [
        docker_bin, "run", "--rm",
        "-v", f"{dataset_dir}:/workspace/dataset:ro",
        "-v", f"{runs_dir}:/workspace/runs",
        "-e", f"MODEL_NAME={cfg.model_name}",
        "-e", f"EPOCHS={cfg.epochs}",
        "-e", f"BATCH_SIZE={cfg.batch_size}",
        "-e", f"IMG_SIZE={cfg.img_size}",
        "-e", f"LR0={cfg.lr0}",
        "-e", f"PATIENCE={cfg.patience}",
        "-e", f"DEVICE={cfg.device}",
        "-e", f"PROJECT_NAME={job['project_name']}",
        "-e", f"RUN_NAME={job['run_name']}",
        "-e", f"FREEZE={cfg.freeze}",
        _TRAIN_IMAGE,
    ]

    rc = _run_process_with_logfile(
        cmd,
        job,
        start_new_session=True,
    )
    job["logs"] += f"\n[runner] Docker training exited with code {rc}.\n"
    job["status"] = "completed" if rc == 0 else "failed"


def _run_training_job(job_id: str, dataset_dir: str, cfg: TrainConfig, runs_dir: str) -> None:
    """Background thread: run RF-DETR training with local executor."""
    job = _train_jobs[job_id]
    try:
        _run_training_local(job, dataset_dir, cfg, runs_dir)
    except Exception as exc:
        job["status"] = "failed"
        job["logs"] += f"\n{exc}"
    finally:
        shutil.rmtree(dataset_dir, ignore_errors=True)


@router.post("/projects/{project_id}/train", response_model=TrainJobOut, status_code=202)
def start_training(project_id: int, cfg: TrainConfig, db: Session = Depends(get_db)):
    """Export ready images and launch a background training job (local or Docker)."""
    p = _project_or_404(project_id, db)

    export_cfg = ExportConfig(
        fmt=cfg.fmt,
        train_ratio=cfg.train_ratio,
        val_ratio=cfg.val_ratio,
        test_ratio=cfg.test_ratio,
        shuffle=cfg.shuffle,
    )
    try:
        buf = build_yolo_zip(project_id, export_cfg, db)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Extract zip into a temp directory that survives to the background thread
    tmp_dir = tempfile.mkdtemp(prefix="rfdetr_train_")
    dataset_dir = os.path.join(tmp_dir, "dataset")
    os.makedirs(dataset_dir, exist_ok=True)
    buf.seek(0)
    prefix = p.name.replace(" ", "_") + "/"
    _extract_zip_to(buf, dataset_dir, prefix)

    job_id = uuid.uuid4().hex
    run_name = cfg.run_name.strip() or f"run_{job_id[:8]}"
    project_name = p.name.replace(" ", "_")

    # Keep the same folder structure for every project: training/runs/<project>/<run>.
    # Optional override is available only when explicitly configured.
    output_project_name = project_name
    if _LEGACY_RUNS_PROJECT_ENV:
        output_project_name = _LEGACY_RUNS_PROJECT_ENV

    job: dict = {
        "job_id": job_id,
        "project_id": project_id,
        "project_name": project_name,
        "output_project_name": output_project_name,
        "status": "starting",
        "logs": "",
        "run_name": run_name,
        "created_at": _dt.utcnow().isoformat(),
    }
    _train_jobs[job_id] = job

    threading.Thread(
        target=_run_training_job,
        args=(job_id, dataset_dir, cfg, str(_RUNS_DIR)),
        daemon=True,
    ).start()

    return TrainJobOut(**job)


@router.get("/projects/{project_id}/train", response_model=list[TrainJobOut])
def list_training_jobs(project_id: int, db: Session = Depends(get_db)):
    """List all training jobs for a project (most recent first)."""
    _project_or_404(project_id, db)
    return [
        TrainJobOut(**j)
        for j in reversed(list(_train_jobs.values()))
        if j["project_id"] == project_id
    ]


@router.get("/projects/{project_id}/train/{job_id}", response_model=TrainJobOut)
def get_training_job(project_id: int, job_id: str):
    job = _train_jobs.get(job_id)
    if not job or job["project_id"] != project_id:
        raise HTTPException(404, "Training job not found")
    return TrainJobOut(**job)


# ── Auto-annotate ─────────────────────────────────────────────────────────────

_AERIAL_CLASSES = {
    "car", "truck", "bus", "motorcycle",
    "cell phone", "backpack", "suitcase", "bottle",
    "handbag", "laptop", "remote", "book",
    "keyboard", "mouse", "tv", "refrigerator",
    "skateboard", "surfboard",
}

# Map generic detector class names → typical project class names
_COCO_TO_VEHICLE = {
    "car": "car",
    "truck": "truck",
    "bus": "bus",
    "motorcycle": "motorcycle",
    # Aerial mis-classifications — map to "car"
    "cell phone": "car", "backpack": "car", "suitcase": "car",
    "bottle": "car",    "handbag": "car",  "laptop": "car",
    "remote": "car",    "book": "car",      "keyboard": "car",
    "mouse": "car",     "tv": "car",         "refrigerator": "car",
    "skateboard": "car", "surfboard": "car",
}


def _run_auto_annotate(
    project_id: int,
    cfg: AutoAnnotateConfig,
    db: Session,
) -> AutoAnnotateResult:
    """CPU-bound work — called via asyncio.to_thread so it doesn't block the event loop."""
    from app.services.detection_service import DetectionService

    # Resolve model path
    backend_dir = Path(__file__).resolve().parents[2]
    model_path = cfg.model_path
    if "/" in model_path or os.sep in model_path:
        candidate = (_PROJECT_ROOT / model_path).resolve()
        if candidate.is_file():
            model_path = str(candidate)
    elif not Path(model_path).is_absolute():
        candidate = backend_dir / model_path
        if candidate.is_file():
            model_path = str(candidate)

    ds = DetectionService(model_path=model_path, confidence=cfg.conf)
    if not ds.load_model():
        detail = ds.last_error or "unknown error"
        raise RuntimeError(f"RF-DETR model failed to load: {detail}")

    # Build class map: project_class_name.lower() → YoloClass
    project_classes = db.query(YoloClass).filter(YoloClass.project_id == project_id).all()
    class_by_name = {c.name.lower(): c for c in project_classes}

    # Fallback: use the class with lowest class_index (usually "car")
    fallback_cls = min(project_classes, key=lambda c: c.class_index) if project_classes else None
    if fallback_cls is None:
        raise RuntimeError("Project has no classes — add at least one class before auto-annotating")

    images = db.query(YoloImage).filter(YoloImage.project_id == project_id).all()
    udir = upload_dir(project_id)

    processed = 0
    created = 0
    skipped = 0
    errors = 0
    class_map: dict[str, str] = {}

    for img in images:
        # Skip already-annotated images unless overwrite=True
        existing = db.query(YoloAnnotation).filter(YoloAnnotation.image_id == img.id).first()
        if existing and not cfg.overwrite:
            skipped += 1
            continue

        img_path = os.path.join(udir, img.filename)
        if not os.path.isfile(img_path):
            errors += 1
            continue

        try:
            with PILImage.open(img_path) as pil:
                iw, ih = pil.size
                img_arr = cv2.cvtColor(cv2.imread(img_path), cv2.COLOR_BGR2RGB)
            if img_arr is None:
                errors += 1
                continue
            detections = ds._predict_detections(img_arr[:, :, ::-1])
        except Exception:
            errors += 1
            continue

        if cfg.overwrite and existing:
            db.query(YoloAnnotation).filter(YoloAnnotation.image_id == img.id).delete()
            db.flush()

        img_anns_created = 0
        for (bx1, by1, bx2, by2), _, coco_name in detections:
            if coco_name not in _AERIAL_CLASSES and coco_name not in {"vehicle", "cars", "car"}:
                continue

            bw_px = bx2 - bx1
            bh_px = by2 - by1
            area_px = bw_px * bh_px
            if area_px < 100 or min(bw_px, bh_px) < 8:
                continue
            aspect = max(bw_px, bh_px) / max(min(bw_px, bh_px), 1)
            if aspect > 6.0:
                continue

            mapped_name = _COCO_TO_VEHICLE.get(coco_name, "car")
            target_cls = class_by_name.get(mapped_name) or fallback_cls
            class_map[coco_name] = target_cls.name

            cx = (bx1 + bx2) / 2 / iw
            cy = (by1 + by2) / 2 / ih
            bw = bw_px / iw
            bh = bh_px / ih
            cx = max(0.0, min(1.0, cx))
            cy = max(0.0, min(1.0, cy))
            bw = max(0.001, min(1.0, bw))
            bh = max(0.001, min(1.0, bh))

            ann = YoloAnnotation(
                image_id=img.id,
                class_id=target_cls.id,
                type="bbox",
                cx=cx,
                cy=cy,
                bw=bw,
                bh=bh,
            )
            db.add(ann)
            img_anns_created += 1

        if img_anns_created > 0:
            img.status = "annotated"

        db.flush()
        created += img_anns_created
        processed += 1

    db.commit()
    return AutoAnnotateResult(
        processed=processed,
        created=created,
        skipped=skipped,
        errors=errors,
        class_map=class_map,
    )


@router.post("/projects/{project_id}/auto-annotate", response_model=AutoAnnotateResult)
async def auto_annotate_project(
    project_id: int,
    cfg: AutoAnnotateConfig,
    db: Session = Depends(get_db),
):
    """Run RF-DETR inference on all images and save bounding boxes as annotations.

    Images that already have annotations are skipped unless ``overwrite=true``.
    This gives you a human-review starting point — inspect each image in the
    annotator and delete/adjust the automatically-created boxes.
    """
    import asyncio
    _project_or_404(project_id, db)
    try:
        result = await asyncio.to_thread(_run_auto_annotate, project_id, cfg, db)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return result


# ── Image Groups ──────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/groups", response_model=GroupOut, status_code=201)
def create_group(
    project_id: int,
    body: GroupCreate,
    db: Session = Depends(get_db),
):
    """Create a named image batch group for a project."""
    _project_or_404(project_id, db)
    group = YoloImageGroup(
        project_id=project_id,
        name=body.name,
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return GroupOut.model_validate(group)


@router.get("/projects/{project_id}/groups", response_model=list[GroupOut])
def list_groups(
    project_id: int,
    db: Session = Depends(get_db),
):
    """Return all image groups for a project."""
    _project_or_404(project_id, db)
    groups = db.query(YoloImageGroup).filter(YoloImageGroup.project_id == project_id).all()
    return [GroupOut.model_validate(g) for g in groups]


@router.patch("/groups/{group_id}/grid", response_model=GroupOut)
def update_group_grid(
    group_id: int,
    body: GroupGridUpdate,
    db: Session = Depends(get_db),
):
    """Set (or replace) the grid configuration for a batch group."""
    import json
    group = db.query(YoloImageGroup).filter(YoloImageGroup.id == group_id).first()
    if not group:
        raise HTTPException(404, "Group not found")
    group.grid_h_lines = json.dumps(body.grid_h_lines)
    group.grid_v_lines = json.dumps(body.grid_v_lines)
    group.grid_h_line_angles = json.dumps(body.grid_h_line_angles or [0.0] * len(body.grid_h_lines))
    group.grid_v_line_angles = json.dumps(body.grid_v_line_angles or [0.0] * len(body.grid_v_lines))
    db.commit()
    db.refresh(group)
    return GroupOut.model_validate(group)


@router.post("/groups/{group_id}/assign-images", response_model=list[ImageOut])
def assign_images_to_group(
    group_id: int,
    body: GroupAssignImages,
    db: Session = Depends(get_db),
):
    """Assign a list of image IDs to a batch group."""
    group = db.query(YoloImageGroup).filter(YoloImageGroup.id == group_id).first()
    if not group:
        raise HTTPException(404, "Group not found")
    image_ids = [int(x) for x in dict.fromkeys(body.image_ids or [])]
    if not image_ids:
        raise HTTPException(400, "No image IDs provided for assignment")

    updated = db.query(YoloImage).filter(
        YoloImage.project_id == group.project_id,
        YoloImage.id.in_(image_ids),
    ).all()
    if not updated:
        raise HTTPException(400, "No matching project images found for assignment")

    for img in updated:
        img.group_id = group_id
    db.commit()

    updated = db.query(YoloImage).filter(
        YoloImage.project_id == group.project_id,
        YoloImage.id.in_(image_ids),
        YoloImage.group_id == group_id,
    ).all()
    return [_enrich_image(img, db) for img in updated]
