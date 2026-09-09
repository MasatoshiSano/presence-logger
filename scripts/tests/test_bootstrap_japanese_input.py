"""フェーズ10(日本語入力)を検証する。

現行機の ~/.xinputrc は run_im fcitx (fcitx4) という残骸で、fcitx4 は入って
いない。設定ファイルの複製ではなく im-config に生成させるのが正しい。
ここを間違えると「動いている現行機の写し」を作って壊れる。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/bootstrap/10-japanese-input.sh"


def test_package_list_covers_every_frontend():
    # gtk3/gtk4/qt5/qt6 のどれかが欠けると、そのツールキットのアプリだけ
    # 日本語入力できないという分かりにくい壊れ方をする
    out = run_bash(f'{SOURCE}; ime_packages', env=dict(os.environ)).stdout.split()
    for pkg in ("fcitx5", "fcitx5-mozc", "fcitx5-frontend-gtk3", "fcitx5-frontend-gtk4",
                "fcitx5-frontend-qt5", "fcitx5-frontend-qt6", "fonts-noto-cjk"):
        assert pkg in out, f"{pkg} が不足"


def test_keyboard_layout_is_rewritten_to_jp(tmp_path):
    kb = tmp_path / "keyboard"
    kb.write_text('XKBMODEL="pc105"\nXKBLAYOUT="gb"\nBACKSPACE="guess"\n', encoding="utf-8")
    run_bash(f'{SOURCE}; ime_set_keyboard_layout "{kb}"', env=dict(os.environ))
    assert 'XKBLAYOUT="jp"' in kb.read_text(encoding="utf-8")
    assert 'BACKSPACE="guess"' in kb.read_text(encoding="utf-8")  # 他行を壊さない


def test_keyboard_rewrite_is_idempotent(tmp_path):
    kb = tmp_path / "keyboard"
    kb.write_text('XKBLAYOUT="jp"\n', encoding="utf-8")
    run_bash(f'{SOURCE}; ime_set_keyboard_layout "{kb}"', env=dict(os.environ))
    assert kb.read_text(encoding="utf-8").count("XKBLAYOUT") == 1


def test_fcitx5_profile_selects_mozc_and_jp_layout():
    out = run_bash(f'{SOURCE}; ime_render_fcitx5_profile', env=dict(os.environ)).stdout
    assert "DefaultIM=mozc" in out
    assert "Default Layout=jp" in out
    assert "Name=keyboard-jp" in out
    assert "Name=mozc" in out


def test_profile_does_not_reference_fcitx4():
    # 現行機の ~/.xinputrc(run_im fcitx)を写さないこと
    out = run_bash(f'{SOURCE}; ime_render_fcitx5_profile', env=dict(os.environ)).stdout
    assert "run_im" not in out


def test_keyboard_layout_appends_when_line_absent(tmp_path):
    """XKBLAYOUT行がない場合、追加される"""
    kb = tmp_path / "keyboard"
    kb.write_text('XKBMODEL="pc105"\nBACKSPACE="guess"\n', encoding="utf-8")
    run_bash(f'{SOURCE}; ime_set_keyboard_layout "{kb}"', env=dict(os.environ))
    content = kb.read_text(encoding="utf-8")
    assert 'XKBLAYOUT="jp"' in content
    assert content.count("XKBLAYOUT") == 1
    assert 'XKBMODEL="pc105"' in content
    assert 'BACKSPACE="guess"' in content


def test_append_is_idempotent(tmp_path):
    """XKBLAYOUT行がない状態で2回実行しても、1行だけ存在する"""
    kb = tmp_path / "keyboard"
    kb.write_text('XKBMODEL="pc105"\nBACKSPACE="guess"\n', encoding="utf-8")
    run_bash(f'{SOURCE}; ime_set_keyboard_layout "{kb}"', env=dict(os.environ))
    run_bash(f'{SOURCE}; ime_set_keyboard_layout "{kb}"', env=dict(os.environ))
    content = kb.read_text(encoding="utf-8")
    assert content.count("XKBLAYOUT") == 1
    assert content.count('XKBLAYOUT="jp"') == 1
