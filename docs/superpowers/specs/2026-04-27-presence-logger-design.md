# Presence Logger 設計書

- **作成日**: 2026-04-27
- **対象**: Raspberry Pi 5 (Bookworm 64bit) 上で動作する人検知・Oracle記録アプリ
- **参考リポジトリ**: https://github.com/MasatoshiSano/color-detector-app（MQTT/Oracle Bridge 構成を踏襲）

---

## 1. 概要

### 1.1 目的

USBカメラの映像から人物の在/不在を検出し、ENTER/EXIT イベントを既存の Oracle テーブル `HF1RCM01` に**正確な時刻・確実に1回**で記録する。

### 1.2 機能要件

- USBカメラ（`/dev/video0`）から常時映像を取得し、人物の有無を検出する
- 検出が確定したタイミング（ENTER/EXIT）を Oracle DB に INSERT する
- 検出時刻は `YYYYMMDDhhmmss`（Asia/Tokyo, 14桁文字列）で保存する
- 接続している WiFi SSID によって、SNTP サーバと Oracle 接続先を切り替える
- ネットワーク断・電源断・DB停止・SNTP未同期などの障害下でも、復旧後に**ちょうど1回** Oracle に記録する（Exactly-once 保証）

### 1.3 非機能要件

- 常時稼働（24/365）
- ログは構造化（JSON Lines）、ISO 8601 タイムスタンプ
- 撮影画像は一切保存しない（プライバシー）
- 将来的な K3s デプロイへの移行性を確保する

### 1.4 スコープ外（Non-goals）

- 個別人物の識別・追跡（顔認識・ID付与は対象外）
- 人数カウント・滞在時間集計（DB側の後処理）
- Web UI（設定変更は YAML 直編集）

---

## 2. アーキテクチャ概要

### 2.1 コンポーネント構成

```
┌──────────────────────── Raspberry Pi 5 ────────────────────────┐
│                                                                 │
│   /dev/video0                                                   │
│       │                                                         │
│       ▼                                                         │
│   ┌────────────┐    publish    ┌────────────┐    subscribe     │
│   │  detector  │ presence/event ▶│ mosquitto  │ presence/event ─▶│
│   │ (Python +  │               │ (内部のみ) │                  │
│   │ MediaPipe) │ ◀ presence/   │            │ ◀ presence/      │
│   │            │   event/ack   │            │   event/ack      │
│   └────────────┘               └────────────┘                  │
│                                       ▲                        │
│                                       │                        │
│                                ┌──────┴───────┐                │
│                                │   bridge     │                │
│                                │ (Python)     │                │
│                                │              │                │
│                                │ ┌──────────┐ │                │
│                                │ │ SQLite   │ │                │
│                                │ │ inbox.db │ │                │
│                                │ └────┬─────┘ │                │
│                                │      │       │                │
│                                │  WiFi SSID   │                │
│                                │  via DBus    │                │
│                                │      │       │                │
│                                └──────┼───────┘                │
└───────────────────────────────────────┼────────────────────────┘
                                        │
                                        ▼
                            ┌─────────────────────┐
                            │  Oracle DB          │
                            │  (SSIDで宛先切替)   │
                            │  HF1RCM01           │
                            └─────────────────────┘
```

3 つのコンテナで構成する（Docker Compose）:

| コンテナ | 役割 |
|---|---|
| `mosquitto` | ローカル MQTT ブローカー（コンテナ間疎結合のため） |
| `detector` | カメラ → MediaPipe 推論 → デバウンス判定 → MQTT publish |
| `bridge` | MQTT subscribe → SQLite 永続化 → SSID プロファイル解決 → Oracle MERGE → ACK publish |

### 2.2 採用技術

| 役割 | 採用 |
|---|---|
| 言語 | Python 3.11+ |
| 推論 | MediaPipe Tasks (Object Detector / EfficientDet-Lite0, person クラスのみ) |
| カメラ | OpenCV (`cv2.VideoCapture`) |
| MQTT クライアント | `paho-mqtt` |
| MQTT ブローカー | `eclipse-mosquitto:2`（公式イメージ） |
| Oracle ドライバ | `python-oracledb`（Thin/Thick 両対応） |
| Instant Client | Basic Light（約30MB）をコンテナにバンドル（Thick 用） |
| SQLite | 標準ライブラリ `sqlite3` |
| 設定ファイル | YAML（`pyyaml`） |
| ロギング | 標準 `logging` + `python-json-logger` + `RotatingFileHandler` |

### 2.3 ネットワーク構成（K3s 移行性を確保）

- 全コンテナを Compose 内部ブリッジネットワーク `presence-net` に配置（`hostNetwork` は使わない）
- bridge は `/run/dbus` と `/var/run/NetworkManager` を read-only でマウントし、**DBus socket 経由で `nmcli` を使って WiFi SSID を取得**する
- mosquitto はホストにポート公開しない（`presence-net` 内部のみで到達）

これにより、将来 K3s 上に DaemonSet として移行する際、アプリコード変更ゼロ・マウント定義の書式変換のみで Pod 化できる。

---

## 3. コンポーネント詳細

### 3.1 detector コンテナ

#### 責務

USB カメラからフレーム取得 → MediaPipe で人検出 → 時間ベースのデバウンス判定 → ENTER/EXIT を MQTT へ publish。ACK 受領まで再送する。

