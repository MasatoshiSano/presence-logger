---
title: "Docker daemon が docker.io を引けない問題 — daemon.json の dns 設定では直らない理由と /etc/resolv.conf 対処"
emoji: "🌐"
type: "tech"
topics: ["Docker", "DNS", "Debian", "Raspberry Pi", "Networking"]
published: true
category: "Debugging"
date: "2026-04-28"
description: "Debian Trixie + Pi 5 で `docker compose build` が `lookup registry-1.docker.io on 192.168.32.1:53: server misbehaving` で失敗。daemon.json に dns を書いても直らない。原因はホスト DNS が AAAA だけ返して A を返さない壊れ方と、daemon.json の dns 設定がコンテナ用であって BuildKit/dockerd 自身の lookup には効かないこと。"
coverImage: "/images/posts/docker-dns-trixie-resolv-conf-cover.jpg"
---

## やりたかったこと

Debian Trixie 64bit (Raspberry Pi 5) で Docker をインストール → `docker compose build` で公式 Python image を pull したい。クリーンインストールしたばかりの環境。

```bash
docker compose build
```

## こんな人向け

- Docker の build で `registry-1.docker.io` への lookup が `server misbehaving` エラーで失敗する
- `/etc/docker/daemon.json` に `dns` を書いたが直らない
- 家庭用ルーターやプロバイダ DNS を使っていて、CDN 系ドメインの解決がたまに壊れる
- ホストでは `getent hosts` が通るのに Docker からは引けない

## ❌ 最初の指示 / アプローチ

ビルドが下記エラーで止まる：

```
#3 [internal] load metadata for docker.io/library/python:3.11-slim-bookworm
#3 ERROR: failed to do request: Head "https://registry-1.docker.io/v2/library/python/manifests/3.11-slim-bookworm":
   dial tcp: lookup registry-1.docker.io on 192.168.32.1:53: server misbehaving
```

「DNS 問題か」と判断し、Docker daemon に Public DNS を指定する公式手順をそのまま実行：

```bash
sudo tee /etc/docker/daemon.json <<EOF
{
  "dns": ["8.8.8.8", "1.1.1.1"]
}
EOF
sudo systemctl restart docker
```

これで直ると思ったが——再 build しても **同じエラー**。

## 何が起きたか

```
ERROR: failed to do request: Head "https://registry-1.docker.io/...":
   dial tcp: lookup registry-1.docker.io on 192.168.32.1:53: server misbehaving
```

`daemon.json` は反映されている（`docker info` で確認可）が、依然として **ホストのルーター DNS (192.168.32.1)** を見に行っている。

ホスト側で名前解決を確認すると、**異常な状態**が見える：

```bash
$ getent ahostsv4 registry-1.docker.io
18.233.229.105  STREAM registry-1.docker.io     ← /etc/resolv.conf に 8.8.8.8 を書いた後

$ getent hosts registry-1.docker.io             ← デフォルトの hosts は v6 優先で v4 返さない
2600:1f18:2148:bc02:df4b:531c:83da:7969 registry-1.docker.io
2600:1f18:2148:bc01:2bed:275b:b952:26d2 registry-1.docker.io
...
```

問題のルーター DNS (192.168.32.1) は **AAAA レコード（IPv6）はちゃんと返すが、A レコード（IPv4）には `SERVFAIL` を返す**壊れ方をしていた。Docker daemon は IPv4 で繋ぎに行こうとするので、A レコードが取れなくて死ぬ。

### なぜこうなるのか

2つの誤解があった：

#### 誤解1: `daemon.json` の `dns` 設定の対象範囲

`/etc/docker/daemon.json` の `dns` キーは **コンテナ内 `/etc/resolv.conf` を生成するときの値**として使われる。**Docker daemon 自身（dockerd）と BuildKit のレジストリ lookup には効かない**。

```
+----------------------------+
| Host (Pi)                  |
| /etc/resolv.conf:          |
|   nameserver 192.168.32.1  | ← ここを見て docker pull する
| dockerd  ──────────────────┐
| BuildKit ──────────────────┤
+----------------------------+
                 │
                 ▼ 「pythonイメージのレジストリどこ？」
            DNS lookup 失敗
+----------------------------+
| Container (起動後)         |
| /etc/resolv.conf:          |
|   nameserver 8.8.8.8       | ← daemon.json の dns はここに反映される
+----------------------------+
```

つまり「**コンテナの中から外に出ていく時の DNS**」は `daemon.json` で制御できるが、「**daemon 自身がレジストリにアクセスする時の DNS**」はホストの `/etc/resolv.conf` に依存する。

