---
title: "Raspberry Pi 5 の USB カメラを Docker コンテナ内の MediaPipe に渡す — `--device /dev/video0` + V4L2 + tflite モデル配置"
emoji: "📷"
type: "tech"
topics: ["Docker", "MediaPipe", "Raspberry Pi", "OpenCV", "IoT"]
published: true
category: "HowTo"
date: "2026-04-28"
description: "Raspberry Pi 5 で USB UVC カメラを Docker コンテナ内の MediaPipe ObjectDetector に渡して人検知させる手順。`--device /dev/video0:/dev/video0` のデバイスパススルー、OpenCV ランタイム依存の apt パッケージ、tflite モデルファイルのイメージ内配置、動作確認用の単発スクリプトまで。"
coverImage: "/images/posts/usb-camera-docker-mediapipe-passthrough-cover.jpg"
---

## 概要

Raspberry Pi 5 でエッジ AI 系のアプリを Docker コンテナ化したい。USB カメラの映像を MediaPipe の `EfficientDet-Lite0` で処理し、人物検知 → 何らかのアクション、というよくあるパターン。

ここではホストの USB カメラ (`/dev/video0`) を Docker コンテナ内の Python プロセスに見せる方法と、コンテナ内で MediaPipe + OpenCV を動かすための最小構成を示す。

## こんな人向け

- Raspberry Pi 5 / Pi 4 / Jetson 等のエッジデバイスでカメラ AI を Docker 化したい
- OpenCV の `cv2.VideoCapture(0)` をコンテナ内で動かしたいが「camera open failed」になる
- Python 3.13 環境で MediaPipe wheel が無いので Docker (Python 3.11) で動かしたい
- USB UVC カメラの映像をコンテナに passthrough する正しい方法を探している

## 前提条件

- Raspberry Pi OS Bookworm 64bit（Trixie でも可）
- Docker / Docker Compose v2 インストール済み
- USB UVC カメラ接続済み（`v4l2-ctl --list-devices` で `/dev/video0` 等が見える）
- MediaPipe の `efficientdet_lite0.tflite` ダウンロード済み

## 1. デバイスパススルー

Docker でホストデバイスをコンテナに見せる方法は 2 種類：

### 方法A: `--device` でホワイトリスト（推奨）

```bash
docker run --rm \
  --device /dev/video0:/dev/video0 \
  myimage:latest
```

特定のデバイスファイルだけコンテナ内に作成し、必要な ioctl 権限も付与される。**最小権限の原則**に合致。

`docker-compose.yml` での書き方：

```yaml
services:
  detector:
    devices:
      - "/dev/video0:/dev/video0"
```

### 方法B: `--privileged`（非推奨）

```bash
docker run --rm --privileged ...
```

すべてのホストデバイスに無制限アクセス可能。デバッグ時のみ。production 投入禁止。

## 2. Dockerfile 最小構成

`python:3.11-slim-bookworm` ベースで OpenCV + MediaPipe を動かすには apt で 2 つのライブラリが必要：

```dockerfile
FROM python:3.11-slim-bookworm

# OpenCV ランタイム (libgl1) と GLib (libglib2.0-0) が無いと
# `import cv2` 自体が ImportError で落ちる
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY models/efficientdet_lite0.tflite /opt/models/efficientdet_lite0.tflite

CMD ["python", "-m", "src.main"]
```

`requirements.txt` に必要な 2 行：

```text
opencv-python-headless==4.11.0.86
mediapipe==0.10.18
```

`opencv-python-headless` は GUI 機能（`cv2.imshow` など）を含まない軽量版。コンテナでは GUI 不要なのでこちらを使う。

### モデルファイルの配置

`COPY models/efficientdet_lite0.tflite /opt/models/...` でビルド時にイメージへ焼き込む。事前にホスト側でダウンロード：

```bash
mkdir -p models
curl -fsSL -o models/efficientdet_lite0.tflite \
  https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float32/latest/efficientdet_lite0.tflite
```

ファイルサイズ約 13 MB、git には含めない（`.gitignore` で `*.tflite` を無視）。

## 3. アプリコード（コンテナ内）

カメラ open + MediaPipe 推論の最小例：

```python
# src/main.py
import cv2
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
import mediapipe as mp

MODEL = "/opt/models/efficientdet_lite0.tflite"

# カメラ open
cap = cv2.VideoCapture(0)        # /dev/video0 は --device で渡されている
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

# MediaPipe Object Detector
opts = mp_vision.ObjectDetectorOptions(
    base_options=mp_python.BaseOptions(model_asset_path=MODEL),
    score_threshold=0.5,
    category_allowlist=["person"],     # 人検知だけ欲しい
    running_mode=mp_vision.RunningMode.IMAGE,
)
detector = mp_vision.ObjectDetector.create_from_options(opts)

# warmup
for _ in range(5):
    cap.read()

while True:
    ok, frame_bgr = cap.read()
    if not ok:
        continue

    # MediaPipe は RGB 期待
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

    result = detector.detect(mp_image)
    for det in result.detections:
        for cat in det.categories:
            if cat.category_name == "person" and cat.score >= 0.5:
                print(f"person detected: score={cat.score:.2f}, "
                      f"bbox={det.bounding_box}")
```

## 4. 動作確認

```bash
docker build -t cam-test .
docker run --rm \
  --device /dev/video0:/dev/video0 \
  cam-test
```

カメラに人を映すと：

```
person detected: score=0.72, bbox=BoundingBox(origin_x=120, origin_y=80, width=320, height=400)
```

## 5. パフォーマンス参考値（Pi 5、640x480、EfficientDet-Lite0）

| 指標 | 実測 |
|---|---|
| 推論レイテンシ p50 | 120 ms |
| 推論レイテンシ p95 | 150 ms |
| 持続可能 FPS | 約 6（推論専有時） |
| アプリ実装 FPS（推論+他処理） | 1.5〜2.0 が現実的 |
| CPU 使用率（4コア中） | 約 25%（1コア相当を推論で消費） |
| メモリ | 約 200 MB（モデル + ランタイム） |

人の在/不在検知だけなら 1〜2 FPS で十分。`time.sleep(0.5)` で間隔を空けて CPU 温度を抑える運用が現実的。

## ポイント・注意点

### Pi 公式カメラ (libcamera) の場合

Pi カメラモジュールは `/dev/video0` ではなく `/dev/video20+` 系（ISP デバイス）に出る。USB UVC カメラのほうが「`/dev/video0` を `cv2.VideoCapture(0)` で開く」普通の構成で動かしやすい。

確認：

```bash
v4l2-ctl --list-devices
```

USB カメラなら `USB 2.0 Camera: ... /dev/video0`、Pi モジュールなら `pispbe ... /dev/video20` のように出る。

### 複数 USB カメラがある場合

`--device` を複数指定し、コード側で `cv2.VideoCapture(0)`、`cv2.VideoCapture(1)` で開く：

```yaml
devices:
  - "/dev/video0:/dev/video0"
  - "/dev/video2:/dev/video2"   # 2台目（同じ USB UVC ドライバ）
```

### USB カメラ抜けた時の挙動

`cap.read()` が `ok=False` を返し続ける。production では「連続 N 回失敗で異常状態に遷移、上位ロジックへ通知」を入れるべき。

```python
consecutive_fail = 0
while True:
    ok, frame = cap.read()
    if not ok:
        consecutive_fail += 1
        if consecutive_fail >= 10:
            # camera lost ... 上位ロジックへ通知
            break
        continue
    consecutive_fail = 0
    # ...
```

### `Error in cpuinfo: prctl(PR_SVE_GET_VL) failed`

Pi 5 (ARM Cortex-A76) で MediaPipe / TensorFlow Lite を読むと出る WARN。SVE（Scalable Vector Extension）対応 CPU か検出するためのもので、Pi 5 は非対応。**動作には影響なし、無視可能**。

### `INFO: Created TensorFlow Lite XNNPACK delegate for CPU.`

これは正常。XNNPACK（Google の最適化された CPU バックエンド）が ARM NEON 命令で推論を高速化している証拠。

## まとめ

- USB カメラを Docker コンテナに渡すには `--device /dev/video0:/dev/video0`（compose なら `devices:` セクション）
- Slim base + `libgl1` + `libglib2.0-0` で OpenCV が動く最小環境になる
- MediaPipe の tflite モデルは Dockerfile の `COPY` でイメージ内に焼く
- Pi 5 + EfficientDet-Lite0 の現実的な実用 FPS は 1〜2、推論レイテンシ約 120ms
- `--privileged` は使わない（最小権限の `--device` が production 標準）

## バイブコーディングで実装する

この記事の内容を AI コーディングアシスタントに実装させるためのプロンプト：

> Raspberry Pi 5 で USB UVC カメラ (`/dev/video0`) を使う MediaPipe の Object Detector アプリを Docker コンテナ化する。
>
> 構成：
> 1. `Dockerfile` は `python:3.11-slim-bookworm` ベース。`apt install libgl1 libglib2.0-0` を追加（OpenCV ランタイム依存）
> 2. `requirements.txt` に `opencv-python-headless` と `mediapipe==0.10.18` を入れる
> 3. `efficientdet_lite0.tflite` を `models/` に配置し Dockerfile の `COPY models/efficientdet_lite0.tflite /opt/models/...` で焼く
> 4. アプリコードは `cv2.VideoCapture(0)` で open、`cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)` で MediaPipe 形式に変換
> 5. `MediaPipe ObjectDetectorOptions` の `category_allowlist=["person"]` で人だけ拾う
> 6. `docker-compose.yml` では `devices: ["/dev/video0:/dev/video0"]` を指定（`--privileged` は使わない）
> 7. 連続読み取り失敗 10 回で「camera lost」と判定し上位ロジックへ通知

### AIに指示するときのポイント

- AI は OpenCV を入れる時 `opencv-python`（GUI 込み）を選びがち。コンテナでは **`opencv-python-headless`** にすると依存が減る
- `libgl1` `libglib2.0-0` を忘れると `import cv2` で `ImportError`。明示的に Dockerfile に入れるよう指示する
- MediaPipe 入力は **RGB** だが、OpenCV のデフォルトは BGR。**`cv2.cvtColor(..., COLOR_BGR2RGB)` の変換を必ず入れる**
- `category_allowlist=["person"]` を付けないと 80 クラス全部評価して無駄に重い
- Compose の `devices:` は `volumes:` ではなく専用セクション。AI が間違えやすい