#### 状態機械

```
ABSENT ◀──────────── EXIT 確定 ──────────── PRESENT
   │                                           ▲
   │                                           │
   └──── ENTER 確定 (PRESENT を3秒継続観測) ───┘
```

「観測 != 現状態」の状態を継続観測した時間が `DEBOUNCE_SEC`（デフォルト 3.0 秒）を超えたら遷移確定する。`DEBOUNCE_SEC` 未満の点滅は無視。

#### 主要パラメータ（デフォルト）

- 解像度: 640×480
- 推論 FPS: 1.5
- スコア閾値: 0.5
- デバウンス: 3.0 秒
- カメラ起動時の捨てフレーム: 5
- カメラ連続読み取り失敗 10 回で「カメラ異常」状態へ遷移し、PRESENT なら EXIT を強制発行（DBに在席を残さない）

#### MQTT publish 仕様

- トピック: `presence/event`
- QoS: 2（Exactly-once 配信）
- ペイロード: 後述「6.3 MQTTメッセージ仕様」参照

### 3.2 mosquitto コンテナ

- イメージ: `eclipse-mosquitto:2`
- 認証なし、リスナーは内部ネットワーク（`presence-net`）のみ
- 永続化なし（QoS 2 のメッセージ消失は detector_buf.db からの再送でカバー）
- ポートはホストに公開しない

### 3.3 bridge コンテナ

#### 責務

1. MQTT subscribe（`presence/event`）
2. 受信イベントを SQLite `inbox` に永続化（`event_id` 重複は `ON CONFLICT DO NOTHING`）
3. WiFi SSID を取得 → `profiles.yaml` からプロファイル解決
4. SNTP 同期状態を確認、未同期なら同期完了まで送信保留
5. Oracle へ MERGE INTO（冪等 INSERT）+ COMMIT
6. 成功した行を `inbox` で `sent` にマークし、ACK を `presence/event/ack` に publish
7. 失敗時は指数バックオフで再試行

#### スレッド構成

| スレッド | 役割 | 周期 |
|---|---|---|
| MQTT Listener | `presence/event` を受信 → `inbox` へ永続化 | イベント駆動 |
| Sender | `status=received` を Oracle へ MERGE → 成功で `sent` → ACK publish | 1秒間隔ポーリング |
| Network Watcher | DBus 経由 nmcli で SSID を取得、変化をログ | 5秒間隔 |
| Time Watcher | `timedatectl show -p NTPSynchronized` を確認、同期完了で補正基準を確定 | 10秒間隔 |
| Stats | `bridge.stats.periodic` ログを出力 | 60秒間隔 |
| Health | `/tmp/bridge.healthy` を touch | 5秒間隔 |

---

## 4. データフロー

### 4.1 正常系

```
T+0.0s    detector: フレーム取得 → has_person=true 観測
T+0.0s    detector: candidate_state=PRESENT、candidate_started_mono=now
T+0.7s    detector: 観測継続 (has_person=true)
T+1.3s    detector: 観測継続
...
T+3.0s    detector: 連続観測 ≥ 3.0s → ENTER 確定
T+3.0s    detector: event_id=UUIDv4 採番、detector_buf.db INSERT (status='pending')
T+3.0s    detector: MQTT publish QoS=2 → presence/event
T+3.01s   bridge:   受信、inbox INSERT (status='received', ON CONFLICT DO NOTHING)
T+3.02s   bridge:   現在SSID → profile 解決、SNTP同期OK
T+3.05s   bridge:   Oracle MERGE INTO HF1RCM01 + COMMIT
T+3.06s   bridge:   inbox UPDATE status='sent'
T+3.07s   bridge:   MQTT publish QoS=2 → presence/event/ack
T+3.08s   detector: ACK受信、detector_buf.db UPDATE status='acked'
```

### 4.2 失敗系1: Oracle 接続不可

bridge は `inbox` に永続化済み。指数バックオフで再試行（5→15→45→135→405→600 秒上限）。Oracle 復旧で MERGE 成功 → ACK publish。

その間、detector 側も ACK 未受領のため再 publish するが、bridge 側は `event_id` 重複を `ON CONFLICT DO NOTHING` で黙殺し、ACK だけ返す。Oracle への重複 INSERT は MERGE 文で防止。

### 4.3 失敗系2: SNTP 未同期で起動直後に検出

detector は止めない。`monotonic_ns` を含めて publish（`wall_clock_synced=false`）。bridge は `inbox` に永続化するが Oracle 送信は同期完了まで保留。同期完了時、`(sync_wall, sync_monotonic_ns)` 基準で `inbox` の未送信行をバッチ補正してから MERGE。

```python
event_wall = sync_wall - (sync_monotonic_ns - event.monotonic_ns) / 1e9
```

### 4.4 失敗系3: detector 再起動

起動時に `detector_buf.db` の `status != 'acked'` 行をロード、再 publish（同じ `event_id`）。bridge 側は重複検知で MERGE 不要、ACK のみ再送。

### 4.5 失敗系4: bridge が Oracle COMMIT 直後にクラッシュ

`status='sent'` で COMMIT 済みの行は再起動後も再 MERGE しない（重複防止）。`status='sent'` で ACK 未送信の行に対して、起動時タスクが ACK を再 publish。

### 4.6 失敗系5: WiFi 切断 / 未知 SSID

