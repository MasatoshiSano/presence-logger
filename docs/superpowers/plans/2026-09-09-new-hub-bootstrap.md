# 新ハブ Pi ブートストラップ 実装プラン

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 空の Raspberry Pi OS が入った Pi 5 を、`git clone` と `sudo bash scripts/bootstrap-hub.sh` の1本で「カメラ無し・ドングルあり の子Pi専用ハブ」として立ち上げられるようにする。

**Architecture:** 機体固有値を `site.env`（Git管理外）へ完全に外出しし、`scripts/bootstrap-hub.sh` が `scripts/bootstrap/NN-*.sh` を番号順に呼ぶフェーズ構成にする。既存の `install.sh` / `setup-dongle-ap.sh` / `setup-autostart.sh` は捨てず、フェーズから呼ぶ被呼び出し側にする。全スクリプトは「関数群 + `main`」に分け、`main` を source ガードで囲むことで、pytest から関数単位で検証できるようにする。

**Tech Stack:** bash / pytest（`scripts/tests/shellhelp.py` の `run_bash` と `fake_bin` フィクスチャ）/ ruff / Docker Compose / NetworkManager (nmcli) / systemd

**Spec:** [`../specs/2026-09-09-new-hub-bootstrap-design.md`](../specs/2026-09-09-new-hub-bootstrap-design.md)

**関連文書:**
- [`../../NEW-HUB-SETUP.md`](../../NEW-HUB-SETUP.md) — Git に載せられない資産の再現手順（運用者向け）
- [`../../sd-clone-pattern-b.md`](../../sd-clone-pattern-b.md) — SDカード複製でハブを作る別経路

## Global Constraints

- 対象環境は **Debian 13 (trixie) / labwc + Wayland / aarch64**。Pi 5。
- **カメラ無し構成**（`HUB_MODE=1`）。detector コンテナは compose の `profiles:` により、明示指定しない限り起動できないこと。
- 機体固有値の置き場は **`site.env` と `wifi-switch.conf` のみ**。どちらも `.gitignore` 対象。ひな型 `*.example` は Git 管理する。
- 秘密情報の置き場は **`/etc/presence-logger/secrets.env`（`600 root:docker`）のみ**。スクリプト・テスト・コミットメッセージ・ログに実値を書かない。
- テストランナーは **pytest に統一**（bats は入れない）。シェルは `scripts/tests/shellhelp.py` の `run_bash` と `conftest.py` の `fake_bin` で検証する。
- 全スクリプトは **関数群 + `main`** に分け、`main` は `[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"` でガードする。テスト専用の環境変数分岐を製品コードに入れないこと。
- **全フェーズは冪等**。2回流しても壊れない。既に済んでいる工程は検出してスキップする。
- ruff 設定は既存のまま（`line-length = 100`、`target-version = "py313"`）。
- コメント・利用者向け出力は**日本語**（既存スクリプトに合わせる）。
- コミットは Conventional Commits。メッセージ末尾に `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` を付ける。

---

## File Structure

### 新規作成

| ファイル | 責務 |
|---|---|
| `site.env.example` | 機体固有値のひな型 |
| `wifi-switch.conf.example` | WiFi切替ランチャー定義のひな型 |
| `scripts/lib/site-env.sh` | `site.env` の読込と検証。**他のどのスクリプトからも source される唯一の入口** |
| `scripts/bootstrap-hub.sh` | フェーズの振り分けのみ。実処理は持たない |
| `scripts/bootstrap/10-japanese-input.sh` | fcitx5 + mozc + jp 配列 |
| `scripts/bootstrap/20-base-packages.sh` | docker / python3-yaml / dkms / venv / ホスト名 / SSH鍵 |
| `scripts/bootstrap/30-dongle-driver.sh` | RTL8811AU の DKMS 導入 |
| `scripts/bootstrap/patches/8821au-add-elecom-056e-4010.patch` | USB ID 追記の差分 |
| `scripts/bootstrap/40-configs.sh` | `site.env` → `/etc/presence-logger/` の生成 |
| `scripts/bootstrap/50-ap.sh` | 子AP 構築（`setup-dongle-ap.sh` を呼ぶ） |
| `scripts/bootstrap/60-stack.sh` | コンテナ起動 + systemd 常駐化 |
| `scripts/bootstrap/70-desktop.sh` | デスクトップ配置 + WiFi切替 + nmcli 播種 |
| `desktop/wifi-switch/switch-wifi.sh` | 保存済み接続への切替（機体非依存） |
| `desktop/wifi-switch/launcher.desktop.tmpl` | 切替ランチャーのテンプレート |
| `desktop/launchers/フリート管理.desktop` | 未追跡だった資産の取り込み |
| `docs/child-migration.md` | 既存の子Pi を新ハブへ引っ越す運用手順 |

### 変更

| ファイル | 変更内容 |
|---|---|
| `.gitignore` | `site.env` / `wifi-switch.conf` を追加 |
| `docker-compose.yml` | detector に `profiles: [camera]` |
| `docker-compose.override.yml` | `10.42.0.1` → `${AP_GW_IP:-10.42.0.1}` |
| `desktop/presence-tools/connect-hime-h-reap.sh` | `site.env` 駆動 + `HUB_MODE` で detector を触らない |
| `desktop/presence-tools/disconnect-hime-h-reap.sh` | 同上 |
| `desktop/presence-tools/watch-records.sh` | `HUB_MODE=1` で detector ログを購読しない |
| `desktop/presence-tools/show-recent-records.sh` | `HUB_MODE=1` で絞り込み既定を `*` にする |
| `desktop/launchers/*.desktop` | `/home/pi/` 直書きを廃しテンプレート化 |
| `fleet_ui/discovery.py` / `fleet_ui/provision.py` | AP インターフェース名を `AP_DEV` 環境変数から取得 |

---

## Task 1: `site.env` の読込と検証

**Files:**
- Create: `scripts/lib/site-env.sh`
- Create: `site.env.example`
- Create: `scripts/tests/test_site_env.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: なし（最初のタスク）
- Produces:
  - `site_env_load [path]` — `site.env` を source する。無ければ stderr にメッセージを出し `1` を返す。既定パスはリポジトリルートの `site.env`
  - `site_env_validate` — 読込済みの変数を検証。問題があれば1行1件で stderr に出し `1` を返す
  - `site_env_require [path]` — `site_env_load` + `site_env_validate`。失敗したら `exit 1`
  - 必須変数: `HUB_HOSTNAME` `HUB_MODE` `FACTORY_SSID` `FACTORY_IP` `FACTORY_GW` `FACTORY_DNS` `FACTORY_SUBNETS` `SNTP_SERVERS` `ORACLE_CLIENT_MODE` `ORACLE_AUTH_MODE` `ORACLE_HOST` `ORACLE_PORT` `ORACLE_SERVICE` `ORACLE_USER` `ORACLE_TABLE` `ORACLE_PASSWORD_VAR` `PARENT_STA_NO1` `PARENT_STA_NO2` `PARENT_STA_NO3` `AP_IF` `AP_SSID` `AP_GW_IP` `AP_BAND` `AP_CHANNEL` `HOME_SSID` `ADMIN_SSID`

- [ ] **Step 1: 失敗するテストを書く**

`scripts/tests/test_site_env.py`:

```python
"""site.env の読込と検証を検証する。

機体固有値の取り違えは「動くが壊れている」形で現れる(固定IP衝突・STA_NO重複)。
起動前にここで弾くのが唯一の防波堤なので、検証の穴はそのまま事故になる。
"""
import os
import textwrap

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/lib/site-env.sh"

VALID = textwrap.dedent("""\
    HUB_HOSTNAME=presence-hub-2
    HUB_MODE=1
    FACTORY_SSID=HIME-H-REAP
    FACTORY_IP=172.22.13.18/24
    FACTORY_GW=172.22.13.1
    FACTORY_DNS=10.166.1.70,10.166.1.17
    FACTORY_SUBNETS="10.166.5.0/24"
    SNTP_SERVERS="133.141.247.101"
    ORACLE_CLIENT_MODE=jdbc
    ORACLE_AUTH_MODE=basic
    ORACLE_HOST=10.166.5.93
    ORACLE_PORT=1521
    ORACLE_SERVICE=HHC001
    ORACLE_USER=ZHH001
    ORACLE_TABLE=HF1RCM01
    ORACLE_PASSWORD_VAR=ORACLE_PASSWORD_HHC
    PARENT_STA_NO1=997
    PARENT_STA_NO2=996
    PARENT_STA_NO3=995
    AP_IF=wlan1
    AP_SSID=presence-hub
    AP_GW_IP=10.42.0.1
    AP_BAND=bg
    AP_CHANNEL=6
    HOME_SSID=UFI_103134
    ADMIN_SSID=F660P-sDcS-A
    """)


def _write(tmp_path, body=VALID, children="zero2\n"):
    env_file = tmp_path / "site.env"
    env_file.write_text(body, encoding="utf-8")
    inv = tmp_path / "children.conf"
    inv.write_text(children, encoding="utf-8")
    return env_file, inv


def _run(env_file, inv, check=False):
    env = dict(os.environ)
    env["CHILDREN_CONF"] = str(inv)
    return run_bash(
        f'{SOURCE}; site_env_load "{env_file}" && site_env_validate',
        env=env, check=check,
    )


def test_valid_file_passes(tmp_path):
    env_file, inv = _write(tmp_path)
    assert _run(env_file, inv).returncode == 0


def test_missing_file_fails_with_path_in_message(tmp_path):
    proc = run_bash(
        f'{SOURCE}; site_env_load "{tmp_path}/nope.env"',
        env=dict(os.environ), check=False,
    )
    assert proc.returncode != 0
    assert "nope.env" in proc.stderr


def test_missing_required_variable_names_it(tmp_path):
    body = VALID.replace("PARENT_STA_NO2=996\n", "")
    env_file, inv = _write(tmp_path, body=body)
    proc = _run(env_file, inv)
    assert proc.returncode != 0
    assert "PARENT_STA_NO2" in proc.stderr


def test_factory_ip_without_prefix_is_rejected(tmp_path):
    body = VALID.replace("FACTORY_IP=172.22.13.18/24", "FACTORY_IP=172.22.13.18")
    env_file, inv = _write(tmp_path, body=body)
    proc = _run(env_file, inv)
    assert proc.returncode != 0
    assert "FACTORY_IP" in proc.stderr


def test_hostname_colliding_with_a_child_is_rejected(tmp_path):
    # 子と同名だと device_id と MQTT client_id が衝突し、2台が互いを蹴り合う
    body = VALID.replace("HUB_HOSTNAME=presence-hub-2", "HUB_HOSTNAME=zero2")
    env_file, inv = _write(tmp_path, body=body, children="zero2\n")
    proc = _run(env_file, inv)
    assert proc.returncode != 0
    assert "zero2" in proc.stderr


def test_ap_gateway_inside_the_factory_subnet_is_rejected(tmp_path):
    # 子APを工場網と同じ /24 に置くと、経路が二重になり Oracle へ届かなくなる
    body = VALID.replace("AP_GW_IP=10.42.0.1", "AP_GW_IP=172.22.13.1")
    env_file, inv = _write(tmp_path, body=body)
    proc = _run(env_file, inv)
    assert proc.returncode != 0
    assert "AP_GW_IP" in proc.stderr


def test_example_file_is_itself_valid(tmp_path):
    # ひな型が検証を通らないと、利用者は最初の一歩で詰まる
    inv = tmp_path / "children.conf"
    inv.write_text("zero2\n", encoding="utf-8")
    env = dict(os.environ)
    env["CHILDREN_CONF"] = str(inv)
    proc = run_bash(
        f'{SOURCE}; site_env_load site.env.example && site_env_validate',
        env=env, check=False,
    )
    assert proc.returncode == 0, proc.stderr
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `.venv/bin/pytest scripts/tests/test_site_env.py -v`
Expected: FAIL（`scripts/lib/site-env.sh: No such file or directory`）

- [ ] **Step 3: `scripts/lib/site-env.sh` を実装**

