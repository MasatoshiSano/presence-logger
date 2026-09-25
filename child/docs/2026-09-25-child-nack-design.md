# 子側 nack 対応 設計書（恒久失敗を子に伝える）

- 日付: 2026-09-25
- 前段: `docs/2026-09-23-child-oracle-ack-design.md`（ACK完了契約）の追補
- 対象: `child/ack_delivery.py`, `child/child-csv-to-mqtt.py`, `child/tests/test_ack_delivery.py`
- 触らない: `services/bridge/`（e5b248a で完成済み）、MERGE の ON 句・照合キー・failed 判定

## 0. 前提として確認した事実

| 事実 | 出典 |
|---|---|
| ブリッジは恒久失敗時に `presence/record/nack` へ QoS2・非retainで publish | `mqtt_listener.py:148` `publish_nack`、`main.py:250` 既定値 |
| nack payload は `{"event_id","reason","failed_at_iso","schema_version":1}`。**mk_date は含まない** | `mqtt_listener.py:158` |
| 子が failed 行を再送すると、内容が一致すればブリッジは nack を**再発行**する | `record_sender.py:76-84` |
| failed はブリッジ側で終端状態。failed の後に ACK が来ることはない | `record_sender.py:76-84`（failed なら return） |
| `event_id` = SHA1(`DEVICE_ID|行内容`)。同じ event_id なら同じ内容で同じ子 | `child-csv-to-mqtt.py:80` |
| 子は ack トピックしか購読せず、`deliver_file` は `pending==0` のときだけ complete | `ack_delivery.py:149,262` |

→ 897 件の failed 行は、子が 600 秒ごとに再送するたびにブリッジが nack を再発行している。
子が nack を読めるようになれば、**配備後の最初の再送で自動的に解消する**（手作業での台帳修正は要らない）。

## (a) AckSession の購読変更

### 決定
- 1回の `subscribe([(ack, 2), (nack, 2)])` 呼び出しで2トピックをまとめる。paho の複数トピック subscribe は
  **mid が1つ・SUBACK が1つ**（`granted_qos` はトピック順の配列）なので、既存の「mid 1個を記録して一致したら
  subscribed」の仕組みがそのまま使える。
- `granted_qos` の要素ごとに判定する:
  - ack 側が `0x80`（拒否）→ `_subscribed=False` のまま（ACK が取れないなら送らない。現行より厳しくなるが正しい）
  - nack 側が `0x80` → `_subscribed=True`・`_nack_enabled=False`。nack なしで旧動作（待ち続ける）に落ちる。ログに1行出す
- nack トピックは `base_topic + "/nack"` 固定（ブリッジ既定値と同じ形。ack と同じ導出規則）。

### 差分（擬似コード）
```python
SUBACK_FAILURE = 0x80

def __init__(...):
    self._ack_topic = base_topic + "/ack"
    self._nack_topic = base_topic + "/nack"
    self._nack_enabled = False

def _on_connect(self, client, userdata, flags, rc):
    self._subscribed = False
    self._nack_enabled = False
    _rc, mid = self._client.subscribe([(self._ack_topic, 2), (self._nack_topic, 2)])
    self._expected_mid = mid

def _on_subscribe(self, client, userdata, mid, granted_qos):
    if mid != self._expected_mid:
        return
    granted = list(granted_qos)
    ack_ok = len(granted) >= 1 and granted[0] != SUBACK_FAILURE
    self._nack_enabled = len(granted) >= 2 and granted[1] != SUBACK_FAILURE
    self._subscribed = ack_ok

def _on_message(self, client, userdata, msg):
    if msg.retain:
        return
    if msg.topic == self._ack_topic:
        self._handle_ack(msg.payload)      # 現行の本体をそのまま移す
    elif msg.topic == self._nack_topic:
        self._handle_nack(msg.payload)

def _handle_nack(self, payload):
    data = json.loads(payload)            # 失敗は黙って無視(ackと同じ)
    event_id = data.get("event_id")
    if not event_id: return
    row = self._store.get(event_id)
    if row is None or row.status != "pending": return   # acked/legacy/他の子の行は無視
    self._store.mark_nacked(event_id, reason=str(data.get("reason") or ""),
                            failed_at=data.get("failed_at_iso") or None)
```

