"""新しいホスト名の検証と既定値生成の検証。

ホスト名は device_id と MQTT の client_id の両方を決める。既存の子と重複させると
`client_id=f"child-csv-{DEVICE_ID}"` が衝突し、MQTTは同一client_idの新規接続時に
既存接続を切断するため、2台が互いを蹴り合う無限ループになる。実際に起きた事故なので、
UIは保存前に必ず弾く。
"""
from fleet_ui.hostname import suggest_hostname, validate_hostname


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


def test_suggest_continues_the_existing_sequence():
    assert suggest_hostname(["pizero2w", "pizero2w-2.local"]) == "pizero2w-3"


def test_suggest_skips_used_numbers():
    assert suggest_hostname(["pizero2w", "pizero2w-2", "pizero2w-3"]) == "pizero2w-4"


def test_suggest_from_single_child():
    assert suggest_hostname(["pizero2w"]) == "pizero2w-2"


def test_suggest_with_no_children():
    assert suggest_hostname([]) == "pizero2w-2"


def test_rejects_trailing_newline():
    """`$` は末尾改行の直前でもマッチするため、'abc\\n' を通してしまっていた。
    provision.py の sed/printf へ渡ると2行を書き込む形になる。"""
    assert validate_hostname("pizero2w-3\n", []) is not None
