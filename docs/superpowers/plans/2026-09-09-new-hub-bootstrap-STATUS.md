# 新ハブ ブートストラップ — 実行状況（別セッションからの再開用）

最終更新: 2026-09-09

**プラン**: [`2026-09-09-new-hub-bootstrap.md`](2026-09-09-new-hub-bootstrap.md)（13タスク）
**仕様**: [`../specs/2026-09-09-new-hub-bootstrap-design.md`](../specs/2026-09-09-new-hub-bootstrap-design.md)
**手順書**: [`../../NEW-HUB-SETUP.md`](../../NEW-HUB-SETUP.md) / [`../../child-migration.md`](../../child-migration.md) / [`../../sd-clone-pattern-b.md`](../../sd-clone-pattern-b.md)
**実装ブランチ**: `feat/new-hub-bootstrap`（`main` から分岐）

---

## 再開のしかた

```bash
cd /home/pi/projects/presence-logger
git checkout feat/new-hub-bootstrap
.venv/bin/pytest scripts/tests -q          # 現状の全テストが通ることを確認
cat .superpowers/sdd/2026-09-09-new-hub-bootstrap/progress.md   # 詳細台帳（Git管理外・ローカルのみ）
```

13タスクは完了。以降は仕様 §10 の受入基準を実機の新ハブで確認する。

---

## 進捗

| Task | 内容 | 状態 | コミット |
|---|---|---|---|
| 1 | `site.env` の読込と検証 | ✅ 完了 | `e6b3d96` `d3cb4ef` `050fd49` |
| 2 | フェーズ振り分け `bootstrap-hub.sh` | ✅ 完了 | `a98edf7` `864f172` |
| 3 | フェーズ10 日本語入力 | ✅ 完了 | `c21aa9c` `6f0fa2c` `a50c831` |
| 4 | フェーズ20 基盤パッケージ | ✅ 完了 | `09876da` |
| 5 | フェーズ30 ドングルドライバ | ✅ 完了 | `4896862` `b2ff9fd` |
| 6 | フェーズ40 設定生成 | ✅ 完了 | `77469eb` `8661a6b` |
| 7 | フェーズ50 子AP | ✅ 完了 | `7912beb` `ce04cf2` |
| 8 | フェーズ60 コンテナ・常駐化 | ✅ 完了 | `5ae97d3` `a46da70` |
| 9 | 既存デスクトップ4本のハブ対応 | ✅ 完了 | `e8ac8ec` `9b9c4b0` |
| 10 | WiFi切替の Git 取り込み | ✅ 完了 | `077607b` |
| 11 | フェーズ70 デスクトップ配置 | ✅ 完了 | `0518357` |
| 12 | `fleet_ui` の `AP_DEV` 化 | ✅ 完了 | `bb7f341` |
| 13 | 文書の同期 | ✅ 完了 | 本コミット |

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

**R5 — Task 3 は仕様の `XKBMODEL=pc105` を書く。** プランの関数契約は XKBLAYOUT のみだが、
仕様 §4.1 が両方を要求する。`set -e` は入れず、成功バナーの前に検査する。

**R6 — Task 4 にハイフン付きホスト名のテストを1件足す。** プラン指定テストは
`raspberrypi5`→`presence-hub-2` のみで、`\b` が `pizero2w-2` を壊す既知事故を検出しない。

**R7 — Task 7 の SSID 重複検出は完全一致。** プラン指定テストは無関係な別名のみで、
`presence-hub-guest` を `presence-hub` と誤認する `grep -qF`（`-x` なし）を落とさない。

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
| 3 | 仕様の `XKBMODEL=pc105` を書かず、欠落・非 pc105 を見逃す | レビュー |
| 3 | `im-config` 失敗後も成功バナーを出す（plan-mandated） | レビュー |

いずれも「動かない」ではなく「**動いているように見えて壊れている**」形。

---

## 繰り延べた Minor（最終レビューで再判断）

- Task 1: `set -a; source; set +a` が呼び出し元の同名 export を無条件に上書きする
- Task 1: `site.env.example` が非秘密の本番値（工場SSID・Oracle host/service/table）を
  git 履歴に載せる。`config/site/` で既に公開済みの内容と同種で、リポジトリはプライベート
- Task 2: `--list` が空ディレクトリで無出力・exit 0
- Task 9: hub-skip テストの vacuous pass（check=False）/ resolve_sta 未テスト /
  main 配線未テスト / 切断ヒントの文言 / HOME_SSID 未テスト / source 時の set -u 漏洩
- Task 10: パーサは行頭 `#` のみ（仕様の「`#` 以降」とは不一致。5トークン行を前提）
- Task 11: dash テストが return 0 を固定しない / 負例の vacuous pass / main 未テスト /
  未使用 `local rec` / source 時 set -u 漏洩

---

## 完了の定義

仕様 §10 の受入基準14項目を**実機の新ハブ**で確認できること。特に机上では確認できないもの:

1. 再ログイン後に日本語入力できること（記号キーが刻印どおりであることも含む）
2. 子Pi が新ハブの AP に繋ぎ替わり `scripts/fleet-status.sh` が **exit 0**
3. `pipeline-monitor.sh` で同一 event_id を ②MQTT → ③record_inbox → ④Oracle と追える
