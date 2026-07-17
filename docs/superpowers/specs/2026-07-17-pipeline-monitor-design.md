# pipeline-monitor — 子Pi→MQTT→Oracle 一望TUI 設計書

- **日付**: 2026-07-17
- **状態**: 設計確定（実装計画へ）
- **対象読者**: 現場オペレータ（親Piデスクトップで操作）／保守開発者

## 1. 背景・目的

子ラズパイ（child Pi）から親ラズパイ（hub Pi）へのMQTT接続が確立した。
現在、データは次の4段階を通過するが、各段階の中身を横断して見る手段がない。

1. **子Pi → MQTT**: 子が `presence/record` / `presence/heartbeat/<id>` / `presence/status/<id>` を publish
2. **MQTT → bridge**: bridge が購読し SQLite `record_inbox` に received として保存
3. **bridge → Oracle**: `RecordSender` が JDBC サイドカー経由で MERGE、成功で sent に更新
4. **Oracle**: 実テーブル（HHC001 等）に書き込まれた行

オペレータが知りたいのは「**どの子から何が送られ / MQTTに何が乗り / MQTTからOracleへ何が送られ / Oracleに何が書かれたか**」を一目で追えること。

既存ツールとの棲み分け:
- `記録モニタ`（`watch-records.sh`）: **このPi自身のカメラ検知**(detector→bridge)のログ流し。子Pi経路(`presence/record`)は device_id 別に区別されず埋もれる。→ **残す**。
- `直近30件`（`show-recent-records.sh`）: Oracleを直接SELECTする単発ビューア。→ 本ツールの④ペインが同等機能を内包（既存も残す）。

本ツールは **子Pi経路 + DB段階の追跡** を担う新規ツール。

## 2. スコープ

### やること
- 4段階を1画面（curses TUI）に並べ、`event_id` で段階を横断追跡できるダッシュボードを提供。
- 追加コンテナ・常駐サービス不要。ホスト常駐Pythonプロセス1本で完結。

### やらないこと（YAGNI）
- Web UI / 常駐HTTPサーバは作らない（ターミナルTUIに決定）。
- 履歴の永続化・集計DBは作らない（既存の `record_inbox` とOracleが真実の記録）。
- 既存 `記録モニタ` / `直近30件` の置き換えはしない。

## 3. アーキテクチャ

ホスト上で動く単一Pythonプロセス。裏で `mosquitto_sub` を1本走らせMQTTを購読し、
メインループが毎秒4データ源を読んでcursesで1画面に再描画する。

```
                 ┌───────────── pipeline-monitor (host python) ──────────────┐
 10.42.0.1:1883 ─┤ mosquitto_sub -t 'presence/#' → リングバッファ(直近N/子別集計) │
 (MQTT/匿名)      │                                                            │
 host sqlite ────┤ /var/lib/presence-logger/bridge_record_buf.db を RO SELECT  │
 (record_inbox)  │                                                            │
 JDBC sidecar ───┤ docker exec … /select_recent (15秒ごと / [r]手動)          │
 (Oracle)        └──────────────── curses で4ペイン描画 ─────────────────────┘
```

### アクセス経路（デプロイ実態で裏取り済み）
| データ源 | 到達方法 | 根拠 |
|---|---|---|
| MQTT | ホストから `10.42.0.1:1883` に匿名購読（`mosquitto_sub`） | `docker-compose.override.yml` が broker を AP gateway に publish |
| record_inbox | ホストの `/var/lib/presence-logger/bridge_record_buf.db` を read-only で直接SELECT | `docker-compose.yml` l.74 で bind mount |
| Oracle | `docker exec presence-oracle-jdbc … /select_recent` | 既存 `show-recent-records.sh` と同一機構 |

### 依存
- **Python 標準ライブラリのみ**（`curses`, `sqlite3`, `subprocess`, `json`, `urllib`）+ 既存で使用中の `PyYAML`。
- MQTT購読は `mosquitto_sub`（`mosquitto-clients`）をサブプロセス起動 → ホストへの pip 依存ゼロ。
  未導入環境向けに fallback は実装計画で検討（当面 `mosquitto-clients` 前提、起動時に有無を検査し導線を出す）。
- record_inbox は必ず `mode=ro`（`file:...?mode=ro` URI）で開き、監視ツールが本番DBに書かない。

## 4. コンポーネント（責務分離）

各ユニットは単一責務・純ロジックはI/Oから分離してテスト可能にする。

| ユニット | 責務 | 入力 → 出力 |
|---|---|---|
| `MqttTail` | `mosquitto_sub` を起動・監視し、行を受け取りリングバッファへ | (host, port, topic) → メッセージ列 |
| `mqtt_summarize()` | 生MQTT行を種別判定し要約テキスト化（純関数） | raw line → `MqttMsg`(ts, topic, kind, device_id, summary) |
| `child_rollup()` | メッセージ列を device_id 単位に集計（純関数） | list[MqttMsg] → list[ChildStat] |
| `RecordInboxReader` | record_inbox を RO SELECT、直近N件と集計を返す | db path → (list[Row], counts) |
| `OracleRecentReader` | サイドカーへ問い合わせ応答をパース（既存 `_render_recent` 相当を関数化） | profile+pw → list[OraRow] |
| `link_by_event_id()` | ②③④を event_id で突合し段階ステータスを付与（純関数） | 3データ → link map |
| `Dashboard`(curses) | 4ペインのレイアウト・再描画・キー入力。描画のみ、ロジック無し | 上記の集約状態 → 画面 |

