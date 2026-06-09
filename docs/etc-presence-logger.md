# `/etc/presence-logger/` 設定ファイル・リファレンス

このディレクトリは presence-logger の**実行時設定**を置く唯一の場所です。
リポジトリには実ファイルを含めません（`config/*.yaml` は `.gitignore` で除外）。
各ファイルのひな型は `config/*.example` にあり、`scripts/install.sh` が
このディレクトリへ配置します。

> **秘密情報の方針**: パスワード・PSK は `secrets.env`（`600 root`）だけに平文で持ち、
> 他の `*.yaml` には `${VAR}` の参照しか書きません。だから `*.yaml` は安全に
> 共有・バックアップでき、Git の `*.example` にも載せられます。

---

## 一覧（どのファイルが何を管理するか）

| ファイル | 管理する設定 | 形式 | 権限 / 所有 | 秘密 | ひな型 |
|---|---|---|---|---|---|
| `device.yaml` | この端末の ID とステーション番号 (sta_no1/2/3) | YAML | `644 root:root` | × | `config/device.yaml.example` |
| `profiles.yaml` | **SSID ごと**の SNTP / Oracle 接続先 / 認証方式 / WiFi 設定 / 未知SSID時の挙動 | YAML | `644 root:root` | ×（`${VAR}` 参照のみ） | `config/profiles.yaml.example` |
| `bridge.yaml` | bridge サービスの動作（MQTT・Oracle・JDBCサイドカー・リトライ・サーキットブレーカ・バッファ・ログ） | YAML | `644 root:root` | × | `config/bridge.yaml.example` |
| `detector.yaml` | detector サービスの動作（カメラ・推論モデル・デバウンス・MQTT・バッファ） | YAML | `644 root:root` | × | `config/detector.yaml.example` |
| `secrets.env` | **全パスワード / WiFi PSK** | `KEY=VALUE`（env） | `600 root:docker` | ◎ | `config/secrets.env.example` |
| `wallets/` | Oracle Wallet 一式（`auth_mode: wallet` 利用時のみ） | ディレクトリ | `700 root:docker` | ◎ | （Wallet zip を展開） |
| `.backup-<timestamp>/` | インストーラが上書き前に取る自動バックアップ | ディレクトリ | `root:root` | － | （自動生成） |

`device_id` が `null` のとき、端末IDは `/etc/host_hostname`（コンテナへ
マウントされたホスト名）から自動取得されます。

---

## 各ファイルの詳細

### `device.yaml` — 端末の素性
```yaml
device_id: null            # null = /etc/host_hostname から自動取得
station:                   # この端末が既定で報告するステーション番号
  sta_no1: "001"
  sta_no2: "A"
  sta_no3: "01"
```
- `station` は **既定値**。`profiles.yaml` のプロファイル側に `station` があれば
  そちらが優先される（同じ Pi を工場ごとに別番号で運用できる）。

### `profiles.yaml` — SSID 駆動の接続切替（最重要）
現在の WiFi SSID をキーに、**SNTP サーバと Oracle 接続先を実行時に選ぶ**。
プロファイル名 = 実際の SSID。
```yaml
profiles:
  HIME-H-REAP:                       # キー = 接続する SSID 名
    description: "..."
    wifi:                            # 任意: 運用スクリプト用（bridge は読まない）
      psk: "${WIFI_PSK_HIMEREAP}"    # ← secrets.env から展開
      hidden: true
      static_ipv4:
        address: "172.22.13.17/24"
        gateway: "172.22.13.1"
        dns: ["10.166.1.70", "10.166.1.17"]
    station:                         # 任意: device.yaml の上書き
      sta_no1: "996"; sta_no2: "995"; sta_no3: "994"
    sntp:
      servers: ["133.141.247.101"]   # この SSID のときだけ到達できる工場内NTP
    oracle:
      client_mode: "jdbc"            # thin | thick | jdbc
      auth_mode:   "basic"           # basic | wallet
      host: "10.166.5.93"; port: 1521; service_name: "HHC001"
      user: "ZHH001"
      password: "${ORACLE_PASSWORD_HHC}"   # ← secrets.env から展開
      table_name: "HF1RCM01"
      upcmpflg: 1                    # 任意: INSERT 時の UPCMPFLG 値（整数）

unknown_ssid_policy: "hold"          # 既知SSID以外のときの挙動（後述）
```

**`oracle.client_mode` の選び方:**
| 値 | 用途 | 認証 |
|---|---|---|
| `thin` | 現行検証子のOracle・オンプレ直結 / Autonomous DB | `basic` または `wallet` |
| `jdbc` | **10G旧検証子(0x939)** のDB。`ojdbc11.jar` サイドカー経由（Pi5の16KBページでThick不可のため） | `basic` のみ |
| `thick` | Instant Client が必要なレガシー（イメージに焼き込み） | `basic` |

