import os
import aiofiles
from fastapi import UploadFile

_BASE = os.getenv(
    "YOLO_UPLOADS_DIR",
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "yolo_uploads"),
)


def upload_dir(project_id: int) -> str:
    path = os.path.join(_BASE, str(project_id))
    os.makedirs(path, exist_ok=True)
    return path


async def save_upload(upload: UploadFile, dest: str) -> None:
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await upload.read(1024 * 1024):
            await f.write(chunk)
