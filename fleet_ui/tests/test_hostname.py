"""新しいホスト名の検証と既定値生成の検証。

ホスト名は device_id と MQTT の client_id の両方を決める。既存の子と重複させると
`client_id=f"child-csv-{DEVICE_ID}"` が衝突し、MQTTは同一client_idの新規接続時に
既存接続を切断するため、2台が互いを蹴り合う無限ループになる。実際に起きた事故なので、
UIは保存前に必ず弾く。
"""
from pathlib import Path

from fleet_ui.hostname import read_hub_hostname, suggest_hostname, validate_hostname


def test_accepts_a_normal_name():
    assert validate_hostname("pizero2w-3", ["pizero2w", "pizero2w-2"]) is None


def test_rejects_empty():
    assert validate_hostname("", []) is not None


def test_rejects_duplicate_of_existing_child():
    """重複を許すと MQTT client_id が衝突して2台が互いを切断し合う。"""
    msg = validate_hostname("pizero2w", ["pizero2w", "pizero2w-2"])
    assert msg is not None
    assert "client_id" in msg


def test_duplicate_check_ignores_mdns_suffix():
    """インベントリは `pizero2w-2.local` の形で持つが、ホスト名としては同一。"""
    assert validate_hostname("pizero2w-2", ["pizero2w-2.local"]) is not None


def test_rejects_uppercase_and_symbols():
    assert validate_hostname("PiZero_3", []) is not None
    assert validate_hostname("pi zero", []) is not None


def test_rejects_leading_or_trailing_hyphen():
    assert validate_hostname("-pizero", []) is not None
    assert validate_hostname("pizero-", []) is not None


def test_rejects_too_long():
    assert validate_hostname("a" * 64, []) is not None


def test_suggest_is_hub_name_plus_next_ordinal():
    assert suggest_hostname(["zero2.local"], hub="tpc12345") == "tpc12345-2"


def test_suggest_first_child_is_one():
    assert suggest_hostname([], hub="tpc12345") == "tpc12345-1"


def test_suggest_skips_a_name_already_taken():
    assert suggest_hostname(["tpc12345-2.local"], hub="tpc12345") == "tpc12345-3"


def test_suggest_without_hub_uses_child_prefix():
    assert suggest_hostname([]) == "child-1"
    assert suggest_hostname(["zero2"]) == "child-2"


def test_read_hub_hostname_from_site_env(tmp_path: Path):
    p = tmp_path / "site.env"
    p.write_text("HUB_HOSTNAME=tpc12345\nAP_SSID=tpc12345-hub\n", encoding="utf-8")
    assert read_hub_hostname(p) == "tpc12345"


def test_rejects_trailing_newline():
    """`$` は末尾改行の直前でもマッチするため、'abc\\n' を通してしまっていた。
    provision.py の sed/printf へ渡ると2行を書き込む形になる。"""
    assert validate_hostname("pizero2w-3\n", []) is not None
