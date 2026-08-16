# フリート配備基盤 設計書（親：子 = 1：多）

- 日付: 2026-07-17
- ステータス: ドラフト（レビュー待ち）
- 対象: presence-logger の親ラズパイ／子ラズパイ群のアプリ・MLモデル更新
- 方式: **A — 中央opsに Ansible control node**（既存の実機検証済み on-device 安全処理を流用）

---

## 1. 背景と課題

現状は親（Pi 5）1台・子（Zero 2）1台で、更新は `scripts/deploy-*.sh`（SSH+rsync+systemd＋
ヘルスチェック＋自動ロールバック）で実機検証済み。ただしスクリプトは単一子（`zero2`）決め打ち。

今後、**親が複数**になり、各親配下の**子も複数**に増える（親：子 = 1：多、両方増加）。
1台ずつ手作業で更新するのは非現実的で、失敗時にその拠点の記録が止まるリスクもある。
**中央から・機体別に・安全に・まとめて**更新できる基盤が必要。

## 2. 前提（確定事項）

1. **中央ops → 全親へ到達可**（社内ネット・SSH）。親を踏み台に子まで届く。
2. **拠点・機体ごとに設定/モデルが異なる**（device_id・crop・threshold・使用モデル等）。
3. **リリースは 開発機でビルド → 中央ops へ渡す → 各親へ配布**（親・子は GitHub 非接続／オフライン）。
4. **子は Zero 2（RAM 416MB）**。docker も常駐エージェントも載らない。
5. 子は各親の AP 配下（10.42.0.x）で隔離。子アプリは素 python + systemd（`picamera` / `web_server`）、
   モデルは IMX500 `.rpk`。runtime 状態（`send_target_state.json`・ログ・画像）は端末固有で不可触。

## 3. ゴール / 非ゴール

**ゴール**
- 中央から複数親→複数子へ、アプリ・設定・モデルを**機体別**に配布できる。
- 配布は**1台ずつ（ローリング）**、各台で**ヘルスチェック**、失敗で**自動ロールバック**。
- 親・子が **GitHub 非接続でも**成立する（オフライン配布）。
- 各機体の**稼働版（tag＋モデル）を一覧**できる。

**非ゴール（今回やらない）**
- 子でのコンテナ化 / Kubernetes（K3s）。理由は §9。
- pull 型の自己収束（GitOps-lite）。将来の発展先として §10 に記載のみ。
- 親・子 OS のプロビジョニング（初期セットアップ）自動化。別スコープ。

## 4. 全体アーキテクチャ

```
[開発機(GitHub/build)]──リリース成果物(USB/一時接続)──▶[中央ops = Ansible control node]
                                                        │ inventory + 機体別vars + 成果物保管(=正)
                                                        │ 社内ネット / SSH
                          ┌─────────────────────────────┼─────────────────────────────┐
                        [親1]                          [親2]        ...               [親N]
                    (docker compose / 踏み台・AP)   (踏み台・AP)                    (踏み台・AP)
                    ┌───┼───┐                       ┌───┼───┐
                  子1a 子1b 子1c                    子2a 子2b     ...
                (IMX500・機体別 設定/モデル)
```

| 要素 | 責務 |
|---|---|
| 開発機 | コード編集・`git tag`・IMX500 モデルの pack。リリース成果物を作る。GitHub 接続はここだけ。 |
| 中央ops (control node) | Ansible 本体・リポジトリのミラー・リリース成果物・**inventory（機体別設定/モデル割当）＝正**。全親へ配布、親を踏み台に子へ。 |
| 親 (Pi 5) | 自分の docker compose アプリ。子への **ProxyJump 踏み台**兼 AP。 |
| 子 (Zero 2) | 現状のまま（素 python + systemd + IMX500）。**agent レス**。 |

子は Ansible の `ProxyJump`（control→親→子の2段 SSH）で到達する。子に常駐物を追加しないため
RAM 416MB のままで運用できる。

## 5. リポジトリ / inventory レイアウト

```
fleet/
  inventory/
    hosts.yml                  # 親・子のホスト定義。子は ansible_ssh_common_args で親を ProxyJump
    group_vars/
      all.yml                  # フリート共通の既定
      site_<拠点>.yml          # 拠点（グループ）共通の設定
    host_vars/
      <device_id>.yml          # 機体固有: device_id / crop / threshold / モデル割当 {name,version}
  playbooks/
    deploy-parent.yml
    deploy-child.yml
    deploy-model.yml
    fleet-status.yml
  roles/
    common/                    # backup / healthcheck(/model_status) / rollback（既存bashロジックを移植）
    parent_app/                # local checkout + docker compose up -d --build + health
    child_app/                 # base+overlay を deep-merge→配布 + systemd restart + health + rollback
    child_model/               # 割当モデルを配布 + model_config 差替 + picamera restart + readiness
child/                         # 子アプリの“正”（雛形）。既存を再利用
models/<name>/<version>/       # モデル store（親ローカル/中央ops。.rpk は git 非追跡）。既存を再利用
```

既存の `child/`・`models/`・実機検証済み `scripts/deploy-*.sh` は**破棄せず再利用**する
（scripts は単発・手動フォールバックとして残し、安全処理は role へ移植）。

