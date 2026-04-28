---
title: "Docker コンテナから nmcli で WiFi SSID を取得する — DBus マウントで hostNetwork を避ける"
emoji: "📡"
type: "tech"
topics: ["Docker", "nmcli", "DBus", "K3s", "IoT"]
published: true
category: "HowTo"
date: "2026-04-27"
description: "コンテナ内から `nmcli` で現在接続中の WiFi SSID を取得したい。安易に `network_mode: host` を使うと K3s 移行時にポート競合で詰む。DBus socket と NetworkManager のランタイムディレクトリをマウントすれば cluster networking のまま `nmcli` が動く。"
coverImage: "/images/posts/docker-nmcli-wifi-ssid-dbus-cover.jpg"
---

## 概要

エッジデバイスで動かすコンテナアプリが「**現在接続している WiFi SSID によって挙動を変えたい**」というケースは多い（接続先 DB を切り替える、設定プロファイルを選ぶ、地理ベースで動作モードを変える、など）。

最も愚直な方法は `docker-compose.yml` の `network_mode: host` でホストネットワークを共有してしまうこと。`nmcli` が動くようになるが、**K3s や複数コンテナ協調アーキテクチャに移行するときに大きな足枷**になる。

この記事では `network_mode: host` を **使わずに** コンテナ内で `nmcli` を動かす方法を紹介する。

## こんな人向け

- Raspberry Pi 等のエッジデバイスで Docker コンテナを動かしている
- コンテナ内から現在の WiFi SSID やネットワーク状態を取得したい
- `network_mode: host` を使っているが、将来 K3s に移行したい
- 複数コンテナ間で MQTT や HTTP 通信したいが、host network だと各コンテナのポート公開が衝突する

## 前提条件

- ホスト OS が **NetworkManager** を使っている（Raspberry Pi OS Bookworm のデフォルト、Ubuntu Desktop 等）
- Docker / Docker Compose が動いている
- 対象の Linux に `nmcli` が入っている

`systemd-networkd` のみの環境では本手法は使えない（DBus 経由で NetworkManager と話す前提のため）。

## 仕組み

`nmcli` は内部的に **DBus 経由で `NetworkManager` デーモンと通信**して状態を取得・操作する。コンテナから DBus にアクセスできれば、`nmcli` も動く。

```
┌─── Container (presence-net) ─────┐
│  /usr/bin/nmcli                  │
│      │                           │
│      ▼                           │
│  /run/dbus/system_bus_socket  ◀──┼──┐
│      (mounted from host)         │  │
└──────────────────────────────────┘  │
                                      │ DBus IPC
┌─── Host (Raspberry Pi) ──────────┐  │
│  /run/dbus/system_bus_socket   ◀─┼──┘
│      │                           │
│      ▼                           │
│  /usr/sbin/NetworkManager        │
│      │                           │
│      ▼                           │
│  wlan0, eth0, ...                │
└──────────────────────────────────┘
```

ホストネットワークは共有せず、DBus socket と NetworkManager のランタイム情報だけ覗かせる。

## 実装

### 1. Dockerfile：`nmcli` を入れる

```dockerfile
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        network-manager \
    && rm -rf /var/lib/apt/lists/*

# あとは普通のアプリ install
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
CMD ["python", "-m", "src.main"]
```

`network-manager` パッケージで `nmcli` が入る（NetworkManager デーモン本体は使わないが、CLI とライブラリが必要）。

### 2. docker-compose.yml：DBus と NetworkManager をマウント

```yaml
services:
  bridge:
    build: ./services/bridge
    container_name: presence-bridge
    networks: [presence-net]              # 通常の bridge ネットワーク
    volumes:
      # DBus socket：nmcli が NetworkManager と話すために必須
      - /run/dbus:/run/dbus:ro
      # NetworkManager のランタイム情報（接続状態のキャッシュ等）
      - /var/run/NetworkManager:/var/run/NetworkManager:ro
      # 通常のアプリマウント
      - /etc/myapp:/etc/myapp:ro
      - /var/lib/myapp:/var/lib/myapp
    environment:
      LOG_LEVEL: INFO

networks:
  presence-net:
    driver: bridge
```

