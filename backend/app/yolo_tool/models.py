import json
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.yolo_tool.db import Base


class YoloProject(Base):
    __tablename__ = "yolo_projects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    # Detection segment grid — mirrors DetectionService SEGMENT_COLS/ROWS
    segment_cols = Column(Integer, default=2, nullable=False)
    segment_rows = Column(Integer, default=3, nullable=False)
    # Free-form grid lines (JSON arrays of normalized 0-1 fractions)
    # e.g. grid_h_lines = "[0.333, 0.667]", grid_v_lines = "[0.5]"
    grid_h_lines = Column(Text, default="[0.333, 0.667]")
    grid_v_lines = Column(Text, default="[0.5]")
    # Per-line tilt angles in degrees (small offsets around 0)
    grid_h_line_angles = Column(Text, default="[0.0, 0.0]")
    grid_v_line_angles = Column(Text, default="[0.0]")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    classes = relationship("YoloClass", back_populates="project", cascade="all, delete-orphan", order_by="YoloClass.class_index")
    images = relationship("YoloImage", back_populates="project", cascade="all, delete-orphan")
    groups = relationship("YoloImageGroup", back_populates="project", cascade="all, delete-orphan")


class YoloImageGroup(Base):
    """Per-upload-batch grid configuration.

    Each time a user uploads images or extracts video frames they can
    assign a custom grid to that batch.  Images without a group fall back
    to the project-level grid.
    """
    __tablename__ = "yolo_image_groups"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("yolo_projects.id"), nullable=False)
    name = Column(String(512), default="")  # e.g. video filename
    # Nullable — None means "use project grid"
    grid_h_lines = Column(Text, nullable=True)
    grid_v_lines = Column(Text, nullable=True)
    grid_h_line_angles = Column(Text, nullable=True)
    grid_v_line_angles = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    project = relationship("YoloProject", back_populates="groups")
    images = relationship("YoloImage", back_populates="group")


class YoloClass(Base):
    __tablename__ = "yolo_classes"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("yolo_projects.id"), nullable=False)
    name = Column(String(255), nullable=False)
    color = Column(String(20), default="#3B82F6")
    class_index = Column(Integer, nullable=False)
    project = relationship("YoloProject", back_populates="classes")
    annotations = relationship("YoloAnnotation", back_populates="cls")


class YoloImage(Base):
    __tablename__ = "yolo_images"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("yolo_projects.id"), nullable=False)
    group_id = Column(Integer, ForeignKey("yolo_image_groups.id"), nullable=True)
    filename = Column(String(512), nullable=False)
    original_filename = Column(String(512), nullable=False)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    # unannotated | in_progress | annotated | reviewed | approved
    status = Column(String(50), default="unannotated")
    created_at = Column(DateTime, default=datetime.utcnow)
    project = relationship("YoloProject", back_populates="images")
    group = relationship("YoloImageGroup", back_populates="images")
    annotations = relationship("YoloAnnotation", back_populates="image", cascade="all, delete-orphan")


class YoloAnnotation(Base):
    __tablename__ = "yolo_annotations"
    id = Column(Integer, primary_key=True, index=True)
    image_id = Column(Integer, ForeignKey("yolo_images.id"), nullable=False)
    class_id = Column(Integer, ForeignKey("yolo_classes.id"), nullable=False)
    # "bbox" | "polygon"
    type = Column(String(20), nullable=False, default="bbox")
    # Normalized 0–1 (YOLO centre_x, centre_y, width, height)
    cx = Column(Float, nullable=True)
    cy = Column(Float, nullable=True)
    bw = Column(Float, nullable=True)
    bh = Column(Float, nullable=True)
    # Polygon: JSON flat list [x1,y1,x2,y2,...] normalized 0–1
    polygon_points = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    image = relationship("YoloImage", back_populates="annotations")
    cls = relationship("YoloClass", back_populates="annotations")

    def get_polygon(self) -> list:
        return json.loads(self.polygon_points) if self.polygon_points else []

    def set_polygon(self, pts: list) -> None:
        self.polygon_points = json.dumps(pts)
