# Presence Logger

Raspberry Pi 5 上で USB カメラの映像から人物の在/不在を検出し、Oracle DB に
**確実に1回だけ（Exactly-once）** ENTER/EXIT イベントを書き込む常駐アプリ。
接続中の WiFi SSID によって SNTP サーバ・Oracle 接続先が自動で切り替わる。

## 特徴

- **Exactly-once 保証**: 電源断・ネット切断・DB 一時停止下でも、復旧後に **ちょうど1回**だけ書き込み
- **既存スキーマに変更不要**: `MERGE INTO` を使った冪等 INSERT で、ユニークキー追加なしに重複防止
- **WiFi 切替で接続先切替**: 工場間移動・ネットワーク切替で自動的に正しい DB へ
- **Cloud ADB / オンプレ Oracle 両対応**: Wallet 接続（Thin mode）と直接接続を `profiles.yaml` で切替
- **常時稼働設計**: Pi 5 + USB カメラ + Docker Compose + systemd で起動/再起動/障害復旧を自動化

## アーキテクチャ

```
+---------------------- Raspberry Pi 5 (Bookworm 64bit) ----------------------+
|                                                                             |
|  /dev/video0                                                                |
|     |                                                                       |
|     v                                                                       |
|  +-----------+   MQTT QoS=2   +-----------+   subscribe   +-------------+   |
|  | detector  | -- publish --> | mosquitto | --- event --> |   bridge    |   |
|  | (MediaPipe|                |           |               |             |   |
|  |  + camera)| <-- ACK ------ |           | <-- publish - |             |   |
|  +-----------+                +-----------+               | +---------+ |   |
|                                                           | | SQLite  | |   |
|                                                           | | inbox   | |   |
|                                                           | +----+----+ |   |
|                                                           |      |      |   |
|                                                           |  WiFi SSID  |   |
|                                                           | (DBus/nmcli)|   |
|                                                           +------+------+   |
+------------------------------------------------------------------|----------+
                                                                   |
                                                                   v
                                                       +-----------------------+
                                                       |  Oracle DB            |
                                                       |  (SSID で宛先切替)    |
                                                       |  HF1RCM01 へ MERGE   |
                                                       +-----------------------+
```

| コンテナ | 役割 |
|---|---|
| `mosquitto` | ローカル MQTT broker（ホストにポート公開なし、内部ネットワーク専用） |
| `detector` | USB カメラ → MediaPipe 人検知 → 3秒デバウンス → MQTT publish (QoS=2) |
| `bridge` | MQTT subscribe → SQLite inbox 永続化 → SSID プロファイル解決 → Oracle MERGE → ACK |

3 コンテナは Docker bridge ネットワーク `presence-net` で接続される。`bridge` はホストの
`/run/dbus` と `/var/run/NetworkManager` を読み取り専用マウントすることで、`hostNetwork`
を使わずに `nmcli` で WiFi SSID を取得できる（K3s 移行を見据えた設計）。

詳細：

- 仕様書: [`docs/superpowers/specs/2026-04-27-presence-logger-design.md`](docs/superpowers/specs/2026-04-27-presence-logger-design.md)（1003 行）
- 実装プラン: [`docs/superpowers/plans/2026-04-27-presence-logger.md`](docs/superpowers/plans/2026-04-27-presence-logger.md)（6122 行）
- 受入チェックリスト: [`docs/acceptance-checklist.md`](docs/acceptance-checklist.md)

## 動作要件

- **ハード**: Raspberry Pi 5（Pi 4 でも可、aarch64 / ARM64）
- **OS**: Raspberry Pi OS Bookworm 64bit / Debian Trixie 64bit
- **カメラ**: USB UVC カメラ（`/dev/video0` で見えるもの）
- **WiFi**: NetworkManager 管理下（`nmcli` で SSID 取得できる構成）
- **Oracle**: 既存テーブル `HF1RCM01` （カラム: `MK_DATE`, `STA_NO1`, `STA_NO2`, `STA_NO3`, `T1_STATUS`）