```bash
#!/usr/bin/env bash
# site-env.sh — 機体固有値(site.env)の読込と検証。
#
# 機体固有値の取り違えは「動くが壊れている」形で現れる(固定IP衝突・STA_NO重複)。
# 起動前にここで弾くのが唯一の防波堤なので、検証は厳しめにしてある。
#
# 使い方:  source scripts/lib/site-env.sh; site_env_require

SITE_ENV_REPO_DIR="${SITE_ENV_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

SITE_ENV_REQUIRED=(
    HUB_HOSTNAME HUB_MODE
    FACTORY_SSID FACTORY_IP FACTORY_GW FACTORY_DNS FACTORY_SUBNETS SNTP_SERVERS
    ORACLE_CLIENT_MODE ORACLE_AUTH_MODE ORACLE_HOST ORACLE_PORT ORACLE_SERVICE
    ORACLE_USER ORACLE_TABLE ORACLE_PASSWORD_VAR
    PARENT_STA_NO1 PARENT_STA_NO2 PARENT_STA_NO3
    AP_IF AP_SSID AP_GW_IP AP_BAND AP_CHANNEL
    HOME_SSID ADMIN_SSID
)

site_env_load() {
    local path="${1:-$SITE_ENV_REPO_DIR/site.env}"
    if [ ! -f "$path" ]; then
        echo "site.env が見つかりません: $path" >&2
        echo "  site.env.example をコピーして作成してください" >&2
        return 1
    fi
    # shellcheck disable=SC1090
    set -a; source "$path"; set +a
    SITE_ENV_PATH="$path"
}

# 先頭3オクテットが一致するかで簡易に同一サブネット判定する。
# /24 以外の設計は現状存在しないため、これで十分かつ誤検出しない。
_site_env_same_24() {
    [ "${1%.*}" = "${2%.*}" ]
}

site_env_validate() {
    local errors=0 v
    for v in "${SITE_ENV_REQUIRED[@]}"; do
        if [ -z "${!v:-}" ]; then
            echo "必須項目が未設定です: $v" >&2
            errors=$((errors + 1))
        fi
    done
    [ "$errors" -gt 0 ] && return 1

    if ! [[ "$FACTORY_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$ ]]; then
        echo "FACTORY_IP は CIDR 形式で指定してください (例 172.22.13.18/24): $FACTORY_IP" >&2
        errors=$((errors + 1))
    fi

    local inv="${CHILDREN_CONF:-$SITE_ENV_REPO_DIR/fleet/children.conf}"
    if [ -f "$inv" ] && grep -v '^[[:space:]]*#' "$inv" | grep -qFx "$HUB_HOSTNAME"; then
        echo "HUB_HOSTNAME が子Piと重複しています: $HUB_HOSTNAME" >&2
        echo "  ホスト名は device_id と MQTT client_id を決めるため、重複すると相互切断する" >&2
        errors=$((errors + 1))
    fi

    if _site_env_same_24 "$AP_GW_IP" "${FACTORY_IP%/*}"; then
        echo "AP_GW_IP が工場網と同一サブネットです: $AP_GW_IP / $FACTORY_IP" >&2
        errors=$((errors + 1))
    fi

    [ "$errors" -eq 0 ]
}

site_env_require() {
    site_env_load "${1:-}" || exit 1
    site_env_validate || exit 1
}
```

- [ ] **Step 4: `site.env.example` を作る**

```bash
# site.env.example — 機体固有値のひな型。
#   cp site.env.example site.env && chmod 600 site.env && nano site.env
# site.env は .gitignore 済み。秘密は書かない(パスワード類は
# /etc/presence-logger/secrets.env のみ)。

# --- この端末 ---
HUB_HOSTNAME=presence-hub-2      # 既存の親・子と重複しない名前。
                                 # device_id と MQTT client_id を決めるので重複は事故になる
HUB_MODE=1                       # 1=カメラ無し(ハブ)。detector を起動しない

# --- 工場網(内蔵 wlan0) ---
FACTORY_SSID=HIME-H-REAP
FACTORY_IP=172.22.13.18/24       # 情シスへ申請した *この機体の* 固定IP。現行機(.17)と別
FACTORY_GW=172.22.13.1
FACTORY_DNS=10.166.1.70,10.166.1.17
FACTORY_HIDDEN=yes
FACTORY_SUBNETS="10.166.5.0/24 10.166.1.0/24 133.141.247.101/32"
SNTP_SERVERS="133.141.247.101"

# --- Oracle ---
ORACLE_CLIENT_MODE=jdbc          # thin | thick | jdbc
ORACLE_AUTH_MODE=basic           # basic | wallet
ORACLE_HOST=10.166.5.93
ORACLE_PORT=1521
ORACLE_SERVICE=HHC001
ORACLE_USER=ZHH001
ORACLE_TABLE=HF1RCM01
ORACLE_PASSWORD_VAR=ORACLE_PASSWORD_HHC   # secrets.env のキー名(値ではない)
UPCMPFLG=1
UNKNOWN_SSID_POLICY=drop         # hold | drop | use_last

# --- 親自身のカメラ検知用 STA_NO ---
# HUB_MODE=1 では Oracle に書かれないが、bridge の必須項目なので値は要る。
# 将来カメラを付けたときにそのまま使えるよう、他機・全子Piと重複しない値を採番すること。
PARENT_STA_NO1=997
PARENT_STA_NO2=996
PARENT_STA_NO3=995

# --- 子Pi 用 AP(ドングル wlan1) ---
# 引っ越し: 現行ハブと同じ値にすると子は設定変更ゼロで繋ぎ替わる
#           (条件: 旧ハブの AP を先に落とすこと)
# 増設:     SSID と AP_GW_IP を必ず別にし、子の send_target_config.json も変える
AP_IF=wlan1
AP_SSID=presence-hub
AP_GW_IP=10.42.0.1
AP_BAND=bg                       # bg=2.4GHz。子(Pi Zero 2 W)は2.4GHzのみ
AP_CHANNEL=6

# --- 平時のインターネット接続(切断時の戻り先) ---
HOME_SSID=UFI_103134

# --- 保守用ネットワーク ---
# ここから離れると遠隔操作できなくなる接続。WiFi切替の警告文に使う。
ADMIN_SSID=F660P-sDcS-A
```

テスト `test_example_file_is_itself_valid` が通ること（ひな型が検証を通らないと、利用者は最初の一歩で詰まる）。

- [ ] **Step 5: `.gitignore` に追記**

```
# --- 機体固有値: 新しいハブごとに手で書く。Git には載せない ---
site.env
wifi-switch.conf
```

- [ ] **Step 6: テストを実行して成功を確認**

Run: `.venv/bin/pytest scripts/tests/test_site_env.py -v`
Expected: 7 passed

- [ ] **Step 7: lint**

Run: `.venv/bin/ruff check scripts/tests/test_site_env.py`
Expected: All checks passed

- [ ] **Step 8: コミット**

```bash
git add scripts/lib/site-env.sh site.env.example scripts/tests/test_site_env.py .gitignore
git commit -m "$(cat <<'EOF'
feat(bootstrap): validate per-machine values before anything runs

Getting a machine-specific value wrong does not fail loudly -- a
duplicate static IP or STA_NO shows up as records silently going
missing. site.env is the only place those values live, so it is also
the only place to catch them, before a single package is installed.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: フェーズ振り分け（`bootstrap-hub.sh`）

**Files:**
- Create: `scripts/bootstrap-hub.sh`
- Create: `scripts/tests/test_bootstrap_dispatch.py`

**Interfaces:**
- Consumes: `scripts/lib/site-env.sh`（Task 1）
- Produces:
  - `bootstrap_discover_phases <dir>` — `NN-*.sh` を番号順に1行1件で stdout へ出す
  - `bootstrap_select_phases <dir> [from] [to]` — 範囲で絞る。引数無しは全件
  - `main` は `[[ "${BASH_SOURCE[0]}" == "$0" ]]` ガードの中でのみ実行
  - CLI: `bootstrap-hub.sh` / `bootstrap-hub.sh 10` / `bootstrap-hub.sh 30 60` / `--list`

- [ ] **Step 1: 失敗するテストを書く**

`scripts/tests/test_bootstrap_dispatch.py`:

```python
"""bootstrap-hub.sh のフェーズ振り分けを検証する。

フェーズを飛ばせること・順序が番号どおりであることは、途中で失敗した作業を
再開する上での前提になる。番号順が崩れると、AP を上げる前に compose を
起動するなど「順序が意味を持つ」工程が壊れる。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/bootstrap-hub.sh"


def _phases(tmp_path):
    d = tmp_path / "bootstrap"
    d.mkdir()
    for name in ("70-desktop.sh", "10-japanese-input.sh", "40-configs.sh"):
        p = d / name
        p.write_text("#!/usr/bin/env bash\necho ran $0\n", encoding="utf-8")
        p.chmod(0o755)
    return d


def test_phases_are_listed_in_numeric_order(tmp_path):
    d = _phases(tmp_path)
    out = run_bash(f'{SOURCE}; bootstrap_discover_phases "{d}"',
                   env=dict(os.environ)).stdout.split()
    assert [os.path.basename(p) for p in out] == [
        "10-japanese-input.sh", "40-configs.sh", "70-desktop.sh"]


def test_single_phase_selection(tmp_path):
    d = _phases(tmp_path)
    out = run_bash(f'{SOURCE}; bootstrap_select_phases "{d}" 40 40',
                   env=dict(os.environ)).stdout.split()
    assert [os.path.basename(p) for p in out] == ["40-configs.sh"]


def test_range_selection_is_inclusive(tmp_path):
    d = _phases(tmp_path)
    out = run_bash(f'{SOURCE}; bootstrap_select_phases "{d}" 40 70',
                   env=dict(os.environ)).stdout.split()
    assert [os.path.basename(p) for p in out] == ["40-configs.sh", "70-desktop.sh"]


def test_no_range_selects_everything(tmp_path):
    d = _phases(tmp_path)
    out = run_bash(f'{SOURCE}; bootstrap_select_phases "{d}"',
                   env=dict(os.environ)).stdout.split()
    assert len(out) == 3


def test_sourcing_does_not_execute_main(tmp_path):
    # source しただけで root チェックや apt が走らないこと(テスト可能性の前提)
    proc = run_bash(f'{SOURCE}; echo SOURCED_OK', env=dict(os.environ), check=False)
    assert proc.returncode == 0
    assert "SOURCED_OK" in proc.stdout
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `.venv/bin/pytest scripts/tests/test_bootstrap_dispatch.py -v`
Expected: FAIL（`scripts/bootstrap-hub.sh: No such file or directory`）

- [ ] **Step 3: `scripts/bootstrap-hub.sh` を実装**

```bash
#!/usr/bin/env bash
# bootstrap-hub.sh — 新しいハブ Pi を立ち上げる。
#
#   sudo bash scripts/bootstrap-hub.sh          全フェーズ
#   sudo bash scripts/bootstrap-hub.sh 10       フェーズ10 だけ
#   sudo bash scripts/bootstrap-hub.sh 30 60    フェーズ30〜60
#   bash scripts/bootstrap-hub.sh --list        フェーズ一覧
#
# 実処理は持たない。scripts/bootstrap/NN-*.sh を番号順に呼ぶだけ。
set -uo pipefail

BOOTSTRAP_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP_DIR="${BOOTSTRAP_DIR:-$BOOTSTRAP_REPO_DIR/scripts/bootstrap}"

bootstrap_discover_phases() {
    local dir="${1:-$BOOTSTRAP_DIR}"
    find "$dir" -maxdepth 1 -name '[0-9][0-9]-*.sh' -type f | sort
}

bootstrap_select_phases() {
    local dir="${1:-$BOOTSTRAP_DIR}" from="${2:-0}" to="${3:-99}" p num
    while IFS= read -r p; do
        num="$(basename "$p")"; num="${num%%-*}"
        if [ "$((10#$num))" -ge "$((10#$from))" ] && [ "$((10#$num))" -le "$((10#$to))" ]; then
            printf '%s\n' "$p"
        fi
    done < <(bootstrap_discover_phases "$dir")
}

main() {
    if [ "${1:-}" = "--list" ]; then
        bootstrap_discover_phases | while IFS= read -r p; do
            printf '  %s\n' "$(basename "$p")"
        done
        return 0
    fi
    if [ "$EUID" -ne 0 ]; then
        echo "root で実行してください: sudo bash $0 $*" >&2
        return 1
    fi
    local phases; phases="$(bootstrap_select_phases "$BOOTSTRAP_DIR" "${1:-0}" "${2:-${1:-99}}")"
    if [ -z "$phases" ]; then
        echo "該当するフェーズがありません。--list で確認してください" >&2
        return 1
    fi
    local p
    while IFS= read -r p; do
        printf '\n==================== %s ====================\n' "$(basename "$p")"
        bash "$p" || { echo "失敗しました: $(basename "$p")" >&2; return 1; }
    done <<< "$phases"
    printf '\n完了しました。\n'
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
```

- [ ] **Step 4: テストを実行して成功を確認**

Run: `.venv/bin/pytest scripts/tests/test_bootstrap_dispatch.py -v`
Expected: 5 passed

- [ ] **Step 5: コミット**

```bash
git add scripts/bootstrap-hub.sh scripts/tests/test_bootstrap_dispatch.py
git commit -m "$(cat <<'EOF'
feat(bootstrap): phase dispatcher for the hub bootstrap

Ordering carries meaning here -- the AP has to exist before compose
binds to its gateway IP -- so phases are numbered files run in numeric
order, and a range can be re-run after a failure without redoing the
rest. main is guarded so tests can source the functions without
tripping the root check.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: フェーズ10 — 日本語入力

**最優先タスク。** これ単体で価値があり、単体で検証できる。

**Files:**
- Create: `scripts/bootstrap/10-japanese-input.sh`
- Create: `scripts/tests/test_bootstrap_japanese_input.py`

**Interfaces:**
- Consumes: なし（`site.env` にも依存しない。機体固有値を使わない唯一のフェーズ）
- Produces:
  - `ime_packages` — 導入するパッケージ名を1行1件で stdout へ
  - `ime_set_keyboard_layout <file>` — `/etc/default/keyboard` の `XKBLAYOUT` を `jp` にする（既に jp なら何もしない）
  - `ime_render_fcitx5_profile` — fcitx5 profile の内容を stdout へ
  - `main` — apt 導入 → 配列設定 → `im-config -n fcitx5` → profile 配置 → 再ログイン案内

- [ ] **Step 1: 失敗するテストを書く**

`scripts/tests/test_bootstrap_japanese_input.py`:

```python
"""フェーズ10(日本語入力)を検証する。