- detector: 撮影・検知・`detector_buf.db` への蓄積・publish 試行は継続
- bridge: 受信・`inbox` への永続化は継続。`unknown_ssid_policy="hold"` で送信保留
- 既知 SSID 復帰で `inbox` の未送信行をフラッシュ

### 4.7 リングバッファ動作

- `detector_buf.db`、`bridge_buf.db` ともに上限 100,000 行
- 上限到達時:
  - 最古の `acked` / `sent` 行から削除
  - 削除可能行が無ければ最古の `pending` / `received` を削除し WARN ログ

---

## 5. 設定ファイル仕様

### 5.1 ディレクトリレイアウト

```
/etc/presence-logger/
├── device.yaml                # デバイス固定（STA_NO1/2/3、device_id）
├── profiles.yaml              # WiFi SSIDごとのSNTP/Oracle接続情報
├── detector.yaml              # detectorパラメータ
├── bridge.yaml                # bridgeパラメータ
├── secrets.env                # 環境変数（パスワード等、chmod 600 root）
└── wallets/
    ├── factory_b/             # Oracle Wallet を展開した中身
    │   ├── tnsnames.ora
    │   ├── sqlnet.ora
    │   ├── ewallet.p12
    │   └── ...
    └── factory_e/

/var/lib/presence-logger/
├── detector_buf.db
└── bridge_buf.db

/var/log/presence-logger/
├── detector.log               # JSON Lines、10MB×5
└── bridge.log                 # JSON Lines、10MB×5
```

### 5.2 `device.yaml`

```yaml
device_id: null              # null=hostname自動。手動指定する場合は文字列
station:
  sta_no1: "001"
  sta_no2: "A"
  sta_no3: "01"
```

`device_id` が `null` のとき、コンテナは `/etc/host_hostname`（ホストの `/etc/hostname` を read-only でマウントしたファイル）を読み取って値を採用する。Docker の既定では container の hostname がコンテナ ID になるため、ホスト名を意図的に共有する必要がある。

### 5.3 `profiles.yaml`

```yaml
profiles:
  factory_a_wifi:
    description: "工場A 第1ライン棟 (オンプレOracle, Thin)"
    sntp:
      servers: ["ntp.factory-a.local", "ntp.nict.jp"]
    oracle:
      client_mode: "thin"           # thin | thick
      auth_mode: "basic"            # basic | wallet
      host: "10.10.1.50"
      port: 1521
      service_name: "PRDDB"
      user: "presence_user"
      password: "${ORACLE_PASSWORD_A}"
      table_name: "HF1RCM01"

  factory_b_wifi:
    description: "工場B (Autonomous DB, Thin + Wallet)"
    sntp:
      servers: ["ntp.factory-b.local"]
    oracle:
      client_mode: "thin"
      auth_mode: "wallet"
      dsn: "myadb_high"
      user: "presence_user"
      password: "${ORACLE_PASSWORD_B}"
      wallet_dir: "/etc/presence-logger/wallets/factory_b"
      wallet_password: "${WALLET_PASSWORD_B}"
      table_name: "HF1RCM01"

  factory_legacy_wifi:
    description: "工場D (古いOracle, Thick必須)"
    sntp:
      servers: ["ntp.factory-d.local"]
    oracle:
      client_mode: "thick"
      auth_mode: "basic"
      host: "10.40.1.50"
      port: 1521
      service_name: "LEGACYDB"
      user: "presence_user"
      password: "${ORACLE_PASSWORD_D}"
      table_name: "HF1RCM01"

unknown_ssid_policy: "hold"      # hold | use_last | drop（設計確定: hold）
```

#### Thin/Thick の混在ルール

`init_oracle_client()` はプロセス全体に効くため、同一 bridge プロセスで Thin/Thick 混在は不可。bridge 起動時に全プロファイルをスキャンし、**1つでも `thick` があればプロセス全体を Thick で初期化**する。

#### auth_mode による分岐

| `auth_mode` | 必須キー |
|---|---|
| `basic` | `host`, `port`, `service_name`, `user`, `password` |
| `wallet` | `dsn`, `user`, `password`, `wallet_dir`（PKCS12 なら `wallet_password` も必須） |

### 5.4 `detector.yaml`

```yaml
camera:
  device: "/dev/video0"
  width: 640
  height: 480
  warmup_frames: 5

inference:
  model_path: "/opt/models/efficientdet_lite0.tflite"
  target_fps: 1.5
  score_threshold: 0.5
  category: "person"

debounce:
  enter_seconds: 3.0
  exit_seconds: 3.0

mqtt:
  host: "mosquitto"
  port: 1883
  qos: 2
  topic_event: "presence/event"
  topic_ack: "presence/event/ack"
  client_id_prefix: "presence-detector"

retry:
  initial_delay_seconds: 5
  max_delay_seconds: 600
  multiplier: 3              # 5, 15, 45, 135, 405, 600(cap)

buffer:
  path: "/var/lib/presence-logger/detector_buf.db"
  max_rows: 100000
```

### 5.5 `bridge.yaml`

