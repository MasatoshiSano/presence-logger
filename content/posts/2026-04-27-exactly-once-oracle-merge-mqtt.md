---
title: "Exactly-once 配信を Oracle MERGE + MQTT QoS=2 + ACK 往復で実装する — 既存テーブル変更なしで冪等化する設計"
emoji: "🔁"
type: "tech"
topics: ["Oracle", "MQTT", "Architecture", "Idempotency", "IoT"]
published: true
category: "Architecture"
date: "2026-04-27"
description: "センサー → MQTT → 受信ブリッジ → Oracle DB の典型的な IoT パイプラインで、既存テーブルにカラム追加せず exactly-once 配信を保証する設計。MERGE 文の冪等キー、SQLite 二段バッファ、ACK 往復、failure mode の整理。"
coverImage: "/images/posts/exactly-once-oracle-merge-mqtt-cover.jpg"
---

## 概要

センサーで何かを検知して DB に書き込むパイプラインは「**1 イベントが必ず 1 行になる**」ことを保証したい場面が多い。重複しても困るし、欠損も困る。

MQTT QoS=2 を使えば「ブローカー〜クライアント間」の Exactly-once は MQTT プロトコルが保証する。が、それだけでは **電源断・ネットワーク断・DB 一時停止・受信側プロセス再起動** などのハードな failure mode をカバーしきれない。

この記事では、既存の Oracle テーブル（`event_id` のような UUID カラムを足せない縛り）に対して、**スキーマ変更ゼロで Exactly-once を実現する**設計パターンを紹介する。

## こんな人向け

- IoT・産業システムで「センサー → MQTT → DB」のパイプラインを設計している
- DB 側のスキーマを勝手に変更できない（管理部門が別、運用ルールがある等）
- MQTT QoS=2 だけでは不十分なエッジケース（プロセス再起動、DB 一時停止）に悩んでいる
- 「at-least-once + idempotent receiver」パターンを実装に落としたい

## アーキテクチャ概要

```
┌─────────────┐       MQTT QoS=2        ┌─────────────┐                 ┌──────────┐
│  Producer   │ ──── presence/event ───▶│   Broker    │ ────subscribe──▶│ Receiver │
│ (Detector)  │                          │ (Mosquitto) │                 │ (Bridge) │
│             │ ◀── presence/event/ack ─┤             │ ◀── publish ────┤          │
│  ┌────────┐ │                          └─────────────┘                 │ ┌──────┐ │
│  │ Local  │ │                                                          │ │ Inbox│ │
│  │ SQLite │ │                                                          │ │SQLite│ │
│  └────────┘ │                                                          │ └──────┘ │
└─────────────┘                                                          │     │    │
                                                                          │     ▼    │
                                                                          │  Oracle  │
                                                                          │  MERGE   │
                                                                          └──────────┘
```

ポイントは 4つ：

1. **Producer が UUID を採番**して MQTT メッセージに含める
2. **Receiver は受信時に SQLite inbox へ idempotent INSERT**（`ON CONFLICT(event_id) DO NOTHING`）
3. **Oracle 書き込みは MERGE で冪等化**（既存スキーマを使ってユニークキーを論理的に構成）
4. **書き込み完了後、Receiver が ACK を Producer に publish**。Producer は ACK 受領まで再送し続ける

## 設計の詳細

### 1. Producer 側のローカルバッファ

ENTER/EXIT を検知した瞬間に **まずローカル SQLite に persist**、それから MQTT publish。

```sql
CREATE TABLE pending_events (
  event_id            TEXT PRIMARY KEY,    -- UUIDv4
  event_type          TEXT NOT NULL CHECK(event_type IN ('ENTER','EXIT')),
  mk_date             TEXT,
  monotonic_ns        INTEGER NOT NULL,
  status              TEXT NOT NULL CHECK(status IN ('pending','sent','acked')),
  retry_count         INTEGER NOT NULL DEFAULT 0,
  next_retry_at_iso   TEXT
);
```

ステータス遷移：

```
pending ─(publish 成功)─▶ sent ─(ACK 受領)─▶ acked
   │                       │
   └──── ACK タイムアウト → 指数バックオフで再 publish ────────┘
```

プロセス再起動後、起動時に `status != 'acked'` の行を全部再 publish。

### 2. Receiver 側の Inbox