現行機の ~/.xinputrc は run_im fcitx (fcitx4) という残骸で、fcitx4 は入って
いない。設定ファイルの複製ではなく im-config に生成させるのが正しい。
ここを間違えると「動いている現行機の写し」を作って壊れる。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/bootstrap/10-japanese-input.sh"


def test_package_list_covers_every_frontend():
    # gtk3/gtk4/qt5/qt6 のどれかが欠けると、そのツールキットのアプリだけ
    # 日本語入力できないという分かりにくい壊れ方をする
    out = run_bash(f'{SOURCE}; ime_packages', env=dict(os.environ)).stdout.split()
    for pkg in ("fcitx5", "fcitx5-mozc", "fcitx5-frontend-gtk3", "fcitx5-frontend-gtk4",
                "fcitx5-frontend-qt5", "fcitx5-frontend-qt6", "fonts-noto-cjk"):
        assert pkg in out, f"{pkg} が不足"


def test_keyboard_layout_is_rewritten_to_jp(tmp_path):
    kb = tmp_path / "keyboard"
    kb.write_text('XKBMODEL="pc105"\nXKBLAYOUT="gb"\nBACKSPACE="guess"\n', encoding="utf-8")
    run_bash(f'{SOURCE}; ime_set_keyboard_layout "{kb}"', env=dict(os.environ))
    assert 'XKBLAYOUT="jp"' in kb.read_text(encoding="utf-8")
    assert 'BACKSPACE="guess"' in kb.read_text(encoding="utf-8")  # 他行を壊さない


def test_keyboard_rewrite_is_idempotent(tmp_path):
    kb = tmp_path / "keyboard"
    kb.write_text('XKBLAYOUT="jp"\n', encoding="utf-8")
    run_bash(f'{SOURCE}; ime_set_keyboard_layout "{kb}"', env=dict(os.environ))
    assert kb.read_text(encoding="utf-8").count("XKBLAYOUT") == 1


def test_fcitx5_profile_selects_mozc_and_jp_layout():
    out = run_bash(f'{SOURCE}; ime_render_fcitx5_profile', env=dict(os.environ)).stdout
    assert "DefaultIM=mozc" in out
    assert "Default Layout=jp" in out
    assert "Name=keyboard-jp" in out
    assert "Name=mozc" in out


def test_profile_does_not_reference_fcitx4():
    # 現行機の ~/.xinputrc(run_im fcitx)を写さないこと
    out = run_bash(f'{SOURCE}; ime_render_fcitx5_profile', env=dict(os.environ)).stdout
    assert "run_im" not in out
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `.venv/bin/pytest scripts/tests/test_bootstrap_japanese_input.py -v`
Expected: FAIL（ファイルが無い）

- [ ] **Step 3: `scripts/bootstrap/10-japanese-input.sh` を実装**

```bash
#!/usr/bin/env bash
# 10-japanese-input.sh — 日本語入力(fcitx5 + mozc)を使えるようにする。
#
# 注意: 現行機の ~/.xinputrc は `run_im fcitx`(fcitx4)という残骸で、fcitx4 は
# 入っていない。im-config の auto フォールバックが偶然 fcitx5 を拾って動いて
# いるだけなので、あのファイルを複製してはいけない。im-config に生成させる。
set -uo pipefail

ime_packages() {
    cat <<'EOF'
fcitx5
fcitx5-mozc
fcitx5-frontend-gtk3
fcitx5-frontend-gtk4
fcitx5-frontend-qt5
fcitx5-frontend-qt6
fcitx5-config-qt
mozc-utils-gui
fonts-noto-cjk
EOF
}

ime_set_keyboard_layout() {
    local file="${1:-/etc/default/keyboard}"
    grep -q '^XKBLAYOUT="jp"' "$file" && return 0
    sed -i 's/^XKBLAYOUT=.*/XKBLAYOUT="jp"/' "$file"
}

ime_render_fcitx5_profile() {
    cat <<'EOF'
[Groups/0]
Name=Default
Default Layout=jp
DefaultIM=mozc

[Groups/0/Items/0]
Name=keyboard-jp
Layout=

[Groups/0/Items/1]
Name=mozc
Layout=

[GroupOrder]
0=Default
EOF
}

main() {
    local user="${SUDO_USER:-$USER}" home
    home="$(getent passwd "$user" | cut -d: -f6)"

    echo "==> パッケージを導入"
    # shellcheck disable=SC2046
    apt-get install -y $(ime_packages | tr '\n' ' ') || return 1

    echo "==> キーボード配列を jp に"
    ime_set_keyboard_layout /etc/default/keyboard
    setupcon 2>/dev/null || true

    echo "==> im-config で fcitx5 を選択"
    su - "$user" -c "im-config -n fcitx5"

    echo "==> fcitx5 profile を配置"
    install -d -o "$user" -g "$user" -m 700 "$home/.config/fcitx5"
    ime_render_fcitx5_profile > "$home/.config/fcitx5/profile"
    chown "$user:$user" "$home/.config/fcitx5/profile"
    chmod 600 "$home/.config/fcitx5/profile"

    cat <<'EOF'

--------------------------------------------------------------
 ✅ 日本語入力の設定が終わりました。

 ⚠ ここで一度ログアウトして、ログインし直してください。
    labwc(Wayland)では GTK_IM_MODULE 等をセッション開始時の
    im-launch が撒くため、再ログインするまで入力できません。

 再ログイン後の確認:
    env | grep GTK_IM_MODULE      # fcitx と出ること
    pgrep -a fcitx5               # 動いていること
    記号キー(@ : _)が刻印どおり入ること
--------------------------------------------------------------
EOF
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
```

- [ ] **Step 4: テストを実行して成功を確認**

Run: `.venv/bin/pytest scripts/tests/test_bootstrap_japanese_input.py -v`
Expected: 5 passed

- [ ] **Step 5: コミット**

```bash
git add scripts/bootstrap/10-japanese-input.sh scripts/tests/test_bootstrap_japanese_input.py
git commit -m "$(cat <<'EOF'
feat(bootstrap): phase 10, Japanese input

Generates the im-config selection rather than copying this Pi's
~/.xinputrc, which still says `run_im fcitx` for an fcitx4 that was
never installed -- the running setup only works because im-config falls
back to fcitx5. Copying the working machine would have copied the bug.

Ends by telling the operator to log out: on labwc the IM environment is
exported by im-launch at session start, so nothing works until then.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: フェーズ20 — 基盤パッケージ・ホスト名・venv・SSH鍵

**Files:**
- Create: `scripts/bootstrap/20-base-packages.sh`
- Create: `scripts/tests/test_bootstrap_base.py`

**Interfaces:**
- Consumes: `site_env_require`（Task 1）
- Produces:
  - `base_packages` — パッケージ名を1行1件
  - `base_set_hostname <name> <hostname_file> <hosts_file>` — `/etc/hostname` と `/etc/hosts` の `127.0.1.1` 行を書き換える
  - `base_ensure_venv <repo_dir>` — `.venv` が無ければ `python3 -m venv --system-site-packages` で作る

- [ ] **Step 1: 失敗するテストを書く**

```python
"""フェーズ20(基盤)を検証する。

python3-yaml はシステムの python3 に要る(install.sh・connect-hime-h-reap.sh・
show-recent-records.sh・pipeline_monitor が venv ではなくシステム側で yaml を
読むため)。ここが抜けると後段が全部落ちる。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/bootstrap/20-base-packages.sh"


def test_package_list_has_the_non_obvious_ones():
    out = run_bash(f'{SOURCE}; base_packages', env=dict(os.environ)).stdout.split()
    for pkg in ("docker.io", "docker-compose-plugin", "python3-yaml",
                "mosquitto-clients", "dkms", "raspberrypi-kernel-headers"):
        assert pkg in out, f"{pkg} が不足"


def test_hostname_files_are_rewritten(tmp_path):
    hn = tmp_path / "hostname"; hn.write_text("raspberrypi5\n", encoding="utf-8")
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1\tlocalhost\n127.0.1.1\traspberrypi5 raspberrypi5\n",
                     encoding="utf-8")
    run_bash(f'{SOURCE}; base_set_hostname presence-hub-2 "{hn}" "{hosts}"',
             env=dict(os.environ))
    assert hn.read_text(encoding="utf-8").strip() == "presence-hub-2"
    body = hosts.read_text(encoding="utf-8")
    assert "127.0.1.1\tpresence-hub-2" in body
    assert "127.0.0.1\tlocalhost" in body          # 他行を壊さない
    assert "raspberrypi5" not in body


def test_hosts_line_is_added_when_absent(tmp_path):
    hn = tmp_path / "hostname"; hn.write_text("old\n", encoding="utf-8")
    hosts = tmp_path / "hosts"; hosts.write_text("127.0.0.1\tlocalhost\n", encoding="utf-8")
    run_bash(f'{SOURCE}; base_set_hostname presence-hub-2 "{hn}" "{hosts}"',
             env=dict(os.environ))
    assert "127.0.1.1\tpresence-hub-2" in hosts.read_text(encoding="utf-8")


def test_venv_creation_is_skipped_when_present(tmp_path, fake_bin):
    fake_bin("python3", 'printf "python3 %s\\n" "$*" >> "$FAKE_LOG"')
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").touch()
    run_bash(f'{SOURCE}; base_ensure_venv "{tmp_path}"', env=dict(os.environ))
    assert "venv" not in fake_bin.log.read_text(encoding="utf-8")
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `.venv/bin/pytest scripts/tests/test_bootstrap_base.py -v`
Expected: FAIL

- [ ] **Step 3: 実装**

```bash
#!/usr/bin/env bash
# 20-base-packages.sh — docker・python3-yaml・ビルド環境・venv・ホスト名・SSH鍵。
#
# python3-yaml は「システムの python3」に要る。install.sh /
# connect-hime-h-reap.sh / show-recent-records.sh / pipeline_monitor は
# いずれも venv ではなくシステム側の python3 で yaml を読む。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"

base_packages() {
    cat <<'EOF'
docker.io
docker-compose-plugin
python3-yaml
python3-venv
mosquitto-clients
git
rsync
dkms
build-essential
bc
raspberrypi-kernel-headers
EOF
}

base_set_hostname() {
    local name="$1" hn="${2:-/etc/hostname}" hosts="${3:-/etc/hosts}"
    printf '%s\n' "$name" > "$hn"
    # 旧ホスト名では置換しない。`\b` はハイフン手前でも単語境界になり、
    # pizero2w の置換が pizero2w-2 の行を壊す(DEPLOY.md に記録済み)。
    # 127.0.1.1 行はローカルホスト名専用なので丸ごと差し替える。
    if grep -q '^127\.0\.1\.1[[:space:]]' "$hosts"; then
        sed -i "s/^127\.0\.1\.1[[:space:]].*/127.0.1.1\t$name/" "$hosts"
    else
        printf '127.0.1.1\t%s\n' "$name" >> "$hosts"
    fi
}

base_ensure_venv() {
    local repo="${1:-$REPO_DIR}"
    [ -x "$repo/.venv/bin/python" ] && return 0
    # fleet-ui.service が .venv/bin/python を絶対パスで叩くため必須。
    # fleet_ui 自体は標準ライブラリのみだが、システムの python3-yaml も
    # 見えるようにしておく。
    python3 -m venv --system-site-packages "$repo/.venv"
}

main() {
    site_env_require
    echo "==> パッケージを導入"
    apt-get update
    # shellcheck disable=SC2046
    apt-get install -y $(base_packages | tr '\n' ' ') || return 1

    echo "==> ホスト名を $HUB_HOSTNAME に"
    base_set_hostname "$HUB_HOSTNAME" /etc/hostname /etc/hosts
    hostnamectl set-hostname "$HUB_HOSTNAME"

    local user="${SUDO_USER:-$USER}" home
    home="$(getent passwd "$user" | cut -d: -f6)"
    echo "==> $user を docker グループへ"
    usermod -aG docker "$user"

    echo "==> venv を用意"
    base_ensure_venv "$REPO_DIR"
    chown -R "$user:$user" "$REPO_DIR/.venv"

    if [ ! -f "$home/.ssh/id_ed25519" ]; then
        echo "==> SSH 鍵を生成"
        su - "$user" -c "ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519"
    fi

    cat <<EOF

--------------------------------------------------------------
 ⚠ 子Pi へ入るための公開鍵です。**旧親がまだ子に到達できるうちに**
   各子の ~/.ssh/authorized_keys へ入れてください。
   子が新APへ移った後では、どちらの親からも入れられなくなります。

$(cat "$home/.ssh/id_ed25519.pub")

 ⚠ docker グループの反映には再ログイン(または reboot)が必要です。
--------------------------------------------------------------
EOF
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
```

- [ ] **Step 4: 実行して成功を確認**

Run: `.venv/bin/pytest scripts/tests/test_bootstrap_base.py -v`
Expected: 4 passed

- [ ] **Step 5: コミット**

```bash
git add scripts/bootstrap/20-base-packages.sh scripts/tests/test_bootstrap_base.py
git commit -m "$(cat <<'EOF'
feat(bootstrap): phase 20, base packages and machine identity

python3-yaml is in the list because install.sh and three desktop tools
read yaml with the system interpreter rather than the venv; leaving it
out breaks them long after this phase has passed.

Prints the new SSH public key with the warning that it has to reach the
children while the old parent can still log into them.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: フェーズ30 — ドングルドライバ（RTL8811AU）

**Files:**
- Create: `scripts/bootstrap/30-dongle-driver.sh`
- Create: `scripts/bootstrap/patches/8821au-add-elecom-056e-4010.patch`
- Create: `scripts/tests/test_bootstrap_dongle.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `dongle_already_working <ifname>` — そのIFが存在し `iw phy` に `* AP` があれば 0
  - `dongle_render_modprobe_conf` — `/etc/modprobe.d/8821au.conf` の内容を stdout へ
  - `dongle_verify <ifname>` — country_code=JP / IF存在 / AP対応 の3点を確認

- [ ] **Step 1: 失敗するテストを書く**

```python
"""フェーズ30(ドングルドライバ)を検証する。

