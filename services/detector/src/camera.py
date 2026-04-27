import cv2
import numpy as np


class CameraOpenError(RuntimeError):
    pass


class Camera:
    def __init__(self, *, device: str, width: int, height: int, warmup_frames: int):
        self._device = device
        self._width = width
        self._height = height
        self._warmup_frames = warmup_frames
        self._cap: cv2.VideoCapture | None = None
        self.consecutive_failures = 0

    def open(self) -> None:
        cap = cv2.VideoCapture(self._device)
        if not cap.isOpened():
            raise CameraOpenError(f"failed to open camera at {self._device}")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        for _ in range(self._warmup_frames):
            cap.read()
        self._cap = cap

    def read(self) -> np.ndarray | None:
        if self._cap is None:
            raise CameraOpenError("camera not opened")
        ok, frame = self._cap.read()
        if not ok or frame is None:
            self.consecutive_failures += 1
            return None
        self.consecutive_failures = 0
        return frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