```sql
CREATE TABLE inbox (
  event_id            TEXT PRIMARY KEY,
  event_type          TEXT NOT NULL,
  mk_date             TEXT,
  raw_payload         TEXT NOT NULL,        -- 受信時の JSON 原文（解析用）
  status              TEXT NOT NULL CHECK(status IN ('received','sent')),
  received_at_iso     TEXT NOT NULL,
  sent_at_iso         TEXT,
  retry_count         INTEGER NOT NULL DEFAULT 0,
  last_error          TEXT
);
```

MQTT 受信時の処理：

```python
def on_event(payload, raw):
    event = InboxEvent(event_id=payload.event_id, ..., status="received")
    # ON CONFLICT(event_id) DO NOTHING で重複は黙殺
    inbox.insert_received(event)
```

これで Producer が同じ `event_id` で再送してきても、Inbox 上は 1 行のみ。

### 3. Oracle MERGE で冪等化

既存の `HF1RCM01` テーブルに `event_id` カラムを追加できない場合、`(MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS)` の 5 タプルを **論理的なユニークキー**として MERGE：

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

このクエリは：

- **初回**: `rows_affected = 1`（INSERT 実行）
- **2回目以降（同じキー）**: `rows_affected = 0`（NO-OP、冪等）

`STA_NO` がデバイス固定値、`MK_DATE` が秒精度のタイムスタンプ、`T1_STATUS` が ENTER/EXIT (1/2) なので、現実的に **同一秒に同一デバイスで同一遷移が再発する可能性は無い**（3秒デバウンスを噛ませている）。これにより、event_id カラムが無くても冪等性が成立する。

### 4. ACK 往復

Receiver は Oracle COMMIT 成功後、`presence/event/ack` トピックに publish：

```json
{
  "event_id": "0192b6d2-7c34-7a8f-bb01-5c0bce4f9f2e",
  "mk_date_committed": "20260427172345",
  "committed_at_iso": "2026-04-27T17:23:46.012+09:00"
}
```

Producer は ACK 受信スレッドで `event_id` を検出 → ローカル SQLite を `acked` に更新。

### 5. Failure Mode カタログ

| シナリオ | 挙動 | データ重複 | データ欠損 |
|---|---|---|---|
| Producer 再起動（pending 残留） | 起動時に再 publish → Receiver で重複は ON CONFLICT で黙殺 → ACK 再送 | × | × |
| MQTT 切断 | paho の自動再接続。pending は永続化されているのでロスト無し | × | × |
| Receiver 再起動（inbox.received 残留） | 起動時に未送信の行を再 MERGE → 1回目で送信済みなら NO-OP、未送信なら INSERT | × | × |
| **Receiver が Oracle COMMIT 後・ACK publish 前にクラッシュ** | 再起動後、Inbox は `status='sent'` で残る → Producer は再 publish、Receiver は MERGE で NO-OP し ACK だけ送信 | × | × |
| Oracle 一時停止 | inbox に蓄積、指数バックオフで再試行 | × | × |
| Oracle スキーマ問題（恒久エラー） | サーキットブレーカで該当プロファイルを isolate、CRITICAL ログ | × | 復旧まで遅延 |
| ローカル SQLite ディスク満杯 | リングバッファ：古い acked / sent から削除、必要なら警告 | × | リング上限を超えれば一部欠損 |

ポイント: 「**書き込み済みかどうか**」を Receiver 側の SQLite が常に持ち、Oracle の MERGE が冪等なので、**任意のフェーズでクラッシュしても重複も欠損も起きない**（リングバッファの上限以外は）。

## なぜ「QoS=2 だけ」では不十分か

MQTT QoS=2 は以下を保証する：

- メッセージは **クライアント間で正確に 1 回**配送される（PUBREC/PUBREL/PUBCOMP の 4-way handshake）
- Producer がブローカーに送ったメッセージは、ブローカーが永続化する（broker の persistent session 設定次第）

しかし以下は保証しない：

- ブローカー再起動でメモリ内 inflight メッセージが消えること
- Receiver アプリがメッセージを受け取った後、DB 書き込みする前にクラッシュすること
- DB 自体の availability

つまり **QoS=2 はネットワーク区間の Exactly-once しか保証しない**。「DB に 1 回確実に書く」までを保証するには、本記事の「receiver-side de-duplication + idempotent write + producer-side ACK」が必要。

## 実装上のヒント

### event_id は UUIDv4 で十分

128 bit のランダム UUID なら衝突確率は実質ゼロ。タイムスタンプベースの UUIDv7 でも良いが、シンプルさで v4 推奨。

