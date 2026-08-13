# 親1:子多 配備 設計書（設定の所有権是正 ＋ 既存bashのフリート化）

- 日付: 2026-08-13
- ステータス: ドラフト（レビュー待ち）
- 対象: presence-logger の親ラズパイ1台・子ラズパイ複数台
- 関連: [`2026-07-17-fleet-deploy-design.md`](./2026-07-17-fleet-deploy-design.md)（親も複数になる将来像／Ansible中央ops方式。以下「フル設計書」）

---

## 1. 背景

フル設計書は「親も子も複数」の将来像に対し、中央ops＋Ansibleを定義した。しかし現時点の
想定規模は**親1台・子数台**であり、中央ops機はまだ無く、`docs/DEPLOY.md` 自身が
「台数が3〜5に増えたらAnsibleへ発展」と閾値を設けている。今回はその閾値の手前にいる。

設計にあたりコードと実機を調査した結果、**1:多化の本当の障壁は配布の自動化ではなく、
設定ファイルの所有権が曖昧なことによる既存のデータ破壊リスク**だと判明した。本書は
その是正を第一とし、その上で既存の実機検証済みbashを最小限フリート対応させる。

## 2. 調査で判明した事実（設計の根拠）

### 2.1 受信側は既に複数子対応済み（変更不要）

| 箇所 | 確認結果 |
|---|---|
| `child/child-csv-to-mqtt.py:37` | `DEVICE_ID` はホスト名 or 環境変数。台数固定なし |
| `docker/mosquitto/mosquitto.conf` | `allow_anonymous true` のみ。クライアント数制限なし |
| `services/bridge/src/main.py`（`_on_record`/liveness） | `device_id` ごとに動的処理。1台前提の分岐なし |
| `services/bridge/src/record_inbox.py:17` | `event_id = SHA1(device_id + 行内容)` が主キー。device_idが違えば衝突しない |
| `desktop/presence-tools/pipeline_monitor` | dashboard/rollup/inbox_reader すべて device_id 別集計 |

### 2.2 STA_NO の出所は `id_names_config.json`

`child/Picamera.py:819-884`（`SendLogger`）で、`id_names_config.json` の
**region_id →[名前1,名前2,名前3]** が CSV の STA_NO1〜3 になり、そのままOracleへ流れる。
カメラが映す物理的な信号灯ごとの割当であり、**子ごとに必ず異なる値でなければならない**。

Oracle の MERGE キーは `MK_DATE + STA_NO1-3 + T1_STATUS` のみで `device_id` を含まない
（`services/bridge/src/oracle_client.py:73-77`）。したがって2台の子が同じ STA_NO を持つと、
**同時刻・同ステータスのレコードは片方が無警告で捨てられる**。

### 2.3 【重要】既存デプロイに、今すでに存在するデータ破壊リスク

`id_names_config.json` の中身が実機とリポジトリで食い違っている:

| | region 1 | region 2 | region 3 |
|---|---|---|---|
| 実機 zero2（本番実値） | HIME/T120/004020 | HIME/T120/010020 | HIME/T120/002020 |
| リポジトリ `child/` | HIME/ABC/001 | SAND/DEF/012 | (空) |

`deploy-child.sh` は既定で config も配布する（`scripts/deploy-child.sh:35` で
`CODE_ONLY` が0のとき `CHILD_CONFIG_FILES` を追加。既定値は同`:21`で `CODE_ONLY=0`）。つまり**今このスクリプトを既定で実行すると、実機の本番STA_NO割当が
リポジトリのプレースホルダ値で上書きされる**。バックアップは取られるが無警告であり、
子1台の現状でも起こり得る。

### 2.4 設定ファイルは全て「オペレーターがWeb UIで編集する端末状態」

`CHILD_CONFIG_FILES` の各ファイルには、子のWeb UI（`:8080`）に書き込みハンドラがある:

| ファイル | Web UI 書き込み | 分類 |
|---|---|---|
| `id_names_config.json` | `handle_set_id_names` (`web_server.py:1614`) | 機体固有（STA_NO割当） |
| `threshold_config.json` | `handle_set_threshold` (`:1016`) | 機体固有（検出調整） |
| `recognition_config.json` | `handle_set_resolution` 他 (`:1076,1098`) | 機体固有（画角・解像度） |
| `save_config.json` | `:994` | 機体固有（保存トグル） |
| `model_config.json` | `:951`、加えて `deploy-model.sh:66-76` が書く | 機体固有（モデル割当） |
| `status_code_config.json` | `handle_set_status_codes` (`:1631`) | **フリート共通**（業務ルール） |
| `send_target_config.json` | `handle_set_send_target_config` (`:1542`) | **フリート共通**（MQTT宛先） |
| `crop_config.json` | — | `child/*.py` から**参照なし**（未使用の可能性） |