```yaml
mqtt:
  host: "mosquitto"
  port: 1883
  qos: 2
  topic_event: "presence/event"
  topic_ack: "presence/event/ack"
  client_id: "presence-bridge"

oracle:
  connect_timeout_seconds: 10
  query_timeout_seconds: 30
  pool_min: 1
  pool_max: 2
  instant_client_dir: "/opt/oracle/instantclient"   # client_mode=thick 時に使用

network_watcher:
  poll_interval_seconds: 5
  ssid_command: "nmcli -t -f ACTIVE,SSID dev wifi"

time_watcher:
  poll_interval_seconds: 10
  sync_command: "timedatectl show -p NTPSynchronized --value"

retry:
  initial_delay_seconds: 5
  max_delay_seconds: 600
  multiplier: 3

circuit_breaker:
  permanent_ora_codes: [942, 904, 1017, 1031, 12514]
  half_open_after_seconds: 900   # 15分

buffer:
  path: "/var/lib/presence-logger/bridge_buf.db"
  max_rows: 100000

logging:
  level: "INFO"
  buffer_stats_interval_seconds: 60
```

### 5.6 SNTP サーバ設定

profile ごとに動的切替する設計は採用しない。インストール時に **全プロファイルの SNTP サーバを `/etc/systemd/timesyncd.conf` に静的登録**する。timesyncd が到達可能なサーバを自動選択する。

```ini
# /etc/systemd/timesyncd.conf （install.shが生成）
[Time]
NTP=ntp.factory-a.local ntp.factory-b.local ntp.factory-d.local
FallbackNTP=ntp.nict.jp time.cloudflare.com
```

### 5.7 機微情報の取り扱い

- パスワード・ウォレットパスワードは `secrets.env`（`chmod 600`、`root:root` 所有）に環境変数として記述
- `profiles.yaml` には `${VAR}` プレースホルダで参照
- ログ出力時はプロファイルから機微フィールドをスクラブ（`***` に置換）

---

## 6. データモデル

### 6.1 Oracle: `HF1RCM01`（既存）

| カラム | 型 | 値 |
|---|---|---|
| `MK_DATE` | VARCHAR2(14) | `'YYYYMMDDhhmmss'`（Asia/Tokyo） |
| `STA_NO1` | VARCHAR2 | `device.yaml` の固定値 |
| `STA_NO2` | VARCHAR2 | 〃 |
| `STA_NO3` | VARCHAR2 | 〃 |
| `T1_STATUS` | NUMBER | `1`=ENTER, `2`=EXIT |

**冪等 INSERT（MERGE文）**:

```sql
MERGE INTO HF1RCM01 t
USING (SELECT :1 AS MK_DATE, :2 AS STA_NO1, :3 AS STA_NO2, :4 AS STA_NO3, :5 AS T1_STATUS FROM dual) s
ON (t.MK_DATE = s.MK_DATE
    AND t.STA_NO1 = s.STA_NO1
    AND t.STA_NO2 = s.STA_NO2
    AND t.STA_NO3 = s.STA_NO3
    AND t.T1_STATUS = s.T1_STATUS)
WHEN NOT MATCHED THEN
  INSERT (MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS)
  VALUES (s.MK_DATE, s.STA_NO1, s.STA_NO2, s.STA_NO3, s.T1_STATUS)
```

同じ `(MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS)` 5タプルは2回目以降 INSERT されない。再送・再起動時の重複を防止する。

### 6.2 SQLite

#### detector_buf.db (`pending_events`)

```sql
CREATE TABLE pending_events (
  event_id            TEXT PRIMARY KEY,
  event_type          TEXT NOT NULL CHECK(event_type IN ('ENTER','EXIT')),
  mk_date             TEXT,                                     -- 'YYYYMMDDhhmmss' or NULL
  monotonic_ns        INTEGER NOT NULL,
  wall_synced         INTEGER NOT NULL DEFAULT 0,               -- 0|1
  score               REAL,
  status              TEXT NOT NULL CHECK(status IN ('pending','sent','acked')),
  created_at_iso      TEXT NOT NULL,
  retry_count         INTEGER NOT NULL DEFAULT 0,
  next_retry_at_iso   TEXT,
  last_publish_at_iso TEXT
);
CREATE INDEX idx_pending_events_status_retry ON pending_events(status, next_retry_at_iso);
CREATE INDEX idx_pending_events_created_at  ON pending_events(created_at_iso);

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
```

ステータス遷移: `pending` → `sent` （publish成功） → `acked` （ACK受領）。

#### bridge_buf.db (`inbox`)

```sql
CREATE TABLE inbox (
  event_id            TEXT PRIMARY KEY,
  event_type          TEXT NOT NULL CHECK(event_type IN ('ENTER','EXIT')),
  mk_date             TEXT,
  monotonic_ns        INTEGER NOT NULL,
  wall_synced         INTEGER NOT NULL,
  device_id           TEXT,
  score               REAL,
  raw_payload         TEXT NOT NULL,                            -- 受信時のJSON原文
  status              TEXT NOT NULL CHECK(status IN ('received','sent')),
  ssid_at_receive     TEXT,
  profile_at_send     TEXT,
  mk_date_committed   TEXT,                                     -- 補正後の最終mk_date
  received_at_iso     TEXT NOT NULL,
  sent_at_iso         TEXT,
  retry_count         INTEGER NOT NULL DEFAULT 0,
  next_retry_at_iso   TEXT,
  last_error          TEXT
);
CREATE INDEX idx_inbox_status_retry ON inbox(status, next_retry_at_iso);
CREATE INDEX idx_inbox_received_at  ON inbox(received_at_iso);

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
```

### 6.3 MQTTメッセージ仕様

