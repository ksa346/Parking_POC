"""
Parking Service — main business-logic orchestrator.

Detection results flow from local video → YOLO 6-segment → zone assignment → PostgreSQL.
When no frame is available the service reports occupancy as *unknown*
with confidence="no_data" instead of faking numbers.
"""
import asyncio
import logging
import math
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import psycopg2
from psycopg2 import sql as pgsql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from app.models.schemas import (
    ForecastResponse,
    HistoryEntry,
    OccupancyResponse,
    ParkingContext,
    ZoneOccupancy,
)
from app.services.detection_service import DetectionService
from app.services.stream_service import StreamService

logger = logging.getLogger(__name__)

try:
    import cv2
    CV_AVAILABLE = True
except ImportError:
    CV_AVAILABLE = False

# Double-parking / oversized vehicle detection thresholds
OVERSIZED_AREA_FACTOR = 1.8     # bbox area > 1.8x median → likely double-parked
OVERSIZED_CLASS_FACTOR = 1.4    # lower threshold for known large vehicles (truck/bus)
OVERSIZED_CLASSES = {"truck", "bus"}
BOUNDARY_SPAN_RATIO = 0.35      # extends > 35% of vehicle dim into adjacent zone
MIN_DETECTIONS_FOR_MEDIAN = 5   # need ≥5 vehicles to compute reliable median


