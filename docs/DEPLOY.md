# デプロイ / バージョンアップ手順（親 Pi 5 ＋ 子 Zero2）

2層WiFi構成（Oracle ⇄ 親 wlan0、親 wlan1(AP 10.42.0.1) ⇄ 子 10.42.0.51）での
アプリ・MLモデルの更新を、**親から1コマンド**で・**失敗時は自動ロールバック**で行う。

## なぜ K3s ではないのか

- 子は **Pi Zero 2 W（RAM 416MB / 空き ~85MB）**。K3s agent 常駐だけで大半を食い、
  カメラ推論と共存できない。Docker すら載っていない。
- 子は親のAP配下で**外部レジストリに出られない**。K3s なら親に registry + 常時接続が必要で、
  2ノードには重すぎる。
- ノード2台では K3s の旨み（多数ノード・自己修復・宣言的ロールアウト）より運用コストが勝る。

→ 実態（親=git+docker / 子=素ファイル+systemd）に合わせ、**SSH+rsync+systemd の
pull型スクリプト**を採用。台数が 3〜5 に増えたら Ansible へ無理なく発展できる。

## 構成と"正"の置き場

| 対象 | 実行環境 | ソース・オブ・トゥルース | 配布方法 |
|---|---|---|---|
| 親アプリ | git + docker compose | **親ローカルの git ツリー**（tag） | `deploy-parent.sh`（local checkout + compose） |
| 子アプリ | 素python + systemd | `child/`（git管理） | `deploy-child.sh`（rsync + restart） |
| 子モデル | IMX500 `.rpk` | `models/<name>/<version>/`（親ローカル） | `deploy-model.sh`（rsync + restart） |

共通ロジック（バックアップ / ヘルスチェック / ロールバック）は
`scripts/lib/deploy-common.sh`。

> **ネットワーク前提**：親は普段 **社内ネットで GitHub に接続できない**。よってどの経路も
> GitHub を必須にしない。子はそもそも親からの rsync のみ（GitHub 非依存）。親は稼働ツリー
> 兼 git ツリーなので、**親上で `git tag` → checkout でネット無しに完結**する。GitHub は
> 接続できる時だけの「オフサイトバックアップ / 取り込み(`--fetch`)」に留める。

## 手順

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

### 子のMLモデルを更新する
```bash
# 1. models/<name>/<新version>/ に network.rpk / labels.txt / packerOut.zip を置く
scripts/deploy-model.sh --list
scripts/deploy-model.sh signal_tower <新version>   # 配布 → picamera再起動 → 検証
# 戻すとき
scripts/deploy-model.sh signal_tower <旧version>
```

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

#### 状態確認と STA_NO 重複チェック

```bash
scripts/fleet-status.sh          # 全子の稼働状況 + STA_NO割当 + 重複判定
scripts/fleet-status.sh --only zero2
```

**子を増やしたら必ず実行する。** Oracle の MERGE キーは `MK_DATE + STA_NO1-3 +
T1_STATUS` のみで `device_id` を含まないため、子同士で STA_NO が重複すると
同時刻・同ステータスのレコードが**無警告で欠落**する。

終了コード:

| コード | 意味 |
|---|---|
| 0 | 全機体を検査できて重複なし |
| 1 | STA_NO 重複あり（最も実行可能な合図なので、検査不能な子があっても優先） |
| 2 | 検査しきれていない（到達できない子がある／割当を読めない子がある） |

**`2` を `0` と混同しないこと。**「安全」ではなく「確かめられていない」を意味する。
`2` のまま子を追加すると、未検査の機体と衝突している可能性が残る。

### 親を更新する（GitHub 非接続前提）
```bash
# 親の上で直接（社内ネット・ネット無しでOK）
git add -A && git commit -m "..."          # 親で編集→コミット
git tag v1.2.3                             # ローカルにタグ
scripts/deploy-parent.sh v1.2.3            # local checkout + compose + 検証 →(失敗時)戻し

# GitHub に繋がる時だけ：バックアップ push / 取り込み
git push origin --tags                     # 繋がった時にまとめて退避
scripts/deploy-parent.sh --fetch v1.2.3    # 別マシンで打ったタグを取得してから更新
```

## 安全設計

- **runtime 状態と機体固有設定を絶対に触らない**：`send_target_state.json`（送信カーソル）や
  `logs/`・`raw_images/` に加え、機体固有設定（`id_names_config.json` 等、
  `CHILD_DEVICE_OWNED_FILES`）も配布対象外。上書きすると送信巻き戻り/実データ消失、
  および STA_NO 誤送信になるため。
- **配布前バックアップ**：上書き対象の現行版を子の `~/.deploy-backups/<ts>/` に退避
  （直近 `KEEP_BACKUPS=5` 世代を保持）。
- **ヘルスチェック（3層）**：
  1. **即時**：サービス `active` + 数秒の安定確認（crash-loop 検出）+ web_server `:8080` 応答。
  2. **readiness**：`/model_status`==`ready` かつ `/current_model` が期待モデルと一致
     （実トラフィックを待たず、モデル未ロード/誤モデルを決定論的に検出）。
     ※レコードはイベント駆動のため「実レコード到達待ち」は無人時に誤ロールバックを招く。だから採らない。
  3. **E2E（任意, `VERIFY_E2E=1`）**：`/send_logs_now` で1回送信 → `/send_target_status` の
     `last_result` が成功か確認（600s 待たずにデータ経路まで検証）。
- **自動ロールバック**：チェック失敗で直前版へ復元 → 再起動 → 再チェック（readiness 含む）。
  `--no-rollback` で無効化可。

## 上書き可能な設定（環境変数）

`CHILD_SSH`(既定 zero2) / `CHILD_AP_IP`(既定は空＝子から自動取得) /
`CHILD_WEB_PORT`(8080) / `KEEP_BACKUPS`(5) / `HEALTH_STABLE_WAIT`(6秒)
— 詳細は `scripts/lib/deploy-common.sh`。

子のIPは親APのDHCP動的割当のため、既定では**デプロイ時に子自身から取得**する
（`hostname -I` の先頭IPv4）。固定したい場合のみ `CHILD_AP_IP` を明示指定する。