#### `presence/event` (detector → bridge, QoS=2)

```json
{
  "event_id": "0192b6d2-7c34-7a8f-bb01-5c0bce4f9f2e",
  "event": "ENTER",
  "event_time": "20260427172345",
  "event_time_iso": "2026-04-27T17:23:45.123+09:00",
  "monotonic_ns": 12345678901234,
  "wall_clock_synced": true,
  "device_id": "rpi5-line-a-01",
  "score": 0.87,
  "schema_version": 1
}
```

`wall_clock_synced=false` のとき: `event_time` と `event_time_iso` は `null`。

#### `presence/event/ack` (bridge → detector, QoS=2)

```json
{
  "event_id": "0192b6d2-7c34-7a8f-bb01-5c0bce4f9f2e",
  "mk_date_committed": "20260427172345",
  "committed_at_iso": "2026-04-27T17:23:46.012+09:00",
  "schema_version": 1
}
```

### 6.4 Exactly-once 保証の論理

| 区間 | メカニズム |
|---|---|
| detector → mosquitto → bridge | MQTT QoS=2（PUBREC/PUBREL/PUBCOMP の 4-way handshake） |
| bridge → Oracle | MERGE 文による冪等 INSERT |
| bridge → detector | `presence/event/ack` で書き込み完了通知 |
| detector 再起動 | `detector_buf.db` から `status != 'acked'` を再 publish |
| bridge 再起動 | `inbox` から `status='received'` を再 MERGE、`status='sent'` の ACK 未送信行は ACK を再 publish |

`event_id` を全段で伝播することで、重複検知・冪等処理・トレースが可能になる。

---

## 7. エラーハンドリング・リトライ

### 7.1 エラーカテゴリ

| # | カテゴリ | 例 | 対処 |
|---|---|---|---|
| 1 | カメラオープン失敗 | `/dev/video0` がない、権限なし、占有 | 起動時5秒間隔で5回リトライ→失敗で終了→docker再起動 |
| 2 | カメラ読み取り失敗 | `cap.read()` False、USB抜け | 連続10回失敗で「カメラ異常」、現状PRESENTならEXIT強制発行 |
| 3 | MediaPipe推論失敗 | モデルロード失敗 | 起動時失敗で終了。実行時は連続3回失敗で再起動 |
| 4 | MQTT接続切断 | mosquitto再起動、ネット異常 | paho-mqtt 自動再接続（指数バックオフ、上限60秒） |
| 5 | MQTT publish失敗 | 切断中、QoSキュー満杯 | `*_buf.db` を真とし、リトライキューで再 publish |
| 6 | Oracle接続失敗 | DB停止、ネット切断、認証エラー | inbox に保留、指数バックオフ（5→15→45→135→405→600cap） |
| 7 | Oracle MERGE失敗（一時的） | デッドロック、ロック競合、ORA-12541 | リトライ対象 |
| 8 | Oracle MERGE失敗（恒久的） | ORA-942/904/1017/1031/12514 | サーキットOPEN、15分後HALF_OPEN |
| 9 | SNTP未同期 | 起動直後、NTP不到達 | detector は continue、bridge は wall_synced=0 行を保留 |
| 10 | WiFi未接続 / 未知SSID | profilesに該当なし | 検出継続、`hold` ポリシーで送信保留 |
| 11 | SQLite書き込み失敗 | ディスク満杯、破損 | FATAL→終了→docker再起動 |
| 12 | バッファ満杯 | 100,000行到達 | acked/sent から削除、なければpending/received削除しWARN |
| 13 | 設定ファイル不正 | YAML破損、必須キー欠如 | グローバル不正で起動拒否、プロファイル個別不正は当該プロファイルのみ無効化 |
| 14 | WiFiコマンド失敗 | nmcli不在、DBus到達不可 | WARN、SSIDを `unknown` として扱う |

### 7.2 リトライバックオフ

```
試行回数:  1   2    3    4    5    6+
バックオフ: 5s  15s  45s  135s 405s 600s (cap)
```

各レコードに `retry_count`, `next_retry_at_iso` を持たせ、Sender は `now >= next_retry_at_iso` の行のみ拾う。成功でカウンタリセット、失敗で次回時刻を更新する。

### 7.3 サーキットブレーカ

`PERMANENT_ORACLE_ERRORS = {942, 904, 1017, 1031, 12514}` を恒久エラーと判定。

```
状態: closed (正常)
  ↓ 恒久エラー検出
状態: open
  - 当該プロファイルへの送信を停止
  - イベントは inbox に蓄積継続
  - CRITICAL ログを1分間隔で出力
  ↓ 15分経過
状態: half_open
  - 1回だけ MERGE を試行
  - 成功 → closed に復帰
  - 失敗 → open に戻る（再度15分待機）
```

### 7.4 ヘルスチェック

各プロセスは `/tmp/<process>.healthy` を 5 秒間隔で touch する。Docker healthcheck がそのファイルの存在で `healthy/unhealthy` を判定し、`unless-stopped` ポリシーで再起動を発動する。

---

## 8. ロギング仕様

### 8.1 共通