## 6. 設定・モデルの重ね合わせ（overlay）

機体差を壊さないため、設定は3層 deep-merge で生成し、配布する。

```
base:  child/*.json（リポジトリ雛形）
  + group_vars/site_<拠点>.yml   （拠点共通の上書き）
  + host_vars/<device_id>.yml    （機体固有: device_id, crop, threshold, model 割当）
        │ deep-merge → レンダリング
        ▼
   親経由で 子 ~/*.json へ配布
   ※ send_target_state.json / logs / raw_images 等 runtime状態は配布対象外（不可触）
```

モデルも `host_vars` で割当（例 `model: {name: signal_tower, version: 20260422}`）:
中央ops の `models/<name>/<version>/` → 親経由で 子 `~/<name>/` へ rsync →
`model_config.json` を当該モデルに差替 → `picamera` 再起動 → `/model_status`==ready かつ
`/current_model`==割当名 を確認。

## 7. デプロイの流れ（安全処理）

各機体で共通の手順（`common` role）:
1. **バックアップ**: 上書き対象の現行版を `~/.deploy-backups/<ts>/` に退避（直近 `KEEP_BACKUPS` 世代）。
2. **配布**: rsync（`--delete` は使わない／runtime 状態は除外）。
3. **再起動**: `systemctl restart`（子）／`docker compose up -d --build`（親）。
4. **3層ヘルスチェック**:
   - 層1 即時: サービス `active` ＋ 安定（crash-loop 検出）＋ web_server `:8080` 応答。
   - 層2 readiness: `/model_status`==ready かつ `/current_model`==期待モデル（実トラフィック不要）。
   - 層3 E2E（任意 `VERIFY_E2E`）: `/send_logs_now`→`/send_target_status` の `last_result` 成功確認。
5. **自動ロールバック**: 失敗時は退避版へ復元→再起動→再チェック（Ansible `block`/`rescue`）。

**ローリング**: `serial: 1`（またはグループ%）で1台ずつ。`--limit` で canary 先行、`--check --diff` で
dry-run。層1・層2 はハードゲート、層3 は任意。

## 8. リリースの取り込み（オフライン）

```
開発機:  git tag vX + モデル pack → リリース成果物
         = git bundle（コード）+ models tar（.rpk 群）+ manifest（版・チェックサム）
         │ USB / 一時接続で手渡し
         ▼
中央ops: bundle をミラーへ取り込み → models 更新 → inventory で各グループ/機体の
         desired version（コード tag・モデル version）を指定
         │ Ansible（§7）
         ▼
親→子へ収束（GitHub 不要）
```

親・子は一切 GitHub に依存しない。中央ops が唯一の配布ハブ。

## 9. なぜ K3s（Kubernetes）を使わないか

- **子が非力**（RAM 416MB）: K3s agent を常駐できず、更新の主役たる子で動かない。物理制約で規模非依存。
- **オフライン/分断**: k8s は全ノードが API サーバへ常時接続前提。分断された社内ネットでは制御プレーンが脆い。
- **二重管理**: 親だけ k8s 化しても子は別機構が必須。運用系統が増える。
- **必要十分**: 実需は「機体別配布＋ローリング＋ロールバック＋多数ノードへ SSH」で、これは Ansible の領分。

**使う条件（将来）**: 親が多数・同質・常時接続で複数の弾力コンテナを載せ替える運用になり、かつ
エッジ機がコンテナを動かせる性能になり、自己修復・自動再配置が本当に必要になったとき。

## 10. 可観測性・セキュリティ・移行

- **可観測性**: デプロイ時に各機体へ版マーカー（tag＋モデル＋時刻）を記録。`fleet-status.yml` で
  全機体の稼働版を集約表示。
- **セキュリティ**: SSH 鍵認証、ProxyJump（親経由）。秘密情報は Ansible Vault。中央ops を単一の権限点に。
- **移行**: 現 `zero2` を最初の canary として inventory に取り込み、`deploy-child.sh` 相当を role 化して
  段階適用 → 拠点を順次追加。既存 `child/`・`models/` はそのまま正として利用。

## 11. テスト戦略

- `--check --diff` によるドライラン。
- `--limit <canary>` で現 `zero2` に限定して実適用 → 成功後に対象拡大。
- 層2 readiness の期待モデル検証（誤モデルを不合格にできることは実機で確認済み）。
- ロールバック経路の意図的失敗注入（壊れた設定/モデルを配って自動復帰を確認）。

## 12. ロールアウト段階

1. **P1 単セル role 化**: `common`/`child_app`/`child_model`/`parent_app` role ＋ 1親1子の inventory。現 zero2 で緑。
2. **P2 overlay 導入**: group_vars/host_vars で機体別設定・モデル割当。2台目の子で検証。
3. **P3 複数親**: ProxyJump・ローリング・`fleet-status`。2拠点で検証。
4. **P4 リリース取り込み**: bundle/manifest とオフライン配布フローの整備。

## 13. 未解決事項

- 中央ops の OS（Windows の場合 Ansible は WSL 前提）。
- 子 IP の払い出し（DHCP 動的 vs 静的）と inventory への反映方法。
- モデル pack 手順（IMX500 packer 出力）の標準化と manifest 形式の詳細。
