from __future__ import annotations
from datetime import datetime
import json
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str
    grid_h_lines: list[float] = [0.333, 0.667]
    grid_v_lines: list[float] = [0.5]
    grid_h_line_angles: list[float] = [0.0, 0.0]
    grid_v_line_angles: list[float] = [0.0]
    created_at: datetime
    updated_at: datetime
    image_count: int = 0
    annotation_count: int = 0
    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def _parse_grid_lines(cls, data):
        def _parse_lines(value, default):
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    return parsed if isinstance(parsed, list) else default
                except Exception:
                    return default
            if value is None:
                return default
            return value if isinstance(value, list) else default

        def _parse_angles(value):
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    return parsed if isinstance(parsed, list) else []
                except Exception:
                    return []
            if value is None:
                return []
            return value if isinstance(value, list) else []

        if hasattr(data, "__dict__"):
            # SQLAlchemy ORM object — parse JSON strings
            h = getattr(data, "grid_h_lines", None)
            v = getattr(data, "grid_v_lines", None)
            ha = getattr(data, "grid_h_line_angles", None)
            va = getattr(data, "grid_v_line_angles", None)
            parsed_h = _parse_lines(h, [0.333, 0.667])
            parsed_v = _parse_lines(v, [0.5])
            parsed_ha = _parse_angles(ha)
            parsed_va = _parse_angles(va)
            if len(parsed_ha) < len(parsed_h):
                parsed_ha = parsed_ha + [0.0] * (len(parsed_h) - len(parsed_ha))
            if len(parsed_va) < len(parsed_v):
                parsed_va = parsed_va + [0.0] * (len(parsed_v) - len(parsed_va))
            data.__dict__["grid_h_lines"] = parsed_h
            data.__dict__["grid_v_lines"] = parsed_v
            data.__dict__["grid_h_line_angles"] = [float(x) for x in parsed_ha[:len(parsed_h)]]
            data.__dict__["grid_v_line_angles"] = [float(x) for x in parsed_va[:len(parsed_v)]]
        elif isinstance(data, dict):
            parsed_h = _parse_lines(data.get("grid_h_lines"), [0.333, 0.667])
            parsed_v = _parse_lines(data.get("grid_v_lines"), [0.5])
            parsed_ha = _parse_angles(data.get("grid_h_line_angles"))
            parsed_va = _parse_angles(data.get("grid_v_line_angles"))
            if len(parsed_ha) < len(parsed_h):
                parsed_ha = parsed_ha + [0.0] * (len(parsed_h) - len(parsed_ha))
            if len(parsed_va) < len(parsed_v):
                parsed_va = parsed_va + [0.0] * (len(parsed_v) - len(parsed_va))
            data["grid_h_lines"] = parsed_h
            data["grid_v_lines"] = parsed_v
            data["grid_h_line_angles"] = [float(x) for x in parsed_ha[:len(parsed_h)]]
            data["grid_v_line_angles"] = [float(x) for x in parsed_va[:len(parsed_v)]]
        return data


class ProjectGridUpdate(BaseModel):
    grid_h_lines: list[float] = Field(default_factory=list)
    grid_v_lines: list[float] = Field(default_factory=list)
    grid_h_line_angles: list[float] = Field(default_factory=list)
    grid_v_line_angles: list[float] = Field(default_factory=list)

    @field_validator("grid_h_lines", "grid_v_lines")
    @classmethod
    def _valid_fracs(cls, v: list) -> list:
        for x in v:
            if not (0.0 < x < 1.0):
                raise ValueError("All line positions must be strictly between 0 and 1")
        return sorted(v)

    @field_validator("grid_h_line_angles", "grid_v_line_angles")
    @classmethod
    def _valid_angles(cls, v: list) -> list:
        out = []
        for x in v:
            val = float(x)
            if val < -30.0 or val > 30.0:
                raise ValueError("All line angles must be between -30 and 30 degrees")
            out.append(val)
        return out

    @model_validator(mode="after")
    def _align_angle_lengths(self):
        if len(self.grid_h_line_angles) < len(self.grid_h_lines):
            self.grid_h_line_angles = self.grid_h_line_angles + [0.0] * (len(self.grid_h_lines) - len(self.grid_h_line_angles))
        if len(self.grid_v_line_angles) < len(self.grid_v_lines):
            self.grid_v_line_angles = self.grid_v_line_angles + [0.0] * (len(self.grid_v_lines) - len(self.grid_v_line_angles))
        if len(self.grid_h_line_angles) > len(self.grid_h_lines):
            self.grid_h_line_angles = self.grid_h_line_angles[:len(self.grid_h_lines)]
        if len(self.grid_v_line_angles) > len(self.grid_v_lines):
            self.grid_v_line_angles = self.grid_v_line_angles[:len(self.grid_v_lines)]
        return self


class ClassCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    color: str = "#3B82F6"
    class_index: Optional[int] = None

    @field_validator("color")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not (v.startswith("#") and len(v) in (4, 7)):
            raise ValueError("color must be a hex string like '#3B82F6'")
        return v


class ClassUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    color: Optional[str] = None
    class_index: Optional[int] = None


class ClassOut(BaseModel):
    id: int
    project_id: int
    name: str
    color: str
    class_index: int
    model_config = {"from_attributes": True}


class ImageOut(BaseModel):
    id: int
    project_id: int
    group_id: Optional[int] = None
    # Group-level grid overrides (None = fall back to project grid)
    group_grid_h_lines: Optional[list[float]] = None
    group_grid_v_lines: Optional[list[float]] = None
    group_grid_h_line_angles: Optional[list[float]] = None
    group_grid_v_line_angles: Optional[list[float]] = None
    filename: str
    original_filename: str
    width: int
    height: int
    status: str
    created_at: datetime
    annotation_count: int = 0
    model_config = {"from_attributes": True}


class GroupCreate(BaseModel):
    name: str = ""


class GroupOut(BaseModel):
    id: int
    project_id: int
    name: str
    grid_h_lines: Optional[list[float]] = None
    grid_v_lines: Optional[list[float]] = None
    grid_h_line_angles: Optional[list[float]] = None
    grid_v_line_angles: Optional[list[float]] = None
    created_at: datetime
    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def _parse_group_grid(cls, data):
        def _parse(value):
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    return parsed if isinstance(parsed, list) else None
                except Exception:
                    return None
            return value if isinstance(value, list) else None

        def _as_floats(values):
            if values is None:
                return None
            return [float(x) for x in values]

        if hasattr(data, "__dict__"):
            h = _parse(getattr(data, "grid_h_lines", None))
            v = _parse(getattr(data, "grid_v_lines", None))
            ha = _parse(getattr(data, "grid_h_line_angles", None))
            va = _parse(getattr(data, "grid_v_line_angles", None))
            if ha is not None and h is not None:
                if len(ha) < len(h):
                    ha = ha + [0.0] * (len(h) - len(ha))
                else:
                    ha = ha[:len(h)]
            if va is not None and v is not None:
                if len(va) < len(v):
                    va = va + [0.0] * (len(v) - len(va))
                else:
                    va = va[:len(v)]
            data.__dict__["grid_h_lines"] = _as_floats(h)
            data.__dict__["grid_v_lines"] = _as_floats(v)
            data.__dict__["grid_h_line_angles"] = _as_floats(ha)
            data.__dict__["grid_v_line_angles"] = _as_floats(va)
        elif isinstance(data, dict):
            h = _parse(data.get("grid_h_lines"))
            v = _parse(data.get("grid_v_lines"))
            ha = _parse(data.get("grid_h_line_angles"))
            va = _parse(data.get("grid_v_line_angles"))
            if ha is not None and h is not None:
                if len(ha) < len(h):
                    ha = ha + [0.0] * (len(h) - len(ha))
                else:
                    ha = ha[:len(h)]
            if va is not None and v is not None:
                if len(va) < len(v):
                    va = va + [0.0] * (len(v) - len(va))
                else:
                    va = va[:len(v)]
            data["grid_h_lines"] = _as_floats(h)
            data["grid_v_lines"] = _as_floats(v)
            data["grid_h_line_angles"] = _as_floats(ha)
            data["grid_v_line_angles"] = _as_floats(va)
        return data