- 形式: JSON Lines、UTF-8
- 出力先: ファイル（`/var/log/presence-logger/<process>.log`）+ stdout
- ローテーション: 10MB × 5 世代（プロセスあたり最大 60MB）
- タイムスタンプ: ISO 8601、ミリ秒・タイムゾーン付き（例: `"2026-04-27T17:23:45.123+09:00"`）
- ログレベル: 既定 `INFO`、環境変数 `LOG_LEVEL` で変更可（`DEBUG` を有効にするとフレーム毎・候補遷移・推論統計が出る。本番では使わない）

### 8.2 共通フィールド（全行）

| キー | 型 | 必須 |
|---|---|---|
| `ts` | string (ISO8601) | ✓ |
| `level` | string | ✓ |
| `logger` | string | ✓ |
| `event` | string | ✓ |
| `process` | string (`detector` / `bridge`) | ✓ |
| `pid` | int | ✓ |
| `device_id` | string | ✓ |
| `event_id` | string | イベント絡みの行 |
| `error` | object (`type`, `message`, `traceback`) | ERROR以上 |

### 8.3 主要 logger 階層

| logger名 | 役割 |
|---|---|
| `detector.camera` | カメラ open/close/read/警告 |
| `detector.inference` | 推論レイテンシ、スコア（DEBUG: フレーム毎） |
| `detector.fsm` | 状態遷移、デバウンス候補 |
| `detector.mqtt` | publish結果、ACK受信、再接続 |
| `detector.buffer` | SQLite I/O、件数 |
| `detector.stats` | 60秒毎の統計 |
| `bridge.mqtt` | subscribe/受信、ACK publish |
| `bridge.profile` | SSID変化、プロファイル解決、検証 |
| `bridge.network` | nmcli結果、SSID取得失敗 |
| `bridge.time` | NTP同期状態変化、補正基準確定 |
| `bridge.oracle` | 接続、MERGE実行、エラー、サーキット状態 |
| `bridge.buffer` | inbox件数、リング削除 |
| `bridge.stats` | 60秒毎の統計 |

### 8.4 サンプルログ行

```json
{"ts":"2026-04-27T17:23:45.121+09:00","level":"INFO","logger":"detector.fsm","process":"detector","pid":42,"device_id":"rpi5-line-a-01","event":"transition","from":"ABSENT","to":"PRESENT","candidate_duration_ms":3015,"latest_score":0.87,"event_id":"0192b6d2-...","event_type":"ENTER"}
{"ts":"2026-04-27T17:23:45.230+09:00","level":"INFO","logger":"bridge.oracle","process":"bridge","pid":11,"device_id":"rpi5-line-a-01","event":"merge","event_id":"0192b6d2-...","mk_date":"20260427172345","sta_no1":"001","sta_no2":"A","sta_no3":"01","t1_status":1,"rows_affected":1,"latency_ms":74,"profile":"factory_a_wifi"}
{"ts":"2026-04-27T17:24:45.000+09:00","level":"INFO","logger":"bridge.stats","process":"bridge","pid":11,"device_id":"rpi5-line-a-01","event":"periodic","current_ssid":"factory_a_wifi","current_profile":"factory_a_wifi","ntp_synced":true,"oracle_circuit_state":"closed","oracle_failures_last_5min":0,"oracle_p50_latency_ms":74,"oracle_p95_latency_ms":210,"events_committed_last_5min":12}
```

### 8.5 機微情報スクラブ

`bridge.profile.*` のログでは以下を絶対に出力しない:

- `password`
- `wallet_password`
- ウォレットファイル内容
- 環境変数解決後の値

設定ロード時に「機微フィールド」マークを付与し、ログ用 dump 時に `***` へ置換するヘルパーを必ず通す。

---

## 9. ディレクトリ構造・ビルド・起動

### 9.1 リポジトリ構造

```
presence-logger/
├── README.md
├── docker-compose.yml
├── .env.example
├── docs/superpowers/specs/2026-04-27-presence-logger-design.md
├── docker/
│   └── mosquitto/mosquitto.conf
├── services/
│   ├── detector/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── models/efficientdet_lite0.tflite
│   │   └── src/
│   │       ├── main.py
│   │       ├── camera.py
│   │       ├── inference.py
│   │       ├── fsm.py
│   │       ├── buffer.py
│   │       ├── mqtt_client.py
│   │       ├── retry.py
│   │       ├── time_source.py
│   │       ├── logging_setup.py
│   │       └── config.py
│   └── bridge/
│       ├── Dockerfile
│       ├── requirements.txt
│       └── src/
│           ├── main.py
│           ├── mqtt_listener.py
│           ├── ack_publisher.py
│           ├── inbox.py
│           ├── network_watcher.py
│           ├── time_watcher.py
│           ├── profile_resolver.py
│           ├── oracle_client.py
│           ├── circuit_breaker.py
│           ├── sender.py
│           ├── time_correction.py
│           ├── logging_setup.py
│           └── config.py
├── config/
│   ├── device.yaml.example
│   ├── profiles.yaml.example
│   ├── detector.yaml.example
│   └── bridge.yaml.example
├── scripts/
│   ├── install.sh
│   └── tail-logs.sh
├── systemd/
│   └── presence-logger.service
└── tests/
    ├── detector/
    ├── bridge/
    └── integration/
```

### 9.2 Docker Compose

