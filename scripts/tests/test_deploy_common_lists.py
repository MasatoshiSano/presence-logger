"""配布ファイルの3分類が、機体固有設定を配布対象から確実に外していることを検証する。

id_names_config.json は region_id -> STA_NO1..3 の割当であり、子ごとに必ず異なる。
これを配布すると現場の割当が無警告で上書きされ、Oracle に誤った STA_NO が流れる。
"""
from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/lib/deploy-common.sh"


def _array(name: str) -> list[str]:
    out = run_bash(f'{SOURCE}; printf "%s\\n" "${{{name}[@]}}"').stdout
    return [line for line in out.splitlines() if line]


def test_device_owned_files_include_id_names():
    assert "id_names_config.json" in _array("CHILD_DEVICE_OWNED_FILES")


def test_device_owned_files_cover_all_operator_editable_configs():
    expected = {
        "id_names_config.json", "threshold_config.json", "recognition_config.json",
        "save_config.json", "model_config.json", "crop_config.json",
    }
    assert set(_array("CHILD_DEVICE_OWNED_FILES")) == expected


def test_shared_config_files_are_fleet_wide_only():
    assert set(_array("CHILD_SHARED_CONFIG_FILES")) == {
        "status_code_config.json", "send_target_config.json",
    }


def test_shared_and_device_owned_do_not_overlap():
    shared = set(_array("CHILD_SHARED_CONFIG_FILES"))
    owned = set(_array("CHILD_DEVICE_OWNED_FILES"))
    assert shared & owned == set()


def test_all_config_files_is_the_union():
    assert set(_array("CHILD_ALL_CONFIG_FILES")) == (
        set(_array("CHILD_SHARED_CONFIG_FILES")) | set(_array("CHILD_DEVICE_OWNED_FILES"))
    )


def test_legacy_child_config_files_is_removed():
    """旧名が残っていると、取り残された参照が黙って機体固有設定を配布し得る。"""
    proc = run_bash(f'set -u; {SOURCE}; echo "$CHILD_CONFIG_FILES"', check=False)
    assert proc.returncode != 0
