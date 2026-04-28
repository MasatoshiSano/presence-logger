#!/usr/bin/env python3
"""Capture a frame from /dev/video0, save it, and run MediaPipe with all
categories at low threshold to see what's actually visible."""
import sys
from pathlib import Path
sys.path.insert(0, "/app")

import cv2
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
import mediapipe as mp

MODEL = "/opt/models/efficientdet_lite0.tflite"
OUT = "/app/scripts/_diag_frame.jpg"

cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("ERROR: cannot open /dev/video0"); sys.exit(2)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
for _ in range(5): cap.read()
ok, frame = cap.read()
cap.release()
if not ok:
    print("ERROR: read failed"); sys.exit(3)

cv2.imwrite(OUT, frame)
print(f"saved frame {frame.shape} -> {OUT}")
print(f"frame stats: mean={frame.mean():.1f} min={frame.min()} max={frame.max()} std={frame.std():.1f}")

# Run MediaPipe with NO category filter and low threshold to see ALL detections
opts = mp_vision.ObjectDetectorOptions(
    base_options=mp_python.BaseOptions(model_asset_path=MODEL),
    score_threshold=0.05,        # very low
    max_results=20,
    running_mode=mp_vision.RunningMode.IMAGE,
)
detector = mp_vision.ObjectDetector.create_from_options(opts)
rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
result = detector.detect(mp_image)
print(f"\ntotal detections (any category, score>=0.05): {len(result.detections)}")
for i, d in enumerate(result.detections):
    cats = ", ".join(f"{c.category_name}={c.score:.2f}" for c in d.categories)
    bbox = d.bounding_box
    print(f"  [{i}] bbox=({bbox.origin_x},{bbox.origin_y},{bbox.width}x{bbox.height}) cats={cats}")
if not result.detections:
    print("  (nothing detected at all — frame may be too dark, blurry, or empty)")
