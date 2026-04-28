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

### ログとデータの場所

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
