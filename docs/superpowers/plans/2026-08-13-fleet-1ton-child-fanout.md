# 親1:子多 配備（設定所有権の是正 ＋ 既存bashのフリート化）実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 子ラズパイの機体固有設定が配布で上書きされる既存の破壊リスクを止め、親1台から複数の子へ1台ずつ安全に配布・状態確認できるようにする。

**Architecture:** 既存の実機検証済みbash（`scripts/deploy-child.sh` / `deploy-model.sh` / `lib/deploy-common.sh`）の安全処理（バックアップ・3層ヘルスチェック・自動ロールバック）は一切再実装しない。(1) 配布対象ファイルを「コード / フリート共通設定 / 機体固有設定」の3分類に分け、機体固有設定を配布対象から外す。(2) 子のIPをデプロイ時に子自身から取得し、DHCPドリフトによる誤ホストへのヘルスチェックを構造的に防ぐ。(3) インベントリ（平文1行1台）を読んで既存スクリプトを直列に呼ぶ薄いドライバを追加する。判断ロジック（STA_NO重複検出）だけは純Pythonに切り出し、pytestで直接テストする。

**Tech Stack:** bash 5（新規依存なし。coreutils / rsync / ssh / curl / python3 のみ）、pytest（既存）、ruff（既存）

**Spec:** `docs/superpowers/specs/2026-08-13-fleet-1ton-child-fanout-design.md`

## Global Constraints

- **機体固有設定は配布しない。** `id_names_config.json` / `threshold_config.json` / `recognition_config.json` / `save_config.json` / `model_config.json` / `crop_config.json` は子のWeb UIでオペレーターが編集する端末状態であり、どのフラグでも配布対象に入れてはならない。
- **既存の安全処理を再実装しない。** バックアップ・3層ヘルスチェック・自動ロールバックは `scripts/lib/deploy-common.sh` の既存関数をそのまま使う。
- **Ansibleを導入しない。** 中央ops・複数親・ProxyJump は本計画のスコープ外。
- **新規依存を増やさない。** `yq` などの追加ツールは使わない。インベントリは平文で `sed`/`grep` で解析する。
- **runtime 状態を絶対に触らない。** `send_target_state.json`・`logs/`・`raw_images/` は従来どおり配布対象外。
- Python は ruff 準拠（`line-length = 100`、`target-version = "py313"`）。操作用CLIの `print` は `# ruff: noqa: T201` を先頭に置く（`child/child-csv-to-mqtt.py` の先例に従う）。
- bash スクリプトは `set -euo pipefail` を維持する。
- コメント・ログ・ドキュメントは既存に合わせて日本語で書く。

---

### Task 1: 配布ファイルの3分類（deploy-common.sh）

`CHILD_CONFIG_FILES` という単一のくくりをやめ、「フリート共通」と「機体固有」に分ける。旧名は**残さず削除**する（`set -u` により、取り残された参照が黙って機体固有設定を配布するのではなく即エラーになる）。

**Files:**
- Modify: `scripts/lib/deploy-common.sh:26-33`
- Modify: `pyproject.toml`（`testpaths` に `scripts/tests` を追加）
- Create: `scripts/__init__.py`（空）
- Create: `scripts/tests/__init__.py`（空）
- Create: `scripts/tests/shellhelp.py`
- Test: `scripts/tests/test_deploy_common_lists.py`

**Interfaces:**
- Consumes: なし（最初のタスク）
- Produces:
  - bash 配列 `CHILD_CODE_FILES`（既存・変更なし）
  - bash 配列 `CHILD_SHARED_CONFIG_FILES` = `(status_code_config.json send_target_config.json)`
  - bash 配列 `CHILD_DEVICE_OWNED_FILES` = `(id_names_config.json threshold_config.json recognition_config.json save_config.json model_config.json crop_config.json)`
  - bash 配列 `CHILD_ALL_CONFIG_FILES` = 上2つの連結（バックアップ対象に使う）
  - `CHILD_CONFIG_FILES` は**削除される**
  - pytest ヘルパ `scripts.tests.shellhelp.run_bash(script, *, env=None, check=True) -> subprocess.CompletedProcess` と定数 `REPO_ROOT`

- [ ] **Step 1: テスト用ヘルパを作る**

`scripts/__init__.py` と `scripts/tests/__init__.py` は**空ファイル**で作成する。
リポジトリ既存のテストは root の `conftest.py`（`sys.path` にリポジトリルートを挿入）を前提に
`from tests.integration.fakes import ...` のような完全修飾importを使う。同じ流儀に揃える
（`--import-mode=importlib` のため `from conftest import ...` のような相対importは使えない）。

`scripts/tests/shellhelp.py`:

```python
"""scripts/ 配下のシェルスクリプトを検証するための共通ヘルパ。

シェルスクリプトは pytest から subprocess で実行して振る舞いを確認する。
リポジトリに bats は入っていないため、テストランナーは pytest に統一する。
"""
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_bash(script: str, *, env: dict | None = None,
             check: bool = True) -> subprocess.CompletedProcess:
    """bash スニペットをリポジトリルートで実行して結果を返す。"""
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )
```

- [ ] **Step 2: 失敗するテストを書く**

`scripts/tests/test_deploy_common_lists.py`:

```python
"""配布ファイルの3分類が、機体固有設定を配布対象から確実に外していることを検証する。

id_names_config.json は region_id -> STA_NO1..3 の割当であり、子ごとに必ず異なる。
これを配布すると現場の割当が無警告で上書きされ、Oracle に誤った STA_NO が流れる。
"""
from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/lib/deploy-common.sh"


def _array(name: str) -> list[str]:
    out = run_bash(f'{SOURCE}; printf "%s\\n" "${{{name}[@]}}"').stdout
    return [line for line in out.splitlines() if line]


def test_device_owned_files_include_id_names():
    assert "id_names_config.json" in _array("CHILD_DEVICE_OWNED_FILES")


def test_device_owned_files_cover_all_operator_editable_configs():
    expected = {
        "id_names_config.json", "threshold_config.json", "recognition_config.json",
        "save_config.json", "model_config.json", "crop_config.json",
    }
    assert set(_array("CHILD_DEVICE_OWNED_FILES")) == expected


def test_shared_config_files_are_fleet_wide_only():
    assert set(_array("CHILD_SHARED_CONFIG_FILES")) == {
        "status_code_config.json", "send_target_config.json",
    }


def test_shared_and_device_owned_do_not_overlap():
    shared = set(_array("CHILD_SHARED_CONFIG_FILES"))
    owned = set(_array("CHILD_DEVICE_OWNED_FILES"))
    assert shared & owned == set()


def test_all_config_files_is_the_union():
    assert set(_array("CHILD_ALL_CONFIG_FILES")) == (
        set(_array("CHILD_SHARED_CONFIG_FILES")) | set(_array("CHILD_DEVICE_OWNED_FILES"))
    )


def test_legacy_child_config_files_is_removed():
    """旧名が残っていると、取り残された参照が黙って機体固有設定を配布し得る。"""
    proc = run_bash(f'{SOURCE}; echo "${{CHILD_CONFIG_FILES[@]}}"', check=False)
    assert proc.returncode != 0
```

- [ ] **Step 3: テストを実行して失敗を確認する**

まず `pyproject.toml` の `[tool.pytest.ini_options]` の `testpaths` に `"scripts/tests"` を追加する:

```toml
testpaths = ["services/detector/tests", "services/bridge/tests", "tests/integration", "tests/desktop", "scripts/tests"]
```

Run: `python -m pytest scripts/tests/test_deploy_common_lists.py -v`
Expected: FAIL（`CHILD_DEVICE_OWNED_FILES` が未定義のため空配列 or unbound variable エラー）

- [ ] **Step 4: deploy-common.sh の分類を実装する**

`scripts/lib/deploy-common.sh` の既存ブロック:

```bash
# 子アプリの「正」= リポジトリ child/ 配下。~ 直下(flat)へ配る。
# runtime 状態(send_target_state.json / logs 等)は *絶対に* 触らない → 一覧に入れない。
CHILD_CODE_FILES=(Picamera.py web_server.py child-csv-to-mqtt.py index.html)
CHILD_CONFIG_FILES=(
  crop_config.json id_names_config.json model_config.json
  recognition_config.json save_config.json send_target_config.json
  status_code_config.json threshold_config.json
)
```

を、次で置き換える:

```bash
# 子アプリの「正」= リポジトリ child/ 配下。~ 直下(flat)へ配る。
# runtime 状態(send_target_state.json / logs 等)は *絶対に* 触らない → 一覧に入れない。
CHILD_CODE_FILES=(Picamera.py web_server.py child-csv-to-mqtt.py index.html)

# フリート共通の設定。全機体で同じ値であるべきものだけを置く。
# 子のWeb UIからも編集できるため、配布は --with-shared-config 指定時のみ(既定は配らない)。
CHILD_SHARED_CONFIG_FILES=(status_code_config.json send_target_config.json)

# 機体固有の設定。子のWeb UI(:8080)でオペレーターが設定する *端末の状態* であり、
# 配布すると現場の設定を無警告で破壊する。どのフラグでも配布しない。
#   id_names_config.json  : region_id -> STA_NO1..3 の割当。子ごとに必ず異なる。
#                           Oracle の MERGE キーは device_id を含まないため、重複すると
#                           レコードが無警告で欠落する(oracle_client.py の MERGE 条件参照)。
#   threshold/recognition : カメラ個体ごとの検出調整・画角
#   save_config           : 保存トグル(現場の一時設定)
#   model_config          : モデル割当。deploy-model.sh が管理する。
#   crop_config           : child/*.py から参照なし(未使用の可能性。削除判断は別途)
CHILD_DEVICE_OWNED_FILES=(
  id_names_config.json threshold_config.json recognition_config.json
  save_config.json model_config.json crop_config.json
)

# バックアップ対象。配布しないファイルも退避しておく(復旧手段は維持する)。
CHILD_ALL_CONFIG_FILES=("${CHILD_SHARED_CONFIG_FILES[@]}" "${CHILD_DEVICE_OWNED_FILES[@]}")
```

