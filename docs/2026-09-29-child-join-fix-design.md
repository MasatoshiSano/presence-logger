# 子Pi参加設定の引用符バグ修正と、AP_GW_IP 変更時の子への反映 — 設計

- 作成: 2026-09-29（Opus 設計、未実装）
- 対象: `scripts/prepare-child-sd.sh`, `fleet_ui/migrate.py`, `fleet_ui/provision.py`,
  `scripts/bootstrap/50-ap.sh`, `scripts/deploy-child.sh`, 資料一式
- 非対象: `services/bridge/`, `child/`（子の送信コード）, MERGE の ON 句・照合キー・failed 判定

---

## 0. 結論（先に）

| 論点 | 決定 |
|---|---|
| 引用符 | **引用符で囲まない**。NetworkManager 自身と同じ書式にする: `\`→`\\`、先頭の空白→`\s`、SSID の `;`→`\\;`、SSID が非ASCII・制御文字・末尾空白を含むときは **バイト列形式**（`115;115;...;`）。PSK は WPA の規則（表示可能ASCII 8〜63文字 または 16進64文字）以外を**書く前に拒否**する |
| 共通化 | bash と python で**別々の実装のまま、同じ規則表と同じテストベクタ**で揃える。テストは両方とも **NetworkManager 自身の読み手**（`nmcli --offline`）と **GLib.KeyFile** で読み戻して元の値と一致するか確認する |
| 既存の引用符つきプロファイル | **SD は検出して上書きで修復し、修復したことを表示**。稼働中の子は `take_child` を再実行すれば上書きで直る。一度も繋がらず孤立した子は SD 経路でしか直せない（手順を資料に書く）。フリート全体を自動で直す仕組みは作らず、**検出して報告だけ**する |
| AP_GW_IP | **既定（10.42.0.1）を引き継ぎ、衝突する現場でだけ検出して止める**。「常に別の値を強制」は採らない。フェーズ50で AP を起動する**前**に、AP のサブネットが**この機械の他のインターフェースの経路・アドレスと重なっていないか**を確認し、重なっていれば起動せずに空き候補を示して止める |
| 子の送信先 | 付け替え経路（`take_child` / `prepare-child-sd.sh` / `adopt_keeping_identity` / `register_new_child`）で、**毎回**、付け替え先ハブの `AP_GW_IP` に揃える（既定へ戻す向きも同じ処理）。揃える対象は **2つ**ある: `~/send_target_config.json` の `host` と、`child-csv-to-mqtt` の `MQTT_HOST`（systemd drop-in） |
| 共通設定の配布 | `--with-shared-config` は `send_target_config.json` の `host` を**配る前にハブの AP_GW_IP で書き換える** |
| 分割 | A（引用符）と B1（重なり検出・配布・資料）は並行。B2（子の送信先を揃える）は A のマージ後 |

---

## 1. 引用符バグ

### 1.1 何が起きていたか（根拠つき）

`child_sd_nm_quote()` / `nm_quote()` は値を `"..."` で囲む。NetworkManager の keyfile は
**引用符を特別扱いしない**ので、引用符は SSID/PSK の一部として読まれる。つまり子は
`"raspberrypi5-2-hub"`（20バイト、引用符込み）という名前の AP を探しており、そんな AP は
存在しないので一度も接続を試みなかった。実機の症状（DHCP リース0件、関連付け試行ゼロ）と一致する。

**根拠1 — NetworkManager 自身の書き手**（この機体、`nmcli 1.52.1`、`--offline` は
システムの接続を一切変更せず標準出力に keyfile を出すだけ）:

```console
$ nmcli --offline connection add type wifi con-name t ifname '*' ssid '"raspberrypi5-2-hub"' | grep ^ssid
ssid="raspberrypi5-2-hub"
```

**SSID の文字として `"` を含めて渡したときの出力が、今のコードの出力と同じバイト列**になる。
今のコードが書いているのは「引用符を含む SSID」だと確定した。

**根拠2 — NetworkManager 自身の読み手**。今のコードと同じ形のファイルを標準入力から
`nmcli --offline connection modify` に読ませ、何も変えずに書き戻させると、引用符はそのまま残る
（外していれば `ssid=raspberrypi5-2-hub` に正規化されて出てくるはず）:

```console
$ nmcli --offline connection modify connection.autoconnect-retries 0 < quoted.nmconnection
...
[wifi]
mode=infrastructure
ssid="raspberrypi5-2-hub"

[wifi-security]
key-mgmt=wpa-psk
psk="dummy#pass9"
```

**根拠3 — GLib.KeyFile**（NetworkManager の keyfile の土台。`/usr/bin/python3` の `gi`、GLib 2.84）:

```text
入力行: q="quoted"                 → get_string: '"quoted"'   （引用符は値の一部）
入力行: psk=\s\stwo#x;y\\z"q trail  → get_string: '  two#x;y\z"q trail '
```

値の途中の `#` は**コメントにならない**。GLib でコメントになるのは行頭が `#` の行だけ。
引用符を入れた元の動機（「未引用だと # 以降がコメント扱いになる」）は**誤り**だった。

### 1.2 正しい書式 — NetworkManager 自身の出力を正とする

`nmcli --offline connection add type wifi ... ssid "$s" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$p"` の
出力を採取した（値はすべてダミー）:

| 入力 | NM が書く `ssid=` | 入力 | NM が書く `psk=` |
|---|---|---|---|
| `x` | `x` | `sec#ret99` | `sec#ret99` |
| `a#b` | `a#b` | `a;bcdefgh` | `a;bcdefgh` |
| `a;b` | `a\\;b` | `a\bcdefgh` | `a\\bcdefgh` |
| `a\b` | `a\\b` | ` leadpass1`（先頭空白） | `\sleadpass1` |
| `  two`（先頭空白2つ） | `\s\stwo` | `  twolead12` | `\s\stwolead12` |
| ` lead` | `\slead` | `trailpas1 `（末尾空白） | `trailpas1 `（そのまま） |
| `trail `（末尾空白） | `trail `（そのまま） | `a"bcdefgh` | `a"bcdefgh` |
| `a=b` / `a"b` / `a'b` / `a,b` / `#start` | そのまま | `a=bcdefgh` / `a'bcdefgh` / `#startpass` | そのまま |
| `工場-hub`（非ASCII） | `229;183;165;229;160;180;45;104;117;98;` | `工場パスワード12` | そのまま（NM は受けるが WPA 規格外） |
| `tab<TAB>x`（制御文字） | `116;97;98;9;120;` | `tab<TAB>pass12` | 生の TAB（WPA 規格外） |
| `a\;b` | `a\\\\;b` | `a\;bcdefgh` | `a\\;bcdefgh` |

**NM の読み手の挙動**（`nmcli --offline connection modify` に手書きファイルを読ませて確認）:

