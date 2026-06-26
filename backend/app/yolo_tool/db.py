# import os
# from sqlalchemy import create_engine
# from sqlalchemy.orm import sessionmaker, declarative_base

# _DB_PATH = os.getenv("YOLO_TOOL_DB", os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "yolo_tool.db"))
# engine = create_engine(f"sqlite:///{_DB_PATH}", connect_args={"check_same_thread": False})
# SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
# Base = declarative_base()


# def get_db():
#     db = SessionLocal()
#     try:
#         yield db
#     finally:
#         db.close()
import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
 
# Default to a local (non-network) path so SQLite file-locking works reliably.
# On Azure ML the cloudfiles mount is a CIFS/NFS share where POSIX locks fail.
# Override with YOLO_TOOL_DB env-var (e.g. in Docker: /app/data/yolo_tool.db).
_DB_PATH = os.getenv("YOLO_TOOL_DB", "/tmp/yolo_tool.db")
engine = create_engine(
    f"sqlite:///{_DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 60},
)
 
 
@event.listens_for(engine, "connect")
def _set_wal_mode(dbapi_conn, _connection_record):
    """Enable WAL journal mode for better concurrent-access behaviour."""
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    dbapi_conn.execute("PRAGMA busy_timeout=60000")
 
 
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
 
 
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()