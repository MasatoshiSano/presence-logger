"""scripts/ 配下のシェルスクリプト検証で使う pytest フィクスチャ。"""
import os
from pathlib import Path

import pytest


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    """PATH の先頭に偽コマンドを置くためのフィクスチャ。

    ssh / rsync / curl を本物に触らせずにスクリプトの分岐を検証する。
    使い方: fake_bin("rsync", 'printf "%s\\n" "$@" >> "$FAKE_LOG"')
    実行記録は fake_bin.log (Path) に追記される。
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    log.touch()

    def _install(name: str, body: str) -> Path:
        p = bindir / name
        p.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
        p.chmod(0o755)
        return p

    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    _install.log = log
    _install.bindir = bindir
    return _install