class ParkingService:
    """Orchestrates video capture -> YOLO detection -> zone mapping -> storage."""

    def __init__(
        self,
        stream_service: StreamService,
        total_spots: int = 474,
        zone_config: Optional[Dict[str, int]] = None,
    ):
        self.stream_service = stream_service
        self.total_spots = total_spots
        self.zone_config = zone_config or {
            "TL": 96, "TR": 94,
            "ML": 88, "MR": 90,
            "BL": 52, "BR": 54,
        }

        self.detection_service = DetectionService()
        self.detection_loaded = False
        self._executor = ThreadPoolExecutor(max_workers=1)

        # Zone grid for spatial bbox→zone assignment.
        # Default matches the 2-col × 3-row TL/TR/ML/MR/BL/BR layout.
        # _activate_location_internal overwrites this for custom locations.
        self._zone_grid: List[List[str]] = [
            ["TL", "TR"],
            ["ML", "MR"],
            ["BL", "BR"],
        ]
        self._grid_h_lines: List[float] = [1.0 / 3.0, 2.0 / 3.0]
        self._grid_v_lines: List[float] = [0.5]
        self._grid_h_line_angles: List[float] = [0.0, 0.0]
        self._grid_v_line_angles: List[float] = [0.0]
        self._grid_border: Dict[str, float] = {"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0}
        self._grid_exclude_regions: List[List[List[float]]] = []
        self._grid_h_angle_deg: float = 0.0
        self._grid_v_angle_deg: float = 0.0

        # Current cached state
        self.current_occupied = 0
        self.zone_occupancy: Dict[str, int] = {z: 0 for z in self.zone_config}
        self._last_bboxes: list = []
        self._last_scores: list = []
        self._last_class_names: list = []
        self._last_processing_ms: Optional[float] = None
        self._last_frame_source: str = "none"
        self._last_detection_method: str = "none"
        self._last_confidence: str = "no_data"
        self._has_data = False
        self._double_parked_count: int = 0
        self._double_parked_zones: Dict[str, int] = {z: 0 for z in self.zone_config}
        self._double_parked_indices: set = set()

        # Database selector
        self._db_backend = os.getenv("DB_BACKEND", "sqlite").lower()
        if self._db_backend not in ("postgres", "sqlite"):
            logger.warning(f"Invalid DB_BACKEND '{self._db_backend}'; defaulting to sqlite")
            self._db_backend = "sqlite"
        
        # SQLite path
        self._sqlite_path = os.getenv("DB_SQLITE_PATH", os.path.join(os.path.dirname(__file__), "..", "..", "data", "parking.db"))
        Path(self._sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        
        # PostgreSQL credentials (used only if DB_BACKEND=postgres)
        self._db_host = os.getenv("DB_HOST", "host.docker.internal")
        self._db_port = int(os.getenv("DB_PORT", "5432"))
        self._db_name = os.getenv("DB_NAME", "Realtime_Parking")
        self._db_user = os.getenv("DB_USER", "postgres")
        self._db_pass = os.getenv("DB_PASSWORD", "postgres")
        self._init_database()

    # ── Startup ──────────────────────────────────────────────────────
    async def load_detection_model(self):
        """Load YOLO model (called from lifespan)."""
        self.detection_loaded = self.detection_service.load_model()

    def _pg_connect(self, dbname: Optional[str] = None):
        """Return a new psycopg2 connection to the target database."""
        return psycopg2.connect(
            host=self._db_host,
            port=self._db_port,
            dbname=dbname or self._db_name,
            user=self._db_user,
            password=self._db_pass,
        )

    def _sqlite_connect(self):
        """Return a new sqlite3 connection."""
        conn = sqlite3.connect(self._sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _db_connect(self, dbname: Optional[str] = None):
        """Return a database connection based on configured backend."""
        if self._db_backend == "postgres":
            return self._pg_connect(dbname)
        else:
            return self._sqlite_connect()

    def _init_database(self):
        """Create the database and tables if they do not exist."""
        if self._db_backend == "postgres":
            self._init_postgres_database()
        else:
            self._init_sqlite_database()

    def _init_postgres_database(self):
        """Initialize PostgreSQL database."""
        try:
            conn = psycopg2.connect(
                host=self._db_host,
                port=self._db_port,
                dbname="postgres",
                user=self._db_user,
                password=self._db_pass,
            )
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (self._db_name,),
            )
            if not cur.fetchone():
                cur.execute(
                    pgsql.SQL("CREATE DATABASE {}").format(
                        pgsql.Identifier(self._db_name)
                    )
                )
                logger.info(f"Created database: {self._db_name}")
            cur.close()
            conn.close()

            conn = self._pg_connect()
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS public.occupancy_history (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ DEFAULT NOW(),
                    occupied INTEGER NOT NULL,
                    total INTEGER NOT NULL,
                    zone_tl INTEGER DEFAULT 0,
                    zone_tr INTEGER DEFAULT 0,
                    zone_ml INTEGER DEFAULT 0,
                    zone_mr INTEGER DEFAULT 0,
                    zone_bl INTEGER DEFAULT 0,
                    zone_br INTEGER DEFAULT 0,
                    confidence TEXT,
                    detection_method TEXT,
                    processing_ms REAL,
                    double_parked INTEGER DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_occ_hist_ts
                ON public.occupancy_history (timestamp)
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS public.published_locations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    google_maps_url TEXT,
                    video_url TEXT,
                    grid_config JSONB,
                    zones JSONB,
                    total_spots INTEGER NOT NULL DEFAULT 0,
                    parameters JSONB,
                    published_at TIMESTAMPTZ DEFAULT NOW(),
                    lat DOUBLE PRECISION,
                    lon DOUBLE PRECISION
                )
            """)

            conn.commit()
            cur.close()
            conn.close()
            logger.info(f"PostgreSQL occupancy_history + published_locations tables ready at {self._db_name}")
        except Exception as e:
            logger.error(f"PostgreSQL init error: {e}")

    def _init_sqlite_database(self):
        """Initialize SQLite database."""
        try:
            conn = self._sqlite_connect()
            cur = conn.cursor()
            
            # Create occupancy_history table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS occupancy_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    occupied INTEGER NOT NULL,
                    total INTEGER NOT NULL,
                    zone_tl INTEGER DEFAULT 0,
                    zone_tr INTEGER DEFAULT 0,
                    zone_ml INTEGER DEFAULT 0,
                    zone_mr INTEGER DEFAULT 0,
                    zone_bl INTEGER DEFAULT 0,
                    zone_br INTEGER DEFAULT 0,
                    confidence TEXT,
                    detection_method TEXT,
                    processing_ms REAL,
                    double_parked INTEGER DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_occ_hist_ts
                ON occupancy_history (timestamp)
            """)

            # Create published_locations table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS published_locations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    google_maps_url TEXT,
                    video_url TEXT,
                    grid_config TEXT,
                    zones TEXT,
                    total_spots INTEGER NOT NULL DEFAULT 0,
                    parameters TEXT,
                    published_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    lat REAL,
                    lon REAL
                )
            """)

            conn.commit()
            conn.close()
            logger.info(f"SQLite occupancy_history + published_locations tables ready at {self._sqlite_path}")
        except Exception as e:
            logger.error(f"Database init error: {e}")

    # ── Main detection pipeline ──────────────────────────────────────
    async def run_detection(self) -> OccupancyResponse:
        """Run one detection cycle (called from background loop only)."""
        try:
            frame = await self.stream_service.capture_frame()

            if frame is not None:
                self.detection_service.set_exclude_regions(
                    self._grid_exclude_regions,
                    border=self._grid_border,
                )
                # Offload CPU-heavy detection to thread pool
                loop = asyncio.get_event_loop()
                count, bboxes, scores, class_names, proc_ms = await loop.run_in_executor(
                    self._executor,
                    self.detection_service.detect_vehicles,
                    frame,
                )
                self._last_bboxes = bboxes
                self._last_scores = scores
                self._last_class_names = class_names
                self._last_processing_ms = proc_ms
                self._last_frame_source = "video"
                _raw_mp = (self.detection_service.model_path or "base").replace("\\", "/")
                _mp_parts = _raw_mp.split("/")
                if "weights" in _mp_parts:
                    _wi = _mp_parts.index("weights")
                    _model_name = "/".join(_mp_parts[max(0, _wi - 2):_wi] + [_mp_parts[-1]])
                else:
                    _model_name = _mp_parts[-1]
                self._last_detection_method = f"rfdetr:{_model_name}" if self.detection_service.loaded else "none"
                self._last_confidence = (
                    "high" if self.detection_loaded and count > 0
                    else "medium" if count > 0
                    else "low"
                )
                self._has_data = True

                # Distribute bboxes into zones; detect double-parked vehicles
                self._assign_to_zones(bboxes, class_names, frame.shape[1], frame.shape[0])
                self.current_occupied = min(
                    sum(self.zone_occupancy.values()), self.total_spots
                )

                # Create annotated frame for frontend
                self._update_annotated_frame(frame, bboxes, scores)
            else:
                if not self._has_data:
                    return self._no_data_response()

            # Persist
            self._save_to_history()
            return self._build_response()

        except Exception as e:
            logger.error(f"Occupancy error: {e}")
            if self._has_data:
                return self._build_response()
            return self._no_data_response()

    async def get_current_occupancy(self) -> OccupancyResponse:
        """Return the latest cached occupancy (non-blocking)."""
        if self._has_data:
            return self._build_response()
        return self._no_data_response()

    # ── Zone assignment ──────────────────────────────────────────────
    # Uses self._zone_grid which is set from the active location config.
    # Default is [["TL","TR"],["ML","MR"],["BL","BR"]] for the Walmart layout.

    def _assign_to_zones(
        self,
        bboxes: list,
        class_names: list,
        frame_width: int,
        frame_height: int,
    ):
        """Assign detected vehicles to zones; flag oversized / double-parked."""
        for z in self.zone_config:
            self.zone_occupancy[z] = 0
            self._double_parked_zones[z] = 0
        self._double_parked_count = 0
        self._double_parked_indices = set()

        if not bboxes:
            return

        grid = self._zone_grid
        grid_rows = len(grid)
        grid_cols = len(grid[0]) if grid else 2

        border = self._grid_border or {"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0}
        bt = frame_height * float(border.get("top", 0.0)) / 100.0
        br = frame_width * float(border.get("right", 0.0)) / 100.0
        bb = frame_height * float(border.get("bottom", 0.0)) / 100.0
        bl = frame_width * float(border.get("left", 0.0)) / 100.0

        ax1, ay1 = bl, bt
        ax2, ay2 = frame_width - br, frame_height - bb
        act_w = max(1.0, ax2 - ax1)
        act_h = max(1.0, ay2 - ay1)
        cx_mid = (ax1 + ax2) / 2.0
        cy_mid = (ay1 + ay2) / 2.0

        h_lines = sorted(self._grid_h_lines[: max(0, grid_rows - 1)])
        v_lines = sorted(self._grid_v_lines[: max(0, grid_cols - 1)])
        h_bounds = [0.0] + h_lines + [1.0]
        v_bounds = [0.0] + v_lines + [1.0]

        h_line_angles = [float(v) for v in (self._grid_h_line_angles or [])]
        v_line_angles = [float(v) for v in (self._grid_v_line_angles or [])]
        if len(h_line_angles) < len(h_lines):
            h_line_angles = h_line_angles + [float(self._grid_h_angle_deg or 0.0)] * (len(h_lines) - len(h_line_angles))
        if len(v_line_angles) < len(v_lines):
            v_line_angles = v_line_angles + [float(self._grid_v_angle_deg or 0.0)] * (len(v_lines) - len(v_line_angles))
        h_boundary_angles = [0.0] + h_line_angles[:len(h_lines)] + [0.0]
        v_boundary_angles = [0.0] + v_line_angles[:len(v_lines)] + [0.0]

        def _h_boundary_y(boundary_idx: int, px: float) -> float:
            hf = h_bounds[boundary_idx]
            y0 = ay1 + hf * act_h
            h_tan = math.tan(math.radians(h_boundary_angles[boundary_idx]))
            return y0 + h_tan * (px - cx_mid)

        def _v_boundary_x(boundary_idx: int, py: float) -> float:
            vf = v_bounds[boundary_idx]
            x0 = ax1 + vf * act_w
            v_tan = math.tan(math.radians(v_boundary_angles[boundary_idx]))
            return x0 + v_tan * (py - cy_mid)

        def _row_for_point(px: float, py: float) -> int:
            for ri in range(len(h_bounds) - 1):
                upper_y = _h_boundary_y(ri + 1, px)
                if py < upper_y or ri == len(h_bounds) - 2:
                    return ri
            return len(h_bounds) - 2

        def _col_for_point(px: float, py: float) -> int:
            for ci in range(len(v_bounds) - 1):
                upper_x = _v_boundary_x(ci + 1, py)
                if px < upper_x or ci == len(v_bounds) - 2:
                    return ci
            return len(v_bounds) - 2

        # Compute median bbox area for relative oversized detection
        areas = [(x2 - x1) * (y2 - y1) for (x1, y1, x2, y2) in bboxes]
        median_area = (
            float(sorted(areas)[len(areas) // 2])
            if len(areas) >= MIN_DETECTIONS_FOR_MEDIAN
            else None
        )

        for idx, (x1, y1, x2, y2) in enumerate(bboxes):
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2
            col = min(_col_for_point(cx, cy), grid_cols - 1)
            row = min(_row_for_point(cx, cy), grid_rows - 1)

            # Guard: row/col must be valid in current grid
            if row >= len(grid) or col >= len(grid[row]):
                continue
            primary_zone = grid[row][col]
            if primary_zone not in self.zone_occupancy:
                continue

            area     = areas[idx]
            cls_name = class_names[idx] if idx < len(class_names) else "unknown"
            bw, bh   = float(x2 - x1), float(y2 - y1)

            is_oversized = False
            secondary_zones: set = set()

            # Check 1: oversized by area relative to median
            if median_area and median_area > 0:
                if cls_name in OVERSIZED_CLASSES and area > OVERSIZED_CLASS_FACTOR * median_area:
                    is_oversized = True
                elif area > OVERSIZED_AREA_FACTOR * median_area:
                    is_oversized = True

            # Check 2: bbox spans a zone boundary significantly
            # Column boundaries with vertical-line tilt
            y_mid = (y1 + y2) / 2.0
            for c_boundary in range(1, grid_cols):
                boundary_x = _v_boundary_x(c_boundary, y_mid)
                if col == c_boundary - 1 and x2 > boundary_x and bw > 0:
                    if (x2 - boundary_x) > BOUNDARY_SPAN_RATIO * bw:
                        if c_boundary < len(grid[row]):
                            nz = grid[row][c_boundary]
                            if nz in self.zone_occupancy:
                                secondary_zones.add(nz)
                elif col == c_boundary and x1 < boundary_x and bw > 0:
                    if (boundary_x - x1) > BOUNDARY_SPAN_RATIO * bw:
                        if c_boundary - 1 < len(grid[row]):
                            nz = grid[row][c_boundary - 1]
                            if nz in self.zone_occupancy:
                                secondary_zones.add(nz)

            # Row boundaries with horizontal-line tilt
            x_mid = (x1 + x2) / 2.0
            for r_boundary in range(1, grid_rows):
                boundary_y = _h_boundary_y(r_boundary, x_mid)
                if row == r_boundary - 1 and y2 > boundary_y and bh > 0:
                    if (y2 - boundary_y) > BOUNDARY_SPAN_RATIO * bh:
                        if r_boundary < len(grid) and col < len(grid[r_boundary]):
                            nz = grid[r_boundary][col]
                            if nz in self.zone_occupancy:
                                secondary_zones.add(nz)
                elif row == r_boundary and y1 < boundary_y and bh > 0:
                    if (boundary_y - y1) > BOUNDARY_SPAN_RATIO * bh:
                        if r_boundary - 1 < len(grid) and col < len(grid[r_boundary - 1]):
                            nz = grid[r_boundary - 1][col]
                            if nz in self.zone_occupancy:
                                secondary_zones.add(nz)

            secondary_zones.discard(primary_zone)
            is_double_parked = is_oversized or len(secondary_zones) > 0

            # Count primary spot
            self.zone_occupancy[primary_zone] = min(
                self.zone_occupancy[primary_zone] + 1,
                self.zone_config.get(primary_zone, 9999),
            )

            if is_double_parked:
                self._double_parked_count += 1
                self._double_parked_indices.add(idx)
                self._double_parked_zones[primary_zone] += 1

                if secondary_zones:
                    # Spans boundary — also count in secondary zone(s)
                    for sz in secondary_zones:
                        self.zone_occupancy[sz] = min(
                            self.zone_occupancy[sz] + 1,
                            self.zone_config.get(sz, 9999),
                        )
                        self._double_parked_zones[sz] += 1
                else:
                    # Oversized within one zone — extra spot in same zone
                    self.zone_occupancy[primary_zone] = min(
                        self.zone_occupancy[primary_zone] + 1,
                        self.zone_config.get(primary_zone, 9999),
                    )

    # ── Annotated frame for frontend video feed ──────────────────────
    def _update_annotated_frame(self, frame, bboxes, scores):
        """Draw detections on frame and push JPEG to stream service."""
        if not CV_AVAILABLE or frame is None:
            return
        try:
            annotated = self.detection_service.annotate_frame(
                frame, bboxes, scores,
                double_parked_indices=self._double_parked_indices,
                border=self._grid_border,
                exclude_regions=self.detection_service.exclude_regions,
            )
            # Resize for efficient transfer
            small = cv2.resize(annotated, (1280, 720))
            _, jpeg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
            self.stream_service.set_annotated_frame(jpeg.tobytes())
        except Exception as e:
            logger.error(f"Annotate frame error: {e}")

    # ── Response builders ────────────────────────────────────────────
    def _build_response(self) -> OccupancyResponse:
        zones = [
            ZoneOccupancy(
                zone_id=zid,
                occupied=self.zone_occupancy[zid],
                total=self.zone_config[zid],
                double_parked=self._double_parked_zones.get(zid, 0),
            )
            for zid in self.zone_config
        ]
        available = self.total_spots - self.current_occupied
        pct = round((self.current_occupied / self.total_spots) * 100, 1) if self.total_spots else 0

        return OccupancyResponse(
            total_spots=self.total_spots,
            occupied_spots=self.current_occupied,
            available_spots=available,
            occupancy_percent=pct,
            zones=zones,
            confidence=self._last_confidence,
            detection_method=self._last_detection_method,
            sam_processing_ms=self._last_processing_ms,
            frame_source=self._last_frame_source,
            double_parked_count=self._double_parked_count,
        )

    def _no_data_response(self) -> OccupancyResponse:
        """Returned when we have never captured a frame."""
        zones = [
            ZoneOccupancy(zone_id=zid, occupied=0, total=self.zone_config[zid])
            for zid in self.zone_config
        ]
        return OccupancyResponse(
            total_spots=self.total_spots,
            occupied_spots=0,
            available_spots=self.total_spots,
            occupancy_percent=0.0,
            zones=zones,
            confidence="no_data",
            detection_method="none",
            frame_source="none",
        )

    # ── Persistence ──────────────────────────────────────────────────
    def _save_to_history(self):
        try:
            conn = self._db_connect()
            cur = conn.cursor()
            params = (
                self.current_occupied,
                self.total_spots,
                self.zone_occupancy.get("TL", 0),
                self.zone_occupancy.get("TR", 0),
                self.zone_occupancy.get("ML", 0),
                self.zone_occupancy.get("MR", 0),
                self.zone_occupancy.get("BL", 0),
                self.zone_occupancy.get("BR", 0),
                self._last_confidence,
                self._last_detection_method,
                self._last_processing_ms,
                self._double_parked_count,
            )
            if self._db_backend == "postgres":
                cur.execute(
                    """INSERT INTO public.occupancy_history
                       (occupied, total,
                        zone_tl, zone_tr, zone_ml, zone_mr, zone_bl, zone_br,
                        confidence, detection_method, processing_ms,
                        double_parked)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    params,
                )
            else:
                cur.execute(
                    """INSERT INTO occupancy_history
                       (occupied, total,
                        zone_tl, zone_tr, zone_ml, zone_mr, zone_bl, zone_br,
                        confidence, detection_method, processing_ms,
                        double_parked)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    params,
                )
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"Save history error: {e}")

    # ── Context builder (for Chat Service) ───────────────────────────
    async def build_chat_context(self) -> ParkingContext:
        """Build a ``ParkingContext`` snapshot for the chat service."""
        stats = await self.get_stats()
        zones = [
            ZoneOccupancy(
                zone_id=zid,
                occupied=self.zone_occupancy.get(zid, 0),
                total=self.zone_config[zid],
                double_parked=self._double_parked_zones.get(zid, 0),
            )
            for zid in self.zone_config
        ]

        # ── Recent history summary (last 2 hours, sampled) ──
        history_summary = await self._build_history_summary()

        # ── Forecast summary for remaining hours today ──
        forecast_summary = await self._build_forecast_summary()

        return ParkingContext(
            total_spots=self.total_spots,
            occupied_spots=self.current_occupied,
            available_spots=self.total_spots - self.current_occupied,
            occupancy_percent=round((self.current_occupied / self.total_spots) * 100, 1) if self.total_spots else 0,
            zones=zones,
            confidence=self._last_confidence,
            detection_method=self._last_detection_method,
            peak_hour=stats.get("peak_hour"),
            today_average=stats.get("today_average_occupancy"),
            last_updated=datetime.utcnow().isoformat(),
            history_summary=history_summary,
            forecast_summary=forecast_summary,
        )

    async def _build_history_summary(self) -> str:
        """Build a concise text summary of recent occupancy history."""
        try:
            conn = self._db_connect()
            cur = conn.cursor()
            
            # Last 2 hours, one sample every ~5 min (limit 25)
            if self._db_backend == "postgres":
                cur.execute(
                    """SELECT timestamp, occupied, total
                       FROM public.occupancy_history
                       WHERE timestamp > NOW() - INTERVAL '2 hours'
                       ORDER BY timestamp DESC
                       LIMIT 25"""
                )
            else:  # SQLite
                cur.execute(
                    """SELECT timestamp, occupied, total
                       FROM occupancy_history
                       WHERE timestamp > datetime('now', '-2 hours')
                       ORDER BY timestamp DESC
                       LIMIT 25"""
                )
            rows = cur.fetchall()

            # Hourly averages for today
            if self._db_backend == "postgres":
                cur.execute(
                    """SELECT EXTRACT(HOUR FROM timestamp)::int AS hr,
                              ROUND(AVG(occupied)) AS avg_occ,
                              ROUND(AVG(total)) AS avg_total,
                              COUNT(*) AS samples
                       FROM public.occupancy_history
                       WHERE DATE(timestamp) = CURRENT_DATE
                       GROUP BY hr
                       ORDER BY hr"""
                )
            else:  # SQLite
                cur.execute(
                    """SELECT CAST(strftime('%H', timestamp) AS INTEGER) AS hr,
                              ROUND(AVG(occupied)) AS avg_occ,
                              ROUND(AVG(total)) AS avg_total,
                              COUNT(*) AS samples
                       FROM occupancy_history
                       WHERE date(timestamp) = date('now')
                       GROUP BY hr
                       ORDER BY hr"""
                )
            hourly = cur.fetchall()
            cur.close()
            conn.close()

            lines = []
            if rows:
                lines.append("Recent occupancy (last 2 hours, newest first):")
                for ts, occ, tot in rows[:15]:
                    pct = round((occ / tot) * 100, 1) if tot else 0
                    if isinstance(ts, str):  # SQLite returns ISO format string
                        ts_str = ts.split('T')[0] + ' ' + ts.split('T')[1][:5]
                        lines.append(f"  {ts_str.split()[1]} — {occ}/{tot} ({pct}%)")
                    else:  # PostgreSQL returns datetime object
                        lines.append(f"  {ts.strftime('%H:%M')} — {occ}/{tot} ({pct}%)")

            if hourly:
                lines.append("\nToday's hourly averages:")
                for hr, avg_occ, avg_total, samples in hourly:
                    hr = int(hr)  # Ensure integer
                    suffix = "AM" if hr < 12 else "PM"
                    disp = hr if hr <= 12 else hr - 12
                    if hr == 0: disp = 12
                    pct = round((float(avg_occ) / float(avg_total)) * 100, 1) if avg_total else 0
                    lines.append(f"  {disp} {suffix}: ~{int(avg_occ)} vehicles ({pct}%) [{samples} samples]")

            return "\n".join(lines) if lines else "No historical data available yet."
        except Exception as e:
            logger.error(f"History summary error: {e}")
            return "Historical data temporarily unavailable."

    async def _build_forecast_summary(self) -> str:
        """Build a text summary of forecasted occupancy for upcoming hours."""
        try:
            from datetime import datetime as dt
            now_hour = dt.now().hour
            conn = self._db_connect()
            cur = conn.cursor()
            
            # Forecast for each remaining hour today
            if self._db_backend == "postgres":
                cur.execute(
                    """SELECT EXTRACT(HOUR FROM timestamp)::int AS hr,
                              ROUND(AVG(occupied)) AS avg_occ,
                              COUNT(*) AS samples
                       FROM public.occupancy_history
                       WHERE EXTRACT(HOUR FROM timestamp) >= %s
                       GROUP BY hr
                       ORDER BY hr""",
                    (now_hour,),
                )
            else:  # SQLite
                cur.execute(
                    """SELECT CAST(strftime('%H', timestamp) AS INTEGER) AS hr,
                              ROUND(AVG(occupied)) AS avg_occ,
                              COUNT(*) AS samples
                       FROM occupancy_history
                       WHERE CAST(strftime('%H', timestamp) AS INTEGER) >= ?
                       GROUP BY hr
                       ORDER BY hr""",
                    (now_hour,),
                )
            rows = cur.fetchall()
            cur.close()
            conn.close()

            if not rows:
                return "Not enough data to generate forecasts yet."

            lines = ["Predicted occupancy for remaining hours today (based on historical averages):"]
            for hr, avg_occ, samples in rows:
                hr = int(hr)
                suffix = "AM" if hr < 12 else "PM"
                disp = hr if hr <= 12 else hr - 12
                if hr == 0: disp = 12
                pct = round((float(avg_occ) / self.total_spots) * 100, 1) if self.total_spots else 0
                conf = "high" if samples > 100 else "medium" if samples > 50 else "low"
                lines.append(f"  {disp} {suffix}: ~{pct}% occupancy (confidence: {conf}, {samples} samples)")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Forecast summary error: {e}")
            return "Forecast data temporarily unavailable."

    # ── History / Forecast / Stats ───────────────────────────────────
    async def get_history(self, hours: int = 24) -> List[HistoryEntry]:
        try:
            conn = self._db_connect()
            cur = conn.cursor()
            
            if self._db_backend == "postgres":
                cur.execute(
                    """SELECT timestamp, occupied, total
                       FROM public.occupancy_history
                       WHERE timestamp > NOW() - INTERVAL '%s hours'
                       ORDER BY timestamp DESC
                       LIMIT 1000""",
                    (hours,),
                )
            else:  # SQLite
                cur.execute(
                    """SELECT timestamp, occupied, total
                       FROM occupancy_history
                       WHERE timestamp > datetime('now', '-%s hours')
                       ORDER BY timestamp DESC
                       LIMIT 1000""",
                    (hours,),
                )
            
            history = []
            for row in cur.fetchall():
                ts = row[0]
                if isinstance(ts, str):
                    from datetime import datetime as dt
                    ts = dt.fromisoformat(ts.replace('Z', '+00:00'))
                history.append(
                    HistoryEntry(
                        timestamp=ts,
                        occupied=row[1],
                        total=row[2],
                        occupancy_percent=round((row[1] / row[2]) * 100, 1) if row[2] else 0,
                    )
                )
            cur.close()
            conn.close()
            return history
        except Exception as e:
            logger.error(f"History error: {e}")
            return []

    async def get_forecast(self, target_hour: int) -> ForecastResponse:
        try:
            conn = self._db_connect()
            cur = conn.cursor()
            
            if self._db_backend == "postgres":
                cur.execute(
                    """SELECT AVG(occupied), COUNT(*)
                       FROM public.occupancy_history
                       WHERE EXTRACT(HOUR FROM timestamp) = %s""",
                    (target_hour,),
                )
            else:  # SQLite
                cur.execute(
                    """SELECT AVG(occupied), COUNT(*)
                       FROM occupancy_history
                       WHERE CAST(strftime('%H', timestamp) AS INTEGER) = ?""",
                    (target_hour,),
                )
            
            row = cur.fetchone()
            cur.close()
            conn.close()

            if row and row[0] is not None:
                avg_occupied = row[0]
                samples = row[1]
                conf = "high" if samples > 100 else "medium" if samples > 50 else "low"
                return ForecastResponse(
                    target_hour=target_hour,
                    predicted_occupancy=round((avg_occupied / self.total_spots) * 100, 1),
                    confidence=conf,
                    based_on_samples=samples,
                )

            # Not enough historical data — return explicit "insufficient_data"
            return ForecastResponse(
                target_hour=target_hour,
                predicted_occupancy=0.0,
                confidence="insufficient_data",
                based_on_samples=0,
            )
        except Exception as e:
            logger.error(f"Forecast error: {e}")
            return ForecastResponse(
                target_hour=target_hour,
                predicted_occupancy=0.0,
                confidence="error",
                based_on_samples=0,
            )

    async def get_stats(self) -> dict:
        try:
            conn = self._db_connect()
            cur = conn.cursor()

            if self._db_backend == "postgres":
                cur.execute(
                    """SELECT AVG(occupied) FROM public.occupancy_history
                       WHERE DATE(timestamp) = CURRENT_DATE"""
                )
                today_avg = cur.fetchone()[0] or 0

                cur.execute(
                    """SELECT EXTRACT(HOUR FROM timestamp)::int AS hr, AVG(occupied)
                       FROM public.occupancy_history
                       WHERE DATE(timestamp) = CURRENT_DATE
                       GROUP BY hr
                       ORDER BY AVG(occupied) DESC
                       LIMIT 1"""
                )
            else:  # SQLite
                cur.execute(
                    """SELECT AVG(occupied) FROM occupancy_history
                       WHERE date(timestamp) = date('now')"""
                )
                today_avg = cur.fetchone()[0] or 0

                cur.execute(
                    """SELECT CAST(strftime('%H', timestamp) AS INTEGER) AS hr, AVG(occupied)
                       FROM occupancy_history
                       WHERE date(timestamp) = date('now')
                       GROUP BY hr
                       ORDER BY AVG(occupied) DESC
                       LIMIT 1"""
                )

            peak = cur.fetchone()
            peak_hour = int(peak[0]) if peak else None

            cur.close()
            conn.close()

            return {
                "today_average_occupancy": round(today_avg, 1),
                "peak_hour": peak_hour,
                "total_spots": self.total_spots,
                "zones": self.zone_config,
            }
        except Exception as e:
            logger.error(f"Stats error: {e}")
            return {"error": str(e)}