- [ ] **Step 5: テストを実行して成功を確認する**

Run: `python -m pytest scripts/tests/test_deploy_common_lists.py -v`
Expected: 6 passed

この時点で `scripts/deploy-child.sh` は `CHILD_CONFIG_FILES` を参照したまま壊れている。Task 2 で直す。

- [ ] **Step 6: コミット**

```bash
git add scripts/__init__.py scripts/lib/deploy-common.sh scripts/tests pyproject.toml
git commit -m "refactor(deploy): split child files into code / shared / device-owned"
```

---

### Task 2: 配布の既定をコードのみにする（deploy-child.sh）

既定で機体固有設定を配らないようにする。子1台の現状でも即座に効果がある安全修正。

**Files:**
- Modify: `scripts/deploy-child.sh:8-14`（usage）、`:21-28`（引数解析）、`:33-35`（配布対象）、`:52`（バックアップ）
- Modify: `docs/DEPLOY.md`
- Test: `scripts/tests/test_deploy_child_filelist.py`

**Interfaces:**
- Consumes: `CHILD_CODE_FILES` / `CHILD_SHARED_CONFIG_FILES` / `CHILD_DEVICE_OWNED_FILES` / `CHILD_ALL_CONFIG_FILES`（Task 1）、`run_bash`（Task 1）
- Produces: `scripts/deploy-child.sh` の CLI
  - 既定 = コード + systemd unit のみ
  - `--with-shared-config` = フリート共通2ファイルも配布
  - `--code-only` = 既定と同義の非推奨エイリアス（受理のみ、動作は既定と同じ）
- Produces: pytest フィクスチャ `fake_bin(name, body) -> Path`（`scripts/tests/conftest.py`）。
  `fake_bin.log` に実行記録の `Path` を持つ。

- [ ] **Step 1: 偽コマンド用フィクスチャを作る**

`scripts/tests/conftest.py`（新規作成。フィクスチャはconftest経由で自動的に配られるため
import は不要）:

```python
"""scripts/ 配下のシェルスクリプト検証で使う pytest フィクスチャ。"""
import os
from pathlib import Path

import pytest


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    """PATH の先頭に偽コマンドを置くためのフィクスチャ。

    ssh / rsync / curl を本物に触らせずにスクリプトの分岐を検証する。
    使い方: fake_bin("rsync", 'printf "%s\\n" "$@" >> "$FAKE_LOG"')
    実行記録は fake_bin.log (Path) に追記される。
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    log.touch()

    def _install(name: str, body: str) -> Path:
        p = bindir / name
        p.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
        p.chmod(0o755)
        return p

    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    _install.log = log
    _install.bindir = bindir
    return _install
```

なお `run_bash` に `env=dict(os.environ)` を渡すのは、`monkeypatch.setenv` が書き換えた
`PATH` と `FAKE_LOG` を子プロセスへ確実に引き継ぐためである。

- [ ] **Step 2: 失敗するテストを書く**

`scripts/tests/test_deploy_child_filelist.py`:

```python
"""deploy-child.sh が既定で機体固有設定を配らないことを検証する。

--dry-run は rsync までしか到達しないため、偽 rsync に渡された引数を見れば
「何を配ろうとしたか」を実際のコードパスで確認できる。
"""
import os

from scripts.tests.shellhelp import run_bash

DEVICE_OWNED = [
    "id_names_config.json", "threshold_config.json", "recognition_config.json",
    "save_config.json", "model_config.json", "crop_config.json",
]


def _dry_run(fake_bin, args: str) -> str:
    fake_bin("rsync", 'printf "%s\\n" "$@" >> "$FAKE_LOG"')
    run_bash(f"scripts/deploy-child.sh --dry-run {args}", env=dict(os.environ))
    return fake_bin.log.read_text(encoding="utf-8")


def test_default_does_not_distribute_device_owned_files(fake_bin):
    out = _dry_run(fake_bin, "")
    for name in DEVICE_OWNED:
        assert name not in out, f"{name} を既定で配布しようとしている"


def test_default_distributes_code_files(fake_bin):
    out = _dry_run(fake_bin, "")
    assert "Picamera.py" in out
    assert "web_server.py" in out


def test_default_does_not_distribute_shared_config(fake_bin):
    out = _dry_run(fake_bin, "")
    assert "status_code_config.json" not in out


def test_with_shared_config_distributes_shared_only(fake_bin):
    out = _dry_run(fake_bin, "--with-shared-config")
    assert "status_code_config.json" in out
    assert "send_target_config.json" in out
    for name in DEVICE_OWNED:
        assert name not in out, f"--with-shared-config で {name} を配布しようとしている"


def test_code_only_is_accepted_as_deprecated_alias(fake_bin):
    """既存手順を壊さないため、--code-only は受理して既定と同じ動作にする。"""
    out = _dry_run(fake_bin, "--code-only")
    assert "Picamera.py" in out
    for name in DEVICE_OWNED:
        assert name not in out
```

- [ ] **Step 3: テストを実行して失敗を確認する**

Run: `python -m pytest scripts/tests/test_deploy_child_filelist.py -v`
Expected: FAIL（Task 1 で `CHILD_CONFIG_FILES` を消したため `unbound variable` でスクリプトが落ちる）

- [ ] **Step 4: deploy-child.sh を実装する**

usage コメント（`:8-14`）を次に置き換える:

```bash
# 使い方:
#   scripts/deploy-child.sh                      # コード + systemd unit を配布（既定）
#   scripts/deploy-child.sh --with-shared-config # フリート共通設定も配布
#   scripts/deploy-child.sh --dry-run            # 何を送るかだけ表示（変更なし）
#   scripts/deploy-child.sh --no-rollback        # 失敗しても自動復元しない
#
# 機体固有設定(id_names/threshold/recognition/save/model/crop)は *配布しない*。
# 子のWeb UIで設定する端末の状態であり、配布すると現場の設定を無警告で壊すため。
```

引数解析（`:21-28`）を次に置き換える:

```bash
WITH_SHARED=0; DRY_RUN=0; ROLLBACK=1
for a in "$@"; do case "$a" in
  --with-shared-config) WITH_SHARED=1 ;;
  --code-only)   ;;  # 非推奨: 既定と同義。既存手順を壊さないため受理のみする
  --dry-run)     DRY_RUN=1 ;;
  --no-rollback) ROLLBACK=0 ;;
  # 冒頭コメントを最初の空行まで出す。行番号を固定しないので、
  # コメントを増減しても -h がソースを漏らさない。
  -h|--help)     sed -n '2,/^$/p' "$0"; exit 0 ;;
  *) die "unknown arg: $a" ;;
esac; done
```

配布対象（`:33-35`）を次に置き換える:

```bash
# 配布対象を決める（機体固有設定は決して含めない）
files=("${CHILD_CODE_FILES[@]}")
[ "$WITH_SHARED" -eq 0 ] || files+=("${CHILD_SHARED_CONFIG_FILES[@]}")
```

ログ行（`:41`）の `code_only=$CODE_ONLY` を `with_shared=$WITH_SHARED` に直す。

バックアップ（`:52`）を次に置き換える（配らないファイルも退避して復旧手段を残す）:

```bash
child_backup "$TS" "${CHILD_CODE_FILES[@]}" "${CHILD_ALL_CONFIG_FILES[@]}"
```

- [ ] **Step 5: テストを実行して成功を確認する**

Run: `python -m pytest scripts/tests/ -v`
Expected: 11 passed（Task 1 の6件 + 本タスクの5件）

- [ ] **Step 6: DEPLOY.md を更新する**

`docs/DEPLOY.md` の「### 子アプリを更新する」節を次に置き換える:

```markdown
### 子アプリを更新する
```bash
# 1. child/ の中を編集（Picamera.py など）
scripts/deploy-child.sh --dry-run      # 送る差分を確認
scripts/deploy-child.sh                # 配布 → 再起動 → 検証 →(失敗時)自動戻し
```

**機体固有設定は配布されない。** `id_names_config.json`（region_id→STA_NO割当）・
`threshold_config.json`・`recognition_config.json`・`save_config.json`・
`model_config.json`・`crop_config.json` は、子のWeb UI(:8080)でオペレーターが
設定する端末の状態であり、配布対象から外してある。設定は各子のWeb UIで行う。

フリート共通の設定（`status_code_config.json`・`send_target_config.json`）を
配りたいときだけ `--with-shared-config` を付ける。現場で `send_target_config` の
`enabled` を一時的にoffにしている子があると再有効化される点に注意。
```

同ファイルの「## 安全設計」の1項目目を次に置き換える:

```markdown
- **runtime 状態と機体固有設定を絶対に触らない**：`send_target_state.json`（送信カーソル）や
  `logs/`・`raw_images/` に加え、機体固有設定（`id_names_config.json` 等、
  `CHILD_DEVICE_OWNED_FILES`）も配布対象外。上書きすると送信巻き戻り/実データ消失、
  および STA_NO 誤送信になるため。
```

- [ ] **Step 7: コミット**

