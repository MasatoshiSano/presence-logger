---
title: "Raspberry Pi 5 (Python 3.13) で MediaPipe wheel が無いときの逃げ方 — requirements を runtime と test で分離する"
emoji: "🐍"
type: "tech"
topics: ["MediaPipe", "Raspberry Pi", "Python", "Docker", "IoT"]
published: true
category: "DevOps"
date: "2026-04-27"
description: "2026年4月時点で MediaPipe には Python 3.13 / aarch64 の wheel が存在しない。ホスト venv にインストールしようとすると pip がアトミックに失敗してテストすら走らせられない。Docker と venv で要件を分離して回避する。"
coverImage: "/images/posts/mediapipe-rpi5-py313-no-wheel-cover.jpg"
---

## やりたかったこと

Raspberry Pi 5（Bookworm 64-bit）で USB カメラから人を検知する常駐アプリを作っていた。推論には MediaPipe Tasks の Object Detector (`EfficientDet-Lite0`) を使う計画。production は Docker（`python:3.11-slim-bookworm` ベース）、開発は Pi のホスト上で `pytest` を回す、という構成。

## こんな人向け

- Raspberry Pi 5 に Python 3.12 / 3.13 が入っていて MediaPipe を使いたい
- `pip install mediapipe` が `Could not find a version that satisfies the requirement mediapipe` で失敗する
- aarch64 / arm64 のホストで AI/ML 系ライブラリの wheel が無くて困っている
- ローカル venv ではテストだけ動けば十分で、推論本体は Docker 内で動かしたい

## ❌ 最初の指示 / アプローチ

何の疑いもなく requirements に MediaPipe を入れた：

```text
# services/detector/requirements.txt
opencv-python-headless==4.10.0.84
mediapipe==0.10.18
paho-mqtt==2.1.0
pyyaml==6.0.2
python-json-logger==2.0.7
```

そして開発用 venv に一括 install：

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements-dev.txt -r services/detector/requirements.txt -r services/bridge/requirements.txt
```

## 何が起きたか

```
ERROR: Could not find a version that satisfies the requirement mediapipe==0.10.18
       (from versions: none)
ERROR: No matching distribution found for mediapipe==0.10.18
```

`pip install mediapipe`（バージョン指定なし）でも結果は同じ。pip は **トランザクション全体をアトミックに扱う**ので、1つのパッケージで失敗すると **他のパッケージも一切インストールされない**。pytest すら起動できない状態になる。

```bash
.venv/bin/pip index versions mediapipe
# ERROR: No matching distribution found for mediapipe
```

### なぜこうなるのか

- 環境は `aarch64` (ARM64) + Python `3.13.5`
- 2026年4月時点で MediaPipe の PyPI 配布は **Python 3.12 までの wheel が最終**。3.13 用の wheel はまだ無い
- aarch64 の wheel は元から限定的で、x86_64 / arm64 macOS / arm64 Linux のうち arm64 Linux は最新版で対応が遅れがち
- ソースから build する選択肢もあるが、Bazel + protobuf + glog などの巨大依存があり、Pi 上では現実的に build できない

要するに：**MediaPipe は Pi 5 のホスト Python に直接入れる手段が無い**。

唯一の現実解は Docker。`python:3.11-slim-bookworm` ベースなら MediaPipe の wheel が普通に入る（cp311-cp311-linux_aarch64）。

ただし、それだと「ローカル venv で `pytest` 回す」フローが破綻する。アプリのコードに `import mediapipe` が含まれていれば、テスト時に ImportError で即死。

## ✅ 解決した指示 / アプローチ

requirements を **2 ファイルに分割** し、ローカル venv にはテスト互換のものだけを入れる。

### 1. requirements を runtime と test で分ける

```text
# services/detector/requirements.txt （venv にも入る、ローカルテスト用）
opencv-python-headless==4.11.0.86
paho-mqtt==2.1.0
pyyaml==6.0.2
python-json-logger==2.0.7
```

```text
# services/detector/requirements-runtime.txt （Docker でのみ install）
# Runtime-only dependencies installed inside the Docker image.
# Kept out of the local dev venv because mediapipe lacks aarch64 wheels for
# Python 3.13 (host) — the Dockerfile pins a python:3.11 base where they exist.
mediapipe==0.10.18
```

### 2. Dockerfile では両方インストール

```dockerfile
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-runtime.txt

COPY src/ ./services/detector/src/
COPY models/efficientdet_lite0.tflite /opt/models/efficientdet_lite0.tflite