```yaml
version: "3.8"

services:
  mosquitto:
    image: eclipse-mosquitto:2
    container_name: presence-mosquitto
    restart: unless-stopped
    networks: [presence-net]
    volumes:
      - ./docker/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro

  detector:
    build: ./services/detector
    container_name: presence-detector
    restart: unless-stopped
    depends_on: [mosquitto]
    networks: [presence-net]
    devices:
      - "/dev/video0:/dev/video0"
    volumes:
      - /etc/presence-logger/device.yaml:/etc/presence-logger/device.yaml:ro
      - /etc/presence-logger/detector.yaml:/etc/presence-logger/detector.yaml:ro
      - /etc/hostname:/etc/host_hostname:ro          # device_id 自動取得用
      - /var/lib/presence-logger:/var/lib/presence-logger
      - /var/log/presence-logger:/var/log/presence-logger
    environment:
      MQTT_HOST: mosquitto
      LOG_LEVEL: INFO
      TZ: Asia/Tokyo
    healthcheck:
      test: ["CMD", "test", "-f", "/tmp/detector.healthy"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s

  bridge:
    build: ./services/bridge
    container_name: presence-bridge
    restart: unless-stopped
    depends_on: [mosquitto]
    networks: [presence-net]
    volumes:
      - /etc/presence-logger/device.yaml:/etc/presence-logger/device.yaml:ro
      - /etc/presence-logger/profiles.yaml:/etc/presence-logger/profiles.yaml:ro
      - /etc/presence-logger/bridge.yaml:/etc/presence-logger/bridge.yaml:ro
      - /etc/presence-logger/wallets:/etc/presence-logger/wallets:ro
      - /etc/hostname:/etc/host_hostname:ro          # device_id 自動取得用
      - /var/lib/presence-logger:/var/lib/presence-logger
      - /var/log/presence-logger:/var/log/presence-logger
      - /run/dbus:/run/dbus:ro
      - /var/run/NetworkManager:/var/run/NetworkManager:ro
      - /etc/timezone:/etc/timezone:ro
      - /etc/localtime:/etc/localtime:ro
    env_file: [/etc/presence-logger/secrets.env]
    environment:
      MQTT_HOST: mosquitto
      LOG_LEVEL: INFO
      TZ: Asia/Tokyo
    healthcheck:
      test: ["CMD", "test", "-f", "/tmp/bridge.healthy"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s

networks:
  presence-net:
    driver: bridge
```

### 9.3 bridge コンテナ Dockerfile（抜粋）

Oracle Instant Client Basic Light（Linux ARM64 用）のダウンロード URL は Oracle 公式サイト
（https://www.oracle.com/database/technologies/instant-client/linux-arm-aarch64-downloads.html）から
バージョンに応じた具体パスを取得する必要がある。Dockerfile では `INSTANT_CLIENT_URL` をビルド引数で
受け取る形にし、URL 直書きを避ける。

```dockerfile
FROM python:3.11-slim-bookworm

# 例: https://download.oracle.com/otn_software/linux/instantclient/2113000/instantclient-basiclite-linux.arm64-21.13.0.0.0dbru.zip
ARG INSTANT_CLIENT_URL

RUN apt-get update && apt-get install -y --no-install-recommends \
        libaio1 unzip ca-certificates wget network-manager \
    && mkdir -p /opt/oracle \
    && wget -q -O /tmp/ic.zip "${INSTANT_CLIENT_URL}" \
    && unzip -q /tmp/ic.zip -d /opt/oracle \
    && mv /opt/oracle/instantclient_* /opt/oracle/instantclient \
    && rm /tmp/ic.zip \
    && apt-get purge -y wget unzip \
    && rm -rf /var/lib/apt/lists/*

ENV LD_LIBRARY_PATH=/opt/oracle/instantclient

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
CMD ["python", "-m", "src.main"]
```

ビルド時:
```bash
docker compose build --build-arg INSTANT_CLIENT_URL="https://download.oracle.com/.../instantclient-basiclite-linux.arm64-21.13.0.0.0dbru.zip" bridge
```

### 9.4 systemd ユニット

```ini
# /etc/systemd/system/presence-logger.service
[Unit]
Description=Presence Logger (USB camera -> Oracle via MQTT)
Requires=docker.service network-online.target NetworkManager.service
After=docker.service network-online.target NetworkManager.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/presence-logger
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
ExecReload=/usr/bin/docker compose restart
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
```

### 9.5 インストール手順

```bash
# リポジトリを /opt/presence-logger に配置
sudo bash scripts/install.sh
# /etc/presence-logger/、/var/lib/presence-logger、/var/log/presence-logger を作成
# サンプル YAML を /etc/presence-logger/ にコピー
# /etc/systemd/timesyncd.conf に全プロファイルの SNTP サーバを書き込み
# systemctl restart systemd-timesyncd

sudo systemctl daemon-reload
sudo systemctl enable --now presence-logger.service
```

### 9.6 secrets.env

```bash
# /etc/presence-logger/secrets.env  (chmod 600, owner root)
ORACLE_PASSWORD_A=...
ORACLE_PASSWORD_B=...
WALLET_PASSWORD_B=...
ORACLE_PASSWORD_D=...
```

---

## 10. テスト戦略

### 10.1 単体テスト（pytest）

#### detector

- FSM のデバウンス挙動（3秒未満の点滅は無視、3秒以上で遷移）
- SQLiteバッファ CRUD、リングバッファ動作
- 再送スケジューラ（指数バックオフ計算）
- 時刻補正（monotonic_ns → wall）
- 設定検証（必須キー、型チェック）