```bash
git add scripts/deploy-child.sh scripts/tests/conftest.py scripts/tests/test_deploy_child_filelist.py docs/DEPLOY.md
git commit -m "fix(deploy): stop overwriting device-owned child config by default"
```

---

### Task 3: 子のIPを実行時に子自身から取得する

インベントリに固定IPを書かせないための土台。陳腐化したIPへのヘルスチェックが**別の子に対して成功判定を出す**危険を構造的に消す。

**Files:**
- Modify: `scripts/lib/deploy-common.sh:17`（`CHILD_AP_IP` の既定値）、`:42-48`（`require_child_reachable`）
- Modify: `docs/DEPLOY.md`（環境変数の節）
- Test: `scripts/tests/test_child_ap_ip.py`

**Interfaces:**
- Consumes: `rc()` / `die()` / `ok()`（既存）、`fake_bin`（Task 2）
- Produces: bash 関数 `child_resolve_ap_ip()` — `CHILD_AP_IP` が空なら子から取得して同変数へ設定する。明示指定されていれば何もしない。取得できなければ `die`。

- [ ] **Step 1: 失敗するテストを書く**

`scripts/tests/test_child_ap_ip.py`:

```python
"""子のIPを子自身から取得することを検証する。

子のIPは親APのDHCP動的割当であり、インベントリや既定値に固定IPを持つと陳腐化する。
陳腐化したIPへのヘルスチェックは「別の子」に対して成功判定を出しうるため危険。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/lib/deploy-common.sh"


def test_derives_ipv4_from_child(fake_bin):
    # hostname -I は "10.42.0.99 fe80::1" のように複数返す
    fake_bin("ssh", 'echo "10.42.0.99 fe80::1"')
    out = run_bash(
        f'{SOURCE}; CHILD_AP_IP=""; child_resolve_ap_ip; echo "RESULT=$CHILD_AP_IP"',
        env=dict(os.environ),
    ).stdout
    assert "RESULT=10.42.0.99" in out


def test_explicit_value_wins(fake_bin):
    fake_bin("ssh", 'echo "10.42.0.99"')
    out = run_bash(
        f'{SOURCE}; CHILD_AP_IP="10.42.0.7"; child_resolve_ap_ip; echo "RESULT=$CHILD_AP_IP"',
        env=dict(os.environ),
    ).stdout
    assert "RESULT=10.42.0.7" in out


def test_fails_loudly_when_ip_cannot_be_obtained(fake_bin):
    fake_bin("ssh", "exit 1")
    proc = run_bash(
        f'{SOURCE}; CHILD_AP_IP=""; child_resolve_ap_ip', env=dict(os.environ), check=False
    )
    assert proc.returncode != 0


def test_ignores_ipv6_only_output(fake_bin):
    fake_bin("ssh", 'echo "fe80::1 fd00::2"')
    proc = run_bash(
        f'{SOURCE}; CHILD_AP_IP=""; child_resolve_ap_ip', env=dict(os.environ), check=False
    )
    assert proc.returncode != 0
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest scripts/tests/test_child_ap_ip.py -v`
Expected: FAIL（`child_resolve_ap_ip: command not found`）

- [ ] **Step 3: deploy-common.sh に実装する**

`CHILD_AP_IP` の行（`:17`）を次に置き換える:

```bash
# 子IP。空 = デプロイ時に子自身から取得する(DHCP動的割当のため固定値を持たない)。
# 明示指定した場合はそれを優先する。
CHILD_AP_IP="${CHILD_AP_IP:-}"
```

`require_child_reachable()` の直前に次の関数を追加する:

```bash
# ---- 子IPの解決 -------------------------------------------------------------
# 子の実IPを子自身から取得する。親APのDHCPでIPが変わっても、常に正しい相手を
# ヘルスチェックできる。インベントリに固定IPを持たせないための土台でもある。
child_resolve_ap_ip() {
  [ -z "$CHILD_AP_IP" ] || { ok "子IP(指定値): $CHILD_AP_IP"; return 0; }
  local ip=""
  ip="$(rc 'hostname -I' 2>/dev/null | tr ' ' '\n' \
        | grep -E '^[0-9]+(\.[0-9]+){3}$' | head -n1)" || true
  [ -n "$ip" ] || die "子($CHILD_SSH)のIPv4アドレスを取得できません"
  CHILD_AP_IP="$ip"
  ok "子IP(子から取得): $CHILD_AP_IP"
}
```

`require_child_reachable()` の中で、SSH到達確認の直後に `child_resolve_ap_ip` を呼ぶ:

```bash
require_child_reachable() {
  log "子($CHILD_SSH) 到達確認"
  rc 'echo ok >/dev/null' || die "$CHILD_SSH に SSH できません"
  child_resolve_ap_ip
  local free_mb; free_mb=$(rc "df -Pm \$HOME | awk 'NR==2{print \$4}'") || free_mb=0
  [ "${free_mb:-0}" -gt 100 ] || die "子のディスク空きが少なすぎます (${free_mb}MB)"
  ok "到達OK / 空き ${free_mb}MB"
}
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `python -m pytest scripts/tests/ -v`
Expected: 15 passed

- [ ] **Step 5: DEPLOY.md の環境変数節を更新する**

`docs/DEPLOY.md` 末尾の環境変数の記述を次に置き換える（既存の `CHILD_AP_IP`(10.42.0.51) は現行コードの既定値 10.42.0.52 とも食い違っていたので、記述ごと差し替える）:

```markdown
`CHILD_SSH`(既定 zero2) / `CHILD_AP_IP`(既定は空＝子から自動取得) /
`CHILD_WEB_PORT`(8080) / `KEEP_BACKUPS`(5) / `HEALTH_STABLE_WAIT`(6秒)
— 詳細は `scripts/lib/deploy-common.sh`。

子のIPは親APのDHCP動的割当のため、既定では**デプロイ時に子自身から取得**する
（`hostname -I` の先頭IPv4）。固定したい場合のみ `CHILD_AP_IP` を明示指定する。
```

- [ ] **Step 6: コミット**

```bash
git add scripts/lib/deploy-common.sh scripts/tests/test_child_ap_ip.py docs/DEPLOY.md
git commit -m "fix(deploy): resolve child IP from the child itself instead of a fixed default"
```

---

### Task 4: インベントリ `fleet/children.conf` と解析関数

**Files:**
- Create: `fleet/children.conf`
- Modify: `scripts/lib/deploy-common.sh`（末尾に追加）
- Test: `scripts/tests/test_fleet_inventory.py`

**Interfaces:**
- Consumes: `die()`（既存）
- Produces:
  - 変数 `FLEET_INVENTORY`（既定 `$REPO_DIR/fleet/children.conf`、環境変数で上書き可）
  - bash 関数 `fleet_read_inventory [path]` — SSH到達名を1行1件で標準出力へ返す。`#` 以降はコメント、空行と前後空白は無視。有効ホストが0件ならエラー終了。

- [ ] **Step 1: 失敗するテストを書く**

`scripts/tests/test_fleet_inventory.py`:

```python
"""インベントリ解析の検証。

インベントリにIPは書かない（DHCPで変わるため実行時に子から取得する）。
1行1台のSSH到達名のみを持つ。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/lib/deploy-common.sh"


def _read(tmp_path, content: str, check: bool = True):
    f = tmp_path / "children.conf"
    f.write_text(content, encoding="utf-8")
    return run_bash(
        f'{SOURCE}; fleet_read_inventory "{f}"', env=dict(os.environ), check=check
    )


def test_reads_plain_host_list(tmp_path):
    out = _read(tmp_path, "zero2\nzero2b\n").stdout
    assert out.splitlines() == ["zero2", "zero2b"]


def test_skips_comments_and_blank_lines(tmp_path):
    content = "# 見出し\n\nzero2\n   \n# 途中のコメント\nzero2b\n"
    assert _read(tmp_path, content).stdout.splitlines() == ["zero2", "zero2b"]


def test_strips_inline_comments_and_surrounding_space(tmp_path):
    content = "  zero2   # 1号機\nzero2b\t# 2号機\n"
    assert _read(tmp_path, content).stdout.splitlines() == ["zero2", "zero2b"]


def test_accepts_mdns_names(tmp_path):
    assert _read(tmp_path, "pizero2w.local\n").stdout.splitlines() == ["pizero2w.local"]


def test_deduplicates_repeated_hosts(tmp_path):
    """手編集のコピペ重複で、同じ子へ二重配布・二重再起動しないこと。"""
    content = "zero2\nzero2b\nzero2\n"
    assert _read(tmp_path, content).stdout.splitlines() == ["zero2", "zero2b"]


def test_dedup_preserves_first_occurrence_order(tmp_path):
    content = "c\na\nc\nb\na\n"
    assert _read(tmp_path, content).stdout.splitlines() == ["c", "a", "b"]


def test_strips_cr_from_crlf_file(tmp_path):
    """Windows で編集されたインベントリでもホスト名に \\r が残らないこと。"""
    content = "zero2\r\nzero2b\r\n"
    assert _read(tmp_path, content).stdout.splitlines() == ["zero2", "zero2b"]


def test_fails_when_no_valid_host(tmp_path):
    assert _read(tmp_path, "# コメントだけ\n\n", check=False).returncode != 0


def test_fails_when_file_missing(tmp_path):
    proc = run_bash(
        f'{SOURCE}; fleet_read_inventory "{tmp_path}/nope.conf"',
        env=dict(os.environ), check=False,
    )
    assert proc.returncode != 0


def test_repository_inventory_is_parseable():
    """同梱のインベントリが常に解析可能であること。"""
    out = run_bash(f"{SOURCE}; fleet_read_inventory", env=dict(os.environ)).stdout
    assert "zero2" in out.split()
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest scripts/tests/test_fleet_inventory.py -v`
Expected: FAIL（`fleet_read_inventory: command not found`）