### 照合キーについて（課題2の確認結果）
nack payload にある `event_id` だけで照合する。mk_date は要求しない。理由:
1. payload に mk_date が無い（ブリッジは変更不可）。
2. event_id は `DEVICE_ID` と行内容全体の SHA1 なので、event_id 一致なら内容（mk_date を含む）も一致する。
   ACK 側の `mk_date_committed` 照合は「Oracle に実際に書いた MK_DATE の確認」（ブリッジが時刻補正で書き換えうる）
   という意味で、nack にはその意味での対象がない。
3. ブリッジは failed 行の再 nack 前に `_content_matches` で内容一致を確認済み。
4. nack トピックは全子で共有だが、自分の台帳にない event_id は `row is None` で捨てる。
5. **`status=='pending'` の行にしか効かない**。acked を nacked で上書きすることはない（ブリッジでも failed→ACK は起きないが、子側でも防ぐ）。

### send() / 待機ループ
`send()` の戻り値を bool から3値にする。

```python
class SendOutcome(str, Enum):
    ACKED = "acked"; NACKED = "nacked"; PENDING = "pending"
```

- `existing.status == "nacked"` → **publish せず** `NACKED` を返す（二度と再送しない条件その1）。
  内容不一致チェック（`ValueError`）は nacked でも先に行う（acked と同じ順序）。
- `_publish_and_wait` は `acked` なら `ACKED`、`nacked` なら `NACKED` で抜ける。
- 旧呼び出し側互換は不要（呼び出しは `deliver_file` のみ）。既存テストの `is True/False` は
  `== SendOutcome.ACKED / PENDING` に書き換える。

## (b) DeliveryStore の変更

### スキーマ
```sql
CREATE TABLE IF NOT EXISTS delivery (
  destination      TEXT NOT NULL,
  event_id         TEXT NOT NULL,
  mk_date          TEXT NOT NULL,
  payload          TEXT NOT NULL,
  status           TEXT NOT NULL
                   CHECK(status IN ('pending','acked','legacy_unverified','nacked')),
  last_attempt_at  REAL,
  acked_at         TEXT,
  nack_reason      TEXT,      -- 追加: ブリッジの reason（ORA番号入りの文言）
  nacked_at        TEXT,      -- 追加: ブリッジの failed_at_iso
  PRIMARY KEY (destination, event_id)
);
```

### 既存 ack.db の移行（必須）
SQLite は CHECK 制約を ALTER できない。子3台の `ack.db` は旧 CHECK を持つので、
`__init__` で次を行う:

1. `SELECT sql FROM sqlite_master WHERE type='table' AND name='delivery'`
2. 無ければ新 SCHEMA を作成。あって `'nacked'` を含まなければ移行:
   ```sql
   BEGIN IMMEDIATE;
   CREATE TABLE delivery_new (...新スキーマ...);
   INSERT INTO delivery_new (destination,event_id,mk_date,payload,status,last_attempt_at,acked_at)
     SELECT destination,event_id,mk_date,payload,status,last_attempt_at,acked_at FROM delivery;
   DROP TABLE delivery;
   ALTER TABLE delivery_new RENAME TO delivery;
   COMMIT;
   ```
   失敗時は ROLLBACK して例外を上げる（起動失敗＝systemd が再起動。黙って旧スキーマで動かない）。
3. 再度開いたときは `'nacked'` を含むので何もしない（冪等）。

別テーブル（`nack` テーブル）案も検討したが、状態が2テーブルに割れて `status()` の意味が分かりにくくなるため不採用。
台帳は小さい（子あたり数千行）ので再構築のコストは無視できる。

### メソッド
```python
def mark_nacked(self, event_id: str, *, reason: str, failed_at: str | None) -> bool:
    # WHERE ... AND status='pending' を条件に入れる（acked/legacy を上書きしない）
    # 更新行数 == 1 なら True
def nacked_rows(self, event_ids: Iterable[str]) -> list[DeliveryRow]:   # 付帯ファイル用（任意）
```
`DeliveryRow` に `nack_reason: str | None`, `nacked_at: str | None` を追加。

**二度と再送しない条件（まとめ）**: 台帳の `status=='nacked'` であること。これは
(1) `send()` の先頭分岐で publish 前に返す、(2) `mark_nacked` が pending からしか遷移させない、
(3) nacked から他の状態へ戻すメソッドを作らない、の3点で守る。同じ内容の行が CSV に再度現れても
event_id が同じなので同じく送らない。

## (c) CSV ファイル全体の扱い