## 5. 4ペイン仕様

### ①子Pi別 受信サマリ（左上）
device_id ごとに1行:
- 📥 record 受信数（当セッション） / 💓 heartbeat 最終受信からの経過 / 🟢online・🔴offline（heartbeat_timeout基準） / 最新 mk_date。
- device_id は `presence/heartbeat/<id>` `presence/status/<id>` のトピック名、および `presence/record` ペイロードの `device_id` から得る。

### ②MQTT生ログ（右上）
- 購読対象は **`presence/#` 全体**（要件「MQTTに何が書かれているか」を満たすため全トピック）。
- 直近メッセージを `時刻 topic 要約` で新しい順に表示。種別で色分け（record / heartbeat / status / ack）。
- ペイロードは要約（長い場合は event_id と主要フィールドのみ）。

### ③MQTT→Oracle（左下）: record_inbox
- event_id ごとに `status`(received/sent) / `retry_count` / `last_error`(短縮) / `device_id`。
- 下部に集計行: `received（滞留）` / `sent` / 合計。
- received のまま滞留＝Oracleへまだ送れていない（SSID未接続・DBエラー等）ことが一目で分かる。

### ④Oracleテーブル（右下）
- 既存 `_render_recent.py` と同じ体裁の直近N件（mk_date整形 / 🟢ENTER・🔴EXIT / STA(1/2/3) / UPCMPFLG）。
- **15秒ごと自動更新 ＋ `r` キーで即時更新**（工場網越しの負荷を抑えるため他ペインより低頻度）。
- 非工場SSID時は空になる旨の注意書きを表示（既存ツール踏襲）。

## 6. データフロー追跡性（本ツールの核心）

同一 `event_id` を ②MQTT生ログ → ③record_inbox → ④Oracle と辿れる。
③が `event_id` と `device_id` を両方持つため「**どの子の・どのレコードが・今どの段階か**」が1画面で判別できる。
`link_by_event_id()` が各 event_id に `mqtt_seen / inbox_status / oracle_committed` の段階フラグを付ける。

## 7. エラー処理・堅牢性

- 4データ源は**独立に try/except**。1源が失敗しても他ペインは描画継続。失敗ペインは `⚠ 取得失敗: <理由>` を表示。
  - 例: Oracle未接続（非工場SSID）でも ①②③ は正常動作。
- `mosquitto_sub` プロセスが終了/切断したら自動再起動（bridge の resubscribe-on-reconnect と同じ思想）。
- record_inbox は RO オープン。SQLite が WAL 中でも読み取りは安全。
- 端末リサイズ（`KEY_RESIZE`）でレイアウト再計算。
- 起動時に前提（`mosquitto-clients` 有無、DBパス存在、対象コンテナ稼働）を検査し、欠落時は分かりやすいメッセージで案内。
- 終了: `q` / Ctrl-C で `mosquitto_sub` 子プロセスも確実に kill（既存 `watch-records.sh` の setsid+cleanup 思想）。

## 8. 配置・起動（既存パターン踏襲）

- `desktop/presence-tools/pipeline-monitor.py` — TUI本体。
- `desktop/presence-tools/pipeline-monitor.sh` — ラッパ（現在SSID表示・凡例・前提チェック）。
- `desktop/launchers/パイプライン監視.desktop` — デスクトップランチャー。
- 接続情報・パスワード取得は既存 `show-recent-records.sh` の方式を再利用
  （非秘密は `profiles.yaml`、Oracleパスワードは `docker exec presence-bridge printenv`）。

## 9. テスト

- 純ロジックを関数分離し pytest で単体テスト:
  - `mqtt_summarize()`: record / heartbeat / status / ack / 不正JSON の各入力 → 期待要約。
  - `child_rollup()`: 複数子・online/offline 判定・最新mk_date。
  - `RecordInboxReader` 集計: received/sent/滞留カウント（一時SQLiteに投入して検証）。
  - `OracleRecentReader` パース: 正常応答 / ora_code エラー / 0件（既存 `_render_recent` テスト方針を流用）。
  - `link_by_event_id()`: 段階フラグの付与。
- curses描画部（`Dashboard`）は分離してテスト対象外（既存 `_render_recent.py` と同方針）。
- lint: `ruff check` / test: `pytest`。

## 10. 未決事項

なし（設計確定）。実装計画で扱う細目:
- `mosquitto-clients` 未導入時の fallback（paho購読 or docker exec mosquitto_sub）。
- リングバッファ件数 N・Oracle表示件数の既定値。
