---
title: "pytest の `import file mismatch` を `--import-mode=importlib` で解決する — モノレポ同名 test ファイル衝突"
emoji: "🧪"
type: "tech"
topics: ["pytest", "Python", "Testing", "Monorepo"]
published: true
category: "Debugging"
date: "2026-04-27"
description: "サービスごとに `tests/test_config.py` のような同名ファイルがあるモノレポ構成で pytest を回すと、import 経路の衝突で collection エラー。`__init__.py` を増やす対症療法より、`--import-mode=importlib` への切替が圧倒的に楽。"
coverImage: "/images/posts/pytest-importlib-mode-monorepo-cover.jpg"
---

## 概要

複数サービスを同じリポジトリに置く構成で、各サービスが自分の `tests/` ディレクトリを持ち、たまたま同じファイル名（`test_config.py`、`test_logging_setup.py` など）を含むと、pytest の **デフォルト import モード**ではモジュール名が衝突して collection エラーになる。

`--import-mode=importlib` に切り替えれば一発で直る。原因と対策を整理する。

## こんな人向け

- モノレポで複数サービスを管理し、それぞれ `tests/` を持っている
- pytest を root から走らせると `import file mismatch` エラーが出る
- `__init__.py` を足したり消したりして直そうとして泥沼にハマっている
- conftest.py や sys.path をいじる方法を調べているが疲れた

## 前提条件

- pytest 6 以降（5 以降で `importlib` モード対応、推奨は 7 以降）
- Python 3.5 以降

## 起きる現象

ディレクトリ構成：

```
my-monorepo/
├── pyproject.toml
├── conftest.py
├── services/
│   ├── detector/
│   │   ├── src/
│   │   │   └── config.py
│   │   └── tests/
│   │       ├── __init__.py
│   │       └── test_config.py            ← 同名！
│   └── bridge/
│       ├── src/
│       │   └── config.py
│       └── tests/
│           ├── __init__.py
│           └── test_config.py            ← 同名！
└── tests/integration/
    └── test_end_to_end.py
```

`pyproject.toml`：

```toml
[tool.pytest.ini_options]
testpaths = ["services/detector/tests", "services/bridge/tests", "tests/integration"]
python_files = "test_*.py"
addopts = "-v --tb=short"
```

`pytest` を実行すると：

```
==================================== ERRORS ====================================
____________ ERROR collecting services/bridge/tests/test_config.py _____________
import file mismatch:
imported module 'tests.test_config' has this __file__ attribute:
  /home/pi/projects/presence-logger/services/detector/tests/test_config.py
which is not the same as the test file we want to collect:
  /home/pi/projects/presence-logger/services/bridge/tests/test_config.py
HINT: remove __pycache__ / .pyc files and/or use a unique basename for your test file modules
```

`__pycache__` を消しても直らない。ファイル名を変えるのも、サービス間で test 名規約を揃えたいので避けたい。

### なぜこうなるのか

pytest のデフォルト import モードは `prepend`（または `append`）。これは：

1. テストファイルがある最上位ディレクトリ（最も浅い `__init__.py` の親）を `sys.path` に追加
2. テストファイルを `package.module` として import

両方の `tests/` に `__init__.py` があると、それぞれが `tests` というパッケージのルートと認識される。すると：

- 最初に collect された `services/detector/tests/test_config.py` → `tests.test_config` として import 成功
- 次に `services/bridge/tests/test_config.py` を collect → 同じ `tests.test_config` という名前でロードしようとするが、`sys.modules` 内にあるのは別パスのファイル → `import file mismatch`

要するに **「同じモジュール名」が複数ファイルに対応してしまう**のが根本原因。

## 解決策

### `--import-mode=importlib` に切替

`pyproject.toml`：

```toml
[tool.pytest.ini_options]
testpaths = ["services/detector/tests", "services/bridge/tests", "tests/integration"]
python_files = "test_*.py"
addopts = "-v --tb=short --import-mode=importlib"
```

これで全 134 テストが collect 成功する。

```
============================= 134 passed in 0.16s ==============================
```

### `importlib` モードが何をするか

pytest 6 で導入された `importlib` モードは、テストファイルを **Python の `importlib.util` を使って一意な名前空間で読み込む**。

- ファイルパスから一意なモジュール名を生成（衝突しない）
- `sys.path` を勝手に書き換えない
- `__init__.py` の有無に依存しない（あってもなくても OK）

