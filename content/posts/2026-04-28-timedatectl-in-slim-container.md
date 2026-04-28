---
title: "Slim Docker image で `timedatectl` が動かない問題 — `Failed to connect to bus` への graceful degradation 設計"
emoji: "⏰"
type: "tech"
topics: ["Docker", "Python", "systemd", "timedatectl", "Debugging"]
published: true
category: "Debugging"
date: "2026-04-28"
description: "アプリが `timedatectl show -p NTPSynchronized --value` で NTP 同期状態を確認している場合、Slim Docker image (python:3.11-slim 等) では FileNotFoundError か `Failed to connect to bus` で常に失敗する。FileNotFoundError だけでなく stdout 不正出力もホスト時計信頼に倒すべき。"
coverImage: "/images/posts/timedatectl-in-slim-container-cover.jpg"
---

## やりたかったこと

「**ホストの NTP 同期が完了している間だけ DB へイベントを送信する**」設計のアプリを Docker コンテナ化した。同期確認には `timedatectl` を使っていた：

```python
def is_synced(self) -> bool:
    try:
        r = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True, text=True, timeout=2.0, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return r.stdout.strip().lower() == "yes"
```

ホストで動かしていた頃は問題なく動いていた。コンテナ化したら**全イベントが永遠に保留**される現象が発生。

## こんな人向け

- アプリ内で `timedatectl` を呼んで NTP 同期確認している
- それを `python:3.11-slim` などの Slim Docker image で動かしたい
- コンテナ内のクロックが信用できないと処理がブロックされる設計
- 「ホストには systemd-timesyncd があるけどコンテナには無い」という構成
- `Failed to connect to bus` エラーの正体が知りたい

## ❌ 最初の指示 / アプローチ

「コンテナ内に `timedatectl` が無いだけでは？ FileNotFoundError は既に拾っているし大丈夫」と過信。`return False` で「ホスト未同期なのでイベント保留」が返ると、Sender はイベントを inbox に貯めるだけで一切送信しない、という設計になっていた。

## 何が起きたか

3 コンテナ構成（mosquitto + detector + bridge）で起動。detector からイベントは流れているが、bridge ログを見ると：

```json
{"event":"received","event_id":"e6ed87d4-..."}
{"event":"received","event_id":"e6ed87d4-..."}     ← 同じ event_id が何度も再受信
{"event":"received","event_id":"e6ed87d4-..."}
{"event":"periodic","ntp_synced":false,"inbox_count":53}    ← ★ 同期判定が常に false
```

`merge_committed` イベントが**1つも出ていない**。bridge は Sender スレッドの中で `is_synced()` をチェックして false なら `continue`（イベント保留）するロジックだったので、まさにそれに引っかかっていた。

### なぜこうなるのか

bridge コンテナで `timedatectl` を直接実行してみた：

```bash
$ docker compose exec -T bridge which timedatectl
/usr/bin/timedatectl                         ← 存在する（network-manager の依存で入った）

$ docker compose exec -T bridge timedatectl show -p NTPSynchronized --value
System has not been booted with systemd as init system (PID 1). Can't operate.
Failed to connect to bus: Host is down       ← stderr
                                              ← stdout は空
```

**バイナリは存在する**が、`systemd` を init として起動していないコンテナ内では DBus に繋がらず exit code != 0。stdout が空になる。

私のコードは：

```python
return r.stdout.strip().lower() == "yes"
```

stdout が空文字列 → `"yes"` ではない → `False` を返す。**コンテナ内ではほぼ確定で `False`** になる仕様だった。

detector コンテナの方は別の挙動：

```bash
$ docker compose exec detector which timedatectl
exec failed: ... "timedatectl": executable file not found in $PATH
                                              ← FileNotFoundError 相当
```

こちらは `network-manager` を入れていないので、そもそもバイナリが無い。`FileNotFoundError` で `False` 返してた。

つまり 2 つのコンテナで **2 つの異なる失敗モード**：

- detector: FileNotFoundError → False
- bridge: returncode != 0、stdout 空 → False

## ✅ 解決した指示 / アプローチ

「**コンテナ内で同期判定できないなら、ホスト時計を信頼する**」に倒す。`/etc/localtime` を read-only でホストからマウントしている前提なら、ホストで `systemd-timesyncd` が動いていれば時計は正しい。

