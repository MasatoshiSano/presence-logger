# 新ハブ ブートストラップ — 実行状況（別セッションからの再開用）

最終更新: 2026-09-09

**プラン**: [`2026-09-09-new-hub-bootstrap.md`](2026-09-09-new-hub-bootstrap.md)（13タスク）
**仕様**: [`../specs/2026-09-09-new-hub-bootstrap-design.md`](../specs/2026-09-09-new-hub-bootstrap-design.md)
**手順書**: [`../../NEW-HUB-SETUP.md`](../../NEW-HUB-SETUP.md) / [`../../sd-clone-pattern-b.md`](../../sd-clone-pattern-b.md)
**実装ブランチ**: `feat/new-hub-bootstrap`（`main` から分岐）

---

## 再開のしかた

```bash
cd /home/pi/projects/presence-logger
git checkout feat/new-hub-bootstrap
.venv/bin/pytest scripts/tests -q          # 現状の全テストが通ることを確認
cat .superpowers/sdd/2026-09-09-new-hub-bootstrap/progress.md   # 詳細台帳（Git管理外・ローカルのみ）
```

下の表で「未着手」の最初のタスクから再開する。各タスクの作業指示は
`.superpowers/sdd/2026-09-09-new-hub-bootstrap/task-N-brief.md` に切り出してある
（無ければプラン本文の該当 `## Task N:` 節をそのまま使う）。

実行方式は superpowers の subagent-driven-development。1タスクごとに
**実装 → レビュー → （指摘があれば）修正 → 再検証** を回す。

---

## 進捗

| Task | 内容 | 状態 | コミット |
|---|---|---|---|
| 1 | `site.env` の読込と検証 | ✅ 完了 | `e6b3d96` `d3cb4ef` `050fd49` |
| 2 | フェーズ振り分け `bootstrap-hub.sh` | ✅ 完了 | `a98edf7` `864f172` |
| 3 | フェーズ10 日本語入力 | 🔧 修正中 | `c21aa9c` + 修正 |
| 4 | フェーズ20 基盤パッケージ | ⬜ 未着手 | |
| 5 | フェーズ30 ドングルドライバ | ⬜ 未着手（**R2 適用**） | |
| 6 | フェーズ40 設定生成 | ⬜ 未着手 | |
| 7 | フェーズ50 子AP | ⬜ 未着手 | |
| 8 | フェーズ60 コンテナ・常駐化 | ⬜ 未着手 | |
| 9 | 既存デスクトップ4本のハブ対応 | ⬜ 未着手（**R3 適用**） | |
| 10 | WiFi切替の Git 取り込み | ⬜ 未着手 | |
| 11 | フェーズ70 デスクトップ配置 | ⬜ 未着手 | |
| 12 | `fleet_ui` の `AP_DEV` 化 | ⬜ 未着手 | |
| 13 | 文書の同期 | ⬜ 未着手 | |

---

## プランに対する決定（実行中に確定したもの）

プラン本文より**こちらが優先する**。

**R1 — 実装ブランチを分けた。** `feat/recent-records-filter` ではなく
`feat/new-hub-bootstrap` を使う。前者には別プランの SDD がレビュー待ちで走っており、
13タスクを混ぜると両方のレビュー単位が壊れるため。

**R2 — Task 5 は `site_env_require` を呼ぶこと。** プラン本文は
`${AP_IF:-wlan1}` だけで済ませているが、それでは `site.env` で `AP_IF` を変えても
フェーズ30 に届かない（仕様 §5 違反）。フェーズ20 の時点で `site.env` は必須なので、
新たな前提は増えない。既定値 `wlan1` は残す。

**R3 — Task 9 の最初に `fake_bin` フィクスチャをルート `conftest.py` へ移すこと。**
Task 9/10 のテストは `tests/desktop/` に置かれるが、`fake_bin` は
`scripts/tests/conftest.py` にしかなく、**このままでは必ず `fixture not found` で落ちる**。
移設後に `scripts/tests` 全体を流して既存テストが緑のままであることを確認する。

**R4 — Task 1/2 のレビュー指摘は、プラン本文が指定したコードへの指摘でも採用した。**
下記「見つかった欠陥」参照。プラン本文に書いたテストコードをそのまま実装させると、
何も守らないテストがそのまま入る。

---

## 見つかった欠陥（同じ失敗を繰り返さないために）

**3タスク中3つで「テストが緑なのに何も守っていない」を踏んだ。**
以降のタスクでは、レビュー指示に「テストが緑でも証拠にならない」と明記し、
**壊れた実装を作ってテストが落ちるかを実証する**こと。

| Task | 欠陥 | 見つけ方 |
|---|---|---|
| 1 | サブネット判定が `/24` 決め打ちで、`/16` 拠点の衝突を見逃す | レビュー |
| 1 | CIDR 不正時に bash 算術エラーが stderr へ漏れる | コントローラ検証 |
| 1 | **先頭ゼロのオクテット（`172.008.13.18`）で `exit=0`＝衝突を見逃す** | 再レビュー |
| 1 | `AP_GW_IP` が一度も検証されていなかった | 同上 |
| 2 | `range` を完全に無視する実装でもテストが通る | レビュー＋再現 |
| 2 | `10#`（8進トラップ回避）を守るテストが無い | レビュー |
| 3 | `XKBLAYOUT` 行が無いと**黙って何もせず成功を返す** | コントローラ検証 |

いずれも「動かない」ではなく「**動いているように見えて壊れている**」形。

---

## 繰り延べた Minor（最終レビューで再判断）

- Task 1: `set -a; source; set +a` が呼び出し元の同名 export を無条件に上書きする
- Task 1: `site.env.example` が非秘密の本番値（工場SSID・Oracle host/service/table）を
  git 履歴に載せる。`config/site/` で既に公開済みの内容と同種で、リポジトリはプライベート
- Task 2: `--list` が空ディレクトリで無出力・exit 0

---

## 完了の定義

仕様 §10 の受入基準14項目を**実機の新ハブ**で確認できること。特に机上では確認できないもの:

1. 再ログイン後に日本語入力できること（記号キーが刻印どおりであることも含む）
2. 子Pi が新ハブの AP に繋ぎ替わり `scripts/fleet-status.sh` が **exit 0**
3. `pipeline-monitor.sh` で同一 event_id を ②MQTT → ③record_inbox → ④Oracle と追える