| 手書きの値 | NM が読んだ結果（正規化出力） | 意味 |
|---|---|---|
| `ssid=a;b`（未エスケープ） | `a\\;b` | `;` を1文字として読む（許容はされる） |
| `ssid=a\;b` | **エラー**: `802-11-wireless.ssid: property is missing` | GLib で不正なエスケープ → SSID ごと消える |
| `ssid= lead` / `psk=  leadpass1` | `lead` / `leadpass1` | **先頭空白は黙って消える** → `\s` が必須 |
| `ssid=trail ` / `psk=trailpas1 ` | 末尾空白は保持 | GLib は末尾空白を保持 |
| `ssid=工場-hub`（文字列形式） | バイト列形式に正規化、同じ値 | 文字列形式でも読める |

#### 決定した規則（bash・python 共通）

**PSK**
1. 書く前に検証: `8 ≤ len ≤ 63` かつ全文字が表示可能 ASCII（0x20〜0x7E）、**または**
   `len == 64` かつ 16進のみ。外れたら書かずにエラー（「このハブの AP パスワードは子Pi が
   使えない文字を含みます」）。WPA2-PSK の規格どおりで、AP 側（`setup-dongle-ap.sh`）も
   同じ値を受け付けないため、実運用で弾かれる正当な値はない。
2. `\` → `\\`
3. 先頭の連続する空白の**各1文字**を `\s` にする（NM と同じ `\s\s...`）。
4. それ以外（`#` `;` `=` `"` `'` 末尾空白）はそのまま。

