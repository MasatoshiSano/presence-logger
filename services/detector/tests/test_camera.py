from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from services.detector.src.camera import Camera, CameraOpenError


def _make_cv2_mock(read_returns):
    cap = MagicMock()
    cap.isOpened.return_value = True
    cap.read.side_effect = read_returns
    return cap


def test_open_calls_videocapture_with_device():
    with patch("services.detector.src.camera.cv2") as cv2_mock:
        cv2_mock.VideoCapture.return_value.isOpened.return_value = True
        cam = Camera(device="/dev/video0", width=640, height=480, warmup_frames=0)
        cam.open()
        cv2_mock.VideoCapture.assert_called_once_with("/dev/video0")


def test_open_raises_when_isopened_false():
    with patch("services.detector.src.camera.cv2") as cv2_mock:
        cv2_mock.VideoCapture.return_value.isOpened.return_value = False
        cam = Camera(device="/dev/video0", width=640, height=480, warmup_frames=0)
        with pytest.raises(CameraOpenError):
            cam.open()


def test_warmup_consumes_n_frames():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cap = _make_cv2_mock([(True, frame)] * 5)
    with patch("services.detector.src.camera.cv2") as cv2_mock:
        cv2_mock.VideoCapture.return_value = cap
        cam = Camera(device="/dev/video0", width=640, height=480, warmup_frames=3)
        cam.open()
        assert cap.read.call_count == 3  # warmup frames consumed


def test_read_success_returns_frame_and_resets_failures():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cap = _make_cv2_mock([(False, None), (True, frame)])
    with patch("services.detector.src.camera.cv2") as cv2_mock:
        cv2_mock.VideoCapture.return_value = cap
        cam = Camera(device="/dev/video0", width=640, height=480, warmup_frames=0)
        cam.open()
        assert cam.read() is None
        assert cam.consecutive_failures == 1
        assert cam.read() is not None
        assert cam.consecutive_failures == 0


def test_close_releases_capture():
    cap = _make_cv2_mock([])
    with patch("services.detector.src.camera.cv2") as cv2_mock:
        cv2_mock.VideoCapture.return_value = cap
        cam = Camera(device="/dev/video0", width=640, height=480, warmup_frames=0)
        cam.open()
        cam.close()
        cap.release.assert_called_once()