class GroupGridUpdate(BaseModel):
    grid_h_lines: list[float]
    grid_v_lines: list[float]
    grid_h_line_angles: list[float] = []
    grid_v_line_angles: list[float] = []


class GroupAssignImages(BaseModel):
    image_ids: list[int]


class ImageStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def _valid(cls, v: str) -> str:
        allowed = {"unannotated", "in_progress", "annotated", "reviewed", "approved"}
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v


class BBox(BaseModel):
    cx: float = Field(..., ge=0.0, le=1.0)  # YOLO normalised centre_x
    cy: float = Field(..., ge=0.0, le=1.0)
    bw: float = Field(..., gt=0.0, le=1.0)
    bh: float = Field(..., gt=0.0, le=1.0)


class AnnotationCreate(BaseModel):
    class_id: int
    type: str = "bbox"
    bbox: Optional[BBox] = None
    polygon: Optional[list[float]] = None

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        if v not in ("bbox", "polygon"):
            raise ValueError("type must be 'bbox' or 'polygon'")
        return v


class AnnotationUpdate(BaseModel):
    class_id: Optional[int] = None
    bbox: Optional[BBox] = None
    polygon: Optional[list[float]] = None


class AnnotationOut(BaseModel):
    id: int
    image_id: int
    class_id: int
    class_name: str = ""
    class_color: str = "#3B82F6"
    type: str
    bbox: Optional[BBox] = None
    polygon: Optional[list[float]] = None
    created_at: datetime
    model_config = {"from_attributes": True}


class ExportConfig(BaseModel):
    fmt: str = "detection"        # "detection" | "segmentation"
    train_ratio: float = Field(0.7, ge=0.0, le=1.0)
    val_ratio: float = Field(0.2, ge=0.0, le=1.0)
    test_ratio: float = Field(0.1, ge=0.0, le=1.0)
    shuffle: bool = True

    @field_validator("test_ratio")
    @classmethod
    def _sum_one(cls, v: float, info) -> float:
        train = info.data.get("train_ratio", 0)
        val = info.data.get("val_ratio", 0)
        total = round(train + val + v, 6)
        if abs(total - 1.0) > 1e-5:
            raise ValueError(f"train+val+test must equal 1.0, got {total}")
        return v


class TrainConfig(BaseModel):
    """Hyperparameters for automated docker training."""
    model_name: str = "base"
    epochs: int = Field(50, ge=1, le=1000)
    batch_size: int = Field(4, ge=1, le=512)
    img_size: int = Field(640, ge=32, le=4096)
    lr0: float = Field(0.0001, ge=0.0001, le=1.0)
    patience: int = Field(10, ge=0, le=300)
    freeze: int = Field(0, ge=0, le=24, description="Freeze first N backbone layers (0=off, 10=head-only, 21=full backbone)")
    device: str = "cpu"
    run_name: str = "run1"
    # Export settings (same as ExportConfig, embedded so one call does both)
    fmt: str = "detection"
    train_ratio: float = Field(0.7, ge=0.0, le=1.0)
    val_ratio: float = Field(0.2, ge=0.0, le=1.0)
    test_ratio: float = Field(0.1, ge=0.0, le=1.0)
    shuffle: bool = True


class TrainJobOut(BaseModel):
    job_id: str
    project_id: int
    project_name: str
    status: str   # starting | building | running | completed | failed
    logs: str = ""
    run_name: str = ""
    created_at: str


class AutoAnnotateConfig(BaseModel):
    conf: float = Field(0.25, ge=0.01, le=1.0)
    model_path: str = "base"
    overwrite: bool = False  # if True, delete existing annotations before adding


class AutoAnnotateResult(BaseModel):
    processed: int
    created: int
    skipped: int
    errors: int
    class_map: dict[str, str] = {}  # detected_class_name → project_class_name