- [ ] **Step 3: インベントリファイルを作る**

`fleet/children.conf`:

```
# presence-logger — 親AP配下の子ラズパイ一覧
#
# 1行1台。SSH到達名を書く（~/.ssh/config の Host 名、または mDNS の <hostname>.local）。
# IPは書かない: 親APのDHCPで変わるため、デプロイ時に子自身から取得する。
# '#' 以降はコメント。空行は無視される。
#
# 子を追加したら、STA_NO(id_names_config.json)が他機と重複しないことを
# scripts/fleet-status.sh で確認すること。重複すると Oracle でレコードが無警告で欠落する。

zero2
```

- [ ] **Step 4: 解析関数を実装する**

`scripts/lib/deploy-common.sh` の末尾に追加:

```bash
# ---- フリート・インベントリ -------------------------------------------------
FLEET_INVENTORY="${FLEET_INVENTORY:-$REPO_DIR/fleet/children.conf}"

# インベントリを読み、SSH到達名を1行1件で標準出力へ返す。
# '#' 以降はコメント、空行と前後の空白は無視する。有効ホストが0件ならエラー。
#
# 末尾空白の除去は CRLF(Windows で編集した場合の \r)も落とす。\r は [[:space:]] に
# 含まれるため意図的にここで吸収している(将来 s/[[:space:]]*$// を単純化しないこと)。
#
# 重複行は取り除く(順序は保つ)。インベントリは手編集するファイルで、子を追加する際の
# コピペ重複が現実に起こる。ここで1回だけ潰しておけば、配布ドライバ・状態表示・--only
# 展開のすべてが守られ、同じ子へ二重配布・二重再起動することがない。
fleet_read_inventory() {
  local f="${1:-$FLEET_INVENTORY}"
  [ -f "$f" ] || die "インベントリが無い: $f"
  local out
  out="$(sed -e 's/#.*//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' "$f" \
         | grep -v '^$' | awk '!seen[$0]++' || true)"
  [ -n "$out" ] || die "インベントリに有効なホストがありません: $f"
  printf '%s\n' "$out"
}
```

- [ ] **Step 5: テストを実行して成功を確認する**

Run: `python -m pytest scripts/tests/ -v`
Expected: 22 passed

- [ ] **Step 6: コミット**

```bash
git add fleet/children.conf scripts/lib/deploy-common.sh scripts/tests/test_fleet_inventory.py
git commit -m "feat(fleet): add child inventory file and parser"
```

---

### Task 5: `scripts/deploy-fleet.sh`（直列配布ドライバ）

インベントリを読み、対象ごとに既存スクリプトを直列に呼ぶだけの薄いドライバ。各子の配布・ヘルスチェック・ロールバックは既存ロジックがそのまま担う。

**Files:**
- Create: `scripts/deploy-fleet.sh`
- Modify: `docs/DEPLOY.md`
- Test: `scripts/tests/test_deploy_fleet.py`

**Interfaces:**
- Consumes: `fleet_read_inventory()`（Task 4）、`log()`/`ok()`/`warn()`/`die()`（既存）
- Produces: CLI
  - `scripts/deploy-fleet.sh app [--only h1,h2] [--dry-run] [--no-rollback] [--with-shared-config] [--keep-going]`
  - `scripts/deploy-fleet.sh model <name> <version> [--only ...] [--keep-going]`
  - 既定は fail-fast（1台失敗で以降を中止）。全成功で exit 0、1台でも失敗で exit 1。
  - テスト用の環境変数フック: `FLEET_CHILD_SCRIPT` / `FLEET_MODEL_SCRIPT`（既定は同ディレクトリの実スクリプト）

- [ ] **Step 1: 失敗するテストを書く**

`scripts/tests/test_deploy_fleet.py`:

```python
"""フリート直列配布ドライバの検証。

各子への実処理は既存 deploy-child.sh が担うため、ここではループ制御
（順序・fail-fast・--only・サマリ・終了コード）だけを偽スクリプトで検証する。
"""
import os

from scripts.tests.shellhelp import run_bash


def _inventory(tmp_path, hosts: list[str]):
    f = tmp_path / "children.conf"
    f.write_text("".join(f"{h}\n" for h in hosts), encoding="utf-8")
    return f


def _stub(tmp_path, name: str, body: str):
    p = tmp_path / name
    p.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    p.chmod(0o755)
    return p


def _env(tmp_path, inv, child_stub):
    log = tmp_path / "fleet.log"
    log.touch()  # 1台も実行されなかった場合でも read_text() できるようにする
    e = dict(os.environ)
    e["FLEET_INVENTORY"] = str(inv)
    e["FLEET_CHILD_SCRIPT"] = str(child_stub)
    e["FLEET_LOG"] = str(log)
    return e


def test_visits_every_host_in_order(tmp_path):
    inv = _inventory(tmp_path, ["a", "b", "c"])
    stub = _stub(tmp_path, "child.sh", 'echo "$CHILD_SSH" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, stub)
    proc = run_bash("scripts/deploy-fleet.sh app", env=env, check=False)
    assert proc.returncode == 0
    assert (tmp_path / "fleet.log").read_text().split() == ["a", "b", "c"]


def test_stops_after_first_failure_by_default(tmp_path):
    inv = _inventory(tmp_path, ["a", "b", "c"])
    stub = _stub(
        tmp_path, "child.sh",
        'echo "$CHILD_SSH" >> "$FLEET_LOG"; [ "$CHILD_SSH" = b ] && exit 1; exit 0',
    )
    env = _env(tmp_path, inv, stub)
    proc = run_bash("scripts/deploy-fleet.sh app", env=env, check=False)
    assert proc.returncode == 1
    assert (tmp_path / "fleet.log").read_text().split() == ["a", "b"]


def test_keep_going_continues_past_failure(tmp_path):
    inv = _inventory(tmp_path, ["a", "b", "c"])
    stub = _stub(
        tmp_path, "child.sh",
        'echo "$CHILD_SSH" >> "$FLEET_LOG"; [ "$CHILD_SSH" = b ] && exit 1; exit 0',
    )
    env = _env(tmp_path, inv, stub)
    proc = run_bash("scripts/deploy-fleet.sh app --keep-going", env=env, check=False)
    assert proc.returncode == 1
    assert (tmp_path / "fleet.log").read_text().split() == ["a", "b", "c"]


def test_only_limits_targets(tmp_path):
    inv = _inventory(tmp_path, ["a", "b", "c"])
    stub = _stub(tmp_path, "child.sh", 'echo "$CHILD_SSH" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, stub)
    run_bash("scripts/deploy-fleet.sh app --only a,c", env=env, check=False)
    assert (tmp_path / "fleet.log").read_text().split() == ["a", "c"]


def test_only_rejects_unknown_host(tmp_path):
    inv = _inventory(tmp_path, ["a"])
    stub = _stub(tmp_path, "child.sh", "exit 0")
    env = _env(tmp_path, inv, stub)
    proc = run_bash("scripts/deploy-fleet.sh app --only zzz", env=env, check=False)
    assert proc.returncode != 0


def test_passes_through_flags_to_child_script(tmp_path):
    inv = _inventory(tmp_path, ["a"])
    stub = _stub(tmp_path, "child.sh", 'printf "%s\\n" "$@" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, stub)
    run_bash("scripts/deploy-fleet.sh app --dry-run --with-shared-config", env=env, check=False)
    logged = (tmp_path / "fleet.log").read_text().split()
    assert "--dry-run" in logged
    assert "--with-shared-config" in logged


def test_does_not_leak_ip_between_hosts(tmp_path):
    """前の子のIPを次の子に持ち越すと、誤ったホストをヘルスチェックし得る。"""
    inv = _inventory(tmp_path, ["a", "b"])
    stub = _stub(tmp_path, "child.sh", 'echo "[$CHILD_AP_IP]" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, stub)
    env["CHILD_AP_IP"] = "10.42.0.99"
    run_bash("scripts/deploy-fleet.sh app", env=env, check=False)
    assert (tmp_path / "fleet.log").read_text().split() == ["[]", "[]"]


def test_model_mode_forwards_name_and_version(tmp_path):
    inv = _inventory(tmp_path, ["a"])
    stub = _stub(tmp_path, "model.sh", 'printf "%s\\n" "$@" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, _stub(tmp_path, "child.sh", "exit 0"))
    env["FLEET_MODEL_SCRIPT"] = str(stub)
    proc = run_bash("scripts/deploy-fleet.sh model signal_tower 20260422", env=env, check=False)
    assert proc.returncode == 0
    assert (tmp_path / "fleet.log").read_text().split() == ["signal_tower", "20260422"]


def test_model_mode_requires_name_and_version(tmp_path):
    inv = _inventory(tmp_path, ["a"])
    env = _env(tmp_path, inv, _stub(tmp_path, "child.sh", "exit 0"))
    proc = run_bash("scripts/deploy-fleet.sh model signal_tower", env=env, check=False)
    assert proc.returncode != 0


def test_empty_inventory_fails_instead_of_silently_succeeding(tmp_path):
    """0台に配って成功扱いで終わるのが最悪。必ずエラーで止まること。"""
    inv = _inventory(tmp_path, [])
    inv.write_text("# コメントだけ\n\n", encoding="utf-8")
    stub = _stub(tmp_path, "child.sh", 'echo "$CHILD_SSH" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, stub)
    proc = run_bash("scripts/deploy-fleet.sh app", env=env, check=False)
    assert proc.returncode != 0
    assert (tmp_path / "fleet.log").read_text() == ""


def test_missing_inventory_fails(tmp_path):
    stub = _stub(tmp_path, "child.sh", "exit 0")
    env = _env(tmp_path, tmp_path / "nope.conf", stub)
    proc = run_bash("scripts/deploy-fleet.sh app", env=env, check=False)
    assert proc.returncode != 0


def test_only_with_empty_value_is_rejected(tmp_path):
    """--only= が空のとき、フィルタを黙って捨ててフリート全体へ配ってはいけない。"""
    inv = _inventory(tmp_path, ["a", "b"])
    stub = _stub(tmp_path, "child.sh", 'echo "$CHILD_SSH" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, stub)
    proc = run_bash("scripts/deploy-fleet.sh app --only=", env=env, check=False)
    assert proc.returncode != 0
    assert (tmp_path / "fleet.log").read_text() == ""


def test_model_mode_rejects_flag_before_name(tmp_path):
    """フラグを <name> <version> より前に置くと、無視されたうえ名前が化ける。"""
    inv = _inventory(tmp_path, ["a"])
    model_stub = _stub(tmp_path, "model.sh", 'printf "%s|" "$@" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, _stub(tmp_path, "child.sh", "exit 0"))
    env["FLEET_MODEL_SCRIPT"] = str(model_stub)
    proc = run_bash(
        "scripts/deploy-fleet.sh model --keep-going signal_tower 20260422",
        env=env, check=False,
    )
    assert proc.returncode != 0
    assert (tmp_path / "fleet.log").read_text() == ""


def test_only_deduplicates_repeated_hosts(tmp_path):
    """--only は利用者入力なのでパーサの重複除去を通らない。ここでも潰すこと。"""
    inv = _inventory(tmp_path, ["a", "b"])
    stub = _stub(tmp_path, "child.sh", 'echo "$CHILD_SSH" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, stub)
    run_bash("scripts/deploy-fleet.sh app --only a,a", env=env, check=False)
    assert (tmp_path / "fleet.log").read_text().split() == ["a"]
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest scripts/tests/test_deploy_fleet.py -v`
Expected: FAIL（`scripts/deploy-fleet.sh: No such file or directory`）