rtw_country_code=JP が無いと5GHzでAPに拒否され、rtw_power_mgnt=0 が無いと
4-way handshake を取りこぼして切断する。どちらも省略できない。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/bootstrap/30-dongle-driver.sh"


def test_modprobe_conf_has_both_mandatory_options():
    out = run_bash(f'{SOURCE}; dongle_render_modprobe_conf', env=dict(os.environ)).stdout
    assert "rtw_country_code=JP" in out
    assert "rtw_power_mgnt=0" in out


def test_already_working_when_interface_supports_ap(tmp_path, fake_bin):
    fake_bin("ip", 'exit 0')
    fake_bin("iw", 'echo "	Supported interface modes:"; echo "		 * AP"')
    proc = run_bash(f'{SOURCE}; IW_PHY_NAME=phy1 dongle_already_working wlan1',
                    env=dict(os.environ), check=False)
    assert proc.returncode == 0


def test_not_working_when_interface_missing(fake_bin):
    fake_bin("ip", 'exit 1')
    proc = run_bash(f'{SOURCE}; dongle_already_working wlan1',
                    env=dict(os.environ), check=False)
    assert proc.returncode != 0


def test_not_working_when_ap_mode_unsupported(fake_bin):
    fake_bin("ip", 'exit 0')
    fake_bin("iw", 'echo "	Supported interface modes:"; echo "		 * managed"')
    proc = run_bash(f'{SOURCE}; IW_PHY_NAME=phy1 dongle_already_working wlan1',
                    env=dict(os.environ), check=False)
    assert proc.returncode != 0


def test_patch_file_adds_the_elecom_usb_id():
    body = open("scripts/bootstrap/patches/8821au-add-elecom-056e-4010.patch",
                encoding="utf-8").read()
    assert "0x056E, 0x4010" in body
    assert "RTL8821" in body
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `.venv/bin/pytest scripts/tests/test_bootstrap_dongle.py -v`
Expected: FAIL

- [ ] **Step 3: パッチファイルを作る**

現行機の `~/8821au/os_dep/linux/usb_intf.c` に手で入れた1行を差分にする。205-208行目付近（ELECOM の 4007/400E/400F が並ぶ RTL8821 セクション）へ次を追加する差分:

```diff
--- a/os_dep/linux/usb_intf.c
+++ b/os_dep/linux/usb_intf.c
@@
 	{USB_DEVICE(0x056E, 0x400F), .driver_info = RTL8821}, /* ELECOM */
+	{USB_DEVICE(0x056E, 0x4010), .driver_info = RTL8821}, /* ELECOM WDC-433DU2H2-B */
```

- [ ] **Step 4: `scripts/bootstrap/30-dongle-driver.sh` を実装**

```bash
#!/usr/bin/env bash
# 30-dongle-driver.sh — ELECOM WDC-433DU2H2-B (RTL8811AU / 056e:4010) を使えるようにする。
#
# 素の Raspberry Pi OS では pegasus(USB有線LANドライバ)が 056e:4010 に誤マッチ
# して probe が -110 で失敗し、wlan1 自体が現れない。カーネル内蔵 rtw88 は
# 8821cu 系のみで RTL8811AU 非対応のため、外部ドライバを DKMS で入れる。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH="$HERE/patches/8821au-add-elecom-056e-4010.patch"
SRC_DIR="${DONGLE_SRC_DIR:-/usr/local/src/8821au}"
DRIVER_REPO="${DONGLE_REPO:-https://github.com/morrownr/8821au-20210708.git}"

dongle_render_modprobe_conf() {
    # rtw_country_code=JP … 未設定だと5GHzで CTRL-EVENT-ASSOC-REJECT status_code=1
    # rtw_power_mgnt=0    … 省電力ONだと 4-way handshake を落として no-secrets 切断
    printf 'options 8821au rtw_led_ctrl=1 rtw_country_code=JP rtw_power_mgnt=0\n'
}

dongle_already_working() {
    local ifname="${1:-wlan1}" phy
    ip -br link show "$ifname" >/dev/null 2>&1 || return 1
    phy="${IW_PHY_NAME:-$(cat "/sys/class/net/$ifname/phy80211/name" 2>/dev/null)}"
    [ -n "$phy" ] || return 1
    iw phy "$phy" info 2>/dev/null | grep -q -- '\* AP'
}

dongle_verify() {
    local ifname="${1:-wlan1}" ok=0
    grep -q JP /sys/module/8821au/parameters/rtw_country_code 2>/dev/null \
        || { echo "rtw_country_code が JP ではありません" >&2; ok=1; }
    dongle_already_working "$ifname" \
        || { echo "$ifname が無い、または AP 非対応です" >&2; ok=1; }
    return "$ok"
}

main() {
    local ifname="${AP_IF:-wlan1}"
    if dongle_already_working "$ifname"; then
        echo "$ifname は既に動作し AP 対応です。スキップします"
        return 0
    fi

    echo "==> 検出されている USB デバイス"; lsusb | grep -i "056e:4010" || true

    if [ ! -d "$SRC_DIR" ]; then
        echo "==> ドライバソースを取得"
        git clone --depth=1 "$DRIVER_REPO" "$SRC_DIR" || return 1
        echo "==> ELECOM の USB ID を追加"
        git -C "$SRC_DIR" apply "$PATCH" || patch -p1 -d "$SRC_DIR" < "$PATCH" || return 1
    fi

    echo "==> DKMS でビルド・導入"
    ( cd "$SRC_DIR" && ./install-driver.sh NoPrompt ) || return 1

    echo "==> モジュールオプションを配置"
    dongle_render_modprobe_conf > /etc/modprobe.d/8821au.conf
    modprobe -r 8821au 2>/dev/null || true
    modprobe 8821au || true
    sleep 2

    dongle_verify "$ifname" || return 1
    echo "✅ $ifname が AP 対応で使えます"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
```

- [ ] **Step 5: 実行して成功を確認**

Run: `.venv/bin/pytest scripts/tests/test_bootstrap_dongle.py -v`
Expected: 5 passed

- [ ] **Step 6: コミット**

```bash
git add scripts/bootstrap/30-dongle-driver.sh scripts/bootstrap/patches scripts/tests/test_bootstrap_dongle.py
git commit -m "$(cat <<'EOF'
feat(bootstrap): phase 30, out-of-tree driver for the dongle

The one-line USB ID this Pi needs lived only in a hand-edited clone in
someone's home directory; it is a patch file now. Verifies AP mode right
after installing rather than leaving it for the AP phase, where the same
failure is much harder to attribute.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: フェーズ40 — `/etc/presence-logger/` の生成

**このプランで最も事故りやすいタスク。** 生成関数を stdout へ書く純関数にして、`/etc` に触れずに検証する。

**Files:**
- Create: `scripts/bootstrap/40-configs.sh`
- Create: `scripts/tests/test_bootstrap_configs.py`

**Interfaces:**
- Consumes: `site_env_require`（Task 1）
- Produces:
  - `configs_render_profiles_yaml` — `profiles.yaml` の内容を stdout へ
  - `configs_render_device_yaml` — `device.yaml` の内容を stdout へ
  - `configs_missing_secret_keys <secrets_file>` — `secrets.env` に足りないキー名を1行1件で stdout へ

- [ ] **Step 1: 失敗するテストを書く**

```python
"""フェーズ40(設定生成)を検証する。

profiles.yaml に station: を出さないのが要点。親の局番は device.yaml に
一本化する(設計書 4.3)。両方に書くと二重管理になり、片方だけ直して食い違う。
"""
import os
import textwrap

import yaml

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/bootstrap/40-configs.sh"

SITE = textwrap.dedent("""\
    HUB_HOSTNAME=presence-hub-2
    HUB_MODE=1
    FACTORY_SSID=HIME-H-REAP
    FACTORY_IP=172.22.13.18/24
    FACTORY_GW=172.22.13.1
    FACTORY_DNS=10.166.1.70,10.166.1.17
    FACTORY_SUBNETS="10.166.5.0/24"
    FACTORY_HIDDEN=yes
    SNTP_SERVERS="133.141.247.101"
    ORACLE_CLIENT_MODE=jdbc
    ORACLE_AUTH_MODE=basic
    ORACLE_HOST=10.166.5.93
    ORACLE_PORT=1521
    ORACLE_SERVICE=HHC001
    ORACLE_USER=ZHH001
    ORACLE_TABLE=HF1RCM01
    ORACLE_PASSWORD_VAR=ORACLE_PASSWORD_HHC
    UPCMPFLG=1
    UNKNOWN_SSID_POLICY=drop
    PARENT_STA_NO1=997
    PARENT_STA_NO2=996
    PARENT_STA_NO3=995
    AP_IF=wlan1
    AP_SSID=presence-hub
    AP_GW_IP=10.42.0.1
    AP_BAND=bg
    AP_CHANNEL=6
    HOME_SSID=UFI_103134
    ADMIN_SSID=F660P-sDcS-A
    """)


def _render(tmp_path, fn):
    env_file = tmp_path / "site.env"
    env_file.write_text(SITE, encoding="utf-8")
    return run_bash(
        f'{SOURCE}; site_env_load "{env_file}"; {fn}',
        env=dict(os.environ),
    ).stdout


def test_profiles_yaml_is_valid_yaml_keyed_by_ssid(tmp_path):
    doc = yaml.safe_load(_render(tmp_path, "configs_render_profiles_yaml"))
    assert "HIME-H-REAP" in doc["profiles"]


def test_profiles_yaml_carries_the_new_static_ip(tmp_path):
    doc = yaml.safe_load(_render(tmp_path, "configs_render_profiles_yaml"))
    p = doc["profiles"]["HIME-H-REAP"]
    assert p["wifi"]["static_ipv4"]["address"] == "172.22.13.18/24"
    assert p["wifi"]["static_ipv4"]["gateway"] == "172.22.13.1"
    assert p["wifi"]["static_ipv4"]["dns"] == ["10.166.1.70", "10.166.1.17"]


def test_profiles_yaml_has_no_station_override(tmp_path):
    # 親の局番は device.yaml に一本化する(設計書 4.3)
    doc = yaml.safe_load(_render(tmp_path, "configs_render_profiles_yaml"))
    assert "station" not in doc["profiles"]["HIME-H-REAP"]


def test_password_is_a_variable_reference_not_a_value(tmp_path):
    body = _render(tmp_path, "configs_render_profiles_yaml")
    assert "${ORACLE_PASSWORD_HHC}" in body


def test_unknown_ssid_policy_is_carried_through(tmp_path):
    doc = yaml.safe_load(_render(tmp_path, "configs_render_profiles_yaml"))
    assert doc["unknown_ssid_policy"] == "drop"


def test_device_yaml_uses_parent_sta_no(tmp_path):
    doc = yaml.safe_load(_render(tmp_path, "configs_render_device_yaml"))
    assert doc["device_id"] is None
    assert doc["station"] == {"sta_no1": "997", "sta_no2": "996", "sta_no3": "995"}