### 決定
- **全行が acked** → 今まで通り `sent/`（`ARCHIVE_DIR`）へ。
- **acked と nacked だけで、nacked が1件以上**（pending=0・invalid=0・その他 complete 条件も満たす）→
  `sent/` ではなく **`nacked/`（新環境変数 `NACKED_DIR`、既定 `./nacked`）へ移す**。
- pending が1件でも残る → 今まで通り移さない。

`DeliveryResult` に `nacked: int` と `nacked_rows: list[(line_no, event_id)]` を追加。
`complete` の条件式は変えず（`pending==0` は nacked を pending に数えないので自然に満たされる）、
振り分けは呼び出し側（`main()`）で `result.nacked > 0` を見て行う。

### 「黙って成功にしない」ための具体策（3重）
1. **置き場所で区別**: `sent/` には全行成功のファイルしか入らない。`sent/` の意味は変わらない。
2. **付帯ファイル**: `nacked/<CSV名>.nack.jsonl` に nacked 行を1行1件で書く:
   `{"line_no", "event_id", "mk_date", "reason", "failed_at_iso"}`。CSV を見ただけではどの行が失敗か
   分からないため。書き込み順は「移動先の名前を決める → 付帯ファイルを tmp+rename で作る → CSV を移動」。
   途中で落ちても次回の巡回で同じ名前に上書きされる（冪等）。
3. **ログ**: nack 受信時に `NACK: event_id=… mk_date=… reason=…` を1行、ファイル移動時に
   `  {name}: ack=… nacked=… → nacked/ に移動（恒久失敗 N 行）` を出す。既存の1行サマリにも
   `nacked=` を足す。

### 理由
- 移さない（現状）と、同じファイルの他の行が全部成功していても永遠に `outbox/` に残り、600秒ごとの再送が
  止まらない。今回の滞留はまさにこれ。
- `sent/` に混ぜると、後から「sent にあるのは全部 Oracle に入った」という前提が壊れる（沈黙の成功）。
- 行単位で別 CSV に分割する案は、元ファイルの行順・監査性が崩れるので不採用。ファイルはそのまま、失敗行は付帯ファイルに書く。
- ORA-00001 の業務的な解消（どちらの行を正とするか）は範囲外。`nacked/` はそのための調査置き場になる。

`archive_file` は移動先ディレクトリを引数に取るのでそのまま使える。付帯ファイル名を CSV の最終名から
作れるよう、`_choose_target(source, dir) -> Path` を切り出して `archive_file` から使う。

## (d) 後方互換の確認方法

| 組み合わせ | 期待 | 確認手段 |
|---|---|---|
| 旧子 × 新ブリッジ | 旧子は nack を購読しないので何も変わらない。購読者なし・非retain の QoS2 publish は mosquitto が捨てる | ブリッジ側のコメント・設定例が既にこの前提（`bridge.yaml.example:72`）。子側コード変更なしなので追加テスト不要 |
| 新子 × 旧ブリッジ（nack を出さない） | nack が来ないだけで、ACK 動作・再送・archive は現行と同じ | テスト: nack を一度も流さない既存テスト群がそのまま（戻り値の書き換えのみで）通ること |
| 新子 × ブローカーが nack 購読を拒否 | `_subscribed=True`・`_nack_enabled=False` で現行動作 | テスト: `granted_qos=[2, 0x80]` |
| 旧 ack.db × 新子 | 起動時に移行され、既存行・状態が残る | テスト: 旧 SCHEMA で作った DB を新 `DeliveryStore` で開く |
| 新 ack.db × 旧子（ロールバック） | 旧コードは列の追加を気にしない。`nacked` 行は旧コードで「acked でも legacy でもない」扱い→600秒ごと再送→ブリッジが再 nack→旧子は無視。**今日と同じ状態に戻るだけで壊れない** | 手順書に記載（テスト化はしない） |
| ブリッジの「mqtt.acks には成功ACKだけ」 | ブリッジ・`tests/integration/fakes.py` は触らないので不変 | `pytest tests/integration` がそのまま通ること |

## (e) テストケース一覧（`child/tests/test_ack_delivery.py` に追加・修正）

テスト用 `Client` の変更: `subscribe(self, topics, qos=0)` で list を受けて `self.subscription = topics` に記録。
`nack=True` オプションで publish 時に `topic + "/nack"` へ nack を流せるようにする。

購読:
1. `test_connect_subscribes_ack_and_nack_in_one_call` — on_connect で `[(base/ack,2),(base/nack,2)]` を1回だけ subscribe し、mid を1つ記録する。
2. `test_suback_with_both_granted_enables_send_and_nack` — granted=[2,2] で send が publish し、nack を受け付ける。
3. `test_nack_subscription_refused_falls_back_to_ack_only` — granted=[2,0x80] で send は動くが、届いた nack で行は pending のまま。
4. `test_ack_subscription_refused_blocks_send` — granted=[0x80,2] で send は publish せず PENDING を返す。
5. `test_resubscribe_on_reconnect_covers_nack` — 切断→再接続で両トピックを再購読し、新 SUBACK まで送らない（既存テストの拡張）。

nack 受信:
6. `test_nack_marks_pending_row_nacked_with_reason` — pending 行に nack が来ると status='nacked'、`nack_reason`/`nacked_at` が payload の値になる。
7. `test_send_returns_nacked_when_nack_arrives_during_wait` — publish 中に nack が届くと `_publish_and_wait` がタイムアウトを待たず NACKED を返す。
8. `test_nacked_row_is_never_republished` — nacked 後に `resend_after` を過ぎた時刻で send しても publish されず NACKED を返す。
9. `test_nack_for_unknown_event_id_is_ignored` — 台帳にない event_id の nack（他の子の分）で何も書かれない。
10. `test_nack_does_not_override_acked` — acked 行への nack で status は acked のまま。
11. `test_nack_does_not_override_legacy` — legacy_unverified 行への nack で status は変わらない。
12. `test_retained_nack_is_ignored` — retain=True の nack は無視する。
13. `test_malformed_nack_payload_is_ignored` — JSON 不正・event_id 欠落の nack で例外も状態変化も起きない。
14. `test_nack_on_ack_topic_name_mismatch_ignored` — `base/ack` 以外・`base/nack` 以外のトピックのメッセージは無視する。
15. `test_nacked_row_content_mismatch_still_raises` — nacked 行と同じ event_id で内容違いを send すると ValueError。
16. `test_nacked_status_survives_reopen` — DeliveryStore を開き直しても nacked のまま。
17. `test_ack_after_nack_is_ignored` — nacked 行に後から ACK が来ても acked にならない（片方向）。

移行:
18. `test_old_schema_db_is_migrated_preserving_rows` — 旧 CHECK の DB（pending/acked/legacy 行入り）を開くと行・状態・`last_attempt_at` が残り、nacked を書ける。
19. `test_migration_is_idempotent` — 移行済み DB を2回開いてもエラーにならず行数が変わらない。

ファイル単位:
20. `test_file_with_only_acked_rows_goes_to_sent` — 全行 ACK のファイルは `result.nacked==0` で `sent/` に移る（現行維持）。
21. `test_file_with_nacked_row_goes_to_nacked_dir_not_sent` — ACK と nack が混ざったファイルは complete=True・nacked=1 で、`sent/` ではなく `nacked/` に移る。
22. `test_nacked_file_writes_sidecar_listing_failed_rows` — `nacked/<name>.nack.jsonl` に nacked 行の line_no・event_id・mk_date・reason が1件ずつ入る。
23. `test_file_with_pending_and_nacked_stays_in_outbox` — pending が1行でも残れば移動しない。
24. `test_nacked_archive_never_overwrites_and_sidecar_follows_name` — `nacked/` に同名があるとき別名で移り、付帯ファイル名も同じ別名に揃う。
25. `test_summary_line_reports_nacked_count` — main のサマリ出力に `nacked=` が含まれる（`archive_to_destination` 等の関数を切り出して単体でテスト）。

後方互換:
26. `test_no_nack_ever_keeps_existing_behaviour` — nack が来ない前提で、既存の ACK・再送・部分行・更新検知テストが戻り値の書き換えだけで通る（既存テスト群そのもの）。

## 範囲外で気付いたこと（今回は直さない・報告のみ）
- `ack_delivery.py:172` は ACK の `committed_at` を読むが、ブリッジは `committed_at_iso` を送っている（`mqtt_listener.py:143`）。
  そのため `acked_at` は常に NULL。完了判定には影響しないが、別タスクで直すべき。テストの fake も `committed_at` を使っているため気付かれていない。
- `desktop/presence-tools/child-csv-to-mqtt.py` は ACK 対応前の古いコピーで、`child/` 版と一致していない。
- `invalid` 行（パース不能行）を含むファイルも永遠に `outbox/` に残る。同型の問題だが、今回の nack とは別。