ポイント：

- `network_mode: host` は **使わない**
- `/run/dbus` を `:ro` でマウント（書き込み権限は不要）
- `/var/run/NetworkManager` も `:ro` で OK

### 3. アプリコード：nmcli を実行して SSID を取得

```python
import shlex
import subprocess
from typing import Optional


def get_current_ssid() -> Optional[str]:
    """`nmcli -t -f ACTIVE,SSID dev wifi` の出力をパースして
    アクティブな SSID を返す。取得失敗時は None。
    """
    cmd = shlex.split("nmcli -t -f ACTIVE,SSID dev wifi")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        # nmcli の terse モード出力: "yes:my_ssid" または "no:other_ssid"
        # SSID 内のコロンはバックスラッシュエスケープされる: "yes:my\:ssid"
        parts = _split_first_unescaped_colon(line)
        if len(parts) >= 2 and parts[0].strip().lower() == "yes":
            return parts[1].replace("\\:", ":")
    return None


def _split_first_unescaped_colon(line: str) -> list[str]:
    """エスケープされたコロン (`\:`) を区切り文字として扱わずに
    最初の生コロンで 2 つに分割する。"""
    out, buf, i = [], [], 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            buf.append(line[i:i + 2]); i += 2; continue
        if ch == ":":
            out.append("".join(buf))
            out.append(line[i + 1:])
            return out
        buf.append(ch); i += 1
    if buf: out.append("".join(buf))
    return out
```

実行：

```bash
$ docker compose exec bridge nmcli -t -f ACTIVE,SSID dev wifi
yes:my_factory_wifi
no:guest_wifi
no:Buffalo-A-20C1
```

コンテナ内から普通に動く。

## なぜ `network_mode: host` を避けるべきか

### ポート競合

Host network ではコンテナのリスニングポートが **ホスト OS のポート空間と直接競合**する。

```yaml
# 全部 network_mode: host にすると...
mosquitto:
  network_mode: host       # 1883 を占有
  ports: [1883]
detector:
  network_mode: host       # 何かをポート開けると衝突
bridge:
  network_mode: host
```

開発環境やシングルノードならまだしも、本番で複数のサービスを並行運用すると詰む。

### コンテナ間の DNS が使えない

`network_mode: host` のコンテナは Docker の内部 DNS（`mosquitto:1883` のようなコンテナ名解決）を使えない。`localhost:1883` で繋ぐ必要があり、ブローカーも host net 必須になる連鎖が起きる。

### K3s 移行時の壁

K3s（Kubernetes）では Pod 間通信は **Service DNS**（`mosquitto.namespace.svc.cluster.local`）を使うのがセオリー。`hostNetwork: true` の Pod は：

- 同一ノードに 1 Pod しか配置できない（ポート競合）
- ClusterIP Service の DNS が引きにくい（`dnsPolicy` を `ClusterFirstWithHostNet` に変える必要あり）
- DaemonSet 化したときにスケールが効かない

DBus マウント方式なら、Compose の挙動と K3s の挙動がほぼ同じになる。Compose の `volumes` を K8s `volumeMounts` + `hostPath` に置き換えるだけ：

```yaml
# K3s DaemonSet 抜粋
volumeMounts:
- { name: dbus, mountPath: /run/dbus, readOnly: true }
- { name: nm, mountPath: /var/run/NetworkManager, readOnly: true }
volumes:
- { name: dbus, hostPath: { path: /run/dbus, type: Directory } }
- { name: nm, hostPath: { path: /var/run/NetworkManager, type: Directory } }
```

これでアプリコードは Compose と完全同一のまま動く。

