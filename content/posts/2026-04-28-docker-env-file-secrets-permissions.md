---
title: "Docker `env_file` のパーミッションで詰まったら — chmod 600 root が読めない理由と chgrp docker + 640 解決"
emoji: "🔐"
type: "tech"
topics: ["Docker", "Compose", "Secrets", "Linux", "Permissions"]
published: true
category: "DevOps"
date: "2026-04-28"
description: "`docker-compose.yml` の `env_file: secrets.env` でコンテナに環境変数を渡すとき、ファイルを `chmod 600 root:root` にすると `open ...: permission denied` で起動失敗する。読むのは Docker daemon ではなく `docker compose` CLI（呼び出しユーザー）だから。`chgrp docker` + `chmod 640` で安全に解決する。"
coverImage: "/images/posts/docker-env-file-secrets-permissions-cover.jpg"
---

## 概要

`docker-compose.yml` で **env_file** を使ってコンテナに環境変数を渡すとき、セキュリティ意識の高いユーザーほどファイルを `chmod 600 root:root` にしたがる。これをやると `docker compose up` が permission denied で起動失敗する。

```
open /etc/myapp/secrets.env: permission denied
```

「ファイルを読むのは誰か」を理解すれば、適切な権限設定（`chgrp docker` + `chmod 640`）が自然に出てくる。

## こんな人向け

- Docker Compose で `env_file:` を使ってシークレットを渡している
- 平文ファイルを安全に運用したい（暗号化や Vault は大袈裟）
- `chmod 600 root:root` にしたら起動できなくなって困っている
- 「Docker daemon は root で動くから読めるはず」と思っていたら違った
- production 環境で secrets ファイルの権限を最小化したい

## 前提条件

- Docker Compose v2
- env_file 形式：`KEY=VALUE` 形式の plain text
- 呼び出しユーザー（pi、ec2-user、ubuntu 等）が `docker` グループ所属

## 起きる現象

```yaml
# docker-compose.yml
services:
  bridge:
    image: myapp/bridge:latest
    env_file:
      - /etc/myapp/secrets.env
```

```bash
# secrets.env を root 専用に
sudo touch /etc/myapp/secrets.env
sudo chown root:root /etc/myapp/secrets.env
sudo chmod 600 /etc/myapp/secrets.env

# 起動
docker compose up -d
# → open /etc/myapp/secrets.env: permission denied
```

ところが daemon そのものは root で動いている。`sudo cat /etc/myapp/secrets.env` は当然読める。なのに compose が permission denied。

## なぜ permission denied になるのか

理解しにくいのは「**env_file を読むのは Docker daemon ではなく `docker compose` CLI**」というポイント。

```
+------------------------------------+
| User: pi (in docker group)         |
| $ docker compose up -d             |
|         │                          |
|         ▼                          |
|   docker compose CLI               |
|   (process owner = pi)             |
|         │                          |
|         │ ① env_file を読む       |
|         │   ← この時点で pi の権限 |
|         ▼                          |
|   /etc/myapp/secrets.env           |
|   chmod 600 root:root              |
|   ← pi 読めない、ここで失敗       |
+------------------------------------+
```

env_file の中身は **CLI が読み取り → daemon に環境変数を JSON で送信 → daemon がコンテナ起動時に注入**、という流れ。CLI は通常ユーザーで動くので、ファイルがそのユーザーで読めないとアウト。

`docker compose` を `sudo` で実行すれば動くが、それは別の悪手（`docker.sock` の所有権・グループ管理を全部 root 経由にする運用に倒れる）。

## 解決策：`chgrp docker` + `chmod 640`

ファイルのグループを `docker` にして、グループに読み取り権限を与える：

```bash
sudo chown root:docker /etc/myapp/secrets.env
sudo chmod 640 /etc/myapp/secrets.env
ls -l /etc/myapp/secrets.env
# -rw-r----- 1 root docker 66 Apr 28 10:20 /etc/myapp/secrets.env
```

これで：
- **所有者（root）**: 読み書き
- **グループ（docker）**: 読み（`docker compose` を実行できる人 = docker グループ所属者は皆読める）
- **その他**: 一切アクセス不可

`pi` ユーザーは docker グループ所属だから読める。ssh で入れる別ユーザーで docker グループに入れていない人は読めない。**「docker を実行できる人＝secrets を読める人」**という権限境界が成立。

```bash
docker compose up -d
# → 成功
```

## なぜこの設計が良いか

### 既存の権限境界に寄り添う

「**docker daemon に到達できる人**」は既に `docker` グループメンバーで管理されている。そのグループに secrets 読み取り権限を揃えるのは自然。新たな ACL を作らない。

### `sudo docker compose` を避けられる

「daemon が root だから sudo すれば動くだろう」で `sudo docker compose up` する運用は、すぐに：

- `~/.docker/config.json` が `/root/.docker/config.json` を見に行って混乱
- compose で生成されるネットワーク・ボリュームの所有者が root になり、後で消せない
- CI / 自動化で sudoers 設定が必要になる

など連鎖的に問題を起こす。**通常ユーザーで完結**するのが Docker の本来の運用モデル。

### `--secrets`（Compose v3.1+）との比較

