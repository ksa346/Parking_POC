from .router import router as yolo_router  # noqa: F401
from .db import Base, engine
import sqlalchemy


def init_db():
    Base.metadata.create_all(bind=engine)
    # Migrate: add new columns if they don't exist yet (SQLite won't ALTER existing tables via create_all)
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(sqlalchemy.text("PRAGMA table_info(yolo_projects)"))}
        migrations = {
            "segment_cols":  "ALTER TABLE yolo_projects ADD COLUMN segment_cols INTEGER NOT NULL DEFAULT 2",
            "segment_rows":  "ALTER TABLE yolo_projects ADD COLUMN segment_rows INTEGER NOT NULL DEFAULT 3",
            "grid_h_lines":  "ALTER TABLE yolo_projects ADD COLUMN grid_h_lines TEXT DEFAULT '[0.333, 0.667]'",
            "grid_v_lines":  "ALTER TABLE yolo_projects ADD COLUMN grid_v_lines TEXT DEFAULT '[0.5]'",
            "grid_h_line_angles": "ALTER TABLE yolo_projects ADD COLUMN grid_h_line_angles TEXT DEFAULT '[0.0, 0.0]'",
            "grid_v_line_angles": "ALTER TABLE yolo_projects ADD COLUMN grid_v_line_angles TEXT DEFAULT '[0.0]'",
        }
        for col, stmt in migrations.items():
            if col not in existing:
                conn.execute(sqlalchemy.text(stmt))
        # Migrate yolo_images: add group_id FK column
        img_existing = {row[1] for row in conn.execute(sqlalchemy.text("PRAGMA table_info(yolo_images)"))}
        if "group_id" not in img_existing:
            conn.execute(sqlalchemy.text("ALTER TABLE yolo_images ADD COLUMN group_id INTEGER REFERENCES yolo_image_groups(id)"))
        conn.commit()
