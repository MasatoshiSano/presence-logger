from services.bridge.src.profile_resolver import (
    ProfileResolver,
    redact_for_logging,
)


def _profiles():
    return {
        "factory_a_wifi": {
            "description": "A",
            "sntp": {"servers": ["ntp.a"]},
            "oracle": {
                "client_mode": "thin", "auth_mode": "basic",
                "host": "10.0.0.1", "port": 1521, "service_name": "S",
                "user": "u", "password": "p1", "table_name": "HF1RCM01",
            },
        },
        "factory_b_wifi": {
            "description": "B",
            "sntp": {"servers": ["ntp.b"]},
            "oracle": {
                "client_mode": "thin", "auth_mode": "wallet",
                "dsn": "myadb_high", "user": "u", "password": "p2",
                "wallet_dir": "/etc/presence-logger/wallets/factory_b",
                "wallet_password": "wp", "table_name": "HF1RCM01",
            },
        },
    }


def test_resolve_known_ssid_returns_profile():
    resolver = ProfileResolver(profiles=_profiles(), unknown_policy="hold")
    decision = resolver.resolve("factory_a_wifi")
    assert decision.action == "send"
    assert decision.profile_name == "factory_a_wifi"


def test_resolve_unknown_ssid_with_hold_policy():
    resolver = ProfileResolver(profiles=_profiles(), unknown_policy="hold")
    decision = resolver.resolve("guest_wifi")
    assert decision.action == "hold"
    assert decision.profile_name is None


def test_resolve_unknown_ssid_with_use_last_policy():
    resolver = ProfileResolver(profiles=_profiles(), unknown_policy="use_last")
    resolver.resolve("factory_a_wifi")
    decision = resolver.resolve("guest_wifi")
    assert decision.action == "send"
    assert decision.profile_name == "factory_a_wifi"


def test_resolve_unknown_ssid_with_drop_policy():
    resolver = ProfileResolver(profiles=_profiles(), unknown_policy="drop")
    decision = resolver.resolve("guest_wifi")
    assert decision.action == "drop"


def test_resolve_no_ssid_with_hold_policy():
    resolver = ProfileResolver(profiles=_profiles(), unknown_policy="hold")
    decision = resolver.resolve(None)
    assert decision.action == "hold"


def test_redact_for_logging_strips_secrets():
    out = redact_for_logging(_profiles()["factory_b_wifi"])
    assert out["oracle"]["password"] == "***"
    assert out["oracle"]["wallet_password"] == "***"
    assert out["oracle"]["user"] == "u"
    assert out["oracle"]["dsn"] == "myadb_high"


def test_redact_for_logging_does_not_mutate_input():
    profile = _profiles()["factory_a_wifi"]
    _ = redact_for_logging(profile)
    assert profile["oracle"]["password"] == "p1"
