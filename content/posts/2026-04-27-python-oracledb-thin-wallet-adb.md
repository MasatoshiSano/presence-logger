---
title: "python-oracledb Thin で Oracle Autonomous DB に Wallet 接続する完全手順 — DPY-6005/6001 の読み解きまで"
emoji: "🔐"
type: "tech"
topics: ["Oracle", "Python", "oracledb", "Cloud", "Wallet"]
published: true
category: "HowTo"
date: "2026-04-27"
description: "python-oracledb の Thin モード（Instant Client 不要）で Oracle Cloud Autonomous Database に Wallet 経由で接続する手順。tnsnames.ora のエイリアス選択、wallet_password の扱い、DPY-6005/DPY-6001 エラーの見分け方まで。"
coverImage: "/images/posts/python-oracledb-thin-wallet-adb-cover.jpg"
---

## 概要

Oracle Autonomous Database (ADB) に Python から接続するときの定番だった `cx_Oracle` + Instant Client + Wallet の組み合わせは、`python-oracledb` の **Thin モード**（Pure Python 実装）登場で大幅に簡素化された。Instant Client（数十〜数百MB のネイティブライブラリ）を一切インストールせず、Wallet zip だけあれば pip 一発で繋がる。

この記事ではその最短手順と、繋がらないときに出る代表的なエラーコード（`DPY-6005`、`DPY-6001`）の意味を整理する。

## こんな人向け

- Oracle Cloud Autonomous Database に Python から接続したい
- `cx_Oracle` ではなく後継の `python-oracledb` を使いたい
- Instant Client を入れたくない（ARM64 Raspberry Pi、Docker slim image、CI 環境など）
- `DPY-6005: cannot connect to database` のエラーで詰まっている
- `DPY-6001: Service ... is not registered with the listener` の原因がわからない

## 前提条件

- Python 3.8 以降
- OCI コンソールから ADB の **DB Connection wallet** をダウンロード済み（zip ファイル）
- Wallet 内 `tnsnames.ora` に記載されている DSN エイリアス（例: `mydb_low`, `mydb_high`）と接続用ユーザー（例: `ADMIN`）のパスワードを把握している

## 手順

### 1. python-oracledb をインストール

```bash
pip install oracledb
# 例: oracledb-2.5.1
```

`oracledb` は **デフォルトが Thin モード**。Instant Client や `LD_LIBRARY_PATH` 設定は一切不要。

### 2. Wallet zip を展開

任意のディレクトリに展開する。中身は概ね以下：

```
/home/pi/oracle_wallet/
├── tnsnames.ora       # ← 接続エイリアス定義（DSN）
├── sqlnet.ora         # ← TLS 設定。WALLET_LOCATION を含む
├── ewallet.p12        # ← PKCS12 形式の鍵（パスワード保護）
├── ewallet.pem        # ← PEM 形式の鍵（パスワードなしで使える）
├── cwallet.sso        # ← Single Sign-On 形式
├── keystore.jks       # ← Java用（無視してOK）
├── truststore.jks     # ← Java用（無視してOK）
├── ojdbc.properties   # ← JDBC用（無視してOK）
└── README
```

権限を絞っておく（Wallet には秘密鍵が含まれる）：

```bash
chmod 700 /home/pi/oracle_wallet
chmod 600 /home/pi/oracle_wallet/*
```

### 3. tnsnames.ora から DSN エイリアスを選ぶ

`tnsnames.ora` を覗くと、Autonomous DB は通常 5つのワークロードエイリアスを定義している：

```
mydb_high     = (description= ... (service_name=...mydb_high.adb.oraclecloud.com))
mydb_medium   = (description= ... (service_name=...mydb_medium.adb.oraclecloud.com))
mydb_low      = (description= ... (service_name=...mydb_low.adb.oraclecloud.com))
mydb_tp       = (description= ... (service_name=...mydb_tp.adb.oraclecloud.com))
mydb_tpurgent = (description= ... (service_name=...mydb_tpurgent.adb.oraclecloud.com))
```