def test_missing_secret_keys_are_listed(tmp_path):
    secrets = tmp_path / "secrets.env"
    secrets.write_text("ORACLE_PASSWORD_HHC=x\n", encoding="utf-8")
    env_file = tmp_path / "site.env"
    env_file.write_text(SITE, encoding="utf-8")
    out = run_bash(
        f'{SOURCE}; site_env_load "{env_file}"; configs_missing_secret_keys "{secrets}"',
        env=dict(os.environ),
    ).stdout.split()
    assert "WIFI_PSK_HIMEREAP" in out
    assert "WIFI_AP_PSK" in out
    assert "ORACLE_PASSWORD_HHC" not in out
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `.venv/bin/pytest scripts/tests/test_bootstrap_configs.py -v`
Expected: FAIL

- [ ] **Step 3: 実装**

```bash
#!/usr/bin/env bash
# 40-configs.sh — site.env から /etc/presence-logger/ を生成する。
#
# 生成関数は stdout へ書く純関数にしてある(テストが /etc に触らずに済むため)。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
ETC_DIR="${ETC_DIR:-/etc/presence-logger}"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"

_yaml_list() {  # "a,b" -> ["a", "b"]
    local IFS=','; local out=""
    for x in $1; do out="${out:+$out, }\"$x\""; done
    printf '[%s]' "$out"
}

configs_render_profiles_yaml() {
    cat <<EOF
# 自動生成: scripts/bootstrap/40-configs.sh ($SITE_ENV_PATH より)
# 手で直した場合は site.env 側も合わせること。
profiles:
  $FACTORY_SSID:
    description: "$FACTORY_SSID (生成: $HUB_HOSTNAME)"
    wifi:
      psk: "\${WIFI_PSK_HIMEREAP}"
      hidden: ${FACTORY_HIDDEN:-yes}
      static_ipv4:
        address: "$FACTORY_IP"
        gateway: "$FACTORY_GW"
        dns: $(_yaml_list "$FACTORY_DNS")
    sntp:
      servers: $(_yaml_list "${SNTP_SERVERS// /,}")
    oracle:
      client_mode: "$ORACLE_CLIENT_MODE"
      auth_mode: "$ORACLE_AUTH_MODE"
      host: "$ORACLE_HOST"
      port: $ORACLE_PORT
      service_name: "$ORACLE_SERVICE"
      user: "$ORACLE_USER"
      password: "\${$ORACLE_PASSWORD_VAR}"
      table_name: "$ORACLE_TABLE"
      upcmpflg: ${UPCMPFLG:-1}

unknown_ssid_policy: "${UNKNOWN_SSID_POLICY:-hold}"
EOF
}

configs_render_device_yaml() {
    cat <<EOF
# 自動生成: scripts/bootstrap/40-configs.sh
# device_id: null は /etc/host_hostname (ホスト名)から自動取得する意味。
#
# station は「この親自身のカメラ検知」用。HUB_MODE=1(カメラ無し)では Oracle に
# 書かれないが、bridge の必須項目なので値は要る。将来カメラを付けたときに
# そのまま使えるよう、他機・全子Piと重複しない値にしてある。
device_id: null
station:
  sta_no1: "$PARENT_STA_NO1"
  sta_no2: "$PARENT_STA_NO2"
  sta_no3: "$PARENT_STA_NO3"
EOF
}

configs_missing_secret_keys() {
    local f="${1:-$ETC_DIR/secrets.env}" k
    for k in "$ORACLE_PASSWORD_VAR" WIFI_PSK_HIMEREAP WIFI_AP_PSK; do
        grep -q "^$k=" "$f" 2>/dev/null || printf '%s\n' "$k"
    done
}

main() {
    site_env_require
    install -d -m 0755 "$ETC_DIR" /var/lib/presence-logger /var/log/presence-logger
    install -d -m 0700 "$ETC_DIR/wallets"

    echo "==> profiles.yaml / device.yaml を生成"
    configs_render_profiles_yaml > "$ETC_DIR/profiles.yaml"
    configs_render_device_yaml   > "$ETC_DIR/device.yaml"
    chown root:root "$ETC_DIR/profiles.yaml" "$ETC_DIR/device.yaml"
    chmod 640 "$ETC_DIR/profiles.yaml"; chmod 644 "$ETC_DIR/device.yaml"

    echo "==> bridge.yaml / detector.yaml を配置"
    cp -n "$REPO_DIR/config/site/bridge.yaml"   "$ETC_DIR/bridge.yaml"
    cp -n "$REPO_DIR/config/site/detector.yaml" "$ETC_DIR/detector.yaml"
    chmod 644 "$ETC_DIR/bridge.yaml" "$ETC_DIR/detector.yaml"

    # docker がディレクトリとして作ってしまう前に、ファイルとして用意する。
    # install.sh はこれを作らないが docker-compose.yml はマウントする。
    local user="${SUDO_USER:-$USER}"
    [ -e "$ETC_DIR/upcmpflg.override" ] || printf '%s\n' "${UPCMPFLG:-1}" > "$ETC_DIR/upcmpflg.override"
    chown "$user:$user" "$ETC_DIR/upcmpflg.override"

    if [ ! -f "$ETC_DIR/secrets.env" ]; then
        install -m 600 /dev/null "$ETC_DIR/secrets.env"
        getent group docker >/dev/null && chown root:docker "$ETC_DIR/secrets.env"
    fi

    local missing; missing="$(configs_missing_secret_keys "$ETC_DIR/secrets.env")"
    if [ -n "$missing" ]; then
        echo
        echo "⚠ $ETC_DIR/secrets.env に次のキーがありません。値を入れてください:"
        printf '    %s=\n' $missing
        echo "  値は現行機で 'sudo cat $ETC_DIR/secrets.env' を表示して手入力すること。"
        echo "  (scp や共有フォルダを経由させない)"
    fi

    echo "==> timesyncd を設定"
    bash "$REPO_DIR/scripts/install.sh" >/dev/null || true
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
```

- [ ] **Step 4: 実行して成功を確認**

Run: `.venv/bin/pytest scripts/tests/test_bootstrap_configs.py -v`
Expected: 7 passed

- [ ] **Step 5: コミット**

```bash
git add scripts/bootstrap/40-configs.sh scripts/tests/test_bootstrap_configs.py
git commit -m "$(cat <<'EOF'
feat(bootstrap): phase 40, generate /etc/presence-logger from site.env

Renders profiles.yaml without a station: block so the parent's own
station lives in device.yaml alone -- the site snapshot carries it in
both, which is two places to forget. Creates upcmpflg.override as a
file first: install.sh never made it, and docker turns a missing bind
mount into a directory.

Secrets are never written here; the phase reports which keys are missing
and leaves the values to a person.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: フェーズ50 — 子AP 構築

**Files:**
- Create: `scripts/bootstrap/50-ap.sh`
- Create: `scripts/tests/test_bootstrap_ap.py`

**Interfaces:**
- Consumes: `site_env_require`（Task 1）、既存 `desktop/presence-tools/setup-dongle-ap.sh`
- Produces:
  - `ap_duplicate_ssid_present <ssid>` — 周囲に同名SSIDが見えたら 0
  - `ap_needs_explicit_address` — `AP_GW_IP` が `10.42.0.1` 以外なら 0
  - `ap_env_args` — `setup-dongle-ap.sh` に渡す環境変数を `KEY=VALUE` の1行1件で

- [ ] **Step 1: 失敗するテストを書く**

```python
"""フェーズ50(子AP)を検証する。

同一SSIDのAPが2つ生きると子がどちらに繋ぐか不定になり、DEPLOY.md に記録の
ある相互切断事故と同じ構図になる。AP を上げる *前* に止めるのが要点。
"""
import os
import textwrap

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/bootstrap/50-ap.sh"

SITE = textwrap.dedent("""\
    AP_IF=wlan1
    AP_SSID=presence-hub
    AP_GW_IP=10.42.0.1
    AP_BAND=bg
    AP_CHANNEL=6
    HOME_SSID=UFI_103134
    """)


def _site(tmp_path, body=SITE):
    f = tmp_path / "site.env"; f.write_text(body, encoding="utf-8"); return f


def test_duplicate_ssid_is_detected(tmp_path, fake_bin):
    fake_bin("nmcli", 'echo "presence-hub:70:WPA2"')
    f = _site(tmp_path)
    proc = run_bash(f'{SOURCE}; site_env_load "{f}"; ap_duplicate_ssid_present presence-hub',
                    env=dict(os.environ), check=False)
    assert proc.returncode == 0


def test_no_duplicate_when_absent(tmp_path, fake_bin):
    fake_bin("nmcli", 'echo "some-other-ap:70:WPA2"')
    f = _site(tmp_path)
    proc = run_bash(f'{SOURCE}; site_env_load "{f}"; ap_duplicate_ssid_present presence-hub',
                    env=dict(os.environ), check=False)
    assert proc.returncode != 0


def test_default_gateway_needs_no_explicit_address(tmp_path):
    f = _site(tmp_path)
    proc = run_bash(f'{SOURCE}; site_env_load "{f}"; ap_needs_explicit_address',
                    env=dict(os.environ), check=False)
    assert proc.returncode != 0


def test_non_default_gateway_needs_explicit_address(tmp_path):
    # ipv4.method shared は 10.42.0.1/24 を自動で付ける。他の値にするには
    # ipv4.addresses の明示指定が要る
    f = _site(tmp_path, SITE.replace("AP_GW_IP=10.42.0.1", "AP_GW_IP=10.43.0.1"))
    proc = run_bash(f'{SOURCE}; site_env_load "{f}"; ap_needs_explicit_address',
                    env=dict(os.environ), check=False)
    assert proc.returncode == 0


def test_env_args_forward_site_values(tmp_path):
    f = _site(tmp_path)
    out = run_bash(f'{SOURCE}; site_env_load "{f}"; ap_env_args',
                   env=dict(os.environ)).stdout
    assert "AP_IF=wlan1" in out
    assert "AP_SSID=presence-hub" in out
    assert "AP_CHANNEL=6" in out
    assert "UFI_CONN=UFI_103134" in out
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `.venv/bin/pytest scripts/tests/test_bootstrap_ap.py -v`
Expected: FAIL

- [ ] **Step 3: 実装**

```bash
#!/usr/bin/env bash
# 50-ap.sh — 子Pi 用の AP を立てる。実処理は既存の setup-dongle-ap.sh に任せ、
# ここは site.env の値を環境変数として渡すことと、同一SSID の重複検出を担う。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"

ap_duplicate_ssid_present() {
    local ssid="${1:-$AP_SSID}"
    nmcli -t -f SSID,SIGNAL,SECURITY dev wifi list 2>/dev/null \
        | cut -d: -f1 | grep -qFx "$ssid"
}

# setup-dongle-ap.sh は ipv4.method shared を使うため、GW IP は NetworkManager が
# 既定の 10.42.0.1/24 を自動で付ける。それ以外にするには ipv4.addresses の
# 明示指定が要る(=増設時。子側の send_target_config.json の変更も必要)。
ap_needs_explicit_address() {
    [ "${AP_GW_IP:-10.42.0.1}" != "10.42.0.1" ]
}

ap_env_args() {
    printf 'AP_IF=%s\n'      "$AP_IF"
    printf 'AP_SSID=%s\n'    "$AP_SSID"
    printf 'AP_BAND=%s\n'    "$AP_BAND"
    printf 'AP_CHANNEL=%s\n' "$AP_CHANNEL"
    printf 'UFI_CONN=%s\n'   "$HOME_SSID"
    printf 'AP_CONN=%s-ap\n' "$AP_SSID"
}

main() {
    site_env_require
    if [ "${1:-}" != "--force" ] && ap_duplicate_ssid_present "$AP_SSID"; then
        cat >&2 <<EOF
⚠ 同じ SSID の AP が既に見えています: $AP_SSID

  このまま起動すると同名の AP が2つになり、子Piがどちらに繋ぐか不定になります
  (DEPLOY.md に記録のある相互切断事故と同じ構図)。

  引っ越しなら、先に旧ハブの AP を落としてください:
      旧ハブで: sudo nmcli connection down ${AP_SSID}-ap
                sudo nmcli connection modify ${AP_SSID}-ap connection.autoconnect no

  増設なら、site.env の AP_SSID と AP_GW_IP を別の値にしてください。

  それでも続けるなら: bash $0 --force
EOF
        return 1
    fi

    local kv
    while IFS= read -r kv; do export "${kv?}"; done < <(ap_env_args)
    bash "$REPO_DIR/desktop/presence-tools/setup-dongle-ap.sh" || return 1

    if ap_needs_explicit_address; then
        echo "==> AP のゲートウェイIPを $AP_GW_IP に固定"
        nmcli connection modify "${AP_SSID}-ap" ipv4.addresses "${AP_GW_IP}/24"
        nmcli connection up "${AP_SSID}-ap"
        cat <<EOF

⚠ AP のIPが既定(10.42.0.1)ではありません。各子Pi の
  ~/send_target_config.json の "host" を $AP_GW_IP へ変更する必要があります。
EOF
    fi
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
```

- [ ] **Step 4: 実行して成功を確認**

Run: `.venv/bin/pytest scripts/tests/test_bootstrap_ap.py -v`
Expected: 5 passed

- [ ] **Step 5: コミット**