## 本番インストール（Raspberry Pi 5）

```bash
# 1. リポジトリを /opt/presence-logger にクローン
sudo git clone https://github.com/MasatoshiSano/presence-logger.git /opt/presence-logger
cd /opt/presence-logger

# 2. MediaPipe モデルをダウンロード（13 MB）
sudo curl -o services/detector/models/efficientdet_lite0.tflite \
  https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float32/latest/efficientdet_lite0.tflite

# 3. インストーラを実行（/etc/presence-logger/ + timesyncd 設定 + systemd unit 配置）
sudo bash scripts/install.sh

# 4. 設定ファイルを編集
sudo $EDITOR /etc/presence-logger/device.yaml      # ステーション番号 (sta_no1/2/3) を設定
sudo $EDITOR /etc/presence-logger/profiles.yaml    # WiFi SSID -> SNTP/Oracle のプロファイルを定義
sudo $EDITOR /etc/presence-logger/secrets.env      # Oracle / Wallet パスワードを環境変数として設定

# 5. (任意) Wallet 接続を使う場合は zip を展開
sudo unzip /path/to/Wallet_xxx.zip -d /etc/presence-logger/wallets/adb/
sudo chmod 640 /etc/presence-logger/wallets/adb/*
sudo chgrp -R docker /etc/presence-logger/wallets

# 6. (任意) Oracle Thick mode を有効化（Instant Client 同梱イメージ）
echo 'INSTANT_CLIENT_URL=https://download.oracle.com/.../instantclient-basiclite-linux.arm64-21.13.0.0.0dbru.zip' \
  | sudo tee /opt/presence-logger/.env

# 7. ビルドと起動（systemd で常時稼働化）
sudo docker compose --project-directory /opt/presence-logger build
sudo systemctl enable --now presence-logger.service

# 8. 動作確認
sudo systemctl status presence-logger.service
docker compose ps                                  # 3コンテナ全部 healthy か
bash scripts/tail-logs.sh                          # JSON ログをライブ追跡
```

### `profiles.yaml` の例

```yaml
profiles:
  factory_a_wifi:                                  # 工場 A の WiFi SSID
    description: "Factory A — オンプレ Oracle 直接接続"
    sntp:
      servers: ["ntp.factory-a.local", "ntp.nict.jp"]
    oracle:
      client_mode: "thin"
      auth_mode: "basic"                           # user/password で接続
      host: "10.10.1.50"
      port: 1521
      service_name: "PRDDB"
      user: "presence_user"
      password: "${ORACLE_PASSWORD_A}"             # secrets.env から展開
      table_name: "HF1RCM01"

  factory_b_wifi:                                  # 工場 B の WiFi SSID
    description: "Factory B — Cloud ADB Wallet 接続"
    sntp:
      servers: ["ntp.factory-b.local"]
    oracle:
      client_mode: "thin"
      auth_mode: "wallet"                          # Wallet 経由で接続
      dsn: "myadb_low"                             # tnsnames.ora のエイリアス
      user: "ADMIN"
      password: "${ORACLE_PASSWORD_B}"
      wallet_dir: "/etc/presence-logger/wallets/adb"
      wallet_password: "${WALLET_PASSWORD_B}"
      table_name: "HF1RCM01"

unknown_ssid_policy: "hold"                        # 未知 SSID 接続中はイベントを inbox に保留
```

## 開発

開発作業はすべて、production と同じ Docker イメージ内で実行する。ホストの Python venv
は不要（むしろ非推奨：ホストの Python 3.13 には MediaPipe の aarch64 wheel が無い）。

```bash
# 前提：Docker と docker compose v2 がインストール済み + ユーザーが docker グループ所属
#   curl -fsSL https://get.docker.com | sudo sh
#   sudo usermod -aG docker $USER && newgrp docker

# 全テスト + lint 実行（初回は dev イメージをビルド、所要約3〜5分）
bash scripts/test.sh

# 個別サービスのテスト
bash scripts/test.sh detector
bash scripts/test.sh bridge
bash scripts/test.sh integration

# pytest 引数を渡す
bash scripts/test.sh detector -- pytest -k fsm -v
```

