"""scripts/ 配下のシェルスクリプトを検証するための共通ヘルパ。

シェルスクリプトは pytest から subprocess で実行して振る舞いを確認する。
リポジトリに bats は入っていないため、テストランナーは pytest に統一する。
"""
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_bash(script: str, *, env: dict | None = None,
             check: bool = True) -> subprocess.CompletedProcess:
    """bash スニペットをリポジトリルートで実行して結果を返す。"""
    return subprocess.run(  # noqa: S603 (fixed argv, no shell)
        ["bash", "-c", script],  # noqa: S607 (bash は PATH 解決で十分。テスト専用ヘルパ)
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )
