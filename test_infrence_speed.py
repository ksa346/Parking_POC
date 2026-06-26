import sys
from pathlib import Path

# Add backend directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.services.detection_service import DetectionService
import cv2
import time
import numpy as np

# Load a test frame
frame = cv2.imread("data\\img1.jpg")

# Benchmark RF-DETR (checkpoint_best_total.pth)
rfdetr = DetectionService(model_path="checkpoint_best_total.pth", confidence=0.5)
rfdetr.load_model()

times = []
for _ in range(10):
    start = time.time()
    count, bboxes, scores, names, ms = rfdetr.detect_vehicles(frame)
    times.append(ms)

print(f"RF-DETR avg: {np.mean(times):.1f}ms, min: {np.min(times):.1f}ms, max: {np.max(times):.1f}ms")

# Benchmark YOLO (yolo_trained.pt)
yolo = DetectionService(model_path="yolo_trained.pt", confidence=0.5)
yolo.load_model()

times = []
for _ in range(10):
    start = time.time()
    count, bboxes, scores, names, ms = yolo.detect_vehicles(frame)
    times.append(ms)

print(f"YOLO avg: {np.mean(times):.1f}ms, min: {np.min(times):.1f}ms, max: {np.max(times):.1f}ms")