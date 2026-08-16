# models/ — 子(Zero2 / IMX500 AIカメラ)用モデルstore

親(Pi 5)ローカルに置く、バージョン付きモデル保管庫。`.rpk`/`.zip` 実体は
`.gitignore` 済み（git本流を肥大させないため。バージョン管理の"正"はこのディレクトリ構造）。

```
models/
  <name>/            # signal_tower / object_detection …（model_type と一致）
    <version>/       # 例: 20260422（作成日など任意の一意ラベル）
      network.rpk    # IMX500 にロードするコンパイル済みNN
      labels.txt     # クラスラベル
      packerOut.zip  # IMX500 packer 出力（再パック用）
```

## 使い方

```bash
scripts/deploy-model.sh --list                    # 手元の版を確認
scripts/deploy-model.sh signal_tower 20260422     # 指定版を子へ配布して有効化
scripts/deploy-model.sh signal_tower latest       # 名前順で最後の版
```

配布は子の `~/<name>/` を rsync 同期し、`model_config.json` を当該モデルへ向け、
`picamera.service` を再起動する。失敗時は直前版へ自動ロールバック。

## 新バージョンの追加

1. `models/<name>/<新version>/` を作り、`network.rpk` `labels.txt` `packerOut.zip` を置く。
2. `scripts/deploy-model.sh <name> <新version>` で配布。
3. 問題があれば `scripts/deploy-model.sh <name> <旧version>` で即戻し。