```bash
git add scripts/bootstrap/50-ap.sh scripts/tests/test_bootstrap_ap.py
git commit -m "$(cat <<'EOF'
feat(bootstrap): phase 50, bring up the child AP

Refuses to raise an AP whose SSID is already on the air. Forgetting to
take the old hub's AP down is the one mistake that reproduces the
mutual-disconnect incident in DEPLOY.md, and it is only catchable before
the second AP exists.

ipv4.method shared always hands out 10.42.0.1, so a different gateway
needs ipv4.addresses set explicitly -- and the children pointed at it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: フェーズ60 — ハブモードのコンテナ起動と常駐化

**Files:**
- Create: `scripts/bootstrap/60-stack.sh`
- Create: `scripts/tests/test_bootstrap_stack.py`
- Modify: `docker-compose.yml`（detector に `profiles`）
- Modify: `docker-compose.override.yml`（`AP_GW_IP` 変数化）

**Interfaces:**
- Consumes: `site_env_require`
- Produces:
  - `stack_services` — ハブで起動するサービス名を stdout へ（`HUB_MODE=1` なら detector を含めない）
  - `stack_write_env <path>` — compose 用 `.env` に `AP_GW_IP` を書く

- [ ] **Step 1: 失敗するテストを書く**

```python
"""フェーズ60(コンテナ起動と常駐化)を検証する。

detector を profiles: に入れる理由は2つ。カメラ無しのハブで誤って起動しない
ことと、clone 直後に `up -d --build` が detector のビルドで失敗しないこと
(Dockerfile が gitignore された .tflite を COPY するため)。
"""
import os
import subprocess
import textwrap

import yaml

from scripts.tests.shellhelp import REPO_ROOT, run_bash

SOURCE = "source scripts/bootstrap/60-stack.sh"

SITE = textwrap.dedent("""\
    HUB_MODE=1
    AP_GW_IP=10.42.0.1
    """)


def test_hub_mode_excludes_detector(tmp_path):
    f = tmp_path / "site.env"; f.write_text(SITE, encoding="utf-8")
    out = run_bash(f'{SOURCE}; site_env_load "{f}"; stack_services',
                   env=dict(os.environ)).stdout.split()
    assert "detector" not in out
    assert set(out) == {"mosquitto", "oracle-jdbc", "bridge"}


def test_camera_mode_includes_detector(tmp_path):
    f = tmp_path / "site.env"; f.write_text("HUB_MODE=0\nAP_GW_IP=10.42.0.1\n",
                                            encoding="utf-8")
    out = run_bash(f'{SOURCE}; site_env_load "{f}"; stack_services',
                   env=dict(os.environ)).stdout.split()
    assert "detector" in out


def test_env_file_carries_the_ap_gateway(tmp_path):
    f = tmp_path / "site.env"; f.write_text(SITE, encoding="utf-8")
    dst = tmp_path / ".env"
    run_bash(f'{SOURCE}; site_env_load "{f}"; stack_write_env "{dst}"',
             env=dict(os.environ))
    assert "AP_GW_IP=10.42.0.1" in dst.read_text(encoding="utf-8")


def test_detector_is_behind_a_compose_profile():
    doc = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert doc["services"]["detector"].get("profiles") == ["camera"]