dev イメージは `services/<name>/src` と `tests/` を **read-only bind mount** する設計なので、
コード変更後の再ビルド不要で即テスト可能。

### テスト構成

| 種別 | カバレッジ |
|---|---|
| **detector ユニットテスト** | buffer / FSM / camera / inference / mqtt / main loop（30+ ケース） |
| **bridge ユニットテスト** | inbox / network / time / profile / circuit / oracle / mqtt / sender（68+ ケース） |
| **integration（E2E）** | 6 シナリオ：正常 / Oracle 復旧 / 未知 SSID / SNTP 補正 / サーキットブレーカ / 冪等再送 |
| **live スモークテスト** | 実 Oracle に対する MERGE→DELETE（`scripts/smoke_test_real_oracle.py`） |
| **live カメラパイプライン** | 実 USB カメラ + 実 MediaPipe + 実 Oracle（`scripts/live_camera_pipeline_mediapipe.py`） |

合計 **134+ テスト** が Docker 内で 1 秒以内に完走する。

## 運用

### ファイル・ディレクトリ一覧

| 種別 | パス | 内容 |
|---|---|---|
| detector ログ | `/var/log/presence-logger/detector.log` | カメラ・推論・FSM 遷移・MQTT publish・ACK 受信（JSON Lines、10 MB × 5 ローテーション） |
| bridge ログ | `/var/log/presence-logger/bridge.log` | MQTT 受信・SSID 解決・Oracle MERGE・ACK 送信・SNTP 状態（同上） |
| mosquitto ログ | `docker compose logs mosquitto`（永続化なし） | broker 接続・切断 |
| detector バッファ | `/var/lib/presence-logger/detector_buf.db` | 未 ACK イベント（永続化、再起動後リカバリ用） |
| bridge inbox | `/var/lib/presence-logger/bridge_buf.db` | 受信済み・未送信イベント |
| 設定 | `/etc/presence-logger/{device,detector,bridge,profiles}.yaml` | 編集対象 |
| シークレット | `/etc/presence-logger/secrets.env`（chmod 640 root:docker） | パスワード等、Git 対象外 |
| Wallet | `/etc/presence-logger/wallets/adb/` | Oracle Cloud ADB 接続用 |

### ログ仕様（共通フォーマット）

両プロセス（detector / bridge）とも **JSON Lines** で書き出す。1 行 = 1 イベントの整形済み JSON。
ファイルは **10 MB × 5 世代**ローテーション（プロセスあたり最大 60 MB、全体 120 MB 上限）。

#### 共通フィールド（全行に必ず含まれる）

| キー | 型 | 例 |
|---|---|---|
| `ts` | string (ISO 8601 + JST オフセット + ミリ秒) | `"2026-04-28T11:33:37.901+09:00"` |
| `level` | string | `"INFO"` / `"WARNING"` / `"ERROR"` / `"CRITICAL"` |
| `logger` | string（ドット区切り階層） | `"detector.fsm"` / `"bridge.oracle"` |
| `event` | string（このイベントの種類） | `"transition"` / `"merge_committed"` |
| `process` | string | `"detector"` または `"bridge"` |
| `pid` | int | `1` |
| `device_id` | string | `"raspberrypi5"` |
| `event_id` | string | UUIDv4。該当イベントを跨いで**同じ ID** で追跡可能 |
| `error` | object（ERROR 以上のみ） | `{"type":"...","message":"...","traceback":"..."}` |

加えて各イベント固有のキーが付く（後述カタログ参照）。

### イベントカタログ — detector

