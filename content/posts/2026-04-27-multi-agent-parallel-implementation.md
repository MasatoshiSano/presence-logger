---
title: "Claude Code のサブエージェントで 35 タスクの実装プランを並列処理する — サービス境界による分割と検証ゲート設計"
emoji: "🤖"
type: "tech"
topics: ["Claude Code", "AI", "Multi-Agent", "DevOps", "Subagent"]
published: true
category: "DevOps"
date: "2026-04-27"
description: "6122 行の実装プラン (35 タスク) を Claude Code のサブエージェント機能で並列実装した。サービス境界（detector / bridge）でエージェント分割、Phase 完了ごとの検証ゲート、各エージェントへの「他エージェントが触る場所」明示。実体験から得た coordination の勘所。"
coverImage: "/images/posts/multi-agent-parallel-implementation-cover.jpg"
---

## やりたかったこと

Raspberry Pi 5 上で動かす常駐アプリを 0 から作った。仕様書（1003 行）→ 実装プラン（6122 行、35 タスク・170+ ステップ）まで Claude Code で生成し、続く実装フェーズで **サブエージェントを並列に走らせて短時間で完走させたい**。

タスクは 5 Phase（Phase 0: 初期化 / Phase 1: 共通基盤 / Phase 2: detector / Phase 3: bridge / Phase 4: 統合）。Phase 2 と Phase 3 は detector と bridge の独立サービスなので並列化のチャンス。

## こんな人向け

- Claude Code を「単発の補完」ではなく「**多段プロジェクトのオーケストレータ**」として使いたい
- サブエージェント（バックグラウンド Agent タスク）の並列実行を試したことがあるが、ファイル衝突や同期ミスでうまくいかなかった
- 大きな実装プランを「丸ごと 1 エージェントに渡す」のは context が膨れて失敗するという感覚を持っている
- AI コーディングで「並列化の単位」をどこで切るかの判断基準を探している

## ❌ 最初の指示 / アプローチ

最初は「全 35 タスクを 1 つのサブエージェントに渡す」を考えた：

> "Implement the entire plan at `docs/superpowers/plans/...` from Phase 0 through Phase 4. Run all tests, commit after each task."

これは **絶対やってはいけない**。理由：

- 35 タスクの中で 1 つでも失敗すると、エージェントの context にエラー履歴と修正試行が積み上がり、judgment quality が劣化する
- 検証チェックポイントが「全完了後」しかないので、Phase 1 でズレた前提が Phase 4 まで波及する
- 1 セッションで 100,000 トークン超になりうる
- 並列性をまったく使えない（プランの中に detector/bridge の独立部分があるのに sequential になる）

## 何が起きたか

実際に**最初に試した別の問題（Phase 0 を 1 エージェントに任せた）**で、こういう状況に直面した：

```
Phase 0 Step 5: pip install で mediapipe が aarch64+py3.13 wheel 無しで失敗
→ pip がアトミック失敗で他のパッケージも入らない
→ エージェントが「停止して報告」を選択
→ ユーザー側で対処判断が必要に
```

Phase 0 ですら、環境依存のエッジケースで止まる。これを **35 タスク 1 セッションでやろうとすれば、何度もこういう中断**が起きてエージェントの context が崩壊する。

### なぜこうなるのか

サブエージェントは strong だが **「stop and report」が安全策**として組まれている。1 つのエージェントに長い責務を持たせると：

- 中断頻度が高くなる
- 中断のたびにオーケストレータ（メイン会話）が判断 → 続きを指示
- その「続き」が新しい self-contained プロンプトでないと意味が伝わらない（SendMessage 系ツールが無い環境では特に）

つまり **長いプランを 1 エージェントに任せても結局は短いセッションの連鎖**になる。だったら最初から **設計上 sub-task に分割**しておくほうがクリーン。

## ✅ 解決した指示 / アプローチ

5 Phase + 並列化を意識した **Wave** 構成にした：

```
Wave 1: Phase 0 (sequential)        — 1 エージェント
Wave 2: Phase 1                     — 2 エージェント (1A: detector, 1B: bridge) 並列
Wave 3: Phase 2 + Phase 3          — 2 エージェント並列
Wave 4: Phase 4 (sequential)        — 1 エージェント
Wave 5: 実 DB スモークテスト        — オーケストレータが直接実行
```

### 並列化の単位は「サービス境界」

detector と bridge は MQTT トピック契約だけで疎結合。コードの **物理的な共有が無い** ので、別ファイル群を 2 エージェントが同時に書ける。

```
services/
├── detector/      ← Agent A の担当
│   ├── src/
│   └── tests/
└── bridge/        ← Agent B の担当
    ├── src/
    └── tests/
```

各エージェントへのプロンプトに **「触ってはいけない場所」を明示**する：

> Phase 2 (detector) を実装してください。**`services/bridge/` 以下は別エージェントが並列で触っているので、絶対に触らないでください**。`services/detector/` と venv のみが許可された変更範囲です。

これをやらないと **MQTT のメッセージスキーマや `pyproject.toml` のような共有ファイル**を両方が書き換えて衝突する。

### 各エージェントへのプロンプト構造

すべてのエージェントに同じ枠組みで指示：