def test_default_compose_config_has_no_detector():
    # profiles 指定なしの `docker compose config` に detector が現れないこと
    out = subprocess.run(  # noqa: S603
        ["docker", "compose", "config", "--services"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        import pytest
        pytest.skip("docker compose が使えない環境")
    assert "detector" not in out.stdout.split()


def test_override_binds_the_configurable_gateway():
    body = (REPO_ROOT / "docker-compose.override.yml").read_text(encoding="utf-8")
    assert "${AP_GW_IP:-10.42.0.1}:1883:1883" in body
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `.venv/bin/pytest scripts/tests/test_bootstrap_stack.py -v`
Expected: FAIL

- [ ] **Step 3: `docker-compose.yml` の detector に `profiles` を足す**

`detector:` サービスの `container_name` の直後に追加する。既存のコメントは残す。

```yaml
  detector:
    build: ./services/detector
    container_name: presence-detector
    # カメラ無しのハブでは起動しない。`docker compose --profile camera up -d`
    # と明示したときだけ対象になる。clone 直後に `up -d --build` が
    # gitignore された .tflite の COPY で失敗するのも、これで防げる。
    profiles: ["camera"]
    restart: unless-stopped
```

- [ ] **Step 4: `docker-compose.override.yml` の bind を変数化**

```yaml
services:
  mosquitto:
    ports:
      - "${AP_GW_IP:-10.42.0.1}:1883:1883"
```

冒頭のコメントに1行足す: `# AP_GW_IP は scripts/bootstrap/60-stack.sh が .env に書き出す。`

- [ ] **Step 5: `scripts/bootstrap/60-stack.sh` を実装**

```bash
#!/usr/bin/env bash
# 60-stack.sh — コンテナを起動し、再起動でも復活するようにする。
#
# 前提: 子AP が上がっていること。docker-compose.override.yml が mosquitto を
# AP のゲートウェイIPにバインドするため、そのIPが存在しないと起動できない。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"

stack_services() {
    printf 'mosquitto\noracle-jdbc\nbridge\n'
    [ "${HUB_MODE:-1}" = "1" ] || printf 'detector\n'
}

stack_write_env() {
    local dst="${1:-$REPO_DIR/.env}"
    printf '# 自動生成: scripts/bootstrap/60-stack.sh\nAP_GW_IP=%s\n' "$AP_GW_IP" > "$dst"
}

main() {
    site_env_require
    stack_write_env "$REPO_DIR/.env"

    if ! ip -4 addr show | grep -q "inet $AP_GW_IP/"; then
        echo "⚠ $AP_GW_IP がホストに付いていません。先にフェーズ50(子AP)を実行してください" >&2
        return 1
    fi

    echo "==> コンテナを起動"
    local svcs; svcs="$(stack_services | tr '\n' ' ')"
    # shellcheck disable=SC2086
    ( cd "$REPO_DIR" && docker compose up -d --build $svcs ) || return 1

    echo "==> systemd で常駐化"
    HUB_MODE="$HUB_MODE" REPO_DIR="$REPO_DIR" \
        bash "$REPO_DIR/desktop/presence-tools/setup-autostart.sh" || return 1

    echo "==> フリート監視を常駐化"
    install -m 644 "$REPO_DIR/fleet_ui/systemd/fleet-ui.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now fleet-ui.service

    docker ps --format '    {{.Names}}  {{.Status}}'
    ss -ltn | grep 8090 || true
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
```

- [ ] **Step 6: `setup-autostart.sh` を `HUB_MODE` 対応にする**

`ExecStartPost=-/usr/bin/docker stop presence-detector` の行を、`HUB_MODE=1` のときは出力しないようにする（ハブに detector は存在しないため）。

- [ ] **Step 7: 実行して成功を確認**

Run: `.venv/bin/pytest scripts/tests/test_bootstrap_stack.py -v`
Expected: 6 passed（docker が無い環境では1件 skip）

- [ ] **Step 8: コミット**

```bash
git add docker-compose.yml docker-compose.override.yml scripts/bootstrap/60-stack.sh \
        desktop/presence-tools/setup-autostart.sh scripts/tests/test_bootstrap_stack.py
git commit -m "$(cat <<'EOF'
feat(bootstrap): phase 60, hub-mode stack and autostart

Puts detector behind a compose profile. That keeps a camera-less hub
from running it, and it also fixes a fresh clone: detector's image
copies a gitignored .tflite, so `up -d --build` failed before reaching
the three services a hub actually needs.

Refuses to start if the AP gateway IP is not on the host -- mosquitto
binds it, and without the AP it would retry forever instead of saying
why.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: 既存デスクトップツールのハブモード対応

**Files:**
- Modify: `desktop/presence-tools/connect-hime-h-reap.sh`
- Modify: `desktop/presence-tools/disconnect-hime-h-reap.sh`
- Modify: `desktop/presence-tools/watch-records.sh`
- Modify: `desktop/presence-tools/show-recent-records.sh`
- Create: `tests/desktop/test_hub_mode.py`

**Interfaces:**
- Consumes: `site_env_load`（Task 1）
- Produces:
  - 各スクリプトが `HUB_MODE=1` のとき detector を触らない
  - `show-recent-records.sh`: `recent_default_sta_no` が `HUB_MODE=1` のとき `*` を返す

- [ ] **Step 1: 失敗するテストを書く**

```python
"""ハブ(カメラ無し)で既存デスクトップツールが壊れないことを検証する。

3つとも detector コンテナの存在を前提にしている:
 - connect/disconnect は docker start/stop presence-detector を叩く
 - watch-records は docker logs presence-detector を購読する
 - show-recent-records は絞り込み既定値を親の device.yaml station から取る
   (ハブでは placeholder なので、既定のままだと常に0件になる)
"""
import os

from scripts.tests.shellhelp import run_bash


def test_connect_does_not_touch_detector_in_hub_mode(fake_bin):
    fake_bin("docker", 'printf "docker %s\\n" "$*" >> "$FAKE_LOG"')
    run_bash(
        'source desktop/presence-tools/connect-hime-h-reap.sh; '
        'HUB_MODE=1 detector_start',
        env=dict(os.environ), check=False,
    )
    assert "presence-detector" not in fake_bin.log.read_text(encoding="utf-8")


def test_connect_starts_detector_when_camera_present(fake_bin):
    fake_bin("docker", 'printf "docker %s\\n" "$*" >> "$FAKE_LOG"')
    run_bash(
        'source desktop/presence-tools/connect-hime-h-reap.sh; '
        'HUB_MODE=0 detector_start',
        env=dict(os.environ), check=False,
    )
    assert "presence-detector" in fake_bin.log.read_text(encoding="utf-8")


def test_watch_records_streams_bridge_only_in_hub_mode():
    out = run_bash(
        'source desktop/presence-tools/watch-records.sh; '
        'HUB_MODE=1 watch_containers',
        env=dict(os.environ)).stdout.split()
    assert out == ["presence-bridge"]


def test_watch_records_streams_both_with_camera():
    out = run_bash(
        'source desktop/presence-tools/watch-records.sh; '
        'HUB_MODE=0 watch_containers',
        env=dict(os.environ)).stdout.split()
    assert "presence-detector" in out and "presence-bridge" in out


def test_recent_records_defaults_to_all_stations_in_hub_mode():
    # ハブでは親の局番は placeholder。既定で絞ると常に0件になる
    out = run_bash(
        'source desktop/presence-tools/show-recent-records.sh; '
        'HUB_MODE=1 recent_default_sta_no sta_no1 996',
        env=dict(os.environ)).stdout.strip()
    assert out == "*"
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `.venv/bin/pytest tests/desktop/test_hub_mode.py -v`
Expected: FAIL

- [ ] **Step 3: 4本を「関数 + main ガード」に組み替える（テストの前提）**

**この組み替えを先に行わないと Step 1 のテストは書いても動かない。** 4本とも
トップレベルで副作用を持っているため、`source` した瞬間に実行されてしまう。

| ファイル | source 時に起きてしまうこと |
|---|---|
| `connect-hime-h-reap.sh` | 冒頭の `[[ $EUID -ne 0 ]] && exec sudo bash "$0" "$@"` が**自分を sudo で再実行する** |
| `disconnect-hime-h-reap.sh` | 同上 |
| `watch-records.sh` | `docker logs` の購読が始まりブロックする |
| `show-recent-records.sh` | 対話の `read -rp` で入力待ちになる |

各ファイルについて、トップレベルの処理を `main()` に移し、末尾を次にする。

```bash
[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
```

root 昇格（`exec sudo`）も `main` の**先頭**へ移す。振る舞いは変わらない
（直接実行したときは従来どおり sudo で再実行される）が、`source` しても何も起きなくなる。

- [ ] **Step 4: 4本を改修**

各スクリプトの先頭で `site.env` を読み込む（存在しなければ既定値のまま動く）:

```bash
# site.env があれば読む(HUB_MODE / HOME_SSID / PROFILE_NAME 等)。
# 無い機体でも従来どおり動くよう、失敗は無視する。
_SITE_ENV="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/site.env"
# shellcheck disable=SC1090
[ -f "$_SITE_ENV" ] && { set -a; source "$_SITE_ENV"; set +a; }
HUB_MODE="${HUB_MODE:-0}"
```

`connect-hime-h-reap.sh` — detector 起動を関数に切り出す:

```bash
detector_start() {
    [ "$HUB_MODE" = "1" ] && { say "■ ハブ構成のため検知(detector)は起動しません"; return 0; }
    docker start presence-detector >/dev/null 2>&1
}
```

`disconnect-hime-h-reap.sh` — 同様に `detector_stop`。既定の戻り先を `HOME_SSID` から取る:

```bash
HOME_PROFILE="${HOME_PROFILE:-${HOME_SSID:-UFI_103134}}"

detector_stop() {
    [ "$HUB_MODE" = "1" ] && { say "■ ハブ構成のため検知の停止は不要です"; return 0; }
    docker stop presence-detector >/dev/null 2>&1 && say "■ 検知を停止しました（detector）"
}
```

`watch-records.sh` — 購読対象を関数に:

```bash
watch_containers() {
    [ "$HUB_MODE" = "1" ] || printf 'presence-detector\n'
    printf 'presence-bridge\n'
}
```

`:35-38` の `docker logs` 呼び出しを `watch_containers` の出力でループするよう書き換える。

`show-recent-records.sh` — 既定値の決定を関数に:

```bash
recent_default_sta_no() {
    # ハブ(カメラ無し)では親の局番は placeholder であり、Oracle には子の局番しか
    # 無い。既定のまま絞ると常に0件になるので「すべて」を既定にする。
    [ "$HUB_MODE" = "1" ] && { printf '*\n'; return 0; }
    printf '%s\n' "$2"
}
```

`:76-78` の `read -rp` の既定表示と `:92-94` の解決を、この関数経由にする。

- [ ] **Step 5: 実行して成功を確認**

Run: `.venv/bin/pytest tests/desktop/test_hub_mode.py -v`
Expected: 5 passed

- [ ] **Step 6: 既存テストが壊れていないことを確認**

Run: `.venv/bin/pytest tests/desktop scripts/tests -q`
Expected: すべて pass

- [ ] **Step 7: 手で1回動かして、組み替えで壊れていないことを確認**

Run: `bash desktop/presence-tools/show-recent-records.sh 5`
Expected: 従来どおり対話プロンプトが出て、直近5件が表示される

- [ ] **Step 8: コミット**

```bash
git add desktop/presence-tools/connect-hime-h-reap.sh \
        desktop/presence-tools/disconnect-hime-h-reap.sh \
        desktop/presence-tools/watch-records.sh \
        desktop/presence-tools/show-recent-records.sh \
        tests/desktop/test_hub_mode.py
git commit -m "$(cat <<'EOF'
feat(desktop): survive a hub with no detector container

All three tools assumed the detector exists: two start and stop it, the
monitor tails its logs, and the recent-records view defaults its filter
to the parent's own station -- which on a hub is a placeholder, so the
default silently returned nothing.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: WiFi 切替の Git 取り込みと一般化

**Files:**
- Create: `desktop/wifi-switch/switch-wifi.sh`
- Create: `desktop/wifi-switch/launcher.desktop.tmpl`
- Create: `wifi-switch.conf.example`
- Create: `tests/desktop/test_wifi_switch.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `switch-wifi.sh <接続名> <ifname> [--warn-disconnect <SSID>]`
  - `wifi_switch_parse_conf <file>` — `名前|IF|表示名|警告|PSKキー` を `|` 区切りで1行1件
  - `warn_disconnect_message <SSID>` — 切断警告文を stdout へ
  - `switch_wifi <接続名> <ifname>` — 切替の実体

- [ ] **Step 1: 失敗するテストを書く**

```python
"""WiFi切替(旧 ~/Desktop/WiFi切替)を検証する。

旧版は --away-from-f66 という現行機固有の前提を名前に埋めていた。新機では
保守用SSIDが別名になり得るので、SSID名は呼び出し側から与える。
"""
import os
import textwrap

from scripts.tests.shellhelp import run_bash

SOURCE = "source desktop/wifi-switch/switch-wifi.sh"

CONF = textwrap.dedent("""\
    # <NM接続名>       <ifname>  <表示名>       <警告>  <PSKキー名>
    F660P-sDcS-A       wlan0     F66            -       WIFI_PSK_F66
    GallaxyS23FE       wlan0     GallaxyS23FE   warn    WIFI_PSK_GALAXY
    presence-hub-ap    wlan1     presence-hub   -       -
    """)


def test_conf_is_parsed_into_records(tmp_path):
    f = tmp_path / "wifi-switch.conf"; f.write_text(CONF, encoding="utf-8")
    out = run_bash(f'{SOURCE}; wifi_switch_parse_conf "{f}"',
                   env=dict(os.environ)).stdout.strip().splitlines()
    assert out[0] == "F660P-sDcS-A|wlan0|F66|-|WIFI_PSK_F66"
    assert out[2] == "presence-hub-ap|wlan1|presence-hub|-|-"
    assert len(out) == 3          # コメント行は落ちる


def test_switch_calls_nmcli_with_connection_and_interface(fake_bin):
    fake_bin("nmcli", 'printf "nmcli %s\\n" "$*" >> "$FAKE_LOG"; exit 0')
    run_bash(f'{SOURCE}; switch_wifi F660P-sDcS-A wlan0',
             env=dict(os.environ), check=False)
    log = fake_bin.log.read_text(encoding="utf-8")
    assert "connection up F660P-sDcS-A ifname wlan0" in log


def test_warning_names_the_admin_ssid():
    out = run_bash(f'{SOURCE}; warn_disconnect_message F660P-sDcS-A',
                   env=dict(os.environ)).stdout
    assert "F660P-sDcS-A" in out
    assert "away-from-f66" not in out        # 現行機固有の名前を残さない


def test_ap_row_carries_a_dash_for_its_psk_key(tmp_path):
    # presence-hub-ap はフェーズ50 が作る。播種側(Task 11)はこの "-" を見て
    # 二重作成を避けるため、パース結果に残っていることが前提になる
    f = tmp_path / "wifi-switch.conf"; f.write_text(CONF, encoding="utf-8")
    out = run_bash(f'{SOURCE}; wifi_switch_parse_conf "{f}"',
                   env=dict(os.environ)).stdout.strip().splitlines()
    ap = [r for r in out if r.startswith("presence-hub-ap")][0]
    assert ap.endswith("|-")
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `.venv/bin/pytest tests/desktop/test_wifi_switch.py -v`
Expected: FAIL

- [ ] **Step 3: `desktop/wifi-switch/switch-wifi.sh` を実装**

```bash
#!/usr/bin/env bash
# switch-wifi.sh <接続名> <ifname> [--warn-disconnect <保守用SSID>]
#
# 既に NetworkManager に保存済みの接続へ切り替えるだけ。パスワードは持たない。
# 保存済みでない接続へは切り替えられない(フェーズ70 が secrets.env から作る)。
set -uo pipefail

wifi_switch_parse_conf() {
    local f="${1:?}"
    grep -v '^[[:space:]]*#' "$f" | grep -v '^[[:space:]]*$' \
        | awk '{printf "%s|%s|%s|%s|%s\n", $1, $2, $3, $4, $5}'
}

warn_disconnect_message() {
    local admin_ssid="${1:?}"
    cat <<EOF
⚠ 注意: このネットワークに切り替えると、$admin_ssid から離れます。
   $admin_ssid 経由でしか届かない接続(遠隔操作・Claude Code 等)は切れます。
   実行中の作業があれば、先に終わらせてください。
EOF
}

switch_wifi() {
    local conn="${1:?接続名}" ifname="${2:?ifname}"
    echo "→ $ifname を $conn に切り替えます..."
    if nmcli --wait 20 connection up "$conn" ifname "$ifname" >/dev/null 2>&1; then
        echo "✅ 接続しました: $conn"
    else
        echo "❌ 接続に失敗しました（圏外か、保存情報が無い可能性があります）"
        echo "   保存済み接続の一覧: nmcli -t -f NAME connection show"
        return 1
    fi
    nmcli -t -f DEVICE,STATE,CONNECTION dev status | grep "^${ifname}:" || true
}

main() {
    local conn="${1:?接続名を指定してください}" ifname="${2:?ifname を指定してください}"
    if [ "${3:-}" = "--warn-disconnect" ]; then
        warn_disconnect_message "${4:?保守用SSID}"
        read -r -p "続行しますか？ (y/N): " ans
        case "$ans" in y|Y) ;; *) echo "中止しました。"; return 0 ;; esac
        echo
    fi
    switch_wifi "$conn" "$ifname"
    echo; read -n1 -r -p 'Enterキーで閉じます... ' _
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
```

- [ ] **Step 4: `launcher.desktop.tmpl` と `wifi-switch.conf.example` を作る**

```
[Desktop Entry]
Type=Application
Version=1.0
Name=__LABEL__ に接続 (__IFNAME__)
Name[ja]=__LABEL__ に接続 (__IFNAME__)
Comment=__COMMENT__
Exec=lxterminal -t "__LABEL__ に接続" -e bash "__TOOLS_DIR__/switch-wifi.sh" "__CONN__" "__IFNAME__"__WARN_ARG__
Icon=network-wireless
Terminal=false
Categories=Network;
```

`wifi-switch.conf.example`:

```
# wifi-switch.conf — デスクトップ「WiFi切替」に並べるランチャーの定義。
#   cp wifi-switch.conf.example wifi-switch.conf && chmod 600 wifi-switch.conf
# 機体固有・Git管理外。1行1ランチャー。'#' 以降はコメント。
#
# <警告> が warn の行は、切り替え前に「site.env の ADMIN_SSID から離れる」旨を
# 表示して y/N を取る。
# <PSKキー名> が '-' の行は nmcli プロファイルを作らない。presence-hub-ap は
# フェーズ50 の setup-dongle-ap.sh が作るため、必ず '-' にすること。
#
# <NM接続名>       <ifname>  <表示名>       <警告>  <PSKキー名(secrets.env)>
F660P-sDcS-A       wlan0     F66            -       WIFI_PSK_F66
GallaxyS23FE       wlan0     GallaxyS23FE   warn    WIFI_PSK_GALAXY
UFI_103134         wlan0     UFI_103134     warn    WIFI_PSK_UFI
presence-hub-ap    wlan1     presence-hub   -       -
```

- [ ] **Step 5: 実行して成功を確認**

Run: `.venv/bin/pytest tests/desktop/test_wifi_switch.py -v`
Expected: 4 passed

- [ ] **Step 6: コミット**

```bash
git add desktop/wifi-switch wifi-switch.conf.example tests/desktop/test_wifi_switch.py
git commit -m "$(cat <<'EOF'
feat(desktop): bring the WiFi switcher into the repo

It only ever existed on this Pi's SD card, and it named this Pi's
premise in its own flag: --away-from-f66, for the one network that
reaches Claude from here. The SSID is an argument now, so a second hub
warns about whatever its own maintenance network is.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: フェーズ70 — デスクトップ配置と nmcli 播種

**Files:**
- Create: `scripts/bootstrap/70-desktop.sh`
- Create: `desktop/launchers/フリート管理.desktop`
- Create: `scripts/tests/test_bootstrap_desktop.py`
- Modify: `desktop/launchers/*.desktop`（`/home/pi/` → `__TOOLS_DIR__` / `__HOME__`）

**Interfaces:**
- Consumes: `site_env_require`、`wifi_switch_parse_conf`（Task 10）
- Produces:
  - `desktop_render_launcher <tmpl> <conn> <ifname> <label> <warn> <tools_dir> <admin_ssid>`
  - `desktop_seed_profile <conn> <ifname> <psk_key> <secrets_file>` — 未保存なら nmcli プロファイルを作る

- [ ] **Step 1: 失敗するテストを書く**

```python
"""フェーズ70(デスクトップ配置)を検証する。

既存ランチャーは /home/pi/Desktop/... を直書きしており、ユーザー名が pi 以外
だと壊れる。テンプレートから生成する。
"""
import os
import textwrap

from scripts.tests.shellhelp import REPO_ROOT, run_bash

SOURCE = "source scripts/bootstrap/70-desktop.sh"


def test_launcher_has_no_hardcoded_home():
    for p in (REPO_ROOT / "desktop" / "launchers").glob("*.desktop"):
        body = p.read_text(encoding="utf-8")
        assert "/home/pi/" not in body, f"{p.name} に /home/pi/ が残っている"


def test_rendered_launcher_points_at_the_given_tools_dir(tmp_path):
    tmpl = REPO_ROOT / "desktop" / "wifi-switch" / "launcher.desktop.tmpl"
    out = run_bash(
        f'{SOURCE}; desktop_render_launcher "{tmpl}" F660P-sDcS-A wlan0 F66 - '
        f'"/home/alice/Desktop/presence-tools" F660P-sDcS-A',
        env=dict(os.environ)).stdout
    assert "/home/alice/Desktop/presence-tools/switch-wifi.sh" in out
    assert "__TOOLS_DIR__" not in out


def test_warn_row_gets_the_warn_argument(tmp_path):
    tmpl = REPO_ROOT / "desktop" / "wifi-switch" / "launcher.desktop.tmpl"
    out = run_bash(
        f'{SOURCE}; desktop_render_launcher "{tmpl}" GallaxyS23FE wlan0 GallaxyS23FE warn '
        f'"/tmp/tools" F660P-sDcS-A',
        env=dict(os.environ)).stdout
    assert '--warn-disconnect "F660P-sDcS-A"' in out


def test_existing_profile_is_not_recreated(tmp_path, fake_bin):
    fake_bin("nmcli", '''
if [ "$1" = "-t" ]; then echo "F660P-sDcS-A"; exit 0; fi
printf "nmcli %s\\n" "$*" >> "$FAKE_LOG"
''')
    secrets = tmp_path / "secrets.env"
    secrets.write_text("WIFI_PSK_F66=hunter2\n", encoding="utf-8")
    run_bash(
        f'{SOURCE}; desktop_seed_profile F660P-sDcS-A wlan0 WIFI_PSK_F66 "{secrets}"',
        env=dict(os.environ), check=False)
    assert "connection add" not in fake_bin.log.read_text(encoding="utf-8")


def test_missing_psk_key_is_reported_not_silently_skipped(tmp_path, fake_bin):
    fake_bin("nmcli", 'if [ "$1" = "-t" ]; then exit 0; fi; exit 0')
    secrets = tmp_path / "secrets.env"
    secrets.write_text("", encoding="utf-8")
    proc = run_bash(
        f'{SOURCE}; desktop_seed_profile F660P-sDcS-A wlan0 WIFI_PSK_F66 "{secrets}"',
        env=dict(os.environ), check=False)
    assert proc.returncode != 0
    assert "WIFI_PSK_F66" in proc.stderr
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `.venv/bin/pytest scripts/tests/test_bootstrap_desktop.py -v`
Expected: FAIL

- [ ] **Step 3: 既存ランチャー5本をテンプレート化**

`desktop/launchers/*.desktop` の `/home/pi/Desktop/presence-tools` を `__TOOLS_DIR__` に置換。`HIME-H-REAP-切断.desktop` の Comment 内 `UFI_103134` を `__HOME_SSID__` にする。`フリート管理.desktop` を新規作成（`Exec=chromium --app=http://localhost:8090`）。

- [ ] **Step 4: `scripts/bootstrap/70-desktop.sh` を実装**

```bash
#!/usr/bin/env bash
# 70-desktop.sh — デスクトップのアイコン一式を配置し、WiFi切替の接続を播種する。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
ETC_DIR="${ETC_DIR:-/etc/presence-logger}"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"
# shellcheck source=desktop/wifi-switch/switch-wifi.sh
source "$REPO_DIR/desktop/wifi-switch/switch-wifi.sh"

desktop_render_launcher() {
    local tmpl="$1" conn="$2" ifname="$3" label="$4" warn="$5" tools="$6" admin="$7"
    local warn_arg="" comment="$label に切り替えます"
    if [ "$warn" = "warn" ]; then
        warn_arg=" --warn-disconnect \"$admin\""
        comment="$label に切り替えます（$admin から離れます）"
    fi
    sed -e "s|__LABEL__|$label|g" -e "s|__IFNAME__|$ifname|g" \
        -e "s|__CONN__|$conn|g" -e "s|__TOOLS_DIR__|$tools|g" \
        -e "s|__COMMENT__|$comment|g" -e "s|__WARN_ARG__|$warn_arg|g" "$tmpl"
}

desktop_seed_profile() {
    local conn="$1" ifname="$2" psk_key="$3" secrets="${4:-$ETC_DIR/secrets.env}"
    nmcli -t -f NAME connection show 2>/dev/null | grep -qFx "$conn" && return 0
    local psk; psk="$(grep -E "^$psk_key=" "$secrets" 2>/dev/null | head -1 | cut -d= -f2-)"
    if [ -z "$psk" ]; then
        echo "$psk_key が $secrets にありません。$conn は作成しませんでした" >&2
        return 1
    fi
    nmcli connection add type wifi con-name "$conn" ifname "$ifname" ssid "$conn" \
        802-11-wireless-security.key-mgmt wpa-psk \
        802-11-wireless-security.psk "$psk" \
        connection.autoconnect yes >/dev/null
}

main() {
    site_env_require
    local user="${SUDO_USER:-$USER}" home tools desk
    home="$(getent passwd "$user" | cut -d: -f6)"
    desk="$home/Desktop"; tools="$desk/presence-tools"

    echo "==> ツール本体を配置"
    install -d -o "$user" -g "$user" "$desk" "$tools" "$desk/WiFi切替"
    cp -r "$REPO_DIR/desktop/presence-tools/." "$tools/"
    cp "$REPO_DIR/desktop/wifi-switch/switch-wifi.sh" "$desk/WiFi切替/"
    chmod +x "$tools"/*.sh "$desk/WiFi切替/switch-wifi.sh"

    echo "==> ランチャーを生成"
    local f
    for f in "$REPO_DIR"/desktop/launchers/*.desktop; do
        sed -e "s|__TOOLS_DIR__|$tools|g" -e "s|__HOME_SSID__|$HOME_SSID|g" \
            "$f" > "$desk/$(basename "$f")"
    done

    local conf="$REPO_DIR/wifi-switch.conf"
    if [ -f "$conf" ]; then
        echo "==> WiFi切替のランチャーと接続を用意"
        local rec conn ifname label warn key
        while IFS='|' read -r conn ifname label warn key; do
            desktop_render_launcher "$REPO_DIR/desktop/wifi-switch/launcher.desktop.tmpl" \
                "$conn" "$ifname" "$label" "$warn" "$desk/WiFi切替" "$ADMIN_SSID" \
                > "$desk/WiFi切替/$label.desktop"
            [ "$key" = "-" ] || desktop_seed_profile "$conn" "$ifname" "$key" || true
        done < <(wifi_switch_parse_conf "$conf")
    else
        echo "wifi-switch.conf がありません。WiFi切替はスキップします"
    fi

    chown -R "$user:$user" "$desk"
    chmod +x "$desk"/*.desktop "$desk/WiFi切替"/*.desktop
    echo "✅ デスクトップに配置しました（初回はアイコンの「信頼して実行」が要ります）"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
```

- [ ] **Step 5: 実行して成功を確認**

Run: `.venv/bin/pytest scripts/tests/test_bootstrap_desktop.py -v`
Expected: 5 passed

- [ ] **Step 6: コミット**

```bash
git add scripts/bootstrap/70-desktop.sh desktop/launchers scripts/tests/test_bootstrap_desktop.py
git commit -m "$(cat <<'EOF'
feat(bootstrap): phase 70, desktop launchers and saved WiFi profiles

Launchers are rendered from templates instead of carrying /home/pi in
their Exec lines. The switcher can only raise profiles NetworkManager
already has, so this phase creates them from secrets.env -- and reports
a missing key rather than leaving an icon that does nothing when
clicked. The AP profile is left to phase 50, which owns it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: `fleet_ui` の AP インターフェース名を環境変数化

**Files:**
- Modify: `fleet_ui/discovery.py:32`
- Modify: `fleet_ui/provision.py:134`
- Modify: `fleet_ui/tests/test_discovery.py`（既存を壊さないこと）
- Create: `fleet_ui/tests/test_ap_dev.py`

**Interfaces:**
- Consumes: なし
- Produces: `discovery.AP_DEV` を `os.environ.get("AP_DEV", "wlan1")` に。`provision.wait_for_return` は `discovery.AP_DEV` を参照する

- [ ] **Step 1: 失敗するテストを書く**

```python
"""AP インターフェース名が環境変数で差し替えられることを検証する。

新しいハブでドングルが wlan0 として現れると、wlan1 決め打ちのままでは
子が1台も表示されず、wait_for_return も永久に成功しない。
discovery と provision に同じ文字列が二重にあるのも是正する。
"""
import importlib

from fleet_ui import provision


def test_ap_dev_defaults_to_wlan1(monkeypatch):
    monkeypatch.delenv("AP_DEV", raising=False)
    from fleet_ui import discovery
    importlib.reload(discovery)
    assert discovery.AP_DEV == "wlan1"


def test_ap_dev_is_overridable(monkeypatch):
    monkeypatch.setenv("AP_DEV", "wlan0")
    from fleet_ui import discovery
    importlib.reload(discovery)
    assert discovery.AP_DEV == "wlan0"


def test_wait_for_return_uses_the_configured_device(monkeypatch):
    monkeypatch.setenv("AP_DEV", "wlan0")
    from fleet_ui import discovery
    importlib.reload(discovery)
    importlib.reload(provision)
    seen = []

    def runner(cmd):
        seen.append(cmd)
        return "10.42.0.9 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n"

    provision.wait_for_return("aa:bb:cc:dd:ee:ff", runner=runner, sleeper=lambda _: None)
    assert seen[0] == ["ip", "-4", "neigh", "show", "dev", "wlan0"]
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `.venv/bin/pytest fleet_ui/tests/test_ap_dev.py -v`
Expected: FAIL（`provision` が `"wlan1"` をべた書きしている）

- [ ] **Step 3: 実装**

`fleet_ui/discovery.py:32`:

```python
# 子APのインターフェース名。機体によってドングルが wlan0 として現れることが
# あるため環境変数で差し替えられる(site.env の AP_IF を渡す)。
AP_DEV = os.environ.get("AP_DEV", "wlan1")
```

`fleet_ui/provision.py:134` — べた書きをやめて `discovery.AP_DEV` を参照する:

```python
for n in parse_neigh(runner(["ip", "-4", "neigh", "show", "dev", discovery.AP_DEV])):
```

`fleet_ui/systemd/fleet-ui.service` に `Environment=AP_DEV=wlan1` を追加し、コメントで「site.env の `AP_IF` と一致させること」と書く。

- [ ] **Step 4: 実行して成功を確認**

Run: `.venv/bin/pytest fleet_ui/tests -v`
Expected: すべて pass（既存テストを含む）

- [ ] **Step 5: コミット**

```bash
git add fleet_ui/discovery.py fleet_ui/provision.py fleet_ui/systemd/fleet-ui.service \
        fleet_ui/tests/test_ap_dev.py
git commit -m "$(cat <<'EOF'
fix(fleet-ui): stop hardcoding the AP interface in two places

discovery had AP_DEV and provision had the same "wlan1" typed out again,
so they could drift. On a hub where the dongle enumerates as wlan0 the
fleet view lists no children at all and wait_for_return never returns --
a failure that looks like the children are missing.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: 文書を実装に合わせる

**Files:**
- Modify: `docs/NEW-HUB-SETUP.md`
- Modify: `docs/sd-clone-pattern-b.md`
- Modify: `README.md`

- [ ] **Step 1: `NEW-HUB-SETUP.md` の「自動（実装後）」を実物に合わせる**

冒頭の「**実装前の制約**」ブロックを削除し、各節の「自動（実装後）」を実際のコマンドに置き換える。手動手順は**残す**（スクリプトが失敗したときの拠り所になる）。

- [ ] **Step 2: `docs/child-migration.md` を新規作成**

設計書 §9 の引っ越し手順を、運用者が単体で追える形にまとめる。含めるもの:

1. 旧ハブで未送信を送り切る確認コマンド（`docker exec presence-bridge` 経由）
2. **新ハブの公開鍵を各子へ配る**（旧親が到達できるうちに。省くと全工程が失敗する）
3. 旧ハブの AP を落とす（`nmcli connection down` + `autoconnect no`）
4. 新ハブで AP 起動 → 子が自動で繋ぎ替わる
5. `scripts/fleet-status.sh` が exit 0（`1`=STA_NO重複 / `2`=検査不能。`2` を `0` と混同しない）
6. `pipeline-monitor.sh` で ②→③→④ を同一 event_id で追う
7. 増設（両ハブ同時稼働）の場合の差分 — `AP_SSID` / `AP_GW_IP` を分け、子の
   `send_target_config.json` の `host` を変更し、STA_NO が全ハブを通じて一意であることを確認する

- [ ] **Step 3: `sd-clone-pattern-b.md` に `site.env` の扱いを追記**

クローンでは `site.env` も複製されるため、§2 の一覧に追加し、§3 に書き換え手順を足す。

- [ ] **Step 4: `README.md` の「本番インストール」節を更新**

現在の `/opt/presence-logger` へ clone する手順に、ハブ構成のときは `scripts/bootstrap-hub.sh` を使う旨を追記し、`docs/NEW-HUB-SETUP.md` へリンクする。

- [ ] **Step 5: 全テストを流す**

Run: `.venv/bin/pytest -q`
Expected: すべて pass

- [ ] **Step 6: lint**

Run: `.venv/bin/ruff check .`
Expected: All checks passed

- [ ] **Step 7: コミット**

```bash
git add docs/NEW-HUB-SETUP.md docs/child-migration.md docs/sd-clone-pattern-b.md README.md
git commit -m "$(cat <<'EOF'
docs: point the setup guides at the bootstrap scripts

Keeps the manual steps beside the automated ones -- they are what an
operator falls back on when a phase fails at a customer site with no
network.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## 完了の定義

`docs/superpowers/specs/2026-09-09-new-hub-bootstrap-design.md` §10 の受入基準 14 項目を、
**実機の新ハブ**で確認できること。特に次の3つは机上では確認できない。

1. 再ログイン後に日本語入力できること（フェーズ10）
2. 子Pi が新ハブの AP に繋ぎ替わり `scripts/fleet-status.sh` が **exit 0** を返すこと
3. `pipeline-monitor.sh` で同一 event_id を ②MQTT → ③record_inbox → ④Oracle と追えること