- [ ] **Step 3: deploy-fleet.sh を実装する**

`scripts/deploy-fleet.sh`（作成後 `chmod +x`）:

```bash
#!/usr/bin/env bash
# deploy-fleet.sh — 親で実行。インベントリの子へ 1台ずつ順に配布する。
#
#   各子への配布・ヘルスチェック・自動ロールバックは既存の deploy-child.sh /
#   deploy-model.sh がそのまま担う。本スクリプトは「順に回して、失敗したら止める」だけ。
#   既定は fail-fast: 1台失敗した時点で以降へは配らない（不良リリースをフリート全体へ
#   広げないため）。--keep-going で継続できる。
#
# 使い方:
#   scripts/deploy-fleet.sh app                          # 全子へアプリ配布
#   scripts/deploy-fleet.sh app --only zero2,zero2b      # 対象を絞る（canary先行）
#   scripts/deploy-fleet.sh app --dry-run                # 送る差分の確認のみ
#   scripts/deploy-fleet.sh app --keep-going             # 失敗しても後続へ進む
#   scripts/deploy-fleet.sh model signal_tower 20260422  # 全子へモデル配布
#
# その他のフラグ(--with-shared-config / --no-rollback 等)はそのまま子スクリプトへ渡す。
# 対象一覧は fleet/children.conf（FLEET_INVENTORY で上書き可）。

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/deploy-common.sh
source "$HERE/lib/deploy-common.sh"

CHILD_SCRIPT="${FLEET_CHILD_SCRIPT:-$HERE/deploy-child.sh}"
MODEL_SCRIPT="${FLEET_MODEL_SCRIPT:-$HERE/deploy-model.sh}"

[ $# -gt 0 ] || die "使い方: deploy-fleet.sh app|model [引数...]（-h で詳細）"
MODE="$1"; shift
case "$MODE" in
  app|model) ;;
  # 冒頭コメントを最初の空行まで出す(行番号を固定しない)。
  -h|--help) sed -n '2,/^$/p' "$0"; exit 0 ;;
  *) die "不明なモード: $MODE（app または model）" ;;
esac

MODEL_ARGS=()
if [ "$MODE" = model ]; then
  [ $# -ge 2 ] || die "model モードは <name> <version> が必要"
  # フラグは <name> <version> より後ろに置く。ここで弾かないと、フラグがそのまま
  # モデル名として子スクリプトへ渡り、しかも当のフラグは無視される(二重に壊れる)。
  for a in "$1" "$2"; do
    case "$a" in
      -*) die "model モードは <name> <version> をフラグより先に置いてください: '$a'" ;;
    esac
  done
  MODEL_ARGS=("$1" "$2"); shift 2
fi

ONLY=""; KEEP_GOING=0; PASSTHRU=()
while [ $# -gt 0 ]; do
  case "$1" in
    --only)       shift; ONLY="${1:-}"; [ -n "$ONLY" ] || die "--only に値がありません" ;;
    # 空の値を黙って無視すると、--only=$VAR が未展開だった場合に canary の
    # つもりでフリート全体へ配ってしまう。空白形式と同じく必ず弾く。
    --only=*)     ONLY="${1#--only=}"; [ -n "$ONLY" ] || die "--only に値がありません" ;;
    --keep-going) KEEP_GOING=1 ;;
    *)            PASSTHRU+=("$1") ;;
  esac
  shift
done

# 対象ホストを決める
# プロセス置換 < <(...) だと fleet_read_inventory の die がサブシェル止まりになり、
# インベントリ不正時に「0台へ配って成功」で終わってしまう。必ず一度変数で受ける。
inventory_out="$(fleet_read_inventory)" || exit 1
mapfile -t ALL_HOSTS <<< "$inventory_out"
HOSTS=()
if [ -n "$ONLY" ]; then
  IFS=',' read -r -a want <<< "$ONLY"
  for w in "${want[@]}"; do
    w="$(printf '%s' "$w" | tr -d '[:space:]')"
    [ -n "$w" ] || continue
    printf '%s\n' "${ALL_HOSTS[@]}" | grep -qx -- "$w" \
      || die "--only の '$w' はインベントリにありません: $FLEET_INVENTORY"
    # --only は利用者入力から組み立てるため fleet_read_inventory の重複除去を
    # 通らない。同じ子を二重に扱わないよう、ここでも重複を落とす。
    if [ ${#HOSTS[@]} -gt 0 ] && printf '%s\n' "${HOSTS[@]}" | grep -qx -- "$w"; then
      continue
    fi
    HOSTS+=("$w")
  done
  [ ${#HOSTS[@]} -gt 0 ] || die "--only に有効なホストがありません"
else
  HOSTS=("${ALL_HOSTS[@]}")
fi

if [ "$MODE" = model ]; then
  CMD=("$MODEL_SCRIPT" "${MODEL_ARGS[@]}")
else
  CMD=("$CHILD_SCRIPT")
fi
[ ${#PASSTHRU[@]} -eq 0 ] || CMD+=("${PASSTHRU[@]}")

log "フリート配布 mode=$MODE 対象=${#HOSTS[@]}台 (${HOSTS[*]}) fail_fast=$((1-KEEP_GOING))"

OK_HOSTS=(); NG_HOSTS=()
idx=0
for h in "${HOSTS[@]}"; do
  idx=$((idx+1))
  log "===== [$h] ($idx/${#HOSTS[@]}) ====="
  # CHILD_AP_IP は毎回空にする。前の子のIPを持ち越すと別の子をヘルスチェックし得る。
  if CHILD_SSH="$h" CHILD_AP_IP="" "${CMD[@]}"; then
    OK_HOSTS+=("$h")
    ok "[$h] 完了"
  else
    NG_HOSTS+=("$h")
    if [ "$KEEP_GOING" -eq 1 ]; then
      warn "[$h] 失敗。--keep-going のため継続する"
    else
      warn "[$h] 失敗。以降の配布を中止する（継続するには --keep-going）"
      break
    fi
  fi
done

echo
log "結果: 成功 ${#OK_HOSTS[@]} / 失敗 ${#NG_HOSTS[@]} / 対象 ${#HOSTS[@]}"
[ ${#OK_HOSTS[@]} -eq 0 ] || ok "成功: ${OK_HOSTS[*]}"
if [ ${#NG_HOSTS[@]} -gt 0 ]; then
  warn "失敗: ${NG_HOSTS[*]}"
  skipped=$(( ${#HOSTS[@]} - ${#OK_HOSTS[@]} - ${#NG_HOSTS[@]} ))
  [ "$skipped" -le 0 ] || warn "未実施: ${skipped}台"
  exit 1
fi
ok "全 ${#HOSTS[@]} 台へ配布完了"
```

- [ ] **Step 4: 実行権限を付けてテストする**

```bash
chmod +x scripts/deploy-fleet.sh
```

Run: `python -m pytest scripts/tests/ -v`
Expected: 33 passed（Task 1-4 の22件 + 本タスクの11件）

- [ ] **Step 5: DEPLOY.md にフリート手順を追加する**

`docs/DEPLOY.md` の「### 子のMLモデルを更新する」の直後に次の節を挿入する:

```markdown
### 複数の子へまとめて配る（フリート）

対象一覧は `fleet/children.conf`（1行1台、SSH到達名のみ。IPは書かない）。

```bash
scripts/deploy-fleet.sh app --dry-run                 # 全子ぶんの差分を確認
scripts/deploy-fleet.sh app --only zero2              # canary を1台だけ先行
scripts/deploy-fleet.sh app                           # 全子へ 1台ずつ順に配布
scripts/deploy-fleet.sh model signal_tower 20260422   # 全子へモデル配布
```

**既定は fail-fast**。1台で失敗したら以降へは配らない（不良リリースをフリート全体へ
広げないため）。あえて続けるときだけ `--keep-going`。各子の配布・ヘルスチェック・
自動ロールバックは `deploy-child.sh` / `deploy-model.sh` がそのまま担う。
```

- [ ] **Step 6: コミット**

```bash
git add scripts/deploy-fleet.sh scripts/tests/test_deploy_fleet.py docs/DEPLOY.md
git commit -m "feat(fleet): add serial fail-fast fleet deploy driver"
```

---

### Task 6: STA_NO 重複検出（純Python）

Oracle の MERGE キーが `device_id` を含まないため、子同士で STA_NO 三つ組が重複するとレコードが無警告で欠落する。その検出ロジックを純Pythonに切り出し、SSHを介さず直接テストする。

**Files:**
- Create: `scripts/lib/sta_no_report.py`
- Test: `scripts/tests/test_sta_no_report.py`

**Interfaces:**
- Consumes: なし（純関数）
- Produces:
  - `find_duplicate_stations(per_host: dict[str, dict[str, list[str]]]) -> dict[tuple[str, str, str], list[str]]`
    重複した STA_NO 三つ組 → それを持つ `"<host>:<region_id>"` ラベルのリスト。重複が無ければ空dict。
  - `format_report(per_host: dict[str, dict[str, list[str]]]) -> str` 全機体の割当一覧の文字列
  - `main() -> int` 標準入力から `{host: id_names}` のJSONを読み、レポートを表示。重複ありなら 1 を返す。

- [ ] **Step 1: 失敗するテストを書く**

`scripts/tests/test_sta_no_report.py`:

```python
"""STA_NO 重複検出の検証。

Oracle の MERGE キーは (MK_DATE, STA_NO1..3, T1_STATUS) のみで device_id を含まない
（services/bridge/src/oracle_client.py の MERGE 条件）。よって複数の子が同じ
STA_NO 三つ組を持つと、同時刻・同ステータスのレコードは片方が無警告で捨てられる。
"""
import importlib.util
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "lib" / "sta_no_report.py"

_spec = importlib.util.spec_from_file_location("sta_no_report", MODULE_PATH)
sta_no_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sta_no_report)

find_duplicate_stations = sta_no_report.find_duplicate_stations
format_report = sta_no_report.format_report


def test_no_duplicates_returns_empty():
    per_host = {
        "zero2": {"1": ["HIME", "T120", "004020"]},
        "zero2b": {"1": ["HIME", "T120", "010020"]},
    }
    assert find_duplicate_stations(per_host) == {}


def test_detects_duplicate_across_hosts():
    per_host = {
        "zero2": {"1": ["HIME", "T120", "004020"]},
        "zero2b": {"1": ["HIME", "T120", "004020"]},
    }
    dups = find_duplicate_stations(per_host)
    assert list(dups) == [("HIME", "T120", "004020")]
    assert sorted(dups[("HIME", "T120", "004020")]) == ["zero2:1", "zero2b:1"]


def test_detects_duplicate_within_one_host():
    """1台の中で2つの region が同じ STA_NO を持っても Oracle では衝突する。"""
    per_host = {"zero2": {"1": ["A", "B", "C"], "2": ["A", "B", "C"]}}
    dups = find_duplicate_stations(per_host)
    assert sorted(dups[("A", "B", "C")]) == ["zero2:1", "zero2:2"]


def test_ignores_unassigned_regions():
    """3項目すべて空の region は未割当。Picamera.py も送信対象にしない。"""
    per_host = {
        "zero2": {"1": ["", "", ""], "2": ["A", "B", "C"]},
        "zero2b": {"1": ["", "", ""]},
    }
    assert find_duplicate_stations(per_host) == {}


def test_partially_filled_region_is_counted():
    """1つでも入力があれば送信対象（Picamera.py の any(parts) と同じ判定）。"""
    per_host = {"zero2": {"1": ["A", "", ""]}, "zero2b": {"1": ["A", "", ""]}}
    assert find_duplicate_stations(per_host) == {("A", "", ""): ["zero2:1", "zero2b:1"]}


def test_report_lists_every_host_and_assignment():
    per_host = {"zero2": {"1": ["HIME", "T120", "004020"]}}
    report = format_report(per_host)
    assert "zero2" in report
    assert "HIME" in report
    assert "004020" in report


def test_report_marks_hosts_without_assignments():
    assert "未割当" in format_report({"zero2": {}})


def test_cli_exits_1_on_duplicates():
    payload = json.dumps({
        "zero2": {"1": ["A", "B", "C"]},
        "zero2b": {"1": ["A", "B", "C"]},
    })
    proc = subprocess.run(
        ["python3", str(MODULE_PATH)], input=payload,
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 1
    assert "重複" in proc.stdout


def test_cli_exits_0_when_clean():
    payload = json.dumps({"zero2": {"1": ["A", "B", "C"]}})
    proc = subprocess.run(
        ["python3", str(MODULE_PATH)], input=payload,
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest scripts/tests/test_sta_no_report.py -v`
Expected: FAIL（`scripts/lib/sta_no_report.py` が存在しない）

- [ ] **Step 3: sta_no_report.py を実装する**

`scripts/lib/sta_no_report.py`:

```python
#!/usr/bin/env python3
# ruff: noqa: T201  これは操作用CLIなので print 出力は意図的
"""子ごとの STA_NO 割当を突き合わせ、重複を検出する。

Oracle の MERGE キーは (MK_DATE, STA_NO1..3, T1_STATUS) のみで device_id を含まない
（services/bridge/src/oracle_client.py）。よって複数の子が同じ STA_NO 三つ組を持つと、
同時刻・同ステータスのレコードは片方が無警告で捨てられる。子を増やす前・増やした後に
このチェックを通すことで、その事故を運用前に検出する。

標準入力: {"<host>": {"<region_id>": ["名前1", "名前2", "名前3"], ...}, ...}
終了コード:
  0 = 検査済みで重複なし
  1 = 重複あり(最も実行可能な合図なので、検査不能な子があっても優先する)
  2 = 入力が使えず検査しきれていない(JSONが壊れている / 割当が dict でない子がある)
※ 2 を 0 と混同しないこと。「安全」ではなく「確かめられていない」を意味する。
"""
import json
import sys

Assignments = dict[str, dict[str, list[str]]]
Triple = tuple[str, str, str]


def _triple(parts: object) -> Triple:
    """3要素へ正規化する。子側 child/Picamera.py の id_name_parts と同じ規則に揃える。

    - リスト以外(旧形式の単一文字列)は ["旧名", "", ""] とみなす。文字単位に分解しない。
    - None は空文字にする(str(None) の "None" にしてはいけない)。
    - 前後の空白を除去する(子が strip してから CSV に書くため)。
    3要素に満たない場合は空文字で埋める。

    ここが子の正規化とずれると、同じ局を指す2台の子を「別物」と誤判定し、
    Oracle でレコードが無警告に欠落するのを見逃す。
    """
    if isinstance(parts, list):
        p = [("" if x is None else str(x)).strip() for x in parts][:3]
    else:
        p = ["" if parts is None else str(parts).strip()]
    while len(p) < 3:
        p.append("")
    return (p[0], p[1], p[2])


def find_malformed_hosts(per_host: Assignments) -> list[str]:
    """割当が dict になっておらず検査できないホスト名を返す。"""
    return sorted(h for h in per_host if not isinstance(per_host[h], dict))


def find_duplicate_stations(per_host: Assignments) -> dict[Triple, list[str]]:
    """重複した STA_NO 三つ組 -> ["<host>:<region_id>", ...] を返す。

    3項目すべて空の region は未割当として無視する（Picamera.py の any(parts) と同じ判定）。
    同一ホスト内での重複も Oracle では衝突するため検出対象に含める。
    """
    seen: dict[Triple, list[str]] = {}
    for host in sorted(per_host):
        regions = per_host[host]
        if not isinstance(regions, dict):
            continue          # 検査不能。main() が別途警告する。
        for region in sorted(regions, key=lambda r: (len(str(r)), str(r))):
            triple = _triple(regions[region])
            if not any(triple):
                continue
            seen.setdefault(triple, []).append(f"{host}:{region}")
    return {t: labels for t, labels in seen.items() if len(labels) > 1}


def format_report(per_host: Assignments) -> str:
    """全機体の STA_NO 割当一覧を人が読める形にする。"""
    lines: list[str] = []
    for host in sorted(per_host):
        regions = per_host[host]
        lines.append(f"[{host}]")
        if not isinstance(regions, dict):
            lines.append("    (割当を読めませんでした)")
            continue
        assigned = [
            (r, _triple(regions[r]))
            for r in sorted(regions, key=lambda r: (len(r), r))
            if any(_triple(regions[r]))
        ]
        if not assigned:
            lines.append("    (STA_NO 未割当)")
            continue
        for region, triple in assigned:
            lines.append(f"    region {region:>3}  {triple[0]} / {triple[1]} / {triple[2]}")
    return "\n".join(lines)


def main() -> int:
    try:
        per_host: Assignments = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"入力JSONを解析できません: {e}", file=sys.stderr)
        return 2
    if not isinstance(per_host, dict):
        print("入力JSONの最上位は {ホスト名: 割当} である必要があります", file=sys.stderr)
        return 2
    print(format_report(per_host))
    bad = find_malformed_hosts(per_host)
    dups = find_duplicate_stations(per_host)
    if bad:
        print("\n!! 割当を読めなかった子があります(この子は検査できていません) !!")
        for h in bad:
            print(f"    {h}")
    if dups:
        print("\n!! STA_NO 重複を検出 !!")
        for triple, labels in dups.items():
            print(f"    {triple[0]} / {triple[1]} / {triple[2]}  <- {', '.join(labels)}")
        print("\nOracle の MERGE キーは device_id を含まないため、このままでは")
        print("同時刻・同ステータスのレコードが無警告で欠落します。割当を修正してください。")
        return 1
    if bad:
        print("\n上記の子を検査できていないため、重複なしとは断定できません。")
        return 2
    print("\nSTA_NO 重複: なし")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: テストとlintを実行する**

Run: `python -m pytest scripts/tests/test_sta_no_report.py -v && ruff check scripts/lib/sta_no_report.py`
Expected: 9 passed、ruff は `All checks passed!`

- [ ] **Step 5: コミット**

```bash
git add scripts/lib/sta_no_report.py scripts/tests/test_sta_no_report.py
git commit -m "feat(fleet): detect duplicate STA_NO assignments across children"
```

---

### Task 7: `scripts/fleet-status.sh`（稼働状況の一覧）

**Files:**
- Create: `scripts/fleet-status.sh`
- Modify: `docs/DEPLOY.md`
- Test: `scripts/tests/test_fleet_status.py`

**Interfaces:**
- Consumes: `fleet_read_inventory()`（Task 4）、`child_resolve_ap_ip()`（Task 3）、`scripts/lib/sta_no_report.py`（Task 6）
- Produces: CLI `scripts/fleet-status.sh [--only h1,h2]`
  - 各子の稼働状況（サービス状態・モデル・readiness）を表示
  - 全子の STA_NO 割当と重複判定を表示
  - 重複があれば exit 1、無ければ exit 0（到達不能な子があった場合も exit 1）

- [ ] **Step 1: 失敗するテストを書く**

`scripts/tests/test_fleet_status.py`:

```python
"""フリート状態一覧の検証。

ssh / curl を偽物に差し替え、収集と重複判定の結合部分だけを確認する。
重複判定そのものの網羅テストは test_sta_no_report.py にある。
"""
import json
import os