副作用：

- テストファイル間で `from tests.helpers import ...` のような **相対 import に近い書き方ができない**（モジュール名が `tests.test_config` ではなく一意な内部名になる）
- 共通ヘルパは `conftest.py` か、明示的にパスの通った別パッケージから import する必要がある

実用上、テスト間の相対 import を避ける設計（fixture を `conftest.py` に集める）と相性が良いので、モノレポでは **デフォルトで importlib を使うのが推奨**。

## 他の選択肢と比較

| 方法 | メリット | デメリット |
|---|---|---|
| **`__init__.py` を消す** | pytest 流の `rootdir` ベース解決に乗る | 各テストディレクトリを「パッケージ」として扱えなくなる。テスト同士の import が壊れる場合あり |
| **ファイル名を unique にする**<br>(`test_detector_config.py`) | デフォルト動作のまま | 命名規約がサービスごとにバラバラに。リファクタ時に name collision を再発しやすい |
| **conftest.py で `sys.path` を慎重に管理** | 細かく制御できる | 複雑化、デバッグ困難 |
| **`--import-mode=importlib`** | 1行追加で解決、副作用が予測可能 | テストの相対 import ができない（むしろ良い制約） |

筆者の経験ではモノレポで `importlib` モードに切り替えて困ったことは無い。新規プロジェクトで pytest を初期化したら **真っ先に追加するオプション**として扱っている。

## ポイント・注意点

### conftest.py の扱い

`importlib` モードでも `conftest.py` は **ディレクトリ階層を辿って自動 collect** される。だから fixture や共通設定は今まで通り conftest.py に書ける。`importlib` で変わるのは「テストファイル本体の import 方法」だけ。

### sys.path をどう通すか

`importlib` モードでも、テストファイルの中で `from services.detector.src.config import ...` のような **絶対 import** は普通にしたい。これには `sys.path` にプロジェクトルートを通す必要がある。

最小の `conftest.py`：

```python
# /conftest.py（プロジェクトルート）
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
```

これで全テストファイルが `from services...` で import できる。

### CI への適用

`pyproject.toml` の `addopts` は CI でも自動的に効く。`tox.ini` や個別の pytest コマンドで上書きしないよう注意。

## まとめ

- モノレポで `tests/` ディレクトリが複数あり、それぞれに同名テストファイルがあると pytest デフォルト import モードで衝突する
- `pyproject.toml` の `addopts` に **`--import-mode=importlib` を追加するだけ**で直る
- 副作用はほぼ無く、むしろテスト設計が clean に保たれる
- conftest.py を使った fixture 共有は引き続き使える

## バイブコーディングで実装する

この記事の内容を AI コーディングアシスタントに伝えるプロンプト：

> Python のモノレポで pytest を使う。複数サービスがそれぞれ `services/<name>/tests/test_*.py` を持ち、同じ名前のテストファイル（`test_config.py` 等）が複数のサービスに存在する。
>
> pytest 設定は `pyproject.toml` の `[tool.pytest.ini_options]` に書き、`addopts = "-v --tb=short --import-mode=importlib"` を含めること。`importlib` モードはモジュール名衝突を避けるために必須。
>
> 各テストファイルは `from services.detector.src.config import ...` のような絶対 import で書く。これを通すため、プロジェクトルートに最小 `conftest.py` を置き、`sys.path.insert(0, str(Path(__file__).parent))` でルートを sys.path に追加する。
>
> テストファイル間で共通ヘルパが必要な場合は、`conftest.py` の fixture として書く（テストファイルからの直接 import はしない）。

### AIに指示するときのポイント

- AI は pytest を扱うときデフォルトの `prepend` モードしか使わないことが多い。**`importlib` モードを使う指示を明示**する
- AI は `__init__.py` を「あって当然」と思って各 tests ディレクトリに作りたがる。`importlib` モードでは不要なので**作らせない**（あっても害はないが）
- 共通ヘルパは fixture 化する原則を伝えないと、AI は `tests/utils.py` を作って `from tests.utils import ...` と書きがち（importlib モードでは動かない）
- `--import-mode=importlib` を pytest コマンドラインで毎回渡すのではなく、**`pyproject.toml` に書く**よう指示すると CI とローカルで挙動が揃う