```python
def is_synced(self) -> bool:
    try:
        r = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True, text=True, timeout=2.0, check=False,
        )
    except FileNotFoundError:
        # コンテナにバイナリ無し → ホスト時計を信頼
        return True
    except (subprocess.TimeoutExpired, OSError):
        return False
    out = r.stdout.strip().lower()
    if out == "yes":
        return True
    if out == "no":
        return False
    # 不明な出力（"Failed to connect to bus" など）→ ホスト時計を信頼
    return True
```

3 つのケースに分岐：

| 条件 | 戻り値 | 理由 |
|---|---|---|
| `FileNotFoundError`（バイナリ無し） | `True` | コンテナ内に何も無い → ホスト信頼 |
| `TimeoutExpired` / `OSError` | `False` | システム異常の可能性 |
| stdout が `"yes"` | `True` | 正規の同期完了 |
| stdout が `"no"` | `False` | 正規の未同期 |
| その他の stdout（空、エラーメッセージ等） | `True` | コンテナ内 systemd 不在 → ホスト信頼 |

### docker-compose.yml の前提

ホスト時計を信頼できる前提は、コンテナがホストの時計を見ていること：

```yaml
services:
  bridge:
    volumes:
      - /etc/timezone:/etc/timezone:ro
      - /etc/localtime:/etc/localtime:ro    # ← ホストの時計（タイムゾーン込み）を共有
```

`/etc/localtime` は **時刻そのものではなく、time zone 情報** だが、コンテナのプロセスがホストの time syscall を呼ぶ時点でホストの clock を読むので、結果的にホスト時計と完全一致する。

### なぜこれで解決するのか

- コンテナ内の同期判定が壊れる原因は **systemd 不在**による DBus 不通。これはコンテナ設計の本質
- 「同期判定できない」ことと「時計が間違っている」ことは別。ホスト側で NTP が動いている**普通の運用環境**では、ホスト clock を信用する方が現実的
- イベント保留→送信解除のヒステリシス挙動が、コンテナ運用で意図せず発火しなくなる

## 比較まとめ

| | ❌ 最初 | ✅ 改善後 |
|---|---------|-----------|
| 拾う例外 | FileNotFoundError、TimeoutExpired、OSError | 同左に加え、不明 stdout も graceful 扱い |
| stdout 判定 | `=="yes"` 以外は False | `"yes"` / `"no"` / その他で 3 分岐 |
| コンテナ動作 | 全イベント永遠に保留 | ホスト時計を信頼して送信継続 |
| ホスト動作 | 変化なし（正規 yes/no が来るのでそのまま） | 同左 |

## バイブコーディングで実装する

この記事の内容を踏まえた、最初から正しく実装させるためのプロンプト：

> Python アプリで NTP 同期状態を `timedatectl show -p NTPSynchronized --value` で確認する関数を書く。**Docker (python:3.11-slim 等の slim image)** で動かす前提なので、コンテナ内では `timedatectl` が無いか、あっても systemd bus に繋がらず動かない。
>
> 戻り値の決め方：
> - `subprocess.FileNotFoundError`：バイナリ無し → **`True`**（ホスト時計を信頼）
> - `subprocess.TimeoutExpired` / `OSError`：システム異常 → **`False`**
> - stdout が `"yes"`：**`True`**
> - stdout が `"no"`：**`False`**
> - **その他の stdout（空、`"Failed to connect to bus"` 等）→ `True`**（コンテナ内 systemd 不在として扱う）
>
> 前提として、コンテナの `docker-compose.yml` で `/etc/localtime:/etc/localtime:ro` をマウントしてホストの時計を共有していること。これが無いとコンテナ時計が UTC でズレる。

### AIに指示するときのポイント

- AI は `subprocess.run` の `check=False` + stdout 判定を素直に書きがち。**「コンテナ内で常に False になる」エッジケースを明示**する
- `Failed to connect to bus` エラーは AI の学習データではあまり扱われない。`stdout 空 → True` に倒す判断を**明示的に指示**する
- ホスト clock 信頼の前提として `/etc/localtime` のマウント要件を**プロンプトに含める**（compose 設定を忘れさせない）
- ユニットテストは「FileNotFoundError → True」「TimeoutExpired → False」「不明 stdout → True」の **3 ケースを必ずカバー**するよう指示する。1ケースだけだと将来の挙動変更で気づかない
