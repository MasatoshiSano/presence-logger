"""NetworkManager 接続ファイルに書く SSID / PSK のテストベクタ(ダミー値のみ)。

bash 実装(scripts/prepare-child-sd.sh)と python 実装(fleet_ui/migrate.py)は
別々に書かれている。両方のテストがこの一覧を回すので、規則がずれればどちらかが落ちる。
設計: docs/2026-09-29-child-join-fix-design.md §1.2 / §1.3
"""

HEX64 = "0123456789abcdefABCDEF0123456789abcdefABCDEF0123456789abcdefABCD"
assert len(HEX64) == 64

# (ssid, psk): 書いて読み戻したとき、NetworkManager も GLib も元の値に戻る組。
OK = [
    ("sibling-hub", "ap-secret9"),
    ("a#b", "sec#ret99"),
    ("a;b", "a;bcdefgh"),
    ("a\\b", "a\\bcdefgh"),
    ("a\\;b", "a\\;bcdefgh"),
    ("a=b", "a=bcdefgh"),
    ('a"b', 'a"bcdefgh'),
    ("a'b", "a'bcdefgh"),
    ("a,b", "a,bcdefgh"),
    ("#start", "#startpass"),
    (" lead", " leadpass1"),
    ("  two", "  twolead12"),
    ("trail ", "trailpas1 "),
    ("工場-hub", "plain-pass-1"),
    ("tab\tx", "plain-pass-2"),
    ("x", "12345678"),
    ("s" * 32, "p" * 63),
    ("hex64-hub", HEX64),
]

# 書く前に拒否されるべき PSK(WPA: 表示可能ASCII 8〜63文字、または16進64文字)。
BAD_PSK = [
    "short12",                # 7文字
    "p" * 64,                 # 64文字だが16進でない
    "p" * 65,                 # 長すぎる
    "工場パスワード12",          # 非ASCII
    "tab\tpass12",            # 制御文字
    "0123456789abcdefghij" * 3 + "0123",  # 64桁だが16進でない(g-j を含む)
]

# 書く前に拒否されるべき SSID(UTF-8 で 1〜32 バイト)。
BAD_SSID = [
    "",
    "s" * 33,
    "工" * 11,                 # 33 バイト
]
