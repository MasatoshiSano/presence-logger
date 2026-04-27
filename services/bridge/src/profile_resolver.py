import copy
from dataclasses import dataclass
from typing import Any, Literal

REDACTED = "***"  # noqa: S105
SECRET_KEYS = {"password", "wallet_password"}

Action = Literal["send", "hold", "drop"]


@dataclass(frozen=True)
class ResolverDecision:
    action: Action
    profile_name: str | None


class ProfileResolver:
    def __init__(self, *, profiles: dict[str, Any], unknown_policy: str):
        self._profiles = profiles
        self._policy = unknown_policy
        self._last_known: str | None = None

    def resolve(self, ssid: str | None) -> ResolverDecision:
        if ssid and ssid in self._profiles:
            self._last_known = ssid
            return ResolverDecision(action="send", profile_name=ssid)
        if self._policy == "use_last" and self._last_known:
            return ResolverDecision(action="send", profile_name=self._last_known)
        if self._policy == "drop":
            return ResolverDecision(action="drop", profile_name=None)
        return ResolverDecision(action="hold", profile_name=None)

    def get(self, profile_name: str) -> dict[str, Any]:
        return self._profiles[profile_name]


def redact_for_logging(profile: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy then replace any secret-like value with '***'. Pure function."""
    redacted = copy.deepcopy(profile)
    _redact_in_place(redacted)
    return redacted


def _redact_in_place(node: Any) -> None:
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if k in SECRET_KEYS and isinstance(v, str):
                node[k] = REDACTED
            else:
                _redact_in_place(v)
    elif isinstance(node, list):
        for item in node:
            _redact_in_place(item)
