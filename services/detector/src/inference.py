from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np


@dataclass(frozen=True)
class DetectionResult:
    has_person: bool
    top_score: float
    detections_count: int
    infer_ms: float


class _DetectBackend(Protocol):
    def detect(self, mp_image: Any) -> Any: ...


class PersonDetector:
    """Thin wrapper around MediaPipe ObjectDetector.
    The backend can be swapped in tests with a fake.
    """

    def __init__(self, *, backend: _DetectBackend, score_threshold: float, target_category: str):
        self._backend = backend
        self._threshold = score_threshold
        self._target = target_category

    @classmethod
    def from_model_path(
        cls, *, model_path: Path | str, score_threshold: float, target_category: str
    ):
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        opts = mp_vision.ObjectDetectorOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            score_threshold=score_threshold,
            category_allowlist=[target_category],
            running_mode=mp_vision.RunningMode.IMAGE,
        )
        backend = mp_vision.ObjectDetector.create_from_options(opts)
        return cls(
            backend=backend, score_threshold=score_threshold, target_category=target_category
        )

    def detect(self, frame_bgr: np.ndarray) -> DetectionResult:
        import time
        t0 = time.monotonic()
        # MediaPipe expects RGB; conversion is done lazily here to keep the interface simple.
        mp_image = self._to_mp_image(frame_bgr)
        result = self._backend.detect(mp_image)
        elapsed = (time.monotonic() - t0) * 1000.0

        top_score = 0.0
        count = 0
        for det in getattr(result, "detections", []):
            for cat in getattr(det, "categories", []):
                if cat.category_name == self._target and cat.score > top_score:
                    top_score = cat.score
                    count += 1
        return DetectionResult(
            has_person=top_score >= self._threshold,
            top_score=top_score,
            detections_count=count,
            infer_ms=elapsed,
        )

    @staticmethod
    def _to_mp_image(frame_bgr: np.ndarray) -> Any:
        # In tests with a fake backend we never reach this path; keep the dependency lazy.
        try:
            import cv2
            import mediapipe as mp
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            return mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        except ImportError:
            return frame_bgr