## ポイント・注意点

### NetworkManager 以外のホストでは動かない

ホストが `systemd-networkd` のみの構成（一部の minimal Linux、Raspberry Pi OS Lite を `dhcpcd` で構成しているケース等）では `nmcli` も DBus も意味を成さない。`systemctl status NetworkManager` で稼働確認しておく。

代替手段：

- `iw dev wlan0 link` で SSID 取得（コンテナ内から `wlan0` が見える必要あり = `--cap-add=NET_ADMIN` か `--privileged`、これも host net ほどではないが privilege を要求する）
- `/proc/net/wireless` を読む（SSID は出ない）

### SELinux / AppArmor

DBus socket のマウントは SELinux の context によって遮断されることがある。Raspberry Pi OS では問題ないが、CentOS Stream や Ubuntu Server で AppArmor enforcing なら、必要に応じて Profile 追加。

### nmcli のバージョン互換

`nmcli -t -f ACTIVE,SSID dev wifi` の出力フォーマットは NetworkManager 1.x で安定している。2.x 以降で変わる可能性は低いが、本番投入前に `nmcli --version` で揃えておくと安心。

### SSID にコロンが入っているケース

NetworkManager は terse モード (`-t`) のとき SSID 内のコロンを `\:` でエスケープして出す。上記の `_split_first_unescaped_colon` のような小さなパーサで処理する。普通の SSID（`my_wifi`）ならコロンは出てこないが、企業ネットワークだとコロン入りもまれにある。

## まとめ

- コンテナから `nmcli` を動かすために `network_mode: host` を使う必要はない
- `/run/dbus` と `/var/run/NetworkManager` を `:ro` でマウントすれば、cluster networking のまま nmcli が動く
- この方式は K3s への移行が容易（Compose の `volumes` ↔ K8s の `volumeMounts`+`hostPath` で 1 対 1 対応）
- ポート競合・コンテナ間 DNS の問題が起きないので、複数コンテナ協調設計に向く

## バイブコーディングで実装する

この記事の内容を AI コーディングアシスタントに実装させるためのプロンプト：

> Docker コンテナ内から、ホストの **現在接続中の WiFi SSID を `nmcli` で取得**したい。`network_mode: host` は使わない（将来 K3s 移行を考えると hostNetwork は使いたくない）。
>
> アプローチ：
> 1. Dockerfile に `apt-get install -y network-manager` を追加（`nmcli` が入る）
> 2. `docker-compose.yml` で以下を read-only マウント：
>    - `/run/dbus:/run/dbus:ro`
>    - `/var/run/NetworkManager:/var/run/NetworkManager:ro`
> 3. コンテナのネットワークは通常の `bridge` (`networks: [presence-net]`)
> 4. アプリコードで `subprocess.run(shlex.split("nmcli -t -f ACTIVE,SSID dev wifi"))` を実行し、stdout をパース。SSID 内のコロンが `\:` でエスケープされるので、エスケープを意識したパーサを書く
> 5. ホスト側で `systemctl status NetworkManager` が Active であることが前提（systemd-networkd のみの構成では使えない）
>
> 将来 K3s に移行する際は、Compose の `volumes` を K8s の `hostPath` ボリュームに置き換えるだけで動く。

### AIに指示するときのポイント

- AI は「コンテナから nmcli を使いたい」と言うと、ほぼ確実に `network_mode: host` を提案してくる。**hostNetwork を避けたい理由（K3s 互換性）を明示**する
- DBus マウントが必要なことを AI は知らないことが多い。**`/run/dbus:/run/dbus:ro` のマウントを必ず指定**する
- nmcli の terse 出力でコロンエスケープが起きる仕様は、AI が標準ライブラリの `csv.reader` で読もうとしがち。**手書きパーサが必要なことを明示**する
- ホストが NetworkManager を使っているか確認する手順（`systemctl status NetworkManager`）を**プロンプトに含める**
