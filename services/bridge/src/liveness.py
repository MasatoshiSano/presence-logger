"""Child-device liveness tracking for the hub.

Children (detector Pis) publish:
  - retained status on `<status_prefix><device_id>`: "online" on connect, and an
    MQTT Last-Will "offline" the broker emits on ungraceful disconnect.
  - periodic heartbeats on `<heartbeat_prefix><device_id>` (JSON).

The tracker turns those signals into an effective per-device state:
  online  - a fresh alive signal (online status or heartbeat) within the timeout
  stale   - last alive signal older than the timeout, but no offline seen
            (TCP/LWT still up but the app stopped heartbeating -> probably hung)
  offline - the Last-Will fired and is the most recent signal
  unknown - never heard from

Pure logic; `now` is injected (monotonic seconds) so it is fully testable and
thread-safe (a lock guards the dict because record_* runs on the MQTT thread
while snapshot() runs on the main loop).
"""
import threading

ONLINE = "online"
OFFLINE = "offline"


def device_id_from_topic(topic: str, prefix: str) -> str | None:
    """Return the device_id suffix after `prefix`, or None if topic lacks it."""
    if topic.startswith(prefix) and len(topic) > len(prefix):
        return topic[len(prefix):]
    return None


class _Device:
    __slots__ = ("last_status", "last_status_at", "last_hb_at", "last_hb_payload")

    def __init__(self) -> None:
        self.last_status: str | None = None
        self.last_status_at: float | None = None
        self.last_hb_at: float | None = None
        self.last_hb_payload: dict | None = None


class LivenessTracker:
    def __init__(self, *, heartbeat_timeout_seconds: float):
        self._timeout = heartbeat_timeout_seconds
        self._devices: dict[str, _Device] = {}
        self._lock = threading.Lock()

    def record_status(self, device_id: str, status: str, *, now: float) -> None:
        status = ONLINE if status == ONLINE else OFFLINE
        with self._lock:
            d = self._devices.setdefault(device_id, _Device())
            d.last_status = status
            d.last_status_at = now

    def record_heartbeat(self, device_id: str, payload: dict, *, now: float) -> None:
        with self._lock:
            d = self._devices.setdefault(device_id, _Device())
            d.last_hb_at = now
            d.last_hb_payload = payload

    def _alive_at(self, d: _Device) -> float | None:
        """Most recent signal that implies the device is up (online or heartbeat)."""
        candidates = []
        if d.last_hb_at is not None:
            candidates.append(d.last_hb_at)
        if d.last_status == ONLINE and d.last_status_at is not None:
            candidates.append(d.last_status_at)
        return max(candidates) if candidates else None

    def _state(self, d: _Device, now: float) -> tuple[str, float | None]:
        alive_at = self._alive_at(d)
        # offline only if the Last-Will is the newest thing we have
        if d.last_status == OFFLINE and d.last_status_at is not None and (
            alive_at is None or d.last_status_at >= alive_at
        ):
            return OFFLINE, None
        if alive_at is not None:
            age = now - alive_at
            return (ONLINE if age <= self._timeout else "stale"), age
        return "unknown", None

    def snapshot(self, *, now: float) -> list[dict]:
        out = []
        with self._lock:
            for device_id, d in self._devices.items():
                state, age = self._state(d, now)
                out.append({
                    "device_id": device_id,
                    "state": state,
                    "age_seconds": round(age, 1) if age is not None else None,
                    "last_heartbeat": d.last_hb_payload,
                })
        out.sort(key=lambda x: x["device_id"])
        return out