**SSID**
1. 書く前に検証: UTF-8 で 1〜32 バイト、NUL を含まない。外れたら書かずにエラー。
2. 「そのまま書ける形」の条件 = 全文字が表示可能 ASCII、`\` を含まない、先頭・末尾が空白でない。
   - 条件を満たす → 文字列形式。`;` だけ `\\;` にする（NM と同じ）。
   - 満たさない → **バイト列形式**: UTF-8 の各バイトを10進にして `;` 区切り、末尾にも `;`
     （`229;183;...;98;`）。NM 自身が非ASCII・制御文字のときに使う形。エスケープの二重構造
     （GLib 層 + NM 層）を踏まずに済むので、`\` を含む SSID や末尾空白もここへ落とす。

> 補足: 末尾空白は NM も文字列形式で書くが、エディタや `sed` が消しやすいので、こちらは
> バイト列形式に倒す。どちらでも NM は同じ値として読む（上の読み手確認）。
> バイト列形式を手書きして NM が読み戻せることは、実装時に下記テスト A-R6 で必ず確認すること
> （設計時には安全フックにより `nmcli --offline modify` の追加実行を1回見送った）。

### 1.3 実装の形 — 共通化するか

**別々の実装のまま、同じ規則と同じテストベクタで揃える。**

- bash → python を呼ぶ共通化（`prepare-child-sd.sh` から `python3 -m fleet_ui...`）は、SD 準備が
  `sudo env ... bash` で再実行される経路で `PYTHONPATH`/venv が変わり、壊れ方が増える。
- python → bash を呼ぶのは `take_child` が PSK を argv に載せない設計（標準入力で渡す）と相性が悪い。
- 規則は短い（2〜3行の置換 + 1分岐）ので、二重実装のコストより「呼び出し経路が1本増える」
  リスクの方が大きい。
- **テストベクタを1箇所に置く**: `scripts/tests/nm_vectors.py`（SSID/PSK の組と、拒否されるべき値の
  一覧）。bash 側テストと python 側テストが同じ一覧を回す。規則がずれれば片方が落ちる。

#### 擬似コード

`scripts/prepare-child-sd.sh`:

```diff
-# NetworkManager の keyfile は未引用だと # 以降がコメントになる。
-# 引用符とバックスラッシュだけエスケープして二重引用符で囲む。
-child_sd_nm_quote() {
-    local v="${1:-}"
-    v="${v//\\/\\\\}"
-    v="${v//\"/\\\"}"
-    printf '"%s"' "$v"
-}
+# NetworkManager の keyfile(GLib key-file) は引用符を特別扱いしない。囲むと引用符ごと
+# SSID/PSK になり、子は存在しない AP を探し続ける(2026-09-25 に発症)。値の途中の # は
+# コメントにならない。書式は nmcli --offline connection add の出力に合わせる。
+child_sd_psk_valid() {        # 0=OK。WPA: 表示可能ASCII 8..63 か 16進64
+    local v="$1"
+    [[ ${#v} -eq 64 && "$v" =~ ^[0-9A-Fa-f]+$ ]] && return 0
+    (( ${#v} >= 8 && ${#v} <= 63 )) || return 1
+    LC_ALL=C; [[ "$v" =~ ^[\ -~]+$ ]]
+}
+child_sd_nm_escape_psk() {    # \ → \\ 、先頭の空白 → \s
+    local v="$1" lead=""
+    v="${v//\\/\\\\}"
+    while [ "${v:0:1}" = " " ]; do lead+='\s'; v="${v:1}"; done
+    printf '%s%s' "$lead" "$v"
+}
+child_sd_nm_ssid() {          # 文字列形式か、NM と同じバイト列形式
+    local v="$1" n
+    n="$(printf '%s' "$v" | LC_ALL=C wc -c)"
+    (( n >= 1 && n <= 32 )) || return 1
+    if LC_ALL=C; [[ "$v" =~ ^[!-~]([\ -~]*[!-~])?$ && "$v" != *\\* ]]; then
+        printf '%s' "${v//;/\\\\;}"
+    else
+        printf '%s' "$v" | od -An -v -tu1 | tr -s ' \n' '\n' | grep . | tr '\n' ';'
+    fi
+}
```

```diff
 child_sd_write_wifi() {
     local root="${1:?}" ssid="${2:?}" psk="${3:?}"
@@
-    qssid="$(child_sd_nm_quote "$ssid")"
-    qpsk="$(child_sd_nm_quote "$psk")"
+    child_sd_psk_valid "$psk" || { echo "このハブの AP パスワードは子Pi が使えない形式です(8〜63文字の半角英数記号)" >&2; return 1; }
+    qssid="$(child_sd_nm_ssid "$ssid")" || { echo "AP 名が長すぎるか空です: 1〜32バイト" >&2; return 1; }
+    qpsk="$(child_sd_nm_escape_psk "$psk")"
+    child_sd_report_legacy_quotes "$dest"   # §1.5
```

（注: `LC_ALL=C; [[ ... ]]` はサブシェル外で LC_ALL を変えるので、実装では関数内 `local LC_ALL=C` にする。）

`fleet_ui/migrate.py`:

```diff
-def nm_quote(value: str) -> str:
-    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
+_PRINTABLE = re.compile(r"^[\x20-\x7e]+$")
+_HEX64 = re.compile(r"^[0-9A-Fa-f]{64}$")
+
+
+def psk_valid(psk: str) -> bool:
+    """WPA-PSK の規則。外れた値は AP 側でも使えないので書く前に止める。"""
+    return bool(_HEX64.match(psk)) or (8 <= len(psk) <= 63 and bool(_PRINTABLE.match(psk)))
+
+
+def nm_escape_psk(psk: str) -> str:
+    """keyfile は引用符を特別扱いしない。囲むと引用符ごと PSK になる。"""
+    v = psk.replace("\\", "\\\\")
+    stripped = v.lstrip(" ")
+    return "\\s" * (len(v) - len(stripped)) + stripped
+
+
+def nm_ssid(ssid: str) -> str:
+    """nmcli と同じ: 素直な ASCII は文字列形式、それ以外はバイト列形式。"""
+    raw = ssid.encode("utf-8")
+    if not 1 <= len(raw) <= 32:
+        raise ValueError("ssid length")
+    if (_PRINTABLE.match(ssid) and "\\" not in ssid
+            and ssid[0] != " " and ssid[-1] != " "):
+        return ssid.replace(";", "\\\\;")
+    return "".join(f"{b};" for b in raw)
```

```diff
 def nm_join_keyfile(ssid: str, psk: str) -> str:
@@
         "autoconnect-priority=200\n"
+        # SD 側と同じ。既定の4回で諦めると電源再投入まで戻らない(2026-09-23)。
+        "autoconnect-retries=0\n"
@@
-        f"ssid={nm_quote(ssid)}\n"
+        f"ssid={nm_ssid(ssid)}\n"
@@
-        f"psk={nm_quote(psk)}\n"
+        f"psk={nm_escape_psk(psk)}\n"
```

`take_child()` の PSK 確認 `len(psk) < 8` を `not psk_valid(psk)` に置き換える。

> ついでに見つけたずれ: `nm_join_keyfile()` には `autoconnect-retries=0` が**無い**
> （SD 側にはある）。同じタスクで揃える。

### 1.4 テストの書き換え方 — 読み手で読み戻す

今回の不具合の型は「書いた側のテストが `'ssid="sibling-hub"' in body` で通っていたのに、
実際には効いていなかった」。**文字列一致のテストを捨て、独立した読み手2つで読み戻して
元の SSID/PSK に一致すること**を検証する。

新規ヘルパ `scripts/tests/nm_readback.py`（テスト専用。本番コードからは import しない）:

```python
def nm_read(path) -> dict:
    """NetworkManager 自身の読み手: nmcli --offline connection modify に読ませ、
    正規化して書き戻された [wifi] ssid / [wifi-security] psk の行を返す。
    nmcli が無い・--offline 非対応なら pytest.skip(理由つき)。"""

def nm_canonical(ssid, psk) -> dict:
    """同じ nmcli に元の値を直接渡した(add)ときの ssid / psk の行。"""

def glib_read(path) -> dict:
    """GLib.KeyFile(/usr/bin/python3 の gi をサブプロセスで)で get_string した値。
    venv には gi が無いのでサブプロセスにする。"""

def assert_roundtrip(path, ssid, psk):
    assert nm_read(path) == nm_canonical(ssid, psk)          # NM が同じ値として読む
    g = glib_read(path)
    assert g["psk"] == psk                                    # GLib 層で PSK が一致
    if 文字列形式: assert unescape_nm_semicolon(g["ssid"]) == ssid
    else:          assert bytes(int(x) for x in g["ssid"].split(";") if x) == ssid.encode()
```

- `nmcli --offline` は**システムの接続を変更しない**（標準入力を読み、標準出力へ書くだけ。
  sudo 不要、この機体で確認）。ただしフック等で止まる環境があるので、ヘルパは
  失敗時に `pytest.skip("nmcli --offline が使えない")` とし、**GLib 側の検証は独立に走らせる**
  （片方が skip してももう片方で落とせる）。
- skip が常態化すると検出できない。`nm_readback` が skip した回数を `-rs` で見えるようにし、
  Pi 上で走らせる前提をテストの docstring に書く。

### 1.5 既存の引用符つきプロファイルの扱い — 検出・修復・報告

| 置き場所 | 状態 | 扱い |
|---|---|---|
| SD（`prepare-child-sd.sh` を再実行） | `presence-hub-join.nmconnection` がある | **検出して上書き修復し、表示する**。関数 `child_sd_report_legacy_quotes` が書き込み前の旧ファイルの `ssid=`/`psk=` が `"` で始まり `"` で終わるかを見て、該当すれば「以前の書式（引用符つき）で書かれていたので直しました。この子は以前このハブに繋がれなかったはずです」と出す。修復自体は、書き込みが常に全体を上書きするので追加処理は要らない |
| 稼働中の子（`take_child` を再実行） | 旧親の AP にいる子 | 再実行で上書きされ直る。**ただし**旧コードで `take_child` 済みの子は、`WIFI_INSTALL` が他のプロファイルの自動接続を切ったうえで引用符つきに切り替えようとして失敗している可能性が高く、**どこにも繋がらない孤立状態**になっている。これは SSH で届かないので **SD 経路でしか直せない**（資料に明記） |
| 稼働中で、このハブに繋がっている子 | 別名のプロファイルで繋がっており、引用符つき `presence-hub-join` が優先度200で残っている | 害は「毎回先に失敗する接続を試すので再接続が遅い」程度。**検出して報告だけ**（B2 の `child_cli.py align` が各子の `presence-hub-join` の ssid 行を読み、引用符つきなら報告。自動修復はしない — PSK をフリート全体へ流す操作を増やさないため） |

> 付随リスク（同じタスクで直すことを推奨）: `WIFI_INSTALL` は「他のプロファイルの自動接続を切る」→
> 「`connection up`」の順。up が失敗すると子は孤立する。順序を「up 成功を確認してから他を切る」に
> 変え、失敗時は `presence-hub-join` の自動接続を切って旧プロファイルを `up` し直す。SSH 切断で
> シェルが止まらないよう、スクリプト本体を `sudo systemd-run --unit=presence-hub-join-switch --collect`
> で子の上で独立に走らせる。テスト A-M5 参照。

---

## 2. AP_GW_IP

### 2.1 どちらの資料が正しいか

**`docs/NEW-HUB-SETUP.md` §3.1（同じ 10.42.0.1 でよい）が正しく、`site.env.example` の
「増設: SSID と AP_GW_IP を必ず別にし」は誤り**。

- 各ハブの子AP は `ipv4.method shared` の独立したホットスポットで、AP同士は L2 でも L3 でも
  繋がっていない。子は自分が繋いだ AP の `10.42.0.1` に送るので、ハブ2台が同じ値でも衝突しない。
- 衝突するのは **同じ機械の中**で、AP のサブネットが別のインターフェースの経路・アドレスと
  重なったときだけ。今回は wlan0 が 1号機の子AP（10.42.0.0/24）に居るまま、wlan1 で
  10.42.0.1/24 を名乗ったため、1号機宛の返信が wlan1 側に出ていった。
  これは「2台目だから」ではなく「この機械自身が他の 10.42.0.0/24 に居たから」起きた。
  同じことは、工場網や VPN・docker のブリッジが偶然 AP と同じ範囲だった場合にも起きうる
  （AP_GW_IP を 10.42.1.1 に変えても、他の接続が 10.42.1.0/24 なら同じ事故になる）。

**問題になる条件（これが検出条件そのもの）**: AP を起動する時点で、`AP_IF` 以外の
インターフェースに、`AP_GW_IP/24` と重なる IPv4 経路（`default` を除く）またはアドレスがある。

### 2.2 方針の決定: 既定を継承し、重なりを検出して止める

| 案 | 評価 |
|---|---|
| 常に別の値を強制 | ✗ ハブごとに子の送信先2か所（JSON + MQTT_HOST）を揃える手間と、揃え漏れ＝**無警告の送信停止**という別の事故源をフリート全体に持ち込む。しかも別の値にしても他の接続と重なれば同じ事故が起きるので、本質を防がない |
| 既定を継承し、重なりを検出して止める | ✓ 事故の本質（AP 起動で自機の別経路と重なり、無警告で切れる）を**値に関係なく**止める。通常の現場では何も変わらない |

→ **後者を採る**。そのうえで、非既定の値を選んだ現場のために §2.4 の自動反映を用意する。

### 2.3 フェーズ50の起動前チェック

新規ファイル `scripts/lib/ap-subnet.sh`（50-ap.sh とウィザードの両方が source する。
`ip` コマンドの出力を関数の引数/標準入力から受け取れる形にしてテストで偽の出力を流せるようにする）:

```bash
# 10進整数化とプレフィックス重なり判定。どちらかの範囲がもう片方を含めば重なり。
_ap_ip2int() { local IFS=.; set -- $1; echo $(( ($1<<24)|($2<<16)|($3<<8)|$4 )); }
ap_cidr_overlaps() {            # ap_cidr_overlaps 10.42.0.1/24 10.42.0.0/24
    local a="${1%/*}" al="${1#*/}" b="${2%/*}" bl="${2#*/}" l m
    l=$(( al < bl ? al : bl ))
    m=$(( l == 0 ? 0 : (0xFFFFFFFF << (32 - l)) & 0xFFFFFFFF ))
    (( ($(_ap_ip2int "$a") & m) == ($(_ap_ip2int "$b") & m) ))
}

# 重なる相手を "dev cidr" で1行ずつ出す。何も出なければ安全。
# 入力: $1=AP_GW_IP $2=AP_IF。経路は `ip -4 -o route show table main`、
# アドレスは `ip -4 -o addr show` を読む(AP_SUBNET_IP_CMD で差し替え可、テスト用)。
ap_subnet_conflicts() {
    local gw="$1" apif="$2" net="$1/24" dst dev
    "${AP_SUBNET_IP_CMD:-ip}" -4 -o route show table main | while read -r dst rest; do
        [ "$dst" = default ] && continue
        dev="$(sed -n 's/.* dev \([^ ]*\).*/\1/p' <<<" $rest")"
        [ "$dev" = "$apif" ] && continue            # 自分の AP の経路(再実行時)は除く
        [[ "$dst" == */* ]] || dst="$dst/32"
        ap_cidr_overlaps "$net" "$dst" && echo "$dev $dst"
    done
    "${AP_SUBNET_IP_CMD:-ip}" -4 -o addr show | awk '{print $2, $4}' | while read -r dev cidr; do
        [ "$dev" = "$apif" ] || [ "$dev" = lo ] && continue
        ap_cidr_overlaps "$net" "$cidr" && echo "$dev $cidr"
    done | sort -u
}

# 空いている 10.42.N.1 を1つ返す(N=0..254)。候補提示用。
ap_suggest_free_gw() { local n; for n in $(seq 0 254); do
    [ -z "$(ap_subnet_conflicts "10.42.$n.1" "$1")" ] && { echo "10.42.$n.1"; return 0; }
done; return 1; }
```

`scripts/bootstrap/50-ap.sh`:

```diff
 main() {
     site_env_require
+    local conflicts
+    conflicts="$(ap_subnet_conflicts "${AP_GW_IP:-10.42.0.1}" "$AP_IF")"
+    if [ -n "$conflicts" ]; then
+        ap_print_overlap_message "$conflicts" "$(ap_suggest_free_gw "$AP_IF")" >&2
+        return 1            # 上書き用のフラグは作らない。重なった状態は常に誤り
+    fi
     local plan
     plan="$(ap_plan "$@")"
```

- skip（自APが既に動いている）でも**先に**このチェックを通す。既に重なっているなら壊れている
  最中なので、止めて知らせる方が正しい。
- `--force` / `AP_FORCE=1` でもこのチェックは越えない。
- 起動**後**の確認も足す（「起動できた」と「効いている」は別物）: `ip -4 -o addr show dev $AP_IF` に
  `$AP_GW_IP/24` があることを確認し、無ければ失敗で返す（現状は既定値のとき何も確認していない）。

#### 利用者向けメッセージ案（`ap_print_overlap_message`）

```text
✖ 子Pi用の Wi-Fi(AP) を起動しません。
  この機械の別の接続と、AP のアドレスの範囲が重なっています。

  AP の予定        : wlan1  10.42.0.1  (10.42.0.0 〜 10.42.0.255)
  重なっている接続 : wlan0  10.42.0.0/24

  このまま起動すると、この機械は 10.42.0.x 宛ての通信をすべて「自分宛て」と
  取り違え、wlan0 の先の相手(遠隔作業の中継に使っている別のハブなど)と
  ping も SSH も通じなくなります。Wi-Fi の表示は「接続中」のままなので、
  見た目では気づけません(2026-09-25 に再起動3回を要した事故と同じ形)。

  直し方(どちらか一つ):
    1) 別のハブの Wi-Fi を中継に使っているだけなら、作業を工場網に切り替えてから
       もう一度実行してください。子の設定は何も変わりません。
    2) このハブの AP のアドレスを変えます。空いている候補: 10.42.1.1
         site.env の AP_GW_IP=10.42.1.1 に書き換えて
         bash scripts/bootstrap-hub.sh 50 70
       このハブに子を付けるときの送信先は、「子をこのハブへ付ける」と
       「子SDをこのハブ用にする」が自動で 10.42.1.1 に揃えます。
```

ウィザード（`setup-hub-wizard.sh`）でも、確認画面の直前に同じ `ap_subnet_conflicts` を呼び、
重なっていれば上のメッセージの 2) を「候補 10.42.1.1 を使いますか [Y/n]」として聞く
（ウィザードは AP_GW_IP を聞かない現状を保ち、**重なるときだけ**聞く）。
ウィザード時点と フェーズ50時点で状態が変わることがあるので、フェーズ50のチェックは省かない。

> 残るリスク（範囲外として記録）: AP 起動**後**に wlan0 が別ハブの子AP に自動で繋ぎ直すと
> （中継用に作った `presence-hub` のプロファイルが自動接続のまま残っている等）、同じ事故が
> 後から起きる。フェーズ50では防げない。§3.15 に「中継用プロファイルは作業後に
> `connection.autoconnect no` にする」を追記し、`fleet-status` 等での常時監視は別タスクとする。

### 2.4 子の送信先を自動で揃える

#### 揃える対象は2つ（設計時に判明）

| 送信経路 | 送信先の読み元 | 反映 |
|---|---|---|
| カメラ内蔵の送信（`web_server.py` の自動送信） | `~/send_target_config.json` の `host` | 送信のたびに読み直す → **ファイルを書けば再起動不要** |
| `child-csv-to-mqtt.py`（ACK 付きの記録送信） | 環境変数 `MQTT_HOST`（既定 `10.42.0.1`）。**JSON は読まない** | systemd drop-in `/etc/systemd/system/child-csv-to-mqtt.service.d/10-hub-gw.conf` に `Environment=MQTT_HOST=...` を置き、`daemon-reload` + `restart` |

2つ目を揃えないと、非既定のハブでは ACK 付きの経路だけが**黙って届かない**（旧資料の
「JSON の host を変える」だけでは直らない）。子のコード（`child/`）は変えない制約なので drop-in で渡す。
`deploy-child.sh` はユニット本体を上書きするが、`.d/` の drop-in は残る。

**exactly-once への影響**: `DeliveryStore` は `destination = "{MQTT_HOST}:{PORT}/{TOPIC}"` を
主キーに含むので、`MQTT_HOST` を変えると旧 destination の未ACK行は見えなくなる。
outbox に残っている CSV は同じ `event_id` で新 destination から再送され、Oracle の MERGE が
冪等なので**重複しない**（ON 句・照合キーには触れない）。ACK 済みで archive に移った CSV は
再送されない。よって契約は保たれる。これは実装者が確認するためにテスト B2-T9 で残す。

**方針: 付け替えのたびに、付け替え先ハブの値へ「常に」揃える**（非既定のときだけではない）。
これで「既定へ戻す」「別のハブへ移す」も同じ処理になる。

- 値の出どころ: ハブの `site.env` の `AP_GW_IP`、無ければ `10.42.0.1`。
  新関数 `load_ap_gw_ip(repo)`（python）/ `child_sd_hub_gw_ip`（bash）。
- 既定（10.42.0.1）のとき: JSON の `host` を `10.42.0.1` にし、drop-in を**削除**する。
- 非既定のとき: JSON の `host` を値にし、drop-in を書く。
- JSON は `host` 以外のキー（`password` を含む）を変えない。壊れた JSON は上書きせずエラー。
  ファイルが無ければ `{"host": gw}` だけで作る（`enabled` は既定の false のまま＝勝手に送信を始めない）。

#### (i) `fleet_ui/migrate.py` の `take_child()`（稼働中の子へ SSH）

```diff
     inv_name = inventory_name(entry)
     kh = register_host_key(...)
@@
     added = add_to_inventory(inv_name, path=inv_file)
     if not added.ok:
         return added
+
+    # 子はもうこのハブの AP にいる。旧親経由ではなくこのハブから直接 SSH する。
+    gw = load_ap_gw_ip(repo)
+    if _IPV4_RE.match(ip) and not ip_in_subnet(ip, gw, 24):
+        return StepResult(ok=False, message=(
+            f"{entry} の IP {ip} がこのハブの AP の範囲({gw}/24)にありません。"
+            " site.env の AP_GW_IP と実際の AP が食い違っています。"
+            " bash scripts/bootstrap-hub.sh 50 をやり直してください"))
+    aligned = align_send_target(ip if _IPV4_RE.match(ip) else inv_name, gw, runner=runner)
+    if not aligned.ok:
+        return StepResult(ok=False, message=(
+            f"{entry} はこのハブへ移りましたが、記録の送り先を {gw} に揃えられませんでした。"
+            " このままでは記録が届きません。"
+            f" python3 -m fleet_ui.child_cli align {inv_name} を実行してください。"
+            f" {aligned.message}"), output=aligned.output)
```

新規モジュール `fleet_ui/send_target.py`（B2 の所有。`migrate.py` と `provision.py` から使う）:

```python
DEFAULT_GW = "10.42.0.1"
DROPIN = "/etc/systemd/system/child-csv-to-mqtt.service.d/10-hub-gw.conf"

def load_ap_gw_ip(repo: Path | None = None) -> str:
    """ハブの site.env の AP_GW_IP。無ければ既定。IPv4 でなければ ValueError。"""

def align_remote_script(gw: str) -> str:
    """子の上で走る bash。gw は呼ぶ前に IPv4 検証済み(shlex.quote もする)。
    1) python3 で ~/send_target_config.json の host だけ置き換え(一時ファイル→os.replace、
       元のモードを保つ、壊れた JSON なら exit 3)
    2) gw==既定なら drop-in を rm、違えば [Service]\\nEnvironment=MQTT_HOST=<gw> を install -m 644
    3) systemctl daemon-reload; systemctl restart child-csv-to-mqtt
    4) 読み手側で確認して出力:
         HOST_NOW=<json の host を読み直した値>
         ENV_NOW=<systemctl show -p Environment child-csv-to-mqtt>
         ACTIVE_NOW=<systemctl is-active child-csv-to-mqtt>"""

def align_send_target(host: str, gw: str, *, runner) -> StepResult:
    """書いた側の終了コードではなく、読み戻した HOST_NOW / ENV_NOW / ACTIVE_NOW で判定する。
    ok = HOST_NOW==gw かつ (gw==既定 なら ENV_NOW に MQTT_HOST が無い
                           か MQTT_HOST=既定; 非既定なら MQTT_HOST=gw を含む) かつ ACTIVE_NOW==active"""
```

#### (ii) `scripts/prepare-child-sd.sh`（SD の `/home/pi/send_target_config.json`）

```diff
 child_sd_prepare() {
     local root="${1:?}" pub="${2:?}" ssid="${3:?}" psk="${4:?}"
+    local gw="${5:-10.42.0.1}"
     child_sd_is_child_root "$root" || return 1
     child_sd_install_pubkey "$root" "$pub" || return 1
     child_sd_write_wifi "$root" "$ssid" "$psk" || return 1
     child_sd_disable_other_wifi "$root" || return 1
+    child_sd_align_send_target "$root" "$gw" || return 1
 }
+
+# 送り先を付け替え先ハブの AP_GW_IP に揃える。host 以外のキー(password を含む)は変えない。
+child_sd_align_send_target() {
+    local root="$1" gw="$2" f="$1/home/pi/send_target_config.json"
+    local dropin="$1/etc/systemd/system/child-csv-to-mqtt.service.d/10-hub-gw.conf"
+    _site_env_ipv4_octets_valid "$gw" || { echo "AP_GW_IP が不正です: $gw" >&2; return 1; }
+    python3 - "$f" "$gw" <<'PY' || return 1
+import json, os, sys, tempfile
+p, gw = sys.argv[1], sys.argv[2]
+try:
+    with open(p, encoding="utf-8") as fh: cfg = json.load(fh) or {}
+    mode, uid, gid = (lambda s: (s.st_mode & 0o777, s.st_uid, s.st_gid))(os.stat(p))
+except FileNotFoundError:
+    cfg, mode = {}, 0o644; uid = gid = os.stat(os.path.dirname(p)).st_uid
+except json.JSONDecodeError:
+    sys.exit("send_target_config.json が壊れているので書き換えません")
+old = cfg.get("host", "")
+cfg["host"] = gw
+fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p))
+with os.fdopen(fd, "w", encoding="utf-8") as fh: json.dump(cfg, fh, ensure_ascii=False, indent=2)
+os.chmod(tmp, mode); os.chown(tmp, uid, gid); os.replace(tmp, p)
+print(f"  記録の送り先: {old or '(未設定)'} → {gw}")
+PY
+    if [ "$gw" = 10.42.0.1 ]; then rm -f "$dropin"
+    else mkdir -p "$(dirname "$dropin")"
+         printf '[Service]\nEnvironment=MQTT_HOST=%s\n' "$gw" > "$dropin"; chmod 644 "$dropin"
+    fi
+}
```

`main()` は `gw="$(child_sd_hub_gw_ip "$PREPARE_REPO_DIR")"`（`site.env` の `AP_GW_IP`、無ければ既定）を
読み、`sudo env` 再実行にも引数で渡す（`site.env` は再実行後も同じリポジトリから読めるが、
値を1回だけ読んで渡す方が食い違いが起きない）。

#### (iii) それ以外に必要な経路

| 経路 | 必要な変更 |
|---|---|
| `fleet_ui/provision.py` `adopt_keeping_identity()` | 既にこのハブの AP にいる子の取り込み。インベントリ追加後に `align_send_target(ip, gw)`。SD を旧コードで準備した子や、手で繋いだ子もここで揃う |
| `fleet_ui/provision.py` `register_new_child()` | クローン増設。クローン元の子の送り先を引き継いでいるので同じく揃える（再起動・復帰の後、インベントリ追加の後） |
| `fleet_ui/child_cli.py` 新サブコマンド `align [entry...]` | 既存の子（今日手で 10.42.1.1 にした 003 など）を後から揃える/点検する。引数なしならインベントリ全台。**報告も兼ねる**: 各子の `HOST_NOW`/`ENV_NOW` と `presence-hub-join` の ssid 行が引用符つきか（§1.5）を表で出す |
| `scripts/deploy-child.sh --with-shared-config` | §2.5 |

逆方向（既定のハブへ戻す・別ハブへ移す）は、**付け替え先のハブ**で上の経路を実行すれば
その値（既定なら drop-in 削除 + host=10.42.0.1）に揃う。旧ハブ側で何かを戻す必要はない。

### 2.5 `--with-shared-config` との干渉

リポジトリの `child/send_target_config.json` は `host: 10.42.0.1` を持つ。`deploy-child.sh` は
これを子の `~/` へ **rsync でそのまま上書き**するので、非既定ハブの子の host が 10.42.0.1 に戻り、
カメラ内蔵の送信が黙って止まる（**干渉する**）。

決定: 配る前に一時ディレクトリへ複製し、`host` をハブの `AP_GW_IP` に書き換えたものを rsync する。

```diff
 files=("${CHILD_CODE_FILES[@]}")
 [ "$WITH_SHARED" -eq 0 ] || files+=("${CHILD_SHARED_CONFIG_FILES[@]}")
+SRC_DIR="$CHILD_SRC"
+if [ "$WITH_SHARED" -eq 1 ]; then
+    # host はフリート共通ではなく「どのハブの AP にいるか」で決まる値。
+    SRC_DIR="$(mktemp -d)"; trap 'rm -rf "$SRC_DIR"' EXIT
+    cp -a "${files[@]/#/$CHILD_SRC/}" "$SRC_DIR/"
+    render_send_target_host "$SRC_DIR/send_target_config.json" "$(hub_ap_gw_ip "$REPO_DIR")"
+fi
@@
-( cd "$CHILD_SRC" && rsync -a --info=stats0 "${files[@]}" "$CHILD_SSH:~/" )
+( cd "$SRC_DIR" && rsync -a --info=stats0 "${files[@]}" "$CHILD_SSH:~/" )
```

`MQTT_HOST` の drop-in は `deploy-child.sh` が触らない（ユニット本体を置くだけ）ので干渉しない。
（既存の別問題として、共通ファイルの上書きは子の `password` も空にする。今回は範囲外だが
資料に一行残す。）

---

## 3. 資料の一貫性 — 直す箇所

| ファイル・箇所 | 現状 | 直し方 |
|---|---|---|
| `site.env.example` 42行 | 「増設: SSID と AP_GW_IP を必ず別にし、子の send_target_config.json も変える」 | 「増設: AP_SSID は必ず別にする。AP_GW_IP は既定のままでよい(AP同士は孤立)。この機械の別の接続が同じ範囲にあるときだけ変える(フェーズ50が検出して止め、候補を出す)。変えた場合の子の送り先は付け替えツールが揃える」 |
| `docs/NEW-HUB-SETUP.md` §3.1 表 `AP_SSID / AP_GW_IP` 行 | 「GW は同じ 10.42.0.1 でよく、ウィザードも親の値を継承する」 | 内容は正しいので残し、「ただしこの機械の別の接続と範囲が重なる場合は別値（フェーズ50 とウィザードが検出）。別値の子の送り先は自動で揃う（JSON の host と MQTT_HOST の2か所）」を足す |
| `docs/NEW-HUB-SETUP.md` §3.15 AP_GW_IP の項 | 症状が起きてから手で直す手順 | 「フェーズ50が起動前に検出して止める」に書き換え、症状の説明（`station dump` で関連付けは生きている）は診断用に残す。**中継用プロファイルを作業後に自動接続オフにする**を追記 |
| `docs/NEW-HUB-SETUP.md` §3.15（新規の項） | — | 引用符つき `presence-hub-join` の見分け方（`sudo grep -E '^(ssid|psk)="' /etc/NetworkManager/system-connections/presence-hub-join.nmconnection`）と、孤立した子は SD 経路でしか直せないこと |
| `scripts/bootstrap/50-ap.sh` 28-30行コメント | 「=増設時。子側の send_target_config.json の変更も必要」 | 「既定以外は、自機の別経路と重なる現場だけ。子の送り先は付け替えツールが揃える」 |
| `scripts/bootstrap/50-ap.sh` 94行（同名AP検出時） | 「増設なら、site.env の AP_SSID と AP_GW_IP を別の値に」 | 「増設なら、site.env の AP_SSID を別の値に」（AP_GW_IP を外す） |
| `scripts/bootstrap/50-ap.sh` 110-111行（非既定の警告） | 「各子Pi の ~/send_target_config.json の "host" を変更する必要があります」 | 「このハブに付ける子の送り先（JSON の host と MQTT_HOST）は『子をこのハブへ付ける』『子SDをこのハブ用にする』が揃えます。既に付いている子は `python3 -m fleet_ui.child_cli align` で揃えてください」 |
| `docs/child-migration.md` §7 の1・2 | 「AP_SSID と AP_GW_IP を現行機と別の値に」「子の host を手で変更」 | 1 は AP_SSID のみ必須に。2 は「送り先は付け替え時に自動で揃う。手で変えない（2か所あり、片方だけ変えると ACK 経路が止まる）」 |
| `docs/child-migration.md` 9-10行 | 引っ越し時の説明 | 変更不要（引っ越しは同じ値にする、は正しい） |
| `scripts/make_hub_child_pptx.py`「ハブはいつも 10.42.0.1」 | — | 「ハブは既定 10.42.0.1」。優先度低、資料再生成時に |

---

## 4. 実装の分割とファイル所有範囲

重なるファイル: `scripts/prepare-child-sd.sh`, `fleet_ui/migrate.py`（とそのテスト）は 1（引用符）と
2（送り先を揃える）の両方が触る。→ **引用符（A）を先に**。理由: 実機で発症済みで子が孤立する
不具合であり、2 の変更は A で整えた `child_sd_prepare` / `take_child` の形の上に載る。

| タスク | 並行性 | 所有ファイル |
|---|---|---|
| **A: 引用符の修正** | B1 と並行 | `scripts/prepare-child-sd.sh`（1.3 と 1.5 の範囲のみ）, `fleet_ui/migrate.py`（`nm_*`/`psk_valid`/`nm_join_keyfile`/`WIFI_INSTALL`/`take_child` の PSK 検証のみ）, `scripts/tests/test_prepare_child_sd.py`, `fleet_ui/tests/test_migrate.py`, 新規 `scripts/tests/nm_readback.py`, 新規 `scripts/tests/nm_vectors.py` |
| **B1: 重なり検出・配布・資料** | A と並行 | 新規 `scripts/lib/ap-subnet.sh`, `scripts/bootstrap/50-ap.sh`, `scripts/tests/test_bootstrap_ap.py`, 新規 `scripts/tests/test_ap_subnet.py`, `scripts/setup-hub-wizard.sh`（重なり時の確認のみ）, `scripts/tests/test_setup_hub_wizard.py`, `scripts/deploy-child.sh`, `scripts/lib/deploy-common.sh`（`render_send_target_host`/`hub_ap_gw_ip`）, `scripts/tests/test_deploy_child_filelist.py`, `site.env.example`, `docs/NEW-HUB-SETUP.md`, `docs/child-migration.md` |
| **B2: 子の送り先を揃える** | **A のマージ後** | 新規 `fleet_ui/send_target.py`, 新規 `fleet_ui/tests/test_send_target.py`, `fleet_ui/migrate.py`（`take_child` 末尾）, `fleet_ui/provision.py`, `fleet_ui/tests/test_provision.py`, `fleet_ui/child_cli.py`, `fleet_ui/tests/test_child_cli.py`, `scripts/prepare-child-sd.sh`（`child_sd_align_send_target` と `main`）, `scripts/tests/test_prepare_child_sd.py` |

- B1 の資料は B2 の CLI 名（`python3 -m fleet_ui.child_cli align`）を先に書く。名前はこの設計で固定する。
- B1 と B2 の境界: `hub_ap_gw_ip`（bash, deploy-common）と `child_sd_hub_gw_ip`（bash, prepare）と
  `load_ap_gw_ip`（python）は同じ規則（`site.env` の `AP_GW_IP`、無ければ既定、IPv4 検証）。
  共通の小テスト表を両方で回す（B1-T11 / B2-T1）。

---

## 5. テストケース一覧（TDD でそのまま着手できる形）

### A: 引用符（すべて読み戻しで検証。`assert 'ssid=...' in body` 型は使わない）

| ID | ファイル | 検証すること |
|---|---|---|
| A-R1 | test_prepare_child_sd | `child_sd_write_wifi` で `sibling-hub` / `ap-secret9` を書く → `nm_read` が `nm_canonical` と一致し、GLib で読んだ PSK が `ap-secret9` |
| A-R2 | test_prepare_child_sd | `nm_vectors.OK` の全組（`#` `;` `\` `=` `"` `'` 先頭空白1・2個・末尾空白・64桁16進）を書く → NM と GLib の読み戻しが元の値と一致（旧 `test_prepare_quotes_hash_in_psk_so_nm_does_not_truncate` を置き換え、名前は `test_prepare_psk_with_hash_reads_back_intact`） |
| A-R3 | test_prepare_child_sd | 書かれた `ssid=` / `psk=` の行が `"` で始まらない（引用符の再発防止。読み戻しの補助） |
| A-R4 | test_prepare_child_sd | SSID `工場-hub` → バイト列形式で書かれ、NM 読み戻しが `nm_canonical` と一致 |
| A-R5 | test_prepare_child_sd | SSID `a;b`、`a\b`、末尾空白 → NM 読み戻し一致（`a\b` と末尾空白はバイト列形式に落ちる） |
| A-R6 | test_prepare_child_sd | 手書きのバイト列形式 `ssid=116;114;97;105;108;32;` を NM が `trail ` として読む（ヘルパ自体の前提確認） |
| A-R7 | test_prepare_child_sd | `nm_vectors.BAD_PSK`（7文字、64文字超、非ASCII、TAB、64桁だが16進でない）→ 非0終了、ファイルを作らない/前のファイルを壊さない、エラー文に PSK 値を含まない |
| A-R8 | test_prepare_child_sd | 33バイトの SSID、空 SSID → 非0終了 |
| A-R9 | test_prepare_child_sd | 旧書式（`ssid="x"`）のファイルがある SD に書く → 上書きされ、出力に「以前の書式（引用符つき）」が出る。旧書式が無ければ出ない |
| A-R10 | test_prepare_child_sd | 既存 `test_sd_wifi_profile_never_gives_up_reconnecting` は維持（`autoconnect-retries=0` を NM 読み戻しで確認する形に直す） |
| A-M1 | test_migrate | `nm_join_keyfile(ssid, psk)` を一時ファイルに書き、`nm_vectors.OK` 全組で NM/GLib の読み戻しが一致（A-R2 と同じ表） |
| A-M2 | test_migrate | `nm_join_keyfile` を NM が読むと `autoconnect-retries=0` が入っている |
| A-M3 | test_migrate | `nm_ssid` / `nm_escape_psk` が bash 版と同じ出力になる（`nm_vectors` 全組で bash 関数を呼んで比較。二重実装のずれ検出） |
| A-M4 | test_migrate | `take_child` は PSK が `BAD_PSK` のとき SSH を1回も呼ばずに失敗し、メッセージに PSK を含まない |
| A-M5 | test_migrate | `WIFI_INSTALL` の文字列で、他プロファイルの自動接続オフが `connection up` 成功の**後**にあり、失敗時に `presence-hub-join` の自動接続を切る分岐がある（推奨の付随修正を採る場合） |
| A-M6 | test_migrate | 既存 `test_take_child_installs_key_switches_wifi_keeps_identity` の「`sibling-hub` が標準入力に出る」確認は、標準入力の keyfile を NM で読み戻して SSID が `sibling-hub` になる確認に置き換える |

### B1: 重なり検出・配布

| ID | ファイル | 検証すること |
|---|---|---|
| B1-T1 | test_ap_subnet | `ap_cidr_overlaps 10.42.0.1/24 10.42.0.0/24` 真、`10.42.1.1/24` と `10.42.0.0/24` 偽、`/16` が `/24` を含む場合 真、`/32` 1個 真/偽 |
| B1-T2 | test_ap_subnet | 偽の `ip` 出力 = 今回の事故の状態（wlan0 `10.42.0.0/24`、wlan1 なし）→ `ap_subnet_conflicts 10.42.0.1 wlan1` が `wlan0 10.42.0.0/24` を出す |
| B1-T3 | test_ap_subnet | この機体と同じ状態（wlan1 に自AP `10.42.0.0/24`、wlan0 `192.168.128.0/24`、docker 172.17-19）→ 何も出ない（自分の AP の経路は除外） |
| B1-T4 | test_ap_subnet | `default via ...` の行は重なりに数えない |
| B1-T5 | test_ap_subnet | docker ブリッジ `10.42.0.0/16 linkdown` → 重なりとして出る |
| B1-T6 | test_ap_subnet | `ap_suggest_free_gw wlan1` が B1-T2 の状態で `10.42.1.1` を返す |
| B1-T7 | test_bootstrap_ap | 重なりがあると `main` が非0で終わり、`setup-dongle-ap.sh` を呼ばない（偽 bin の呼び出し記録で確認） |
| B1-T8 | test_bootstrap_ap | 重なりがあると `--force` でも `AP_FORCE=1` でも止まる |
| B1-T9 | test_bootstrap_ap | 自APが動いていて（plan=skip）重なりもある → 非0で止まり、メッセージが出る |
| B1-T10 | test_bootstrap_ap | メッセージに、重なっている dev と CIDR、候補 `10.42.1.1`、`bootstrap-hub.sh 50 70` が含まれる |
| B1-T11 | test_bootstrap_ap | AP 起動後に `$AP_IF` に `$AP_GW_IP/24` が付いていなければ非0（既定値のときも確認する） |
| B1-T12 | test_bootstrap_ap | 同名AP検出時のメッセージが `AP_GW_IP` を別にせよと言わない |
| B1-T13 | test_setup_hub_wizard | 重なりがあるときだけ AP_GW_IP の候補を聞き、承諾で site.env に候補が入る。重なりが無ければ聞かず既定を継承 |
| B1-T14 | test_deploy_child_filelist | `--with-shared-config` で rsync される `send_target_config.json` の `host` が site.env の `AP_GW_IP`（10.42.1.1）になっている。`child/send_target_config.json` 本体は変わらない |
| B1-T15 | test_deploy_child_filelist | `--with-shared-config` 無しでは `send_target_config.json` を配らない（既存の確認を維持） |
| B1-T16 | test_deploy_child_filelist | `hub_ap_gw_ip`: site.env に値あり → その値、無し → 10.42.0.1、不正 → 非0 |

### B2: 子の送り先を揃える

| ID | ファイル | 検証すること |
|---|---|---|
| B2-T1 | test_send_target | `load_ap_gw_ip`: site.env に値あり/無し/不正（B1-T16 と同じ表） |
| B2-T2 | test_send_target | `align_remote_script("10.42.1.1")` を一時 HOME で bash 実行（`sudo`/`systemctl` は偽 bin）→ JSON の `host` だけが `10.42.1.1` になり、`password` などの他キーとファイルモードが保たれる |
| B2-T3 | test_send_target | 同、既定 `10.42.0.1` → drop-in が削除される（逆方向） |
| B2-T4 | test_send_target | 同、非既定 → drop-in の中身が `[Service]` + `Environment=MQTT_HOST=10.42.1.1` |
| B2-T5 | test_send_target | 同、壊れた JSON → 上書きせず非0 |
| B2-T6 | test_send_target | `align_send_target` は終了コードでなく読み戻し（`HOST_NOW`/`ENV_NOW`/`ACTIVE_NOW`）で判定する: `HOST_NOW` が違えば失敗、`ENV_NOW` に MQTT_HOST が無ければ（非既定）失敗、`ACTIVE_NOW!=active` で失敗 |
| B2-T7 | test_send_target | gw に IPv4 以外（`10.42.1.1; rm`）を渡すと SSH を呼ばずに ValueError |
| B2-T8 | test_migrate | `take_child` 成功時、`WIFI_INSTALL` の後に**このハブから子の IP へ直接** `align` の SSH が1回出る（旧親経由の入れ子 SSH ではない） |
| B2-T9 | test_migrate | `align` 失敗時、`take_child` は ok=False で「記録が届きません」と `child_cli align` の案内を返す（インベントリには入っている） |
| B2-T10 | test_migrate | 子の IP（wait_fn の出力）が `AP_GW_IP/24` の外 → ok=False、`bootstrap-hub.sh 50` の案内 |
| B2-T11 | test_provision | `adopt_keeping_identity` と `register_new_child` の成功経路で `align` が呼ばれる |
| B2-T12 | test_prepare_child_sd | `child_sd_prepare ... 10.42.1.1` → SD の JSON の `host` が 10.42.1.1、他キー・所有者・モード保持、drop-in が `etc/systemd/system/child-csv-to-mqtt.service.d/10-hub-gw.conf` に書かれる |
| B2-T13 | test_prepare_child_sd | `child_sd_prepare ... 10.42.0.1`（既定へ戻す）→ `host` が 10.42.0.1、既存の drop-in が削除される |
| B2-T14 | test_prepare_child_sd | JSON が無い SD → `{"host": gw}` だけで作られ、`enabled` を足さない |
| B2-T15 | test_prepare_child_sd | `main` が `site.env` の `AP_GW_IP` を読み、`sudo env` 再実行へ引数で渡す（文字列確認でよい。既存 `test_sudo_reexec_passes_pubkey_and_home` と同型） |
| B2-T16 | test_child_cli | `align`（引数なし）がインベントリ全台に対し、`HOST_NOW` と、`presence-hub-join` の ssid 行が引用符つきかを報告し、引用符つきを**修復しない** |
| B2-T17 | test_send_target | （契約の記録）`DeliveryStore` の destination が変わっても、outbox の同じ CSV は同じ `event_id` で再送される — 既存の子側テストの該当箇所を参照するだけのテストでよい（`child/` は変更しない） |

---

## 6. 完了の証拠との対応

| 要求 | 本書の節 |
|---|---|
| (a) 引用符: 決定した書式と根拠（NM 自身の出力） | §1.1, §1.2 |
| (b) AP_GW_IP: 方針・検出条件・メッセージ案 | §2.1〜§2.3 |
| (c) 子の送り先を揃える各経路の擬似コード | §2.4 (i)(ii)(iii), §2.5 |
| (d) テストケース一覧 | §5 |
| (e) 実装タスクの分割とファイル所有範囲 | §4 |
| (f) 既存の引用符つきプロファイルの扱い | §1.5（SD は検出・修復・報告、稼働中は再実行で修復、全体は報告のみ） |

秘密値は本書に書いていない（PSK はすべてダミー。実機の AP パスワードは採取していない）。