from scripts.tests.shellhelp import run_bash


def _inventory(tmp_path, hosts):
    f = tmp_path / "children.conf"
    f.write_text("".join(f"{h}\n" for h in hosts), encoding="utf-8")
    return f


def _env(tmp_path, hosts):
    e = dict(os.environ)
    e["FLEET_INVENTORY"] = str(_inventory(tmp_path, hosts))
    return e


def _fake_ssh_returning(fake_bin, mapping: dict):
    """host -> id_names dict を返す偽 ssh。hostname -I / systemctl にも応答する。"""
    payload = json.dumps(mapping).replace("'", "'\\''")
    fake_bin("ssh", f"""
host="$1"; shift
cmd="$*"
case "$cmd" in
  *hostname\\ -I*) echo "10.42.0.52" ;;
  *is-active*)     echo "active" ;;
  *id_names_config.json*)
      printf '%s' '{payload}' | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({{"id_names": d.get(sys.argv[1], {{}})}}))' "$host" ;;
  *) echo "" ;;
esac
""")
    fake_bin("curl", 'echo "{\\"status\\": \\"ready\\", \\"model_type\\": \\"signal_tower\\"}"')


def test_reports_each_host(tmp_path, fake_bin):
    _fake_ssh_returning(fake_bin, {
        "a": {"1": ["HIME", "T120", "004020"]},
        "b": {"1": ["HIME", "T120", "010020"]},
    })
    proc = run_bash("scripts/fleet-status.sh", env=_env(tmp_path, ["a", "b"]), check=False)
    assert "a" in proc.stdout
    assert "b" in proc.stdout
    assert "004020" in proc.stdout
    assert proc.returncode == 0


def test_exits_1_and_warns_on_duplicate_sta_no(tmp_path, fake_bin):
    _fake_ssh_returning(fake_bin, {
        "a": {"1": ["HIME", "T120", "004020"]},
        "b": {"1": ["HIME", "T120", "004020"]},
    })
    proc = run_bash("scripts/fleet-status.sh", env=_env(tmp_path, ["a", "b"]), check=False)
    assert proc.returncode == 1
    assert "重複" in proc.stdout


def test_only_limits_targets(tmp_path, fake_bin):
    _fake_ssh_returning(fake_bin, {
        "a": {"1": ["HIME", "T120", "004020"]},
        "b": {"1": ["HIME", "T120", "004020"]},
    })
    proc = run_bash(
        "scripts/fleet-status.sh --only a", env=_env(tmp_path, ["a", "b"]), check=False
    )
    assert proc.returncode == 0  # a だけなら重複しない
    assert "b" not in proc.stdout.split()


def test_unreachable_child_is_reported_and_fails(tmp_path, fake_bin):
    fake_bin("ssh", "exit 255")
    fake_bin("curl", "exit 7")
    proc = run_bash("scripts/fleet-status.sh", env=_env(tmp_path, ["a"]), check=False)
    assert proc.returncode == 1
    assert "到達" in proc.stdout or "到達" in proc.stderr
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `python -m pytest scripts/tests/test_fleet_status.py -v`
Expected: FAIL（`scripts/fleet-status.sh` が存在しない）

- [ ] **Step 3: fleet-status.sh を実装する**

`scripts/fleet-status.sh`（作成後 `chmod +x`）:

```bash
#!/usr/bin/env bash
# fleet-status.sh — 親で実行。インベントリの子の稼働状況と STA_NO 割当を一覧する。
#
#   各子から サービス状態 / モデル readiness / STA_NO割当(id_names_config.json) を集め、
#   最後に子を跨いだ STA_NO の重複を検査する。
#   Oracle の MERGE キーは device_id を含まないため、STA_NO が重複するとレコードが
#   無警告で欠落する。子を増やす前後に必ず通すこと。
#
# 使い方:
#   scripts/fleet-status.sh                # 全子
#   scripts/fleet-status.sh --only zero2   # 対象を絞る
#
# 終了コード: 0=正常 / 1=STA_NO重複あり、または到達できない子がある

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/deploy-common.sh
source "$HERE/lib/deploy-common.sh"

ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --only)    shift; ONLY="${1:-}"; [ -n "$ONLY" ] || die "--only に値がありません" ;;
    # 空の値を黙って無視しない(空白形式と同じ扱いにする)。
    --only=*)  ONLY="${1#--only=}"; [ -n "$ONLY" ] || die "--only に値がありません" ;;
    # 冒頭コメントを最初の空行まで出す(行番号を固定しない)。
    -h|--help) sed -n '2,/^$/p' "$0"; exit 0 ;;
    *)         die "unknown arg: $1" ;;
  esac
  shift
done

# プロセス置換 < <(...) だと fleet_read_inventory の die がサブシェル止まりになり、
# インベントリ不正時に「0台へ配って成功」で終わってしまう。必ず一度変数で受ける。
inventory_out="$(fleet_read_inventory)" || exit 1
mapfile -t ALL_HOSTS <<< "$inventory_out"
HOSTS=()
if [ -n "$ONLY" ]; then
  IFS=',' read -r -a want <<< "$ONLY"
  for w in "${want[@]}"; do
    w="$(printf '%s' "$w" | tr -d '[:space:]')"
    [ -n "$w" ] || continue
    printf '%s\n' "${ALL_HOSTS[@]}" | grep -qx -- "$w" \
      || die "--only の '$w' はインベントリにありません: $FLEET_INVENTORY"
    # --only は利用者入力から組み立てるため fleet_read_inventory の重複除去を
    # 通らない。同じ子を二重に扱わないよう、ここでも重複を落とす。
    if [ ${#HOSTS[@]} -gt 0 ] && printf '%s\n' "${HOSTS[@]}" | grep -qx -- "$w"; then
      continue
    fi
    HOSTS+=("$w")
  done
else
  HOSTS=("${ALL_HOSTS[@]}")
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
UNREACHABLE=0

log "フリート状態 (${#HOSTS[@]}台)"
printf '  %-16s %-8s %-10s %-12s %s\n' HOST PICAMERA WEB MODEL READY
for h in "${HOSTS[@]}"; do
  CHILD_SSH="$h"; CHILD_AP_IP=""
  if ! rc 'echo ok' >/dev/null 2>&1; then
    printf '  %-16s %s\n' "$h" "到達できません(SSH失敗)"
    UNREACHABLE=1
    printf '{}' > "$TMP/$h.json"
    continue
  fi
  child_resolve_ap_ip >/dev/null 2>&1 || true

  pica="$(rc 'systemctl is-active picamera.service' 2>/dev/null || echo unknown)"
  web="$(rc 'systemctl is-active web_server.service' 2>/dev/null || echo unknown)"
  url="http://$CHILD_AP_IP:$CHILD_WEB_PORT"
  # model_type と status は両方 /model_status に入っている。1回の取得で足りる。
  # /current_model からは読まないこと: 実機 zero2 では network/labels しか返らず
  # model_type が無いため、常に不明扱いになる。
  st="$(curl -sf -m 5 "$url/model_status" 2>/dev/null || true)"
  model="$(printf '%s' "$st" | grep -o '"model_type"[[:space:]]*:[[:space:]]*"[^"]*"' \
           | sed 's/.*"\([^"]*\)"$/\1/' || true)"
  ready="$(printf '%s' "$st" | grep -o '"status"[[:space:]]*:[[:space:]]*"[^"]*"' \
           | sed 's/.*"\([^"]*\)"$/\1/' || true)"
  printf '  %-16s %-8s %-10s %-12s %s\n' \
    "$h" "$pica" "$web" "${model:-?}" "${ready:-?}"

  # STA_NO 割当を回収（取得できなければ空扱い）
  rc 'cat ~/id_names_config.json' 2>/dev/null > "$TMP/$h.json" || printf '{}' > "$TMP/$h.json"
done

# 全機体の割当を1つのJSONにまとめ、重複検査へ渡す
echo
python3 - "$TMP" "${HOSTS[@]}" <<'PY' > "$TMP/all.json"
import json, pathlib, sys
tmp = pathlib.Path(sys.argv[1])
out = {}
for host in sys.argv[2:]:
    try:
        data = json.loads((tmp / f"{host}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    # 最上位が dict でも id_names の中身が dict とは限らない({"id_names": "壊れた文字列"}
    # のような子が実在しうる)。両方を確かめないと sta_no_report.py へ壊れた形を渡してしまう。
    ids = data.get("id_names") if isinstance(data, dict) else None
    out[host] = ids if isinstance(ids, dict) else {}
print(json.dumps(out))
PY

DUP_RC=0
python3 "$HERE/lib/sta_no_report.py" < "$TMP/all.json" || DUP_RC=$?

if [ "$UNREACHABLE" -ne 0 ]; then
  warn "到達できない子があります(この子は検査できていません)"
fi

# 1(重複あり)は最も実行可能な合図なので、検査不能な子があっても優先する。
if [ "$DUP_RC" -eq 1 ]; then
  exit 1
fi
# 到達できない子、または割当を読めない子があれば「確かめられていない」。
if [ "$DUP_RC" -ne 0 ] || [ "$UNREACHABLE" -ne 0 ]; then
  exit 2
fi
exit 0
```