1. **背景**：何のプロジェクトか、どこまで進んでいるか（context）
2. **環境**：Python のバージョン、pyproject.toml の設定、利用可能な依存
3. **scope**：担当するタスク番号と各タスクの簡潔な要約
4. **coordination**：他エージェントの担当範囲、触らない場所
5. **rules**：プランに従うこと、各 Step の Expected output を verify すること、commit message の HEREDOC フォーマット
6. **reporting**：完了時に何を報告するか（git log、pytest 結果、deviation の有無）

### Phase 完了ごとの検証ゲート

各 Wave 完了後、オーケストレータが：

```bash
.venv/bin/pytest -q | tail -3   # 全テスト pass か
.venv/bin/ruff check .          # lint clean か
git log --oneline | head -10    # 期待通りの commit か
```

を直接実行して **次の Wave に進むかどうかを判断**。Wave 内のエージェント失敗を Wave 間に持ち越さない。

### 実際の coordination 失敗とリカバリ

Wave 2 で実際に起きた事例：

> Phase 1B (bridge) のエージェントが、Phase 1A (detector) の `logging_setup.py` を `cp` する手順をプランで指示されていた。しかし Phase 1A エージェントが書いた最初の版にバグがあり、Phase 1A エージェントが修正している最中に Phase 1B エージェントがコピーしてしまった。後で Phase 1A の修正版をコピーし直して解決。

この経験から得た原則：

- **共有ファイル（コピー元）は片方のエージェントだけが書く時間を確保する**
- **コピー操作は upstream の test pass を待ってから実行**
- 実装プランの中に `# wait for detector test to pass` のような同期ヒントを入れる

## 比較まとめ

| | ❌ 最初 (1 エージェント全タスク) | ✅ 改善後 (5 Wave 構成) |
|---|---|---|
| エージェント数 | 1 | 5（直列+並列） |
| 並列化 | なし | Wave 2 / Wave 3 で 2 並列 |
| 検証ゲート | 全完了後の 1 回 | 各 Wave 完了後に毎回 |
| エージェント context | 35 タスク分の試行錯誤が累積 | 各エージェント 2-11 タスクのみ |
| 中断耐性 | 中断のたびに巻き戻し | Wave 単位で回復可能 |
| ファイル衝突リスク | n/a (sequential) | サービス境界で分割すれば最小化 |
| 完走時間 | 推定 60-90 分 | 実測 35 分 |

## バイブコーディングで実装する

この記事の内容を踏まえた、Claude Code でサブエージェント並列実装を組み立てるためのプロンプト：

> 大きな実装プラン（数十タスク以上）を Claude Code のサブエージェントで並列実装したい。
>
> 設計原則：
> 1. **タスクをサービス境界・モジュール境界で物理的に分離**できる単位に分ける（共有ファイルが無い、または片方しか書かない状態にする）
> 2. **Wave 構成**で進める：
>    - Wave 1：プロジェクト初期化 (sequential, 1 エージェント)
>    - Wave 2：共通基盤 (並列可能なら並列)
>    - Wave 3：独立サービス本体 (並列、サービスごとに 1 エージェント)
>    - Wave 4：統合・デプロイ (sequential)
> 3. **各エージェントへのプロンプトに必ず含める要素**：
>    - プロジェクト背景・現在の状態（commit ハッシュや test 数）
>    - 環境（Python バージョン、依存ライブラリ）
>    - 担当 scope（タスク番号と簡潔な要約）
>    - **触ってはいけない場所**（並列エージェントの担当範囲）
>    - プラン参照パス
>    - commit message フォーマット（HEREDOC、Co-Authored-By trailer）
>    - 完了時の報告内容（git log、pytest 結果、deviation）
> 4. **Wave 完了ごとに検証**：オーケストレータが `pytest`、`ruff check`、`git log` を直接実行して次に進むか判断
> 5. **共有ファイルのコピー操作**は upstream の test pass を待つよう、プラン本文にヒントを入れる
> 6. **長い背景タスクは `run_in_background: true` で並列化**し、完了通知を待つ

### AI に指示するときのポイント

- 「全部やって」と丸投げしない。**Wave 構成を AI に提示して同意を取る**ところから始める
- 各サブエージェントは **fresh context で起動する**（SendMessage で続けるより、self-contained プロンプトを毎回新規発行するほうが管理しやすい）
- 「触ってはいけない場所」の明示を忘れると **共有ファイル衝突**が起きる。サービス境界で `services/<name>/` を担当領域として明示
- 各タスクの Expected output（test 数、commit 数）を **数字で書く**と、エージェントが「想定外の結果」を検出しやすい
- エージェントの完了報告には deviation の有無を必ず聞く。ruff の lint fix や型変換などの **小さな修正は受け入れる**前提で

## おまけ：実測タイミング

```
Phase 0 (sequential, 1 agent):     ~ 5 分（pip install 環境問題で 1 回中断あり）
Phase 1 (parallel, 2 agents):      ~ 7 分（並列、長い方の time）
Phase 2 + 3 (parallel, 2 agents):  ~12 分（並列、長い方）
Phase 4 (sequential, 1 agent):     ~ 7 分
実 DB スモークテスト:                ~ 4 分
─────────────────────────────────
合計:                                 ~35 分
```

並列なしの単純計算：5 + 14 + 24 + 7 + 4 = **54 分** → 並列化で **約 35% 短縮**。
それ以上に重要なのは「**1 エージェントに長い文脈を持たせない**」ことで quality が安定したこと。
