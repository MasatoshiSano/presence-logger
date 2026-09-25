"""新機へ入れる前の衝突点検 (preflight-new-hub.sh) の検証。

新しいハブにする Pi へ、別のアプリが既に入っていることがある。
bootstrap はホスト名・docker・NetworkManager・systemd・~/Desktop を
書き換えるので、先に何とぶつかるかを人に見せてから進めたい。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/preflight-new-hub.sh"


def _env(extra=None):
    env = dict(os.environ)
    if extra:
        env.update(extra)
    return env


def _verdict(call):
    """判定関数を1つ呼び、出力を返す。

    「BLOCK が出ない」だけを見ると、スクリプトが無い・関数が無いときの
    空出力でも通ってしまう。関数が実際に走って何か判定を出したことを
    ここで必ず確かめる。
    """
    proc = run_bash(f"{SOURCE}; {call}", env=_env(), check=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), f"判定が何も出力されていない: {call}"
    return proc.stdout


def test_existing_docker_ce_is_preserved_but_warned():
    """現行フェーズ20は docker-ce がある場合にパッケージ導入をスキップする。"""
    out = _verdict('preflight_docker_verdict "docker-ce"')
    assert out.startswith("[WARN]")
    assert "保持" in out
    assert "削除" not in out
    assert "docker-ce" in out


def test_containerd_io_alone_is_also_blocking():
    """docker.io は containerd に依存し、containerd.io は containerd と排他。"""
    out = _verdict('preflight_docker_verdict "containerd.io docker-ce-cli"')
    assert out.startswith("[BLOCK]")
    assert "containerd.io" in out


def test_docker_io_already_installed_is_fine():
    out = _verdict('preflight_docker_verdict "docker.io"')
    assert "BLOCK" not in out
    assert out.startswith("[OK]")


def test_no_docker_at_all_is_fine():
    out = _verdict('preflight_docker_verdict ""')
    assert "BLOCK" not in out
    assert out.startswith("[OK]")


def test_hostname_change_is_reported_with_both_names():
    """ホスト名は別アプリの識別子になっていることがある。変える前に見せる。"""
    out = _verdict('preflight_hostname_verdict "oldbox" "raspberrypi5-2"')
    assert out.startswith("[WARN]")
    assert "oldbox" in out
    assert "raspberrypi5-2" in out


def test_same_hostname_needs_no_warning():
    out = _verdict('preflight_hostname_verdict "samebox" "samebox"')
    assert "WARN" not in out
    assert "BLOCK" not in out
    assert out.startswith("[OK]")


def test_unknown_new_hostname_is_info_not_silence():
    """site.env がまだ無いと新しい名は分からない。黙らずにそう言う。"""
    out = _verdict('preflight_hostname_verdict "oldbox" ""')
    assert out.startswith("[INFO]")
    assert "oldbox" in out


# --- 既存の ~/projects/presence-logger -------------------------------------

def test_missing_repo_dir_is_fine(tmp_path):
    out = _verdict(f'preflight_repo_verdict "{tmp_path / "presence-logger"}"')
    assert out.startswith("[OK]")


def test_existing_presence_logger_repo_is_fine(tmp_path):
    repo = tmp_path / "presence-logger"
    (repo / "scripts").mkdir(parents=True)
    (repo / "services" / "bridge").mkdir(parents=True)
    (repo / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (repo / "scripts" / "bootstrap-hub.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    out = _verdict(f'preflight_repo_verdict "{repo}"')
    assert out.startswith("[OK]")


def test_foreign_content_in_repo_dir_is_blocking(tmp_path):
    """同じ名前の別物に kit_copy_dir が上書きすると、元のアプリが壊れる。"""
    repo = tmp_path / "presence-logger"
    repo.mkdir()
    (repo / "app.py").write_text("print('someone else')\n", encoding="utf-8")
    out = _verdict(f'preflight_repo_verdict "{repo}"')
    assert out.startswith("[BLOCK]")
    assert "app.py" in out


# --- ~/Desktop/presence-tools ----------------------------------------------

def test_desktop_tools_absent_is_fine(tmp_path):
    out = _verdict(f'preflight_desktop_tools_verdict "{tmp_path / "presence-tools"}" ""')
    assert out.startswith("[OK]")


def test_desktop_tools_lists_only_files_that_would_change(tmp_path):
    """cp -r は同名を上書きし、無い名前は残す。変わるものだけを見せる。"""
    src = tmp_path / "src"
    dst = tmp_path / "presence-tools"
    src.mkdir()
    dst.mkdir()
    (src / "same.sh").write_text("a\n", encoding="utf-8")
    (dst / "same.sh").write_text("a\n", encoding="utf-8")
    (src / "edited.sh").write_text("new\n", encoding="utf-8")
    (dst / "edited.sh").write_text("local change\n", encoding="utf-8")
    (dst / "mine.txt").write_text("kept\n", encoding="utf-8")
    out = _verdict(f'preflight_desktop_tools_verdict "{dst}" "{src}"')
    assert out.startswith("[WARN]")
    assert "edited.sh" in out
    assert "same.sh" not in out
    assert "mine.txt" not in out


def test_desktop_tools_identical_is_fine(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "presence-tools"
    src.mkdir()
    dst.mkdir()
    (src / "a.sh").write_text("a\n", encoding="utf-8")
    (dst / "a.sh").write_text("a\n", encoding="utf-8")
    out = _verdict(f'preflight_desktop_tools_verdict "{dst}" "{src}"')
    assert out.startswith("[OK]")


# --- /etc/modprobe.d/8821au.conf -------------------------------------------

def test_modprobe_conf_is_always_announced(tmp_path):
    out = _verdict(f'preflight_modprobe_verdict "{tmp_path / "8821au.conf"}"')
    assert out.startswith("[WARN]")
    assert "8821au.conf" in out


def test_existing_modprobe_conf_is_shown_before_overwrite(tmp_path):
    conf = tmp_path / "8821au.conf"
    conf.write_text("options 8821au rtw_country_code=US\n", encoding="utf-8")
    out = _verdict(f'preflight_modprobe_verdict "{conf}"')
    assert out.startswith("[WARN]")
    assert "rtw_country_code=US" in out


# --- NetworkManager / systemd / timesyncd / 日本語入力 ------------------------

def test_nm_announces_ap_and_preserves_home_autoconnect():
    """現行50-ap.shはUFI_CONNを空にし戻り先のautoconnectを保持する。"""
    out = _verdict('preflight_nm_verdict "wlan1" "F66" ""')
    assert out.startswith("[WARN]")
    assert "wlan1" in out
    assert "F66" in out
    assert "autoconnect" in out
    assert "変更しません" in out
    assert "no にします" not in out


def test_nm_names_connection_that_currently_uses_the_ap_interface():
    active = "factory:wlan0\\nother-app-wifi:wlan1"
    out = _verdict(f'preflight_nm_verdict "wlan1" "F66" "$(printf "{active}")"')
    assert "other-app-wifi" in out


def test_systemd_announces_unit_and_autostart(tmp_path):
    out = _verdict(f'preflight_systemd_verdict "{tmp_path / "presence-logger.service"}"')
    assert out.startswith("[WARN]")
    assert "presence-logger.service" in out
    assert "setup-autostart" in out


def test_systemd_existing_unit_is_called_out(tmp_path):
    unit = tmp_path / "presence-logger.service"
    unit.write_text("[Service]\nWorkingDirectory=/srv/other\n", encoding="utf-8")
    out = _verdict(f'preflight_systemd_verdict "{unit}"')
    assert "/srv/other" in out


def test_timesyncd_shows_ntp_lines_that_will_be_lost(tmp_path):
    conf = tmp_path / "timesyncd.conf"
    conf.write_text("[Time]\nNTP=ntp.example.local\n#FallbackNTP=x\n", encoding="utf-8")
    out = _verdict(f'preflight_timesyncd_verdict "{conf}"')
    assert out.startswith("[WARN]")
    assert "NTP=ntp.example.local" in out
    assert "#FallbackNTP" not in out


def test_timesyncd_default_conf_still_warns_about_restart(tmp_path):
    out = _verdict(f'preflight_timesyncd_verdict "{tmp_path / "missing.conf"}"')
    assert out.startswith("[WARN]")
    assert "systemd-timesyncd" in out


def test_ime_warns_and_names_other_input_method():
    out = _verdict('preflight_ime_verdict "ibus"')
    assert out.startswith("[WARN]")
    assert "fcitx5" in out
    assert "ibus" in out


# --- 情報 ------------------------------------------------------------------

def test_disk_space_low_is_warned():
    out = _verdict('preflight_disk_verdict 1048576')  # 1 GiB (KiB 単位)
    assert out.startswith("[WARN]")


def test_disk_space_enough_is_info():
    out = _verdict('preflight_disk_verdict 20971520')  # 20 GiB
    assert out.startswith("[INFO]")


def test_wlan1_absent_is_info():
    out = _verdict('preflight_wlan1_verdict "wlan1" ""')
    assert out.startswith("[INFO]")
    assert "wlan1" in out


def test_containers_listed_as_info():
    out = _verdict('preflight_containers_verdict "$(printf "grafana\\nnode-red")" 0')
    assert out.startswith("[INFO]")
    assert "grafana" in out
    assert "node-red" in out


def test_containers_unreadable_says_so():
    """docker を叩く権限が無いと一覧は空になる。「無い」と言ってはいけない。"""
    out = _verdict('preflight_containers_verdict "" 1')
    assert out.startswith("[INFO]")
    assert "読めません" in out


def test_port_1883_listener_is_reported():
    out = _verdict(
        'preflight_port1883_verdict \'LISTEN 0 100 0.0.0.0:1883 0.0.0.0:* '
        'users:(("mosquitto",pid=9,fd=5))\''
    )
    assert out.startswith("[INFO]")
    assert "mosquitto" in out


def test_debian_compose_without_candidate_is_warned():
    """現行フェーズ20が要求するDebian版Composeの候補が無い場合。"""
    out = _verdict('preflight_compose_verdict "" "(none)"')
    assert out.startswith("[WARN]")
    assert "docker-compose" in out


def test_compose_plugin_installed_is_fine():
    out = _verdict('preflight_compose_verdict "5.1.3" "5.1.3"')
    assert out.startswith("[OK]")


# --- 結論 ------------------------------------------------------------------

def _conclude(lines):
    return run_bash(
        f"{SOURCE}; preflight_conclude", env=_env(), check=False, stdin=lines,
    )


def test_conclude_block_says_stop_and_fails():
    proc = _conclude("[OK] a: x\n[WARN] b: y\n[BLOCK] c: z\n")
    assert proc.returncode == 2
    assert len(proc.stdout.strip().splitlines()) == 1
    assert "進めてはいけません" in proc.stdout


def test_conclude_warn_only_may_proceed_after_review():
    proc = _conclude("[OK] a: x\n[WARN] b: y\n        detail [BLOCK] in text\n")
    assert proc.returncode == 0
    assert len(proc.stdout.strip().splitlines()) == 1
    assert "WARN 1件" in proc.stdout


def test_conclude_all_ok():
    proc = _conclude("[OK] a: x\n[INFO] b: y\n")
    assert proc.returncode == 0
    assert "進めてよい" in proc.stdout


# --- 実機から集めて出す（main） -------------------------------------------

MUTATING = ("modify", "add", "delete", " up", " down", "start", "stop", "restart",
            "enable", "disable", "install", "remove", "set-hostname")


def _fake_system(fake_bin, *, docker_pkgs="docker-ce docker-ce-cli containerd.io"):
    """実機の代わり。書き換え系の呼び出しがあれば全部ログに残る。"""
    pkgs = " ".join(f"ii {p}" for p in docker_pkgs.split())
    fake_bin("dpkg-query",
             'echo dpkg-query "$@" >> "$FAKE_LOG"; '
             f'printf "%s\\n" {pkgs!r} | tr " " "\\n" | paste - -')
    fake_bin("apt-cache", 'echo apt-cache "$@" >> "$FAKE_LOG"; echo "  Candidate: (none)"')
    fake_bin("hostname", 'echo oldbox')
    fake_bin("nmcli", 'echo nmcli "$@" >> "$FAKE_LOG"; echo "factory:wlan0"')
    fake_bin("docker", 'echo docker "$@" >> "$FAKE_LOG"; echo other-app')
    fake_bin("ss", 'echo ss "$@" >> "$FAKE_LOG"')
    fake_bin("systemctl", 'echo systemctl "$@" >> "$FAKE_LOG"; exit 1')
    fake_bin("apt-get", 'echo apt-get "$@" >> "$FAKE_LOG"; exit 1')
    fake_bin("sudo", 'echo sudo "$@" >> "$FAKE_LOG"; exit 1')


def _assert_read_only(log):
    for line in log.read_text(encoding="utf-8").splitlines():
        cmd = line.split()[0]
        assert cmd not in ("systemctl", "apt-get", "sudo"), line
        if cmd == "nmcli":
            assert not any(m in f" {line}" for m in MUTATING), line
        if cmd == "docker":
            assert line.split()[1] == "ps", line


def test_main_warns_on_preserved_docker_ce_and_stays_read_only(tmp_path, fake_bin):
    _fake_system(fake_bin)
    home = tmp_path / "home"
    home.mkdir()
    proc = run_bash(
        "bash scripts/preflight-new-hub.sh",
        env=_env({"HOME": str(home), "PREFLIGHT_ETC": str(tmp_path / "etc")}),
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[WARN] docker:" in proc.stdout
    assert "other-app" in proc.stdout
    assert proc.stdout.strip().splitlines()[-1].startswith("結論: ⚠")
    _assert_read_only(fake_bin.log)


def test_main_runs_from_usb_kit_root(tmp_path, fake_bin):
    """新機ではまだリポジトリが無い。キット直下のコピーから動き、site.env.template を読む。"""
    _fake_system(fake_bin, docker_pkgs="")
    kit = tmp_path / "presence-hub-kit"
    tools = kit / "payload" / "presence-logger" / "desktop" / "presence-tools"
    tools.mkdir(parents=True)
    (tools / "a.sh").write_text("new\n", encoding="utf-8")
    (kit / ".kit").mkdir()
    (kit / ".kit" / "site.env.template").write_text(
        "HUB_HOSTNAME=\nAP_IF=wlan9\nHOME_SSID=HomeNet\n", encoding="utf-8")
    (kit / "preflight-new-hub.sh").write_text(
        (run_bash("cat scripts/preflight-new-hub.sh").stdout), encoding="utf-8")
    home = tmp_path / "home"
    (home / "Desktop" / "presence-tools").mkdir(parents=True)
    (home / "Desktop" / "presence-tools" / "a.sh").write_text("old\n", encoding="utf-8")
    proc = run_bash(
        f'bash "{kit}/preflight-new-hub.sh"',
        env=_env({"HOME": str(home), "PREFLIGHT_ETC": str(tmp_path / "etc")}),
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "wlan9" in proc.stdout
    assert "HomeNet" in proc.stdout
    assert "a.sh" in proc.stdout  # キットの中身と比べて上書き対象を出せている
    assert proc.stdout.strip().splitlines()[-1].startswith("結論: ⚠")
    _assert_read_only(fake_bin.log)


def test_desktop_tools_list_is_capped_and_skips_caches(tmp_path):
    """一覧が長いと大事な行が流れる。キャッシュは再生成されるので数えない。"""
    src = tmp_path / "src"
    dst = tmp_path / "presence-tools"
    for d in (src / "__pycache__", dst / "__pycache__"):
        d.mkdir(parents=True)
    (src / "__pycache__" / "x.pyc").write_bytes(b"new")
    (dst / "__pycache__" / "x.pyc").write_bytes(b"old")
    for i in range(14):
        (src / f"f{i:02d}.sh").write_text("new\n", encoding="utf-8")
        (dst / f"f{i:02d}.sh").write_text("old\n", encoding="utf-8")
    out = _verdict(f'preflight_desktop_tools_verdict "{dst}" "{src}"')
    assert "14 個" in out
    assert "x.pyc" not in out
    assert "f09.sh" in out
    assert "f10.sh" not in out
    assert "ほか 4 個" in out


def test_main_blocks_on_orphaned_containerd(tmp_path, fake_bin):
    _fake_system(fake_bin, docker_pkgs="containerd.io docker-ce-cli")
    home = tmp_path / "home"
    home.mkdir()
    proc = run_bash(
        "bash scripts/preflight-new-hub.sh",
        env=_env({"HOME": str(home), "PREFLIGHT_ETC": str(tmp_path / "etc")}),
        check=False,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "[BLOCK] docker:" in proc.stdout
    _assert_read_only(fake_bin.log)


def test_port_probe_failure_is_not_reported_as_unused():
    out = _verdict('preflight_port1883_verdict "" 1')
    assert "読めません" in out
    assert "ありません" not in out


def test_source_has_no_exit_failure():
    proc = run_bash(SOURCE, check=False)
    assert proc.returncode == 0
    assert proc.stdout == ""


def test_preserved_docker_ce_without_compose_needs_manual_setup():
    out = _verdict('preflight_compose_verdict "" "2.26.1" "docker-ce"')
    assert out.startswith("[WARN]")
    assert "スキップ" in out
    assert "手動" in out
    assert "apt-get install がまるごと失敗" not in out