| logger | event | レベル | 主なフィールド | 意味 |
|---|---|---|---|---|
| `detector.fsm` | `transition` | INFO | `from`, `to`, `event_type`, `event_id`, `candidate_duration_ms`, `latest_score` | ENTER または EXIT が確定 |
| `detector.fsm` | `candidate_start` | DEBUG | `candidate`, `score` | 状態遷移候補の開始（デバウンス開始） |
| `detector.fsm` | `candidate_cancel` | DEBUG | `held_ms`, `reason` | 候補取り消し（デバウンス未満の点滅） |
| `detector.inference` | `frame` | DEBUG | `infer_ms`, `top_score`, `has_person` | フレーム単位の推論結果（高頻度） |
| `detector.mqtt` | `publish` | INFO | `topic`, `qos`, `event_id`, `mid` | MQTT publish 実行 |
| `detector.mqtt` | `ack_received` | INFO | `event_id`, `mk_date_committed`, `round_trip_ms` | bridge から ACK 到着、送信完了確定 |
| `detector.mqtt` | `ack_timeout` | WARNING | `event_id`, `retry_count`, `next_retry_at` | ACK タイムアウト、再送スケジュール |
| `detector.camera` | `failure` | ERROR | `consecutive_failures` | 連続読み取り失敗（USB 抜け検知） |
| `detector.stats` | `periodic` | INFO（60s 毎） | `fps_observed`, `infer_p50_ms`, `buffer_pending` | 統計サマリ |
| `detector.main` | `startup` | INFO | `config_path` | プロセス起動 |

### イベントカタログ — bridge

| logger | event | レベル | 主なフィールド | 意味 |
|---|---|---|---|---|
| `bridge.main` | `received` | INFO | `event_id` | MQTT 受信、`inbox` に永続化 |
| `bridge.sender` | `merge_committed` | INFO | `event_id`, `mk_date`, `rows_affected`, `profile`, `latency_ms` | Oracle MERGE 成功（rows_affected=0 は冪等 skip） |
| `bridge.sender` | `merge_failed` | ERROR | `event_id`, `ora_code`, `retry_count`, `next_retry_at` | Oracle 書き込み失敗、再試行スケジュール |
| `bridge.mqtt` | `ack_published` | INFO | `event_id` | detector に ACK を返した |
| `bridge.profile` | `resolve` | INFO | `ssid`, `profile`, `oracle_host`, `auth_mode`, `client_mode` | SSID → プロファイル解決 |
| `bridge.profile` | `switch` | INFO | `from`, `to` | プロファイル切替（WiFi 移動） |
| `bridge.profile` | `unknown_ssid` | WARNING | `ssid`, `policy`, `inbox_pending` | プロファイル未定義の SSID |
| `bridge.network` | `nmcli_failed` | WARNING | `error.type`, `error.message` | nmcli 実行失敗（DBus 不通等） |
| `bridge.time` | `sync_acquired` | INFO | `sync_wall_iso`, `sync_monotonic_ns`, `backfill_count` | NTP 同期完了、補正基準確定 |
| `bridge.time` | `sync_lost` | WARNING | `unsynced_for_seconds` | 同期喪失 |
| `bridge.oracle` | `circuit_open` | CRITICAL | `profile`, `ora_code`, `reopens_at` | 恒久エラー検出、サーキット OPEN（15 分後 half-open） |
| `bridge.oracle` | `circuit_close` | INFO | `profile` | サーキット復旧 |
| `bridge.stats` | `periodic` | INFO（60s 毎） | `current_ssid`, `ntp_synced`, `inbox_count`, `oracle_circuit_state` | 統計サマリ |

### 実サンプル行