CMD ["python", "-m", "services.detector.src.main"]
```

### 3. ライブラリ import を遅延させる

ホスト上で `pytest` がモジュールを import しても落ちないように、`mediapipe` の import は推論コンストラクタ内に閉じ込める：

```python
# services/detector/src/inference.py
class PersonDetector:
    def __init__(self, *, backend, score_threshold, target_category):
        self._backend = backend          # テストではフェイクを注入
        self._threshold = score_threshold
        self._target = target_category

    @classmethod
    def from_model_path(cls, *, model_path, score_threshold, target_category):
        # 本番起動時にだけ呼ばれる。ここで初めて mediapipe を import。
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        opts = mp_vision.ObjectDetectorOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            score_threshold=score_threshold,
            category_allowlist=[target_category],
            running_mode=mp_vision.RunningMode.IMAGE,
        )
        backend = mp_vision.ObjectDetector.create_from_options(opts)
        return cls(backend=backend, score_threshold=score_threshold, target_category=target_category)
```

ユニットテスト側は `from_model_path` を使わず、フェイクの `_DetectBackend` を直接渡す：

```python
class _FakeBackend:
    def __init__(self, results): self._results, self.calls = results, 0
    def detect(self, mp_image):
        r = self._results[self.calls]; self.calls += 1; return r

def test_returns_has_person_true_when_score_meets_threshold():
    backend = _FakeBackend([_FakeMpResult([_FakeDetection([_FakeCategory("person", 0.7)])])])
    det = PersonDetector(backend=backend, score_threshold=0.5, target_category="person")
    r = det.detect(_frame())
    assert r.has_person is True
```

これで `pytest` は ホストで mediapipe 無しでも全部通り、Docker 内では本物の MediaPipe で動く。

### なぜこれで解決するのか

- `pip install -r requirements.txt` の **依存集合からwheel無しのパッケージを除く**ことで、トランザクション失敗を回避
- ライブラリの **import を関数スコープに閉じ込める**ことで、import-time error を遅延させ、テストフェーズでは触らずに済む
- Docker と venv で **同じ requirements.txt は共有しつつ、追加の runtime 専用 requirements で分離**することで、共通部分の管理コストは増えない

## 比較まとめ

| | ❌ 最初 | ✅ 改善後 |
|---|---------|-----------|
| requirements の数 | 1 ファイル | runtime / test の 2 ファイル |
| ローカル venv install | mediapipe で即失敗、何も入らない | テスト依存だけ全部入る |
| Docker build | 同じ requirements、mediapipe 入る | runtime ファイルも追加で読む |
| `pytest` 実行 | 起動すらしない | 134 件パス |
| import 戦略 | `import mediapipe` をモジュールトップ | `from_model_path` 内に遅延 import |

## バイブコーディングで実装する

この記事の内容を AI コーディングアシスタントに実装させるためのプロンプト：

> Python アプリで MediaPipe を使うが、開発環境は Raspberry Pi 5（aarch64、Python 3.13）で MediaPipe の wheel が存在しない。本番は Docker（`python:3.11-slim-bookworm`）で動かす。
>
> 要件：
> 1. requirements を 2 ファイルに分ける：
>    - `requirements.txt`：MediaPipe を**含めない**。opencv-python-headless 等のテスト互換ライブラリのみ。ローカル venv にも入る
>    - `requirements-runtime.txt`：MediaPipe を含める。Docker でのみ install
> 2. Dockerfile では `RUN pip install -r requirements.txt -r requirements-runtime.txt` で両方入れる
> 3. アプリコードでは `import mediapipe` を**モジュール先頭で書かない**。`PersonDetector.from_model_path()` のような factory メソッド内で遅延 import する。テスト時は backend を引数注入できるようコンストラクタを別に用意（`__init__(self, backend=..., ...)`）
> 4. ユニットテストはフェイク backend で書き、本物の MediaPipe を import せず通せるようにする

### AIに指示するときのポイント

- AI は「requirements は 1 ファイル」が普通だと思っているので、**runtime/test で分ける理由（環境依存）を必ず説明**する
- `import` を遅延させる手段は AI には不自然に映ることが多い。**「テスト時に mediapipe を import したくない」と目的を明示**すれば適切に factory パターンを使う
- AI は `from_model_path` の中で `import mediapipe.tasks as mp_python` のような誤った import を書きがち。**正しいパス `from mediapipe.tasks import python as mp_python` を例示**する
- aarch64 / arm64 環境であることを明示しないと、AI は x86_64 を仮定してインストール手順を出す
