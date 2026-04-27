# MediaPipe models

Place `efficientdet_lite0.tflite` here before building the detector image.

Download:
```bash
wget -O efficientdet_lite0.tflite \
  https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float32/latest/efficientdet_lite0.tflite
```

The file is ignored by git (`*.tflite` is in `.gitignore`); only this README is tracked
via `models/.gitkeep`.
