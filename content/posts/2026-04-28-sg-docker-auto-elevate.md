---
title: "`sg docker -c` で `usermod -aG docker` 直後のセッションでも docker を使う — newgrp との違いとスクリプト auto-elevate パターン"
emoji: "🔑"
type: "tech"
topics: ["Linux", "Docker", "Bash", "Permissions", "Shell"]
published: true
category: "DevOps"
date: "2026-04-28"
description: "`usermod -aG docker $USER` を実行した直後のシェルから `docker ps` が permission denied になる。`newgrp docker` で解決できるが、スクリプト内では使えない。`sg docker -c \"...\"` を使う方法と、`docker info` 失敗を検知して自動で sg 経由再実行する defensive な script パターン。"
coverImage: "/images/posts/sg-docker-auto-elevate-cover.jpg"
---

## 概要

Docker をインストール後、ユーザーを `docker` グループに追加する：

```bash
sudo usermod -aG docker $USER
```

このコマンドは **新しい login session 以降のシェルでだけ反映される**。すでに開いているシェル（ssh セッション、Claude Code のような長寿命プロセス、CI runner、systemd の `User=` 起動プロセス…）は、自分の supplementary groups を fork 時にカーネルから取得して保持しているので、後から `usermod` で追加されてもそれを知らない。

```bash
$ docker ps
permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock
```

`newgrp docker` で対話的シェルは解決できるが、**スクリプト/自動化からは使えない**。代替手段の `sg` と、それを応用した auto-elevate パターンを紹介する。

## こんな人向け

- 新しい Linux サーバや Pi にセットアップ中で Docker をいま入れたばかり
- ssh で繋いだセッションから `docker` を打ちたいが permission denied になる
- スクリプトの中で「グループ追加直後でも動く」状態を作りたい
- CI runner（Jenkins、GitHub Actions self-hosted runner 等）が docker を打てない
- AI コーディングアシスタント（Claude Code、Cursor 等）の Bash tool から docker を使いたいが、セッション開始時に group が無かった

## 前提条件

- Linux（任意のディストリ、`util-linux` か `coreutils` の sg コマンドが入っていること）
- Docker インストール済み + ユーザーを `docker` グループに追加済み

## 解決の選択肢

### 1. `sg docker -c "<command>"`：1 コマンドだけ docker グループ付きで実行

```bash
$ sg docker -c "docker ps"
CONTAINER ID   IMAGE     COMMAND   CREATED   STATUS    PORTS     NAMES
```

`sg` は **set group**。指定したグループを補助グループとして付与した子シェルでコマンドを実行する。子プロセスの寿命だけで終わるので、元のシェルには影響しない。

```bash
$ sg docker -c "docker compose up -d && docker ps"
$ sg docker -c "docker run --rm hello-world"
```

複数コマンドは **シェル機能のフルセット**を使える（リダイレクト、パイプ、変数展開）。

### 2. `newgrp docker`：現在のシェル**そのもの**を切り替える

```bash
$ newgrp docker
$ docker ps        # ← OK
$ exit             # 元のシェル（docker グループ無し）に戻る
```

対話的なターミナル向き。スクリプトには向かない。

### 3. 比較

| 観点 | `sg docker -c "..."` | `newgrp docker` |
|---|---|---|
| スコープ | 1 コマンド分の子シェル | 親シェルそのものを置き換え |
| 用途 | スクリプト・自動化 | 対話的ターミナル |
| シンタックス | bash -c 同様 | exec 風 |
| 元シェルへの影響 | なし | exit するまで戻らない |

## スクリプト auto-elevate パターン

スクリプト先頭に「**docker socket に届かなければ sg 経由で自分自身を再実行する**」コードを書くと、グループ追加直後でも動くスクリプトになる。

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Auto-elevate to the `docker` group if the current shell doesn't have access.
# - docker info に失敗 = socket に届かない
# - id -nG に docker が入っていない = グループ未取得
# 両方満たせば sg で自分自身を exec 再実行
if ! docker info >/dev/null 2>&1; then
    if id -nG | grep -qw docker; then
        : # group には入っているが socket 落ちている等の別問題、エラーは出させる
    elif command -v sg >/dev/null 2>&1; then
        exec sg docker -c "bash $0 $*"
    fi
fi

