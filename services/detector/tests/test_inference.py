from dataclasses import dataclass

import numpy as np

from services.detector.src.inference import PersonDetector


@dataclass
class _FakeCategory:
    category_name: str
    score: float


@dataclass
class _FakeDetection:
    categories: list


@dataclass
class _FakeMpResult:
    detections: list


class _FakeBackend:
    def __init__(self, results: list):
        self._results = results
        self.calls = 0

    def detect(self, mp_image):  # noqa: ARG002
        r = self._results[self.calls]
        self.calls += 1
        return r


def _frame() -> np.ndarray:
    return np.zeros((480, 640, 3), dtype=np.uint8)


def test_returns_has_person_true_when_score_meets_threshold():
    backend = _FakeBackend([
        _FakeMpResult(detections=[
            _FakeDetection(categories=[_FakeCategory("person", 0.7)]),
        ])
    ])
    det = PersonDetector(backend=backend, score_threshold=0.5, target_category="person")
    r = det.detect(_frame())
    assert r.has_person is True
    assert r.top_score == 0.7
    assert r.detections_count == 1


def test_returns_has_person_false_when_below_threshold():
    backend = _FakeBackend([
        _FakeMpResult(detections=[
            _FakeDetection(categories=[_FakeCategory("person", 0.3)]),
        ])
    ])
    det = PersonDetector(backend=backend, score_threshold=0.5, target_category="person")
    r = det.detect(_frame())
    assert r.has_person is False
    assert r.top_score == 0.3


def test_ignores_non_person_categories():
    backend = _FakeBackend([
        _FakeMpResult(detections=[
            _FakeDetection(categories=[_FakeCategory("cat", 0.99)]),
        ])
    ])
    det = PersonDetector(backend=backend, score_threshold=0.5, target_category="person")
    r = det.detect(_frame())
    assert r.has_person is False


def test_empty_detections_returns_no_person():
    backend = _FakeBackend([_FakeMpResult(detections=[])])
    det = PersonDetector(backend=backend, score_threshold=0.5, target_category="person")
    r = det.detect(_frame())
    assert r.has_person is False
    assert r.top_score == 0.0
    assert r.detections_count == 0