- [ ] **Step 4: 実行権限を付けてテストする**

```bash
chmod +x scripts/fleet-status.sh
```

Run: `python -m pytest scripts/tests/ -v`
Expected: 46 passed（Task 1-6 の42件 + 本タスクの4件）

- [ ] **Step 5: DEPLOY.md に状態確認手順を追加する**

Task 5 で追加した「### 複数の子へまとめて配る（フリート）」節の末尾に追記する:

```markdown
#### 状態確認と STA_NO 重複チェック

```bash
scripts/fleet-status.sh          # 全子の稼働状況 + STA_NO割当 + 重複判定
scripts/fleet-status.sh --only zero2
```

**子を増やしたら必ず実行する。** Oracle の MERGE キーは `MK_DATE + STA_NO1-3 +
T1_STATUS` のみで `device_id` を含まないため、子同士で STA_NO が重複すると
同時刻・同ステータスのレコードが**無警告で欠落**する。重複を検出すると exit 1 になる。
```

- [ ] **Step 6: コミット**

```bash
git add scripts/fleet-status.sh scripts/tests/test_fleet_status.py docs/DEPLOY.md
git commit -m "feat(fleet): add fleet status view with STA_NO duplicate check"
```

---

### Task 8: 実機検証（回帰確認 → 2台目投入）

ここまでは自動テストのみ。**実機 zero2 に対する回帰確認**と、**2台目の子の投入**を行う。TDDではなくオペレーター手順としてのチェックリスト。

**Files:**
- Modify: `docs/DEPLOY.md`（子の増設手順を追記）
- 実機: 親Pi5、子 zero2、追加する子

**Interfaces:**
- Consumes: Task 1〜7 のすべて
- Produces: 実運用に乗った親1:子多構成

- [ ] **Step 1: 既存の子の設定が壊れないことを確認する（最重要の回帰確認）**

配布前に現行値を控える:

```bash
ssh zero2 'cat ~/id_names_config.json' > /tmp/before_id_names.json
cat /tmp/before_id_names.json
```

期待: `{"id_names": {"1": ["HIME", "T120", "004020"], "2": [...], ...}}`（リポジトリの
`child/id_names_config.json` の HIME/ABC/001 とは**異なる**ことを確認する）

- [ ] **Step 2: 既定の配布を実行し、機体固有設定が保持されることを確認する**

```bash
scripts/deploy-child.sh --dry-run   # id_names_config.json が出てこないことを目視確認
scripts/deploy-child.sh
ssh zero2 'cat ~/id_names_config.json' > /tmp/after_id_names.json
diff /tmp/before_id_names.json /tmp/after_id_names.json && echo "OK: 機体固有設定は保持された"
```

期待: `diff` が差分なし。修正前のコードでは**ここで上書きされていた**。
失敗した場合は Task 2 の実装を見直すこと。

- [ ] **Step 3: 子IPの自動取得が実IPと一致することを確認する**

```bash
bash -c 'source scripts/lib/deploy-common.sh; CHILD_SSH=zero2 CHILD_AP_IP=""; child_resolve_ap_ip'
ssh zero2 'hostname -I'
```

期待: 両者の先頭IPv4が一致する。

- [ ] **Step 4: 子1台でフリート経路が既存と同等に動くことを確認する**

```bash
scripts/deploy-fleet.sh app --dry-run
scripts/deploy-fleet.sh app
scripts/fleet-status.sh
```

期待: 配布成功、`fleet-status.sh` が zero2 を `active` / `signal_tower` / `ready` と表示し、
STA_NO 重複なしで exit 0。

- [ ] **Step 5: 2台目の子を準備する**

新しい子（Zero 2 W）に対して:

1. **ホスト名を一意にする**（クローンSDを使う場合は必須。既定の `DEVICE_ID` はホスト名）:
   ```bash
   sudo hostnamectl set-hostname pizero2w-2
   sudo reboot
   ```
2. 親APに接続できることを確認（IPはDHCPで自動。固定不要）:
   ```bash
   ping -c1 pizero2w-2.local
   ```
3. 親からSSH鍵で入れることを確認:
   ```bash
   ssh pizero2w-2.local 'hostname; hostname -I'
   ```

- [ ] **Step 6: インベントリに2台目を追加する**

`fleet/children.conf` の末尾に追記:

```
pizero2w-2.local
```

```bash
scripts/fleet-status.sh
```

期待: 2台目が「到達できません」ではなく表示される（アプリ未配布なら
PICAMERA/WEB が `inactive` や `unknown`、STA_NO は未割当）。

- [ ] **Step 7: 2台目へ配布し、STA_NO を設定する**

```bash
scripts/deploy-fleet.sh app --only pizero2w-2.local
```

配布後、**2台目のWeb UI**（`http://<2台目のIP>:8080`）で、1台目と**重複しない**
STA_NO 割当を設定する。

```bash
scripts/fleet-status.sh
```

期待: 重複なしで exit 0。試しに1台目と同じ割当を入れると exit 1 になり重複が報告されること
（1度確認したら正しい値に戻す）。

- [ ] **Step 8: ローリング配布と障害隔離を確認する**

```bash
scripts/deploy-fleet.sh app          # 2台へ1台ずつ順に配布される
```

期待: `[host] (1/2)` `[host] (2/2)` の順で進み、全成功で `全 2 台へ配布完了`。

障害隔離の確認（意図的な失敗注入）:

```bash
# 2台目のサービスを壊してから配布し、1台目が巻き込まれないことを見る
ssh pizero2w-2.local 'sudo systemctl mask picamera.service'
scripts/deploy-fleet.sh app --only pizero2w-2.local ; echo "exit=$?"
ssh pizero2w-2.local 'sudo systemctl unmask picamera.service'
scripts/deploy-fleet.sh app --only pizero2w-2.local
scripts/fleet-status.sh
```

期待: 失敗時に自動ロールバックが走り exit 1。1台目は `fleet-status.sh` で正常のまま。
最後に2台目も復旧して全台正常。

- [ ] **Step 9: DEPLOY.md に子の増設手順を追記する**

「#### 状態確認と STA_NO 重複チェック」の後に追記:

```markdown
#### 子を増やす

1. 新しい子の**ホスト名を一意にする**（`sudo hostnamectl set-hostname <名前>` → 再起動）。
   `DEVICE_ID` は既定でホスト名。クローンSDのまま増やすと2台が同一デバイス扱いになる。
2. 親APに接続し、親から `ssh <名前>.local` で入れることを確認（IPはDHCPで自動、固定不要）。
3. `fleet/children.conf` に1行追加する。
4. `scripts/deploy-fleet.sh app --only <名前>.local` で配布。
5. 子のWeb UI(:8080)で **他機と重複しない STA_NO** を設定する。
6. `scripts/fleet-status.sh` で重複が無いこと（exit 0）を確認する。
```

- [ ] **Step 10: コミット**

```bash
git add fleet/children.conf docs/DEPLOY.md
git commit -m "docs(fleet): add child provisioning steps verified on hardware"
```

---

## 完了条件

- `python -m pytest scripts/tests/ -v` が全件成功する。
- `ruff check scripts/lib/sta_no_report.py` が通る。
- 実機 zero2 で `deploy-child.sh` 実行前後に `id_names_config.json` が変化しない。
- `scripts/fleet-status.sh` が全子を表示し、STA_NO 重複時に exit 1 する。
- `scripts/deploy-fleet.sh app` が1台ずつ配布し、失敗時に以降を中止する。
- `docs/DEPLOY.md` が新しい既定（機体固有設定を配らない）とフリート手順を説明している。