| エイリアス | 想定ワークロード | 並列度 |
|---|---|---|
| `_high` | 重い分析クエリ | 高い（DB側で並列化） |
| `_medium` | 通常のクエリ | 中 |
| `_low` | 軽量な OLTP/INSERT | 低（単一プロセス的） |
| `_tp` | 高頻度トランザクション | 中（Transaction Processing 最適化） |
| `_tpurgent` | 緊急性の高いトランザクション | 高 |

**INSERT / MERGE が中心の常駐プロセスなら `_low` か `_tp`** がコスト効率良い。

### 4. 接続コード

```python
import oracledb

conn = oracledb.connect(
    user="ADMIN",
    password="your-db-password",
    dsn="mydb_low",                              # tnsnames.ora の alias
    config_dir="/home/pi/oracle_wallet",         # tnsnames.ora の場所
    wallet_location="/home/pi/oracle_wallet",    # wallet ファイル群の場所
    wallet_password="your-wallet-password",      # PKCS12 を使う場合のみ
)

with conn.cursor() as cur:
    cur.execute("SELECT 1 FROM dual")
    print(cur.fetchone())   # -> (1,)

conn.close()
```

ポイント：

- **`config_dir` と `wallet_location` は同じディレクトリ**でよい（`tnsnames.ora` も `ewallet.*` も同じ場所に展開しているため）
- **`wallet_password` は PKCS12 (`ewallet.p12`) を使うときだけ必要**。PEM (`ewallet.pem`) ベースなら省略できる（ただし PEM は秘密鍵が平文なのでファイル権限を絞る）
- **DSN は `host:port/service_name` ではなく tnsnames のエイリアス名**を渡す。Thin モードはこれを内部で解決する

### 5. プロファイル設定として持つ場合

YAML 設定ファイルから読み込む構造：

```yaml
oracle:
  client_mode: "thin"          # thick(=Instant Client必要) との分岐用
  auth_mode: "wallet"          # basic(=user/password+host) との分岐用
  dsn: "mydb_low"
  user: "ADMIN"
  password: "${ORACLE_PASSWORD}"           # 環境変数から展開
  wallet_dir: "/etc/myapp/wallets/prod"
  wallet_password: "${WALLET_PASSWORD}"
  table_name: "MY_TABLE"
```

接続コード側：

```python
def open_connection(cfg: dict) -> oracledb.Connection:
    user = cfg["user"]
    password = cfg["password"]

    if cfg["auth_mode"] == "basic":
        dsn = oracledb.makedsn(cfg["host"], cfg["port"], service_name=cfg["service_name"])
        return oracledb.connect(user=user, password=password, dsn=dsn)

    if cfg["auth_mode"] == "wallet":
        kwargs = dict(
            user=user, password=password, dsn=cfg["dsn"],
            config_dir=cfg["wallet_dir"], wallet_location=cfg["wallet_dir"],
        )
        if cfg.get("wallet_password"):
            kwargs["wallet_password"] = cfg["wallet_password"]
        return oracledb.connect(**kwargs)
```

## ポイント・注意点

### エラーコード対応表

`oracledb` 特有のエラーコード（`DPY-`）と従来の `ORA-` の対応関係を覚えておくとデバッグが速い：

| エラーコード | 意味 | チェックポイント |
|---|---|---|
| **DPY-6005** | TCP 接続不可（タイムアウト・ホスト到達不能） | ネットワーク・FW・VPN・port forwarding |
| **DPY-6001** (Similar to ORA-12514) | リスナーには到達したがサービスが未登録 | DB 自体が停止中、サービス名のtypo、ADB ならインスタンス停止 |
| **DPY-4011** | サーバ側から切断 | DB のセッション制限、認証失敗を含むことあり |
| **ORA-01017** | invalid username/password | パスワード、user 名、Wallet password 確認 |
| **ORA-12541** | TNS:no listener | リスナー（≒ DB のフロント）自体が停止 |
| **ORA-12514** | listener does not currently know of service | DPY-6001 と同義（古い形式） |

特に **DPY-6005 と DPY-6001 の見分け** は重要：

- DPY-6005 → **TCP すら通らない**。NW 起因
- DPY-6001 → **TCP は通り TLS まで通った**。サービス起動状況の問題

ADB を OCI コンソールで stop したまま接続しようとすると **DPY-6001** が出る（リスナーは生きているが、対象 DB instance がない）。