```json
{"ts":"2026-04-28T11:33:37.114+09:00","level":"INFO","logger":"detector.mqtt","process":"detector","pid":1,"device_id":"raspberrypi5","event":"publish","topic":"presence/event","qos":2,"event_id":"e6ed87d4-1a92-4aa6-bbb2-129dc66c327b","payload_size_bytes":237,"mid":5}
{"ts":"2026-04-28T11:33:37.122+09:00","level":"INFO","logger":"detector.fsm","process":"detector","pid":1,"device_id":"raspberrypi5","event":"transition","from":"ABSENT","to":"PRESENT","event_type":"ENTER","event_id":"e6ed87d4-1a92-4aa6-bbb2-129dc66c327b","candidate_duration_ms":3329,"latest_score":0.6289836168289185}
{"ts":"2026-04-28T11:33:37.127+09:00","level":"INFO","logger":"bridge.main","process":"bridge","pid":1,"device_id":"raspberrypi5","event":"received","event_id":"e6ed87d4-1a92-4aa6-bbb2-129dc66c327b"}
{"ts":"2026-04-28T11:33:37.901+09:00","level":"INFO","logger":"bridge.time","process":"bridge","pid":1,"device_id":"raspberrypi5","event":"sync_acquired","sync_wall_iso":"2026-04-28T11:33:37.901+09:00","sync_monotonic_ns":1456585470085}
{"ts":"2026-04-28T11:33:38.412+09:00","level":"INFO","logger":"bridge.sender","process":"bridge","pid":1,"device_id":"raspberrypi5","event":"merge_committed","event_id":"e6ed87d4-1a92-4aa6-bbb2-129dc66c327b","mk_date":"20260428113337","rows_affected":1,"profile":"286345207328","latency_ms":68}
```

### ログレベルとトリアージ指針

| レベル | 意味 | 運用アクション |
|---|---|---|
| `INFO` | 通常動作、状態遷移、統計 | 監視ダッシュボードで集計 |
| `WARNING` | 自己回復可能な異常（ACK タイムアウト、未知 SSID、nmcli 一時失敗） | 件数集計、急増したら調査 |
| `ERROR` | 一時的な失敗（Oracle 接続失敗、MERGE 失敗） | retry_count を見て累積するなら調査 |
| `CRITICAL` | 恒久エラー、サーキット OPEN（テーブル無し、認証失敗等） | **即対応**：設定 / 権限 / DB 状態を確認 |
| `FATAL` | プロセス終了 | systemd / Docker 再起動。再発するなら設定不正 |

### 運用 jq レシピ

```bash
# 特定の event_id の「一生」を時系列で追う（detector → bridge → ACK）
EID="e6ed87d4-1a92-4aa6-bbb2-129dc66c327b"
sudo cat /var/log/presence-logger/*.log | jq -c "select(.event_id == \"$EID\")" | jq -s 'sort_by(.ts)'

# 直近 5 分の ENTER / EXIT 確定だけ抽出
sudo tail -n 5000 /var/log/presence-logger/detector.log | jq -c 'select(.event == "transition")'

# Oracle MERGE 成功を時系列で（mk_date と latency 列だけ）
sudo cat /var/log/presence-logger/bridge.log | jq -c 'select(.event == "merge_committed") | {ts, mk_date, rows_affected, latency_ms}'

# サーキット OPEN 履歴（重大インシデント追跡）
sudo cat /var/log/presence-logger/bridge.log | jq -c 'select(.event | startswith("circuit_"))'

# ERROR / CRITICAL だけ抽出
sudo cat /var/log/presence-logger/*.log | jq -c 'select(.level | IN("ERROR","CRITICAL","FATAL"))'

# 60 秒統計の inbox_count 推移を CSV 化（ダッシュボード投入用）
sudo cat /var/log/presence-logger/bridge.log | jq -r 'select(.event == "periodic") | [.ts, .inbox_count, .ntp_synced, .current_ssid] | @csv'

# ENTER から ACK 受領までの round trip ヒストグラム
sudo cat /var/log/presence-logger/detector.log | jq -r 'select(.event == "ack_received") | .round_trip_ms' | sort -n | uniq -c
```

### 機微情報のスクラブ

`bridge.profile.resolve` などのログでも、**`password` / `wallet_password` フィールドは絶対に
出力しない**。設定ロード時に「機微フィールド」マークが付き、ログ用 dump で `"***"` に置換される
（`services/bridge/src/profile_resolver.py` の `redact_for_logging()` 参照）。