さらに `load_*_config()` はファイル欠損時に既定値を書き込んで自己修復する
（`web_server.py:144,167,187,219`）。よって**新規の子は設定を配布しなくても既定値で起動でき、
そこからWeb UIで機体固有値を設定する**のが本来の運用フローである。

`model_config.json` については追加の衝突がある: `deploy-model.sh` がモデル割当のために
このファイルを書くのに、`deploy-child.sh` がリポジトリ値で上書きし得る。子ごとに異なる
モデルを使う場合、アプリ配布がモデル割当を静かに巻き戻す。

## 3. 設計判断

上記を踏まえた確定事項:

1. **機体固有設定は端末所有とし、配布しない。** `send_target_state.json` と同じ「不可触」
   扱いにする。配布の既定を「コードのみ」に変更する。
2. **配布機構は既存bashの拡張とする。Ansibleは今回導入しない。** 理由:
   - 既存の backup / 3層healthcheck / 自動rollback は実機検証済みであり、role へ移植すると
     本番データ経路の安全処理を再検証する必要がありリグレッションを背負う。
   - Ansible の主な付加価値だった「per-device設定のJinjaレンダリング」は、決定1により不要になる。
   - `CHILD_SSH` / `CHILD_AP_IP` は既に環境変数で上書き可能なため、必要なのは
     インベントリとループだけである。
   - プロジェクト自身が Ansible の閾値を3〜5台以上としている。
3. **IPはインベントリに書かず、デプロイ時に子から取得する。** 子のIPは親APのDHCP動的割当
   （実機 `10.42.0.52/24 dynamic`、静的設定なし）であり、インベントリに固定IPを書くと陳腐化する。
   陳腐化したIPへのヘルスチェックは、**別の子に対して成功判定を出す**という危険な失敗モードを持つ。

## 4. ゴール / 非ゴール

**ゴール**
- 機体固有設定の上書き事故を止める（子1台の現状でも即効果がある）。
- 親から複数の子へ、1台ずつ安全に配布できる（既存の安全処理はそのまま）。
- フリート全体の稼働版・モデル・STA_NO割当を一覧できる。
- 子を跨いだ **STA_NO 重複を検出**し、Oracleでの無言のレコード欠落を未然に防ぐ。

**非ゴール（フル設計書の将来像に委ねる）**
- Ansible化 / 中央ops機の構築 / 複数親対応 / ProxyJump。
- git bundle + manifest によるオフラインリリース取り込み。
- 設定のリポジトリ管理（GitOps化）。今回は端末所有と決めた。

## 5. 変更内容

### 5.1 `scripts/lib/deploy-common.sh` — ファイル分類の明確化

```
CHILD_CODE_FILES          # 現状のまま（Picamera.py / web_server.py / child-csv-to-mqtt.py / index.html）
CHILD_SHARED_CONFIG_FILES # status_code_config.json, send_target_config.json  ← opt-in で配布
CHILD_DEVICE_OWNED_FILES  # id_names / threshold / recognition / save / model / crop  ← 配布しない
```

`CHILD_DEVICE_OWNED_FILES` は単なる除外リストではなく、**意図の記録**として残す
（なぜ配らないかをコメントで明示し、将来うっかり配布対象へ戻すことを防ぐ）。
バックアップ対象からは外さない（復旧手段は維持する）。

### 5.2 `scripts/deploy-child.sh` — 既定を安全側へ

- **既定** = コード + systemd unit のみ配布（現在の `--code-only` 相当）。
- `--with-shared-config` = フリート共通2ファイルも配布（opt-in）。
- 機体固有ファイルはどのフラグでも配布しない。
- `--code-only` は既定と同義の非推奨エイリアスとして残す（既存手順を壊さない）。

### 5.3 AP IP の実行時導出

`CHILD_AP_IP` の固定値依存をやめ、デプロイ時に対象の子自身から取得する:

```bash
CHILD_AP_IP="$(rc "hostname -I | awk '{print \$1}'")"
```

これにより、インベントリはSSH到達名だけを持てばよくなり、DHCPでIPが変わっても
ヘルスチェックが誤った子を叩くことがなくなる。環境変数で明示指定された場合はそれを優先する
（既存の上書き手段は維持）。

なお実機で mDNS が機能することを確認済み（親から `pizero2w.local` → `10.42.0.52` を解決）。
新しい子は `~/.ssh/config` にIPを書かずとも `<hostname>.local` で追加できる。

### 5.4 インベントリ `fleet/children.conf`

SSH到達名を1行1台で列挙するだけの平文ファイル（新規依存なし、`while read` で解析）:

```
# 親AP配下の子ラズパイ。IPは書かない（DHCPで変わるため実行時に子から取得する）。
# ~/.ssh/config のエントリ名、または mDNS の <hostname>.local を書く。
zero2
```