#### bridge

- inbox CRUD、`ON CONFLICT DO NOTHING` 挙動
- プロファイル解決（既知 SSID / 未知 SSID）
- 時刻補正の `backfill_count` 計算
- サーキットブレーカ状態遷移（closed→open→half_open→closed）
- Oracle MERGE文の組み立て（auth_mode×client_mode の4組合せ）
- 機微情報スクラブ（password が `***` に置換される）

### 10.2 統合テスト

- `docker-compose.test.yml` で mosquitto実コンテナ + detector + bridge + モックOracle を起動
- 主要シナリオ:
  1. ENTER→EXIT 正常系（DBに2行）
  2. ENTER中に bridge再起動 → ACKが2度目で届く（DBには1行のみ）
  3. SNTP未同期で起動 → 5分後同期 → 同期前のENTERイベントがDBに正しい時刻でINSERTされる
  4. WiFi切断 → イベントは inbox に蓄積 → 再接続でフラッシュ
  5. 未知SSIDに接続 → イベント保留 → 既知SSIDに戻ってフラッシュ
  6. Oracle停止 → 指数バックオフで再試行 → 復旧後INSERT成功
  7. ORA-00942（テーブル無し）→ サーキットOPEN → 15分後HALF_OPEN → 設定修正後復旧
  8. デバウンス3秒未満の点滅 → DBに何も書かれない
  9. カメラ抜け → EXIT発行 → DBに正しいEXIT行が残る
  10. detector_buf が100,000件で満杯 → リングバッファ動作

### 10.3 手動受け入れテスト

実機 Pi 5 + 実 USB カメラ + 実 Oracle で以下を確認:

- 起動 → 人が立つ → 3秒後 ENTER 行が DB に出現
- 人が消える → 3秒後 EXIT 行が DB に出現
- WiFi を切る → 復旧後にイベントがフラッシュされる
- ログファイルが10MBで自動ローテーションされる

---

## 11. K3s 移行時の差分（参考情報）

将来 K3s 上で運用する場合、以下の差分のみで Pod 化できる。アプリコード・設定ファイル形式・MQTT トピック・SQLite スキーマは全て無変更。

### 11.1 mosquitto

`Deployment` + `ClusterIP Service`（`mosquitto.presence.svc.cluster.local:1883`）として配置。

### 11.2 detector / bridge

DaemonSet 化。各ノードに 1 Pod ずつ配置（ノード固有のカメラ・WiFi を扱うため）。

```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata: { name: presence-bridge, namespace: presence }
spec:
  selector: { matchLabels: { app: presence-bridge } }
  template:
    metadata: { labels: { app: presence-bridge } }
    spec:
      containers:
      - name: bridge
        image: registry.local/presence-bridge:1.0
        env:
        - { name: MQTT_HOST, value: "mosquitto.presence.svc.cluster.local" }
        envFrom:
        - secretRef: { name: presence-secrets }
        volumeMounts:
        - { name: dbus, mountPath: /run/dbus, readOnly: true }
        - { name: nm, mountPath: /var/run/NetworkManager, readOnly: true }
        - { name: device, mountPath: /etc/presence-logger/device.yaml, subPath: device.yaml, readOnly: true }
        - { name: profiles, mountPath: /etc/presence-logger/profiles.yaml, subPath: profiles.yaml, readOnly: true }
        - { name: wallets, mountPath: /etc/presence-logger/wallets, readOnly: true }
        - { name: lib, mountPath: /var/lib/presence-logger }
        - { name: log, mountPath: /var/log/presence-logger }
      volumes:
      - { name: dbus, hostPath: { path: /run/dbus, type: Directory } }
      - { name: nm, hostPath: { path: /var/run/NetworkManager, type: Directory } }
      - { name: device, configMap: { name: presence-device } }
      - { name: profiles, configMap: { name: presence-profiles } }
      - { name: wallets, secret: { secretName: presence-wallets } }
      - { name: lib, hostPath: { path: /var/lib/presence-logger, type: DirectoryOrCreate } }
      - { name: log, hostPath: { path: /var/log/presence-logger, type: DirectoryOrCreate } }
```

### 11.3 主な変更点

| 項目 | Compose | K3s |
|---|---|---|
| MQTT_HOST | `mosquitto` | `mosquitto.presence.svc.cluster.local` |
| 設定ファイル | hostPath bind mount | ConfigMap / Secret |
| ノード固有設定 | デバイスごとの YAML | ノードラベル + ConfigMap で配信 |
| 起動制御 | systemd | K3s（`kubectl apply`） |

---

## 12. 用語

| 用語 | 定義 |
|---|---|
| ENTER | 「人がいない」状態から「人がいる」状態へ確定遷移したイベント |
| EXIT | 「人がいる」状態から「人がいない」状態へ確定遷移したイベント |
| デバウンス | 状態遷移を確定する前の継続観測時間（既定 3 秒） |
| プロファイル | WiFi SSID をキーとした SNTP/Oracle 接続情報の組 |
| サーキットブレーカ | 恒久エラー検出時に該当プロファイルへの送信を一時停止する機構 |
| Exactly-once | 1 つの ENTER/EXIT イベントが Oracle に**ちょうど 1 回**記録されることの保証 |
| Thin / Thick mode | python-oracledb の動作モード。Thin は Pure Python、Thick は Oracle Instant Client 経由 |
