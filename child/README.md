# child/ — 子ラズパイ(pizero2w)アプリの"正"

子(Zero2 W, RAM 416MB)は **git も docker も無い**素ファイル + systemd 運用。
そのソース・オブ・トゥルースをここに置き、親(Pi 5)から `scripts/deploy-child.sh`
で `~`(flat)へ配布する。

## 中身

| ファイル | 役割 | 配布先 |
|---|---|---|
| `Picamera.py` | IMX500 カメラ推論本体（picamera.service） | `~/Picamera.py` |
| `web_server.py` | 設定/監視用 HTTP UI（web_server.service, :8080） | `~/web_server.py` |
| `child-csv-to-mqtt.py` | CSV → MQTT(`10.42.0.1:1883` topic `presence/record`) | `~/child-csv-to-mqtt.py` |
| `index.html` | web_server の画面 | `~/index.html` |
| `*_config.json` (8) | 各種設定（crop/threshold/recognition/…） | `~/*.json` |
| `systemd/*.service` | picamera / web_server の unit | `/etc/systemd/system/` |
| `docs/` | 要求仕様書 等（配布しない参照資料） | — |

## 配布しない = runtime 状態（子でのみ生成・保持）

`send_target_state.json`（送信済み行カーソル）, `logs/ send_logs/ received_logs/
raw_images/ images/`, `__pycache__/`。**deploy スクリプトはこれらを一切触らない**
（上書きすると送信が巻き戻る / 実データが消えるため）。モデル(`.rpk`)は `models/` 側で管理。

## 使い方

```bash
scripts/deploy-child.sh --dry-run     # 送る差分の確認（変更なし）
scripts/deploy-child.sh               # code + config を配布
scripts/deploy-child.sh --code-only   # コードのみ（config は子の現状維持）
```

配布 → `picamera`/`web_server` 再起動 → ヘルスチェック（サービス active + 安定 +
web_server :8080 応答）→ 失敗なら自動ロールバック（`~/.deploy-backups/<ts>`）。