#### 誤解2: 「ホストで `nslookup docker.io` が通るから DNS は OK」

家庭用ルーターは AAAA はちゃんと返すので、**`nslookup` や `host` で見ると正常に見える**ことがある。だが Docker は IPv4 で接続するので、A だけ取れない壊れ方では機能しない。診断時は `getent ahostsv4` で **明示的に A だけ問い合わせる**べき。

## ✅ 解決した指示 / アプローチ

ホストの `/etc/resolv.conf` を直接書き換えて Public DNS に切り替える：

```bash
sudo tee /etc/resolv.conf <<'EOF'
nameserver 8.8.8.8
nameserver 1.1.1.1
EOF
```

確認：

```bash
$ getent ahostsv4 registry-1.docker.io
18.233.229.105  STREAM registry-1.docker.io   ← v4 が返ってきた

$ docker pull python:3.11-slim-bookworm
3.11-slim-bookworm: Pulling from library/python
...                                          ← 成功
```

### NetworkManager に書き戻されないように恒久化

`/etc/resolv.conf` は NetworkManager が再接続時に上書きする。恒久対策として、接続プロファイルに DNS を埋め込む：

```bash
# 現在のアクティブな接続を取得
SSID=$(nmcli -t -f ACTIVE,SSID dev wifi | awk -F: '$1=="yes"{print $2}')

# その接続プロファイルに 8.8.8.8 / 1.1.1.1 を設定
sudo nmcli connection modify "$SSID" ipv4.dns "8.8.8.8 1.1.1.1"
sudo nmcli connection modify "$SSID" ipv4.ignore-auto-dns yes
sudo nmcli connection up "$SSID"
```

これで再起動・再接続後も Public DNS が維持される。

### なぜこれで解決するのか

- `dockerd` と BuildKit はホストの DNS resolver chain（`/etc/nsswitch.conf` → `/etc/resolv.conf`）を使う。ホスト DNS を Public 系に切り替えれば直る
- `daemon.json` の `dns` は引き続き残しておくと、**起動するコンテナ内**でも安定して名前解決できる（保険）

## 比較まとめ

| | ❌ 最初 | ✅ 改善後 |
|---|---------|-----------|
| 設定場所 | `/etc/docker/daemon.json` の `dns` | ホストの `/etc/resolv.conf` (NetworkManager 経由で恒久化) |
| 効果 | コンテナ内 `/etc/resolv.conf` だけ書き換わる | dockerd/BuildKit の registry lookup に効く |
| ルーター A レコード壊れた状態 | 依然として失敗 | 解決（Public DNS が A を返す） |
| トラブル切り分け | `nslookup` で OK と判断 | `getent ahostsv4` で v4 だけを確認 |

## バイブコーディングで実装する

この記事の内容を踏まえた、最初から正しく対処させるためのプロンプト：

> Debian / Ubuntu / Raspberry Pi OS で Docker のレジストリ lookup が `server misbehaving` で失敗するときの対処。
>
> 順序：
> 1. **症状切り分け**：`getent ahostsv4 registry-1.docker.io` を実行。**v4 アドレスが返らない**なら、ルーター DNS が A レコードを引けていない（よくある壊れ方）。`nslookup` だけで判断しない（v6 で見えると OK と誤認する）
> 2. **暫定対処**：ホストの `/etc/resolv.conf` を `nameserver 8.8.8.8` に上書き
> 3. **恒久対処**：NetworkManager 経由で接続プロファイルに DNS を埋め込む：
>    ```bash
>    sudo nmcli connection modify "<SSID>" ipv4.dns "8.8.8.8 1.1.1.1"
>    sudo nmcli connection modify "<SSID>" ipv4.ignore-auto-dns yes
>    sudo nmcli connection up "<SSID>"
>    ```
> 4. **コンテナ側の保険**：`/etc/docker/daemon.json` の `dns` はコンテナ内 resolv.conf にしか効かないが、保険として `["8.8.8.8", "1.1.1.1"]` を入れておく
>
> `daemon.json` の `dns` だけで直そうとしないこと（dockerd/BuildKit のレジストリ lookup には効かない）。

### AIに指示するときのポイント

- AI は Docker DNS 問題に対して **真っ先に `daemon.json` を編集**するアドバイスを出す。それでは直らない場合があると先回りで知らせる
- AAAA だけ返って A が返らない壊れ方は AI の知識に薄い。**`getent ahostsv4` で IPv4 だけ問い合わせる切り分けを明示**する
- NetworkManager 環境では `/etc/resolv.conf` 直編集は再接続時に消える。**`nmcli connection modify` で恒久化**する手順を必ず添える