### MERGE と INSERT IGNORE / ON CONFLICT との違い

- MySQL: `INSERT ... ON DUPLICATE KEY UPDATE` または `INSERT IGNORE`
- PostgreSQL: `INSERT ... ON CONFLICT DO NOTHING`
- SQL Server: `MERGE INTO ... WHEN NOT MATCHED THEN INSERT`
- Oracle: `MERGE INTO ... WHEN NOT MATCHED THEN INSERT`（このパターン）

すべて同じ「既に存在すれば NO-OP、無ければ INSERT」を表現できる。Oracle の MERGE は SQL 標準の構文に近く、`ON` 句で論理キーを明示できるので冪等性の意図が明確になる。

### バックオフのスケジュール

筆者は `5s → 15s → 45s → 135s → 405s → 600s (cap)` で運用。`initial=5, multiplier=3, cap=600` の指数。停電・短時間 NW 切断は数分以内に復旧することが多く、長期障害でも 10 分間隔で再試行が走り続ける。

### サーキットブレーカ

`ORA-00942` (table or view does not exist)、`ORA-01017` (invalid credentials)、`ORA-01031` (insufficient privileges) などの **恒久エラー**は再試行しても無駄。検出したらプロファイル単位でサーキットを open し、15 分後に half-open で 1 回だけ再試行。これがないと、設定ミスで全イベントが 600 秒バックオフループに入り、ログがゴミで溢れる。

## まとめ

- Exactly-once 配信は「at-least-once 配送 + idempotent 受信処理」の組み合わせで実現する
- Producer 側は UUID 採番 + ローカル SQLite で再送状態を管理
- Receiver 側は Inbox SQLite で `ON CONFLICT DO NOTHING` による de-dup
- Oracle 書き込みは MERGE 文で論理キーによる冪等化（既存スキーマを変更しない）
- ACK を Producer に返して送信完了を確定
- ローカル SQLite は WAL モード + リングバッファで運用

## バイブコーディングで実装する

この記事の内容を AI コーディングアシスタントに実装させるためのプロンプト：

> センサー → MQTT → 受信ブリッジ → Oracle DB のパイプラインを Python で書く。要件は「**1 イベントが Oracle に正確に 1 行**」（Exactly-once）。
>
> 設計：
> 1. Producer 側で `uuid.uuid4()` を採番し、MQTT publish 前にローカル SQLite (`pending_events` テーブル: `event_id PRIMARY KEY, status IN ('pending','sent','acked')`) に保存
> 2. MQTT QoS=2 で publish。Receiver は `inbox` テーブルに `ON CONFLICT(event_id) DO NOTHING` で idempotent INSERT
> 3. Oracle 書き込みは `MERGE INTO <table> ... WHEN NOT MATCHED THEN INSERT` を使い、論理ユニークキー（既存テーブルのカラム組み合わせ）で冪等化。schema は変更しない
> 4. COMMIT 成功後、`presence/event/ack` トピックに `{event_id, mk_date_committed}` を QoS=2 で publish
> 5. Producer は ACK 受信で SQLite を `acked` に更新。タイムアウトしたら指数バックオフで再 publish（`5→15→45→135→405→600` 秒）
> 6. Receiver は再起動時に `inbox.status='sent'` だが ACK 未送信のものを再 ACK。`status='received'` を再 MERGE
> 7. 恒久エラー (ORA-00942/01017/01031 等) はサーキットブレーカで isolate、15 分後 half-open で再試行
>
> ローカル SQLite は WAL モード、上限到達時はリングバッファ（acked/sent から優先削除）。

### AIに指示するときのポイント

- 「Exactly-once」とだけ言うと AI は QoS=2 だけで済ませがち。**「DB 書き込みまで 1 回」**を明示する
- MERGE の論理ユニークキーは AI が誤解しやすい。`ON (...)` 句に **明示するカラム組み合わせを具体的に列挙**する
- ACK 往復を忘れがち。**「Receiver は COMMIT 後に ACK publish、Producer は ACK 受領まで再送」**を必ず書く
- failure mode の網羅は AI が手を抜きやすい部分。本記事の **failure mode カタログを参考資料として渡す**と、抜け漏れが減る
- サーキットブレーカは「恒久エラーで暴走を止める」目的を**明示**しないと、汎用的な再試行ロジックに混ぜ込まれて構造が壊れる