**`unknown_ssid_policy`（既知SSIDに一致しないとき）:**
| 値 | 挙動 |
|---|---|
| `hold` | inbox に貯めて Oracle 書込はしない（安全側・ひな型の既定） |
| `drop` | 受信時に破棄。後で工場WiFiに繋いでも「場外の検知」が流れ込まない |
| `use_last` | 直近の既知プロファイルを流用 |

> 現地の実運用ファイルでは、場外キャプチャの混入を防ぐため `drop` を採用している
> 拠点があります。ひな型(`hold`)とは異なる場合があるので、拠点ごとに確認すること。

### `bridge.yaml` — bridge サービスの動作
| セクション | 管理内容 |
|---|---|
| `mqtt` | detector とつなぐ MQTT（host/port/qos/topic/client_id） |
| `oracle` | 接続/クエリのタイムアウト、プールサイズ、Instant Client パス |
| `oracle_jdbc` | **`client_mode: jdbc` のとき**サイドカーの URL とタイムアウト（既定 `http://oracle-jdbc:8086`） |
| `network_watcher` | SSID 取得コマンドとポーリング間隔 |
| `time_watcher` | NTP 同期状態の確認コマンドと間隔 |
| `retry` | 指数バックオフ（初期/最大遅延・倍率） |
| `circuit_breaker` | 永続エラー扱いの ORA コード一覧、半開までの秒数 |
| `buffer` | ローカルバッファ DB のパスと最大行数（通信断時のバックフィル用） |
| `logging` | ログレベル、バッファ統計の出力間隔 |

### `detector.yaml` — detector サービスの動作
| セクション | 管理内容 |
|---|---|
| `camera` | デバイス（`/dev/video0`）・解像度・ウォームアップフレーム数 |
| `inference` | TFLite モデルパス・目標FPS・スコア閾値・検出カテゴリ（`person`） |
| `debounce` | ENTER/EXIT 確定までの秒数（チャタリング防止） |
| `mqtt` | bridge へ送る MQTT 設定 |
| `retry` / `buffer` | bridge と同形（detector 側の独立バッファ） |

### `secrets.env` — 全パスワード（唯一の秘密）
```dotenv
# docker-compose の env_file で読み込まれ bridge コンテナへ注入される
ORACLE_PASSWORD_HHC=...      # HIME-H-REAP の Oracle(HHC001)
ORACLE_PASSWORD_A=...        # factory_a
ORACLE_PASSWORD_B=...        # factory_b
WALLET_PASSWORD_B=...        # factory_b の Wallet
ORACLE_PASSWORD_D=...        # factory_legacy
WIFI_PSK_HIMEREAP=...        # HIME-H-REAP の WiFi PSK（接続スクリプトが root で読む）
```
**2系統の読まれ方:**
- **Oracle パスワード**: `docker-compose.yml` の `env_file: [/etc/presence-logger/secrets.env]`
  により bridge コンテナの環境変数になり、`profiles.yaml` の `${ORACLE_PASSWORD_*}` を展開。
- **WiFi PSK**: コンテナではなく**接続スクリプト**（[`desktop/presence-tools/connect-hime-h-reap.sh`](../desktop/presence-tools/connect-hime-h-reap.sh)）が
  実行時に root で読み、`${WIFI_PSK_HIMEREAP}` を展開して `nmcli` に直接渡す（画面・履歴に出さない）。
  現地オペレーター用のデスクトップツール一式は [`desktop/`](../desktop/) を参照。

### `wallets/` — Oracle Wallet（`auth_mode: wallet` のときのみ）
- Wallet zip を `wallets/<profile>/` に展開（例 `wallets/factory_b/`）。
- bridge コンテナへ `:ro`（読み取り専用）でマウントされる。
- プロファイル側で `wallet_dir` と `${WALLET_PASSWORD_*}` を指定する。

---

## 配置・権限の復旧手順
新しい Pi へ移行する／権限が壊れたときは:
```bash
# ひな型から配置（インストーラが既存を .backup-* に退避してからコピー）
sudo bash scripts/install.sh

# 秘密ファイルの権限を是正
sudo chown root:docker /etc/presence-logger/secrets.env
sudo chmod 600        /etc/presence-logger/secrets.env
sudo chmod 700        /etc/presence-logger/wallets
sudo chgrp -R docker  /etc/presence-logger/wallets
```
編集後は `docker compose up -d --force-recreate bridge` で再読込（env は再生成時に反映）。
