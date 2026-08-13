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

### 子のMLモデルを更新する
```bash
# 1. models/<name>/<新version>/ に network.rpk / labels.txt / packerOut.zip を置く
scripts/deploy-model.sh --list
scripts/deploy-model.sh signal_tower <新version>   # 配布 → picamera再起動 → 検証
# 戻すとき
scripts/deploy-model.sh signal_tower <旧version>
```

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

- **runtime 状態を絶対に触らない**：`send_target_state.json`（送信カーソル）や
  `logs/`・`raw_images/` は配布対象外。上書きすると送信巻き戻り/実データ消失になるため。
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

`CHILD_SSH`(既定 zero2) / `CHILD_AP_IP`(10.42.0.51) / `CHILD_WEB_PORT`(8080) /
`KEEP_BACKUPS`(5) / `HEALTH_STABLE_WAIT`(6秒) — 詳細は `scripts/lib/deploy-common.sh`。
