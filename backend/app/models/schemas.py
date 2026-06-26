"""Pydantic models for the parking API"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class ZoneOccupancy(BaseModel):
    """Occupancy data for a single parking zone"""
    zone_id: str
    occupied: int
    total: int
    available: int = Field(default=0)
    double_parked: int = 0

    def model_post_init(self, __context):
        object.__setattr__(self, "available", self.total - self.occupied)


class OccupancyResponse(BaseModel):
    """Current parking occupancy response"""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    total_spots: int
    occupied_spots: int
    available_spots: int
    occupancy_percent: float
    zones: List[ZoneOccupancy]
    confidence: str = "high"
    detection_method: str = "sam"
    sam_processing_ms: Optional[float] = None
    frame_source: str = "live"
    double_parked_count: int = 0


class HistoryEntry(BaseModel):
    """Historical occupancy entry"""
    timestamp: datetime
    occupied: int
    total: int
    occupancy_percent: float


class ForecastRequest(BaseModel):
    """Request for occupancy forecast"""
    target_hour: int = Field(ge=0, le=23)


class ForecastResponse(BaseModel):
    """Forecasted occupancy response"""
    target_hour: int
    predicted_occupancy: float
    confidence: str
    based_on_samples: int


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    version: str
    sam_loaded: bool
    stream_active: bool
    openai_configured: bool = False


class ChatMessage(BaseModel):
    """A single message in the conversation history"""
    role: str = Field(..., pattern=r'^(user|assistant)$')
    content: str


class ChatRequest(BaseModel):
    """Chat request from the Parking Assistant"""
    message: str = Field(..., min_length=1, max_length=2000)
    history: List[ChatMessage] = Field(default_factory=list, max_length=50)


class ChatResponse(BaseModel):
    """Chat response from GPT-5.2"""
    reply: str
    model: str = "gpt-5.2"
    tokens_used: Optional[int] = None


class ParkingContext(BaseModel):
    """Live parking context injected into GPT system prompt"""
    total_spots: int = 120
    occupied_spots: int = 0
    available_spots: int = 120
    occupancy_percent: float = 0.0
    zones: List[ZoneOccupancy] = []
    confidence: str = "unknown"
    detection_method: str = "unknown"
    peak_hour: Optional[int] = None
    today_average: Optional[float] = None
    last_updated: Optional[str] = None
    history_summary: Optional[str] = None
    forecast_summary: Optional[str] = None


class SAMDetectionResult(BaseModel):
    """Result from SAM vehicle detection"""
    vehicle_count: int
    bounding_boxes: List[tuple]
    confidence_scores: List[float]
    processing_time_ms: float
    frame_shape: Optional[tuple] = None


class SAMDetectionResult(BaseModel):
    """Result from SAM vehicle detection"""
    vehicle_count: int
    bounding_boxes: List[tuple]
    confidence_scores: List[float]
    processing_time_ms: float
    frame_shape: Optional[tuple] = None


# ── Developer Wizard schemas ─────────────────────────────────────────
class GridConfig(BaseModel):
    """Grid segmentation configuration from the developer wizard."""
    horizontal_lines: List[float] = Field(default_factory=list, description="Y-positions as fractions 0-1")
    vertical_lines: List[float] = Field(default_factory=list, description="X-positions as fractions 0-1")
    horizontal_line_angles: List[float] = Field(default_factory=list, description="Per-horizontal-line tilt angles")
    vertical_line_angles: List[float] = Field(default_factory=list, description="Per-vertical-line tilt angles")
    exclude_regions: List[List[List[float]]] = Field(
        default_factory=list,
        description="List of polygon regions to ignore, points as [x_frac, y_frac] in active region",
    )
    horizontal_angle_deg: float = Field(0.0, ge=-45.0, le=45.0, description="Tilt angle for horizontal lines")
    vertical_angle_deg: float = Field(0.0, ge=-45.0, le=45.0, description="Tilt angle for vertical lines")
    border: dict = Field(default_factory=lambda: {"top": 0, "right": 0, "bottom": 0, "left": 0},
                         description="Border inset percentages 0-100")


class ZoneSpotConfig(BaseModel):
    """Per-zone spot configuration after GPT estimation."""
    zone_id: str
    estimated_spots: int
    user_spots: int
    zone_image_base64: Optional[str] = None


class GPTEstimateRequest(BaseModel):
    """Request GPT to estimate spots from a frame + grid config."""
    grid_config: GridConfig
    frame_base64: Optional[str] = None


class GPTEstimateResponse(BaseModel):
    """GPT response with estimated spots per zone."""
    zones: List[ZoneSpotConfig]
    total_spots: int
    model: str = "gpt-5.2"


class TuneParametersRequest(BaseModel):
    """Fine-tuning hyperparameters from user input."""
    confidence_threshold: float = Field(0.15, ge=0.01, le=1.0)
    nms_iou_threshold: float = Field(0.4, ge=0.1, le=1.0)
    segment_overlap: float = Field(0.12, ge=0.0, le=0.5)
    min_vehicle_area: int = Field(800, ge=100)
    max_vehicle_area: int = Field(80000, ge=1000)
    user_prompt: str = Field("", max_length=1000)
    model_path: str = Field("base", max_length=500)


class PublishLocationRequest(BaseModel):
    """Publish a new parking location."""
    name: str = Field(..., min_length=1, max_length=200)
    google_maps_url: str = Field("", max_length=500)
    video_url: str = Field(..., min_length=1, max_length=500)
    grid_config: GridConfig
    zones: List[ZoneSpotConfig]
    total_spots: int
    parameters: TuneParametersRequest = Field(default_factory=TuneParametersRequest)


class PublishLocationResponse(BaseModel):
    """Response after publishing a location."""
    id: str
    name: str
    total_spots: int
    zone_count: int
    status: str = "published"