将来Ansibleへ移行する際、この一覧はそのまま inventory の `children` グループになる。

### 5.5 `scripts/deploy-fleet.sh`（新規）

インベントリを読み、対象ごとに `CHILD_SSH=<host>` で既存の `deploy-child.sh` /
`deploy-model.sh` を**直列に**呼ぶ薄いドライバ。

- `--only <host>[,<host>...]` — 対象を絞る（canary先行用）。
- `--dry-run` — 各子に対する既存スクリプトの dry-run を通す。
- **既定は fail-fast**。1台目が失敗したら以降へ配らない（不良リリースをフリート全体へ広げない）。
  `--keep-going` で継続可能。
- 末尾に全台の成否サマリを出す。

各子の配布・ヘルスチェック・ロールバックは既存ロジックがそのまま担う（再実装しない）。

### 5.6 `scripts/fleet-status.sh`（新規）

各子について、device_id（ホスト名）・サービス稼働状態・`/model_status`・`/current_model`・
`id_names_config.json` の STA_NO 割当を収集し、表形式で表示する。

**STA_NO 重複検出**: 収集した割当を突き合わせ、複数の子が同一の
`(名前1, 名前2, 名前3)` を持つ場合に警告する。これは §2.2 の Oracle 無言欠落を
運用前に検出するためのもので、デプロイ前チェックとしても使える。

## 6. 安全設計（既存を維持）

各子に対する処理は現行のまま変更しない:

1. **バックアップ**: `~/.deploy-backups/<ts>/`（直近 `KEEP_BACKUPS` 世代）。
2. **配布**: rsync（`--delete` は使わない／runtime状態は除外）。
3. **再起動**: `systemctl restart`。
4. **3層ヘルスチェック**: 層1 即時（active＋安定＋`:8080`応答）／層2 readiness
   （`/model_status`==ready、期待モデル一致）／層3 E2E（任意 `VERIFY_E2E`）。
5. **自動ロールバック**: 失敗時は退避版へ復元→再起動→再チェック。

フリート化で追加されるのは「これを1台ずつ順に回し、失敗したら止める」という外側のループだけである。

## 7. テスト戦略

1. **設定所有権の是正**: 実機 zero2 で `id_names_config.json` の内容を記録 →
   `deploy-child.sh` を既定で実行 → **内容が変化していないこと**を確認（現状は破壊される）。
2. **AP IP 導出**: 導出したIPが実機の実IPと一致すること。環境変数指定時はそちらが優先されること。
3. **インベントリ＋ループ（子1台）**: 現行 `deploy-child.sh` 単体と同じ結果になること。
   `--dry-run` が全台分の差分を表示すること。
4. **フリート挙動（子2台）**: `serial` 相当の直列配布、1台目失敗時に2台目へ配布しない
   （fail-fast）、`--only` で対象を絞れること。
5. **ロールバック**: 壊れた配布物を意図的に投入し、当該の子だけが自動復旧し、
   他の子が影響を受けないこと。
6. **STA_NO 重複検出**: 意図的に同一割当を持つ2台を作り、`fleet-status.sh` が警告すること。

## 8. ロールアウト段階

| Step | 内容 | 効果 |
|---|---|---|
| 1 | 設定所有権の是正（§5.1・5.2）＋ AP IP 実行時導出（§5.3） | 子1台の現状でも上書き事故が消える |
| 2 | インベントリ（§5.4）＋ `deploy-fleet.sh`（§5.5）＋ `fleet-status.sh`（§5.6） | 複数子へ安全に配れる |
| 3 | 2台目の子を実投入し、ローリング配布・STA_NO重複検出・ロールバック隔離を実証 | 親1:子多が実運用に乗る |

Step 1 は単独で価値があり、子を増やす前に適用しておくべきものである。

## 9. 将来Ansibleへ移行する際の接続点

- `fleet/children.conf` の一覧 → Ansible inventory の `children` グループ。
- ファイル分類（コード / フリート共通 / 機体固有）→ role のタスク分割にそのまま対応。
- 機体固有設定を端末所有としたため、移行時も `host_vars` によるレンダリングは不要。
  リポジトリを正にする方針へ変える場合のみ、フル設計書§6の overlay を導入する。
- 中央ops追加時は、子への到達を `ansible_ssh_common_args` の ProxyJump に置き換えるだけでよい。

## 10. 未解決事項

- `crop_config.json` が `child/*.py` から参照されていない。未使用なら整理対象だが、
  今回のスコープでは「配布しない」に倒し、削除判断は別途行う。
- `send_target_config.json` をフリート共通として配布した場合、現場で `enabled` を
  一時的にoffにしている子があると再有効化してしまう。opt-in 配布に留める理由でもあるが、
  運用ルールとして明記が必要。
- 親AP（wlan1）のDHCPプールが想定台数分の空きを持つか未確認（リポジトリ外のOS設定）。