### 接続できないときの体系的な切り分け

```
1. ping <host>                    # ICMP（OCI は応答しないことも）
2. nc -zv <host> <port>           # TCP open check
3. openssl s_client -connect ...  # TLS handshake
4. oracledb.connect(...)          # 認証 + サービス
```

**3 まで成功して 4 で DPY-6001** なら、99% は **DB instance が停止中**。OCI コンソールで Start を押す。

### Thin vs Thick の選択

| | Thin | Thick |
|---|---|---|
| Instant Client | 不要 | 必要（数十MB） |
| インストール | `pip install oracledb` のみ | + `apt install libaio1` + Instant Client unzip |
| ARM64 サポート | OK | Linux ARM64 版 Instant Client は提供あり |
| ADB Wallet | OK | OK |
| Old DB (≤11g) | 一部制約あり | フル対応 |
| Advanced Queuing 等 | 未サポート | サポート |

**新規プロジェクトは Thin で始め、必要な機能が出てきたら Thick に切替** が良い。`oracledb.init_oracle_client(lib_dir=...)` を **プロセス起動時に一度だけ呼ぶ** と Thick に切り替わる（プロセス全体で有効、混在不可）。

### Wallet 内のファイルの読み解き

- `cwallet.sso` がある＝ SSO（パスワード不要）で開ける状態。`wallet_password` 省略可
- `ewallet.p12` のみ＝ PKCS12 形式、`wallet_password` 必須
- `ewallet.pem` がある＝ PEM 形式、平文の秘密鍵。`wallet_password` 不要だがファイル権限注意

OCI からダウンロードしたままの zip だと `cwallet.sso` と `ewallet.p12` の両方が入っていることが多い。SSO が優先で読まれる。

## まとめ

- `python-oracledb` の Thin モードは Instant Client 不要、`pip install` 一発で動く
- Wallet 接続は `config_dir` + `wallet_location` を **同じディレクトリ**にし、必要なら `wallet_password` を追加するだけ
- `DPY-6005` は TCP 不通、`DPY-6001` はサービス未登録（DB 停止中の可能性大）
- ワークロードに応じて `_low`/`_medium`/`_high`/`_tp`/`_tpurgent` から DSN を選ぶ
- Thin で始めて、機能要件が出たら Thick に移行できる

## バイブコーディングで実装する

この記事の内容を AI コーディングアシスタントに実装させるためのプロンプト：

> Python から Oracle Cloud Autonomous Database (ADB) に接続したい。`cx_Oracle` ではなく後継の `python-oracledb` の **Thin モード**を使うこと（Instant Client は入れない）。
>
> Wallet zip は `/home/pi/oracle_wallet/` に展開済み（`tnsnames.ora`、`ewallet.p12`、`cwallet.sso` など）。
>
> 接続コードは `oracledb.connect(user, password, dsn="mydb_low", config_dir="/home/pi/oracle_wallet", wallet_location="/home/pi/oracle_wallet", wallet_password=...)` のシグネチャにする。`config_dir` と `wallet_location` は同じディレクトリでよい。
>
> エラーが出たら：`DPY-6005` は TCP 不通（NW 問題）、`DPY-6001` はサービス未登録（DB 停止中の可能性）として切り分けること。`DPY-6001` が出たら OCI コンソールで ADB が Running か確認するよう案内すること。
>
> ワークロード別 DSN は `_low`（軽量 INSERT）/`_medium`（通常）/`_high`（並列分析）から選ぶ。INSERT/MERGE 中心の常駐プロセスなら `_low` か `_tp`。

### AIに指示するときのポイント

- AI は古い学習データから `cx_Oracle` を使うコードを出しがち。**`python-oracledb` を使うことと Thin モードを明示**する
- Wallet 接続では `config_dir` を忘れるサンプルが多い。`tnsnames.ora` の場所として **必ず `config_dir` を指定するよう明示**する
- AI は `oracledb.makedsn()` で host/port を組み立てたがる。Wallet 接続では tnsnames のエイリアスを `dsn=` に直接渡すと**強調**する
- エラーコード `DPY-` 系は AI の学習データが薄い。**この記事のエラー対応表をプロンプトに含める**と切り分けが速くなる