# 以降は安全に docker を使える
docker compose up -d
```

### 動作の流れ

```
1. 起動時シェル：pi グループ持ち、docker グループ無し（usermod 直後）
2. docker info → permission denied
3. id -nG → "pi" だけ → docker 無し → sg を試す
4. exec sg docker -c "bash <script>" → 自分自身を docker 持ちで再実行
5. 新シェル：docker グループあり
6. docker info → OK → そのままスクリプト本体へ
```

`exec` を使うことで PID 数が増えない。`bash $0 $*` で元のスクリプトを再帰呼び出し。

### 副作用がない条件

- スクリプトを 2 回 source/exec しても安全（冪等）であること
- `exec` 後の処理で副作用のある初期化を済ませていない（ログ出力なら問題なし）

## ポイント・注意点

### `sudo` との違い

| | `sudo` | `sg docker` |
|---|---|---|
| ユーザー切替 | `root` などへ | しない（同じユーザー） |
| グループ切替 | しない（root の groups になる） | する（`docker` を補助グループに追加） |
| パスワード | 必要（sudoers 設定次第） | 不要 |
| 用途 | 特権昇格 | グループ切替 |

`sudo docker ...` でも動くが、それは「root が docker daemon に話す」という別経路。**`sg docker -c "docker ..."` は普通のユーザーが docker グループ経由で daemon に話す**ので、production 運用に近い。

### 恒久的な解決策

これは過渡期の仕組み。**ユーザーが logout → login すれば不要**になる。Pi なら reboot が一番手っ取り早い：

```bash
sudo reboot
```

新しいセッションは `usermod -aG` の結果を反映している。ただし、Claude Code のような長寿命の親プロセスが reboot 不可能 / 維持したい場合や、CI 環境では auto-elevate スクリプトの方が便利。

### Docker socket ACL という別の手段

`setfacl` で個別ユーザーに socket アクセス権を付与する手もある：

```bash
sudo setfacl -m u:pi:rw /var/run/docker.sock
# Docker daemon の再起動でリセットされるので systemd drop-in で自動付与
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/acl.conf <<'EOF'
[Service]
ExecStartPost=/usr/bin/setfacl -m u:pi:rw /var/run/docker.sock
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker
```

これだとグループ追加すら不要だが、user 単位で管理が増えるので普通は `usermod -aG docker` の方が良い。

## まとめ

- `usermod -aG docker $USER` は新しい session 以降でしか反映されない
- 既に開いているシェルは `sg docker -c "<cmd>"` で 1 コマンドだけグループ付与できる
- `newgrp docker` は対話的シェル向き、スクリプトには `sg` を使う
- スクリプト先頭に「socket に届かなければ `exec sg docker -c "bash $0 $*"`」を入れると、CI / 自動化でも安心
- 恒久的には reboot か再ログインで全プロセスがグループを取り直す

## バイブコーディングで実装する

スクリプトを AI に書かせるときの指示：

> Bash スクリプトを書く。スクリプト内で `docker` コマンドを使うが、実行ユーザーが `docker` グループにまだ反映されていないシェル（`usermod -aG docker` 直後の login session など）からも動くようにしたい。
>
> 設計：
> - スクリプト先頭に auto-elevate ブロックを入れる
> - `docker info` で socket 到達性を確認、失敗なら `id -nG` で docker グループ所属を確認
> - グループ未所属で `sg` コマンドが使えるなら `exec sg docker -c "bash $0 $*"` で自分自身を再実行
> - グループに既に所属していれば socket 自体の問題なのでそのまま進めて素直にエラーを出す
>
> ```bash
> if ! docker info >/dev/null 2>&1; then
>     if id -nG | grep -qw docker; then
>         :
>     elif command -v sg >/dev/null 2>&1; then
>         exec sg docker -c "bash $0 $*"
>     fi
> fi
> ```
>
> CI（GitHub Actions self-hosted runner、Jenkins agent 等）でも同じパターンが使える。`sg` がない環境（Alpine 系の minimal image）では fallback として `sudo` か reboot に倒す。

### AIに指示するときのポイント

- AI は「docker permission denied」に対して `sudo docker ...` を提案しがち。**普通のユーザーで動く正しい方法は sg/newgrp / 再ログイン**だと明示する
- `sg` と `newgrp` の違いを AI が混同することがある。**「スクリプト = sg、対話 = newgrp」**と用途で書き分けさせる
- `exec` を使う理由（PID 増えない、スタック深くならない）を**明示**しないと、AI は普通の sub-process 呼び出しで書きがち