Compose には Swarm 由来の `secrets:` フィールドがあるが、Compose 単体での運用では「ファイルを `/run/secrets/` にマウント」という挙動になり、コードを大きく書き換える必要がある。**env_file は単純で導入コストが低い**。

| 機能 | env_file | secrets |
|---|---|---|
| シンタックス | `env_file: [path]` | `secrets:` セクション |
| 渡し方 | 環境変数 | tmpfs マウント (`/run/secrets/<name>`) |
| アプリ側変更 | 不要 | コード書き換え必要 |
| ローテーション | コンテナ再起動 | 同左 |
| 暗号化 | なし（FS 権限のみ） | なし（同左） |
| Swarm 連携 | なし | あり |

少人数運用で「secrets は env として読めればいい」レベルなら env_file + 適切な権限が現実解。

## ポイント・注意点

### CI / GitHub Actions での扱い

CI 環境では `secrets.env` をリポジトリに含めず、GitHub Actions の Secrets から動的生成する：

```yaml
- name: Write secrets.env
  run: |
    sudo install -d -m 0750 -o root -g docker /etc/myapp
    sudo tee /etc/myapp/secrets.env > /dev/null <<EOF
    ORACLE_PASSWORD=${{ secrets.ORACLE_PASSWORD }}
    WALLET_PASSWORD=${{ secrets.WALLET_PASSWORD }}
    EOF
    sudo chown root:docker /etc/myapp/secrets.env
    sudo chmod 640 /etc/myapp/secrets.env
```

### `chgrp docker` を忘れた場合の症状

`chmod 640` だけして `chgrp` を root のままにすると、グループも root のまま → docker グループのユーザーは other 扱いで permission denied。**chown も忘れずに**。

### マルチユーザー環境

複数のユーザーが `docker compose up` を打つ環境では、全員 `docker` グループに入っていれば全員 secrets を読める。**役割で分けたい場合は env_file を分ける**：

```yaml
services:
  bridge:
    env_file:
      - /etc/myapp/common.env       # chmod 644（公開情報）
      - /etc/myapp/secrets.env      # chmod 640 docker グループのみ
```

### コンテナ内での扱い

env_file の中身はコンテナ内では普通の環境変数として見える：

```bash
docker compose exec bridge env | grep ORACLE_PASSWORD
# ORACLE_PASSWORD=...
```

これは **コンテナ内 root 以外でも読める**。アプリプロセスが non-root で動くなら問題ないが、root 同居で潜む脆弱性がある場合は `secrets:` の方が良い。

### `.env` との混同に注意

Compose には 2 種類の env ファイルがある：

| | `env_file:` | `.env`（プロジェクトルート） |
|---|---|---|
| 用途 | コンテナに環境変数を注入 | compose YAML 内の `${VAR}` 展開用 |
| 注入先 | コンテナ内 environment | compose 自身 |
| ファイル名 | 任意 | `.env` 固定 |
| 権限管理 | この記事のテーマ | 同様だが用途が違う |

`.env` は `${INSTANT_CLIENT_URL}` みたいなビルド引数に使うことが多い。**secrets はあくまで `env_file:` 経由**で渡す。

## まとめ

- `env_file:` を読むのは **Docker daemon ではなく `docker compose` CLI（呼び出しユーザー）**
- `chmod 600 root:root` だと CLI 実行ユーザーが読めず permission denied
- 推奨は `chown root:docker` + `chmod 640`：docker グループメンバーだけが読める
- 「docker を実行できる人＝secrets を読める人」という権限境界が既存設計に乗る
- `sudo docker compose` で力技解決は避ける（連鎖的に問題発生）
- 高度な要件（暗号化、ローテーション）が必要なら Vault や SOPS を検討

## バイブコーディングで実装する

この記事の内容を AI コーディングアシスタントに実装させるためのプロンプト：

> Docker Compose で `env_file:` を使って secrets を注入するアプリの権限設計：
>
> ファイル配置：
> - `/etc/myapp/secrets.env`：`KEY=VALUE` 形式の平文
> - 所有者: `root:docker`（root が書き、docker グループが読む）
> - 権限: `0640`
>
> ```bash
> sudo install -m 0640 -o root -g docker /dev/null /etc/myapp/secrets.env
> sudo tee /etc/myapp/secrets.env > /dev/null <<'EOF'
> ORACLE_PASSWORD=...
> WALLET_PASSWORD=...
> EOF
> ```
>
> docker-compose.yml：
> ```yaml
> services:
>   app:
>     env_file:
>       - /etc/myapp/secrets.env
> ```
>
> 注意：
> - `chmod 600 root:root` にすると `docker compose` CLI が読めず permission denied になる
> - `sudo docker compose up` で回避するのは禁止（root 所有のネットワーク・ボリュームができる悪手）
> - .env（プロジェクトルート、compose 自身用）と env_file（コンテナ用）は別物。混同しない

### AIに指示するときのポイント

- AI は secrets ファイルの権限を `chmod 600 root:root` で書きがち。**「読むのは CLI で実行ユーザーが必要」**を明示する
- `chgrp docker` を忘れさせない。AI は chmod だけして group は root のまま放置することが多い
- `.env` と `env_file` を混同するパターンが多い。**用途を明示**する
- AI が「sudo docker compose で動くからこれでいい」と提案したら、**なぜダメかを記事のような副作用で押し戻す**