### よく使うコマンド

```bash
# JSON ログをライブ追跡
bash scripts/tail-logs.sh

# 特定 event_id の一生を時系列で追う
sudo grep '<event_id>' /var/log/presence-logger/*.log | jq -s 'sort_by(.ts)'

# bridge inbox の状態
sudo sqlite3 /var/lib/presence-logger/bridge_buf.db 'SELECT status, COUNT(*) FROM inbox GROUP BY status;'

# detector buffer の状態
sudo sqlite3 /var/lib/presence-logger/detector_buf.db 'SELECT status, COUNT(*) FROM pending_events GROUP BY status;'

# サービス再起動
sudo systemctl restart presence-logger.service

# コンテナだけ再起動
docker compose restart bridge

# 完全停止
sudo systemctl stop presence-logger.service
docker compose down
```

### コンテナ起動方法

| 用途 | コマンド |
|---|---|
| 開発・テスト時 | `bash scripts/test.sh` |
| 手動で起動して挙動確認 | `docker compose up -d` |
| 本番常駐（電源 ON で自動起動） | `sudo systemctl enable --now presence-logger.service` |
| 停止（コンテナ削除） | `docker compose down` |
| 停止（コンテナ保持、再起動可能） | `docker compose stop` |

## トラブルシューティング

| 症状 | 原因 / 対処 |
|---|---|
| `docker compose build` が `server misbehaving` で DNS 失敗 | ホスト DNS が壊れている。`/etc/resolv.conf` を `nameserver 8.8.8.8` に書き換え。詳細はブログ記事 `2026-04-28-docker-dns-trixie-resolv-conf.md` 参照 |
| `pip install mediapipe` が失敗 | aarch64 + Python 3.13 に wheel が無い。Docker (Python 3.11) で動かす設計なのでホスト venv は不要 |
| pytest が `import file mismatch` | `pyproject.toml` で `--import-mode=importlib` 指定済みのはず。`__pycache__` を削除して再実行 |
| 全イベントが永遠に保留される | bridge ログで `ntp_synced: false` が続いていれば、`timedatectl` がコンテナで動いていない。`time_source.py` / `time_watcher.py` の `is_synced()` が「ホスト時計を信頼」フォールバックを実装済みか確認 |
| `Oracle DPY-6005 timed out` | TCP 不到達（ネット問題、factory WiFi 圏外） |
| `Oracle DPY-6001 service not registered` | リスナーには到達したが DB instance が停止中。OCI コンソールで ADB を Start |
| 検知後 3 秒経っても DB に行が出ない | bridge ログで `merge_committed` が出ているか確認。出ていなければ Sender のスキップ条件（SSID 未知、circuit open、SNTP 未同期）を疑う |

## ブログ記事（このプロジェクトの開発知見）

このリポジトリの `content/posts/` には、開発過程で得た知見を 12 本の記事にまとめた下書きが
含まれている：

- amqtt と paho-mqtt の MQTT v5/v3.1.1 互換問題
- python-oracledb Thin で Oracle ADB に Wallet 接続する完全手順
- Pi 5 + Python 3.13 で MediaPipe wheel が無いときの逃げ方
- pytest `--import-mode=importlib` でモノレポ同名 test ファイル衝突
- Exactly-once 配信を Oracle MERGE + MQTT QoS=2 + ACK で実装
- Docker コンテナから nmcli で WiFi SSID を取得する
- Claude Code のサブエージェントで 35 タスクを並列実装
- Docker daemon が docker.io を引けない問題
- Slim Docker image で `timedatectl` が動かない
- `sg docker -c` で usermod 直後のセッションでも docker を使う
- Pi 5 USB カメラを Docker の MediaPipe に渡す
- Docker `env_file` のパーミッション設計

## ライセンス

未指定（プライベート利用想定）。配布する場合は適宜ライセンスを追加。
