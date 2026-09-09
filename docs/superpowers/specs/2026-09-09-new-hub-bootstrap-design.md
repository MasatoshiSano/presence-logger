# 新ハブ Pi ブートストラップ設計

作成日: 2026-09-09

空の Raspberry Pi OS が入っただけの Raspberry Pi 5 を、`git clone` と 1 本の
スクリプトで **presence-logger の子Pi専用ハブ**として立ち上げられるようにする。

## 1. 背景

現行機（`raspberrypi5` / Debian 13 trixie）は、手作業と複数の手順書に分散した
設定の積み重ねで出来上がっている。ドングルのドライバに入れた 1 行の USB ID 追記、
`im-config` の設定、systemd の drop-in、デスクトップのランチャー配置などは、
**この機体の中にしか存在しない暗黙知**になっている。2 台目を作るとき、これらを
手順書から人手で再現するのは再現性が低く、抜けても気づきにくい。

## 2. ゴールと非ゴール

### ゴール

新機で次が動くこと。

1. **日本語入力**（最優先）
2. 子Pi との接続・通信（ドングル AP + MQTT ブローカ）
3. ネットワーク設定（工場網 = 内蔵 wlan0、子AP = ドングル wlan1）
4. HIME-H-REAP への接続 / 切断（デスクトップのアイコン）
5. パイプライン監視 / フリート監視 / 記録モニタ
6. 保守用 WiFi の切替（デスクトップの `WiFi切替` 相当）

### 非ゴール（YAGNI）

- Ansible 化。2 台では運用コストが勝る（`docs/DEPLOY.md` の K3s を採らない判断と同じ理由）。
  台数が 3〜5 に増えた時点で、本設計のフェーズ構造をそのまま playbook へ移せる。
- 自機カメラでの検知（detector）。今回はカメラ無し構成のため、**起動できないようにする**。
- オフライン環境への対応。新機はセットアップ時にインターネットへ到達できる前提。

## 3. 確定した設計判断

| # | 論点 | 決定 | 根拠 |
|---|---|---|---|
| 1 | 新機の役割 | **カメラ無し・ドングルあり＝子Pi専用ハブ**（`HUB_MODE=1`） | ユーザー確定 |
| 2 | 想定シナリオ | 「同一工場網への増設」と「別拠点への展開」の**両方**を同じスクリプトで扱う | ユーザー確定。機体固有値を全て `site.env` へ外出しすることで両立する |
| 3 | 子Pi | **既存の子（`zero2`, `pizero2w-2.local`）を新ハブへ引っ越す** | ユーザー確定 |
| 4 | 回線 | セットアップ時はインターネットに到達できる | ユーザー確定。apt / DKMS ビルド / git clone をその場で実行できる |
| 5 | リポジトリ | GitHub プライベート。`config/site/` を含め現状のまま push してよい | ユーザー確定 |
| 6 | ドングル | **現行と同型 ELECOM WDC-433DU2H2-B**（RTL8811AU / `056e:4010`） | ユーザー確定。AP モード動作の実績を優先し、未検証チップのリスクを取らない |
| 7 | 親の STA_NO | ハブでは Oracle に書かれない。ただし必須キーなので値は要る（`PARENT_STA_NO*`） | 4.3 節に根拠 |
| 8 | WiFi 切替の接続情報 | `secrets.env` の `WIFI_PSK_*` から **nmcli プロファイルを非対話で自動作成** | ユーザー確定。`connect-hime-h-reap.sh` が既にとっている方式と同じで、秘密を 1 ファイルに集約できる |

## 4. 現行機の実測値（再現の元データ）

### 4.1 日本語入力

| 要素 | 実値 |
|---|---|
| OS / セッション | Debian 13 (trixie) / labwc + Wayland (`rpd-labwc`) |
| IME | `fcitx5`, `fcitx5-mozc`, `fcitx5-frontend-{gtk3,gtk4,qt5,qt6}`, `fcitx5-config-qt`, `mozc-utils-gui` |
| フォント | `fonts-noto-cjk` |
| キーボード | `/etc/default/keyboard` → `XKBMODEL="pc105"` / `XKBLAYOUT="jp"` |
| ロケール | `LANG=en_GB.UTF-8`（UI は英語のまま、入力のみ日本語） |
| 起動 | `/etc/xdg/autostart/im-launch.desktop` → im-config 経由で `fcitx5 -d` |
| fcitx5 設定 | `~/.config/fcitx5/profile`: `Default Layout=jp` / `DefaultIM=mozc` / items = `keyboard-jp`, `mozc` |

**注意**: 現行機の `~/.xinputrc` は `run_im fcitx`（fcitx **4**）と書かれているが、fcitx4 は
インストールされていない（`im-config -l` は `fcitx5 xim` のみ）。今動いているのは im-config の
auto フォールバックが fcitx5 を拾っているため。**このファイルをコピーしてはならない**。
新機では `im-config -n fcitx5` を実行する。

### 4.2 WiFi ドングル

| 項目 | 実測値 |
|---|---|
| USB ID | `056e:4010`（`lsusb` は "LD-USB20" と誤表示。USB-ID DB 上は Elecom の有線LAN） |
| ドライバ | `rtl8821au`（カーネル内蔵ではない） |
| DKMS | `rtl8821au/5.12.5.2, 6.18.29+rpt-rpi-2712, aarch64: installed` |
| ソース | `~/8821au`（`{USB_DEVICE(0x056E, 0x4010), .driver_info = RTL8821}` を追記済み）/ `/usr/src/rtl8821au-5.12.5.2` |
| オプション | `/etc/modprobe.d/8821au.conf`: `options 8821au rtw_led_ctrl=1 rtw_country_code=JP rtw_power_mgnt=0` |
| AP 対応 | phy1（ドングル）= AP 可 / phy0（内蔵 brcmfmac）= AP 可 |

素の Raspberry Pi OS では `pegasus`（USB 有線LANドライバ）が `056e:4010` に誤マッチして
`probe ... failed with error -110` となり、**`wlan1` 自体が現れない**。

**内蔵を AP に回せない理由**: 工場網は MAC ホワイトリスト制で、登録されるのは内蔵 wlan0 の
MAC である。加えて内蔵 1 枚で AP とクライアントを同時に張ると両者が同一チャンネルに縛られ、
隠しSSID・5GHz の工場AP と 2.4GHz の子Pi を同時に満たせない。

**ドングルの要件は 2 点のみ**: ① AP モード対応 ② 2.4GHz が出せること。
子は Pi Zero 2 W で 2.4GHz しか掴めないため（`setup-dongle-ap.sh` の `AP_BAND=bg` / ch6）、
5GHz や WiFi6 の性能は AP 役では効かない。

### 4.3 STA_NO の 2 経路（親の局番がハブで使われない根拠）

| 経路 | STA_NO の出所 | ハブでの使用 |
|---|---|---|
| detector 経路（親のカメラ ENTER/EXIT） | `services/bridge/src/sender.py:113` → `station_for_profile(profile, device_cfg)`。`profiles.yaml` の `station:` 上書き、無ければ `device.yaml` の `station:` | **使わない**（detector を起動しない） |
| record 経路（子Pi の CSV レコード） | `services/bridge/src/record_sender.py:64-66` → `rec.sta_no1/2/3`。子が MQTT ペイロードに載せた値 | **こちらのみ** |

`services/bridge/src/record.py` の docstring が明示している:

> the timestamp, all three station numbers and the T1_STATUS come from the child's CSV line …
> The bridge writes these values straight to Oracle (no ENTER/EXIT merge, **no profile station lookup**).

`REQUIRED_RECORD_KEYS` に `sta_no1/2/3` が含まれ、欠ければ `ValueError` で弾かれる。
**親の設定値へフォールバックする経路は存在しない。**

ただし `services/bridge/src/config.py:41` の `_DEVICE_REQUIRED = {"station": {...}}` により、
`device.yaml` の `station` は **必須キー**である。空にすると bridge が `ConfigError` で起動しない。
よって「使わないが値は要る」状態になる。

**将来リスク**: 後からカメラを挿して detector を有効化した瞬間、そこに入れていた値が本番
テーブルへ書かれ始める。placeholder のつもりで現行機と同じ 996/995/994 を入れると、そのとき
初めて衝突する（しかも `docs/DEPLOY.md` の言う「無警告で欠落」する側の事故）。
したがって `PARENT_STA_NO*` は **今のうちに他機・全子Piと重複しない値を採番する**。

### 4.4 デスクトップ資産の実体と Git との乖離

現行機のデスクトップには次があるが、**リポジトリに入っているのは 5 個のランチャーだけ**である。
`WiFi切替/` と `フリート管理.desktop` は**この SD カードの中にしか存在しない**。新機で再現
できないだけでなく、現行機が故障すれば失われる。本設計でリポジトリへ取り込む。

| 実体 | Git 追跡 | 備考 |
|---|---|---|
| `~/Desktop/HIME-H-REAP-{接続,切断}.desktop` | あり | `desktop/launchers/` |
| `~/Desktop/{記録モニタ,直近30件,パイプライン監視}.desktop` | あり | 同上 |
| `~/Desktop/フリート管理.desktop` | **なし** | `chromium --app=http://localhost:8090` |
| `~/Desktop/WiFi切替/switch-wifi.sh` | **なし** | 機体非依存。nmcli の薄いラッパ |
| `~/Desktop/WiFi切替/{F66,GallaxyS23FE,UFI_103134,presence-hub}.desktop` | **なし** | 機体固有（SSID とインターフェース割当） |

`switch-wifi.sh` の性質:

- `nmcli --wait 20 connection up "$CONN" ifname "$IFNAME"` を叩くだけ。**パスワードを持たない**
- 第3引数 `--away-from-f66` のときだけ「Claude Code への接続が切れる」警告と `y/N` 確認を出す
- **NetworkManager に保存済みの接続しか切り替えられない**。したがって空のラズパイにこの
  フォルダを置いても何も起きない（`connection up` が失敗する）

`--away-from-f66` は「F66 経由でしか Claude/Anthropic に到達できない」という**現行機固有の
前提**を名前に埋め込んでいる。新機では保守用の SSID が別名になり得るため、`--warn-disconnect`
へ一般化し、警告文中の SSID 名は `site.env` の `ADMIN_SSID` から生成する。

| ランチャー | 接続名 | IF | 警告 | 役割 |
|---|---|---|---|---|
| F66 | `F660P-sDcS-A` | wlan0 | なし | 保守用。通常状態への復帰 |
| GallaxyS23FE | `GallaxyS23FE` | wlan0 | あり | スマホテザリング |
| UFI_103134 | `UFI_103134` | wlan0 | あり | モバイルルータ |
| presence-hub | `presence-hub-ap` | wlan1 | なし | **子AP を他のWiFiに奪われたときの復旧用** |

## 5. `site.env` — 機体固有値

リポジトリ直下に置く。`.gitignore` に追加し、Git には載せない。
ひな型は `site.env.example` として Git 管理する。

```bash
# --- この端末 ---
HUB_HOSTNAME=presence-hub-2      # ホスト名。device_id と MQTT client_id を決める
HUB_MODE=1                       # 1=カメラ無し。detector を起動不能にする

# --- 工場網（内蔵 wlan0）---
FACTORY_SSID=HIME-H-REAP         # profiles.yaml のキー = 実SSID
FACTORY_IP=172.22.13.18/24       # 新機の固定IP。情シス申請値。現行機(.17)と別
FACTORY_GW=172.22.13.1
FACTORY_DNS=10.166.1.70,10.166.1.17
FACTORY_HIDDEN=yes
FACTORY_SUBNETS="10.166.5.0/24 10.166.1.0/24 133.141.247.101/32"
SNTP_SERVERS="133.141.247.101"

# --- Oracle ---
ORACLE_CLIENT_MODE=jdbc          # thin | thick | jdbc
ORACLE_AUTH_MODE=basic
ORACLE_HOST=10.166.5.93
ORACLE_PORT=1521
ORACLE_SERVICE=HHC001
ORACLE_USER=ZHH001
ORACLE_TABLE=HF1RCM01
ORACLE_PASSWORD_VAR=ORACLE_PASSWORD_HHC   # secrets.env のキー名
UPCMPFLG=1
UNKNOWN_SSID_POLICY=drop

# --- 親自身のカメラ検知用 STA_NO ---
# HUB_MODE=1 では Oracle に書かれない（4.3 節）。ただし bridge の必須項目なので
# 値は要る。将来カメラを付けたときそのまま使えるよう、他機・全子Piと重複しない
# 値を今のうちに採番しておくこと。
PARENT_STA_NO1=997
PARENT_STA_NO2=996
PARENT_STA_NO3=995

# --- 子Pi 用 AP（ドングル wlan1）---
# 【引っ越し】現行ハブと同じ値にすると、子側は設定変更ゼロで繋ぎ替わる。
#            条件: 旧ハブの AP を先に落とすこと（同一SSIDのAPが2つ生きると
#            どちらに繋ぐか不定になり、DEPLOY.md の相互切断事故と同じ構図になる）
# 【増設】  現行ハブと同時に稼働させるなら SSID と AP_GW_IP を必ず別にし、
#          子側の send_target_config.json の host も変更する
AP_IF=wlan1
AP_SSID=presence-hub
AP_GW_IP=10.42.0.1
AP_BAND=bg
AP_CHANNEL=6

# --- 平時のインターネット接続（戻り先）---
HOME_SSID=UFI_103134             # 切断時に戻す先。スクリプトのハードコードを廃してここへ

# --- 保守用ネットワーク ---
# ここから離れると遠隔操作できなくなる接続。WiFi切替ランチャーの警告文に使う。
# 現行機では F66（この SSID 経由でしか Claude/Anthropic に到達できない）。
ADMIN_SSID=F660P-sDcS-A
```

### 検証（`scripts/lib/site-env.sh`）

読み込み時に次を検査し、満たさなければ**起動前に**落とす。

- 必須変数の欠落
- `FACTORY_IP` が CIDR 形式であること
- `PARENT_STA_NO*` が空でないこと
- `HUB_HOSTNAME` が既存の子（`fleet/children.conf`）と重複しないこと
- `AP_GW_IP` が `FACTORY_IP` と同一サブネットでないこと

### `wifi-switch.conf` — WiFi 切替ランチャーの定義

`site.env` と同じく機体固有・Git 管理外。ひな型 `wifi-switch.conf.example` を Git 管理する。
1 行 1 ランチャー、`#` 以降はコメント。

```
# <NM接続名>       <ifname>  <表示名>       <警告>  <PSKキー名(secrets.env)>
F660P-sDcS-A       wlan0     F66            -       WIFI_PSK_F66
GallaxyS23FE       wlan0     GallaxyS23FE   warn    WIFI_PSK_GALAXY
UFI_103134         wlan0     UFI_103134     warn    WIFI_PSK_UFI
presence-hub-ap    wlan1     presence-hub   -       -
```

- `<警告>` が `warn` の行は、切替前に「`ADMIN_SSID` から離れるため遠隔操作できなくなる」旨を
  表示し `y/N` を取る
- `<PSKキー名>` が `-` の行は **nmcli プロファイルを作らない**。`presence-hub-ap` は
  フェーズ 50 が `setup-dongle-ap.sh` 経由で作るため、ここで二重に作ってはならない

## 6. `scripts/bootstrap-hub.sh` — フェーズ設計

```bash
sudo bash scripts/bootstrap-hub.sh          # 全フェーズ
sudo bash scripts/bootstrap-hub.sh 10       # 日本語入力のみ
sudo bash scripts/bootstrap-hub.sh 30 40    # 範囲指定
sudo bash scripts/bootstrap-hub.sh --list   # フェーズ一覧
```

全フェーズは**冪等**。既に済んでいる工程は検出してスキップし、何度流しても壊れない。

| # | フェーズ | 実装 | 完了後に人が行う操作 |
|---|---|---|---|
| **10** | **日本語入力** | `scripts/bootstrap/10-japanese-input.sh` | **ログアウト → 再ログイン** |
| 20 | 基盤パッケージ | `20-base-packages.sh` | docker グループ反映のため **再ログイン or reboot** |
| 30 | ドングルドライバ | `30-dongle-driver.sh` | `wlan1` の出現確認 |
| 40 | 設定生成 | `40-configs.sh` | `secrets.env` の中身を投入 |
| 50 | 子AP 構築 | `50-ap.sh`（`site.env` の値を環境変数で渡して既存 `setup-dongle-ap.sh` を呼ぶ） | 子Pi が `AP_GW_IP` のサブネットで IP を取れる確認 |
| 60 | コンテナ + 常駐化 | `60-stack.sh` | — |
| 70 | デスクトップ / 監視ツール | `70-desktop.sh` | アイコンの「信頼して実行」 |

### フェーズ 10（日本語入力）

1. `apt-get install -y fcitx5 fcitx5-mozc fcitx5-frontend-gtk3 fcitx5-frontend-gtk4
   fcitx5-frontend-qt5 fcitx5-frontend-qt6 fcitx5-config-qt mozc-utils-gui fonts-noto-cjk`
2. `/etc/default/keyboard` の `XKBLAYOUT` を `jp` に（`XKBMODEL=pc105`）。変更時のみ `setupcon`
3. `im-config -n fcitx5`（`~/.xinputrc` はコピーせず生成させる。4.1 節の理由）
4. `~/.config/fcitx5/profile` を配置（`Default Layout=jp` / `DefaultIM=mozc` /
   items = `keyboard-jp`, `mozc`）。所有者は実ユーザー、`700` の `~/.config/fcitx5/`
5. **検証**: 対象パッケージが導入済み・`XKBLAYOUT=jp`・`im-config -l` に `fcitx5`・
   profile の `DefaultIM=mozc` を確認し、**「ログアウトして再ログインしてください」を明示出力**

再ログインが必要なのは、Wayland(labwc) セッションで `GTK_IM_MODULE` / `QT_IM_MODULE` /
`XMODIFIERS` を撒くのが `im-launch`（セッション開始時）だからである。スクリプト直後には
まだ日本語入力できない。**これを工程として明示することが本フェーズの要件**。

### フェーズ 20（基盤パッケージ）

- docker-ce / docker-compose-plugin、`mosquitto-clients`、`python3-yaml`、`git`、
  `dkms`, `build-essential`, `bc`, `raspberrypi-kernel-headers`
- 実ユーザーを `docker` グループへ追加
- `python3 -m venv .venv` + 依存導入（`fleet-ui.service` が `.venv/bin/python` を
  絶対パスで叩くため必須。既存 venv は Git 管理外）
- `hostnamectl set-hostname $HUB_HOSTNAME` + `/etc/hosts` の更新

### フェーズ 30（ドングルドライバ）

1. `lsusb` で VID:PID を確認。既に `wlan1` があり AP 対応なら**丸ごとスキップ**
2. `git clone --depth=1 https://github.com/morrownr/8821au-20210708.git`
3. `scripts/bootstrap/patches/8821au-add-elecom-056e-4010.patch` を適用
   （現行機の `~/8821au` に手で入れた 1 行を Git 管理下へ移す）
4. `./install-driver.sh NoPrompt`
5. `/etc/modprobe.d/8821au.conf` を配置（`rtw_country_code=JP` / `rtw_power_mgnt=0`）
6. **検証**: `cat /sys/module/8821au/parameters/rtw_country_code` = `JP`、`wlan1` の存在、
   `iw phy <phy> info` に `* AP` があること

手順 6 の AP 対応チェックを**ドライバ導入直後**に置く。`setup-dongle-ap.sh` も同じ検査を
するが、そこまで進んでから落ちると原因が分かりにくいため。

### フェーズ 40（設定生成）

`site.env` から `/etc/presence-logger/` を生成する。

- `profiles.yaml`（`0640 root:root`）— `FACTORY_SSID` をキーに、`wifi.static_ipv4`・
  `sntp`・`oracle` を埋める。**`station:` ブロックは出力しない**（`device.yaml` に
  一本化して二重管理を消す）。パスワードは `${VAR}` 参照のみ
- `device.yaml`（`0644`）— `device_id: null` + `PARENT_STA_NO*`。4.3 節の注意をコメントで埋め込む
- `bridge.yaml` / `detector.yaml`（`0644`）— `config/site/` から複製
- `secrets.env`（`0600 root:docker`）— **存在しなければ雛形を作り、必要なキーを一覧表示して
  人の入力を促す**。既存があれば touch しない
- `upcmpflg.override`（pi 書込可）
- `/var/lib/presence-logger`, `/var/log/presence-logger`, `wallets/`(`0700 root:docker`)
- timesyncd を `SNTP_SERVERS` + 公開NTP フォールバックで構成（既存 `install.sh` と同じ方針）

### フェーズ 50（子AP 構築）

既存の `desktop/presence-tools/setup-dongle-ap.sh` を再利用する。同スクリプトは
`AP_IF` / `AP_CONN` / `AP_SSID` / `AP_BAND` / `AP_CHANNEL` / `UFI_CONN` / `AP_PSK` を
環境変数で上書きできるので、`50-ap.sh` は `site.env` の値を環境変数として渡して呼ぶ。
PSK はファイルに書かず、実行時に `/etc/presence-logger/secrets.env` の `WIFI_AP_PSK` を
root で読む既存の挙動をそのまま使う。

**`AP_GW_IP` を変える場合の注意**: `setup-dongle-ap.sh` は `ipv4.method shared` を使うため、
ゲートウェイIPは NetworkManager が既定の `10.42.0.1/24` を自動で付ける。既定以外にするには
AP プロファイルに `ipv4.addresses <AP_GW_IP>/24` を明示指定する必要がある。したがって
`50-ap.sh` は次のように分岐する。

- `AP_GW_IP` が `10.42.0.1` → 現行と同じ。`ipv4.method shared` のまま（引っ越しの既定）
- `AP_GW_IP` がそれ以外 → `ipv4.method shared` + `ipv4.addresses` を明示（増設時）。
  この場合は子側の `send_target_config.json` の `host` 変更が別途必要（9 節）

**同一SSID の重複検出**: AP を上げる前に、周囲に同じ `AP_SSID` のAPが既に存在しないかを
`nmcli device wifi list` で確認し、見つかったら**警告して停止する**（`--force` で続行可）。
旧ハブの AP を落とし忘れたまま新ハブを上げる事故を、ここで止める。

### フェーズ 60（コンテナ + 常駐化）

- `HUB_MODE=1` のとき **detector を起動しない**。compose の `profiles:` を使い、
  明示的に有効化しない限り `docker compose up -d` の対象外にする
- `docker-compose.override.yml` の `10.42.0.1:1883:1883` を `${AP_GW_IP}:1883:1883` へ変数化
  （`.env` に `AP_GW_IP` を書き出す）
- `systemd/presence-logger.service` の `WorkingDirectory` を実ツリーへ向ける drop-in
  （既存 `setup-autostart.sh` と同じ方式。ただし `ExecStartPost` の detector 停止は
  ハブでは不要なので `HUB_MODE` で出し分ける）
- `fleet-ui.service` を配置し `enable --now`。`ss -ltn` で **127.0.0.1:8090 のみ**を確認

### フェーズ 70（デスクトップ / 監視ツール）

- `desktop/presence-tools` を `$HOME/Desktop/presence-tools` へ配置
- `.desktop` ランチャーを**テンプレートから生成**（現状の `/home/pi/` 直書きを廃し、
  実ユーザーの `$HOME` を埋める）
- `HIME-H-REAP-切断.desktop` の Comment に埋まっている `UFI_103134` も `HOME_SSID` から生成
- `フリート管理.desktop` を配置（`chromium --app=http://localhost:8090`）
- **WiFi 切替**（4.4 節）:
  1. `desktop/wifi-switch/switch-wifi.sh` を `$HOME/Desktop/WiFi切替/` へ配置
  2. `wifi-switch.conf` の各行から `.desktop` を生成。`warn` 行には
     `--warn-disconnect "$ADMIN_SSID"` を渡す
  3. **nmcli プロファイルの播種**: `<PSKキー名>` が `-` でない行について、その接続が未保存で
     あれば `secrets.env` の対応キーを root で読み、`nmcli connection add type wifi` で作成する。
     キーが `secrets.env` に無ければ**作成せず、不足しているキー名を一覧表示する**
     （黙って失敗させない）
  4. 検証: 各行の接続が `nmcli -t -f NAME connection show` に現れること

**PSK の扱いに関する注記**: 手順 3 は PSK を `nmcli` のコマンドライン引数として渡すため、
ごく短時間 `ps` に露出する。これは既存の `connect-hime-h-reap.sh` と同じ方式であり、
単一ユーザーの機体では許容する。露出も避けたい場合は
`/etc/NetworkManager/system-connections/<名前>.nmconnection` を `600 root:root` で直接
書き出す方式に差し替えられる（本設計では採らない）。

## 7. 既存資産の改修

| 対象 | 改修内容 | 理由 |
|---|---|---|
| `docker-compose.yml` | detector を `profiles: [camera]` 化 | ハブで誤って起動しないようにする |
| `docker-compose.override.yml` | `10.42.0.1` → `${AP_GW_IP}` | 増設時に AP サブネットを変えられるようにする |
| `desktop/launchers/*.desktop` | `/home/pi/` 直書きを廃止しテンプレート化 | ユーザー名が `pi` 以外だと壊れる |
| `connect-hime-h-reap.sh` | `PROFILE_NAME` / `FACTORY_SUBNETS` / detector 起動を `site.env` 駆動に。`HUB_MODE=1` なら detector を触らない | ハブには detector が存在しない |
| `disconnect-hime-h-reap.sh` | `HOME_PROFILE` の既定を `site.env` の `HOME_SSID` から。`HUB_MODE=1` なら `docker stop presence-detector` を行わない | 同上（`:20` で無条件に停止しようとする） |
| `watch-records.sh` | `HUB_MODE=1` では `presence-detector` のログを購読せず bridge のみにする | `:35-38` が無条件に `docker logs presence-detector` を叩き、ハブではエラーになる |
| `show-recent-records.sh` | 絞り込み既定値の出所を変更。`HUB_MODE=1` では親の station ではなく **`*`（すべて）を既定**にする | `:63-65` が親の `device.yaml` station を既定にするため、ハブでは placeholder で絞られ**常に 0 件**になる |
| `scripts/install.sh` | フェーズ 40 から呼ばれる形に整理（重複を作らない） | 既存資産を捨てない |
| `.gitignore` | `site.env` と `wifi-switch.conf` を追加 | 機体固有値を Git に載せない |
| `~/Desktop/WiFi切替/switch-wifi.sh` | `desktop/wifi-switch/switch-wifi.sh` として**新規に Git 管理**。`--away-from-f66` → `--warn-disconnect <SSID>` へ一般化 | 現行機の SD カードにしか存在せず、現行機固有の前提が名前に埋まっている（4.4 節） |
| `~/Desktop/WiFi切替/*.desktop` | テンプレート + `wifi-switch.conf` から生成する形で Git 管理 | 同上。SSID とインターフェース割当が機体固有 |
| `~/Desktop/フリート管理.desktop` | `desktop/launchers/` へ取り込む | 同上（Git 未追跡） |

## 8. Git に載せられないもの — どこに何を置くか

`docs/NEW-HUB-SETUP.md` に恒久的に記載する。パスは実ユーザーが `pi`、
リポジトリが `/home/pi/projects/presence-logger` の場合。

| 絶対パス | 何を書くか | 権限 / 所有 | 入手元 |
|---|---|---|---|
| `/home/pi/projects/presence-logger/site.env` | 5 節の機体固有値 | `600 pi:pi` | **手入力**（`site.env.example` をコピー） |
| `/home/pi/projects/presence-logger/wifi-switch.conf` | WiFi 切替ランチャーの定義（5 節） | `600 pi:pi` | **手入力**（`wifi-switch.conf.example` をコピー） |
| `/etc/presence-logger/secrets.env` | `ORACLE_PASSWORD_HHC=` / `WIFI_PSK_HIMEREAP=` / `WIFI_AP_PSK=` / WiFi 切替用の `WIFI_PSK_*`（`wifi-switch.conf` で参照するキー名）（必要に応じて `ORACLE_PASSWORD_A,B,D`・`WALLET_PASSWORD_B`） | `600 root:docker` | 現行機の同ファイル、または情シス。**Git にもコミットログにも残さない** |
| `/etc/presence-logger/profiles.yaml` | 工場プロファイル | `640 root:root` | **フェーズ 40 が `site.env` から生成** |
| `/etc/presence-logger/device.yaml` | `device_id: null` + `PARENT_STA_NO*` | `644 root:root` | フェーズ 40 が生成 |
| `/etc/presence-logger/bridge.yaml` | bridge 動作パラメータ | `644 root:root` | `config/site/bridge.yaml` から複製（Git にある） |
| `/etc/presence-logger/detector.yaml` | detector 動作パラメータ（ハブでは未使用だが配置する） | `644 root:root` | `config/site/detector.yaml` から複製 |
| `/etc/presence-logger/upcmpflg.override` | UPCMPFLG の実行時上書き（任意） | pi 書込可 | フェーズ 40 が生成 |
| `/etc/presence-logger/wallets/` | Oracle Wallet 一式（`auth_mode: wallet` のときのみ。HHC001 は `basic` なので通常不要） | `700 root:docker` | Wallet zip を展開 |
| `/home/pi/projects/presence-logger/.venv/` | Python 仮想環境（`fleet-ui.service` が絶対パスで参照） | `755 pi:pi` | フェーズ 20 が生成 |
| `/home/pi/projects/presence-logger/models/<name>/<version>/network.rpk`・`packerOut.zip` | 子Pi の IMX500 モデル実体。現行機には `signal_tower/20260422`・`signal_tower/20260730`・`object_detection/20260422` がある | `644 pi:pi` | 現行機から `rsync` |
| `/home/pi/.ssh/id_ed25519` + `.pub` | 子Pi への SSH 鍵 | `600` / `644` | **新規生成**し `ssh-copy-id` で各子へ登録（現行鍵の複製でも可） |
| `/home/pi/.ssh/config` | `Host zero2` 等の到達名定義（IP は書かない） | `600` | 現行機からコピー |
| `/home/pi/.ssh/known_hosts` | 子の host key | `600` | `ssh-keyscan` で各子を登録 |
| `/home/pi/projects/presence-logger/fleet/known_macs.json` | TOFU 学習済み MAC | `644 pi:pi` | 空でよい。運用で自動生成 |
| `/var/lib/presence-logger/` | bridge の SQLite バッファ | `755 root` | **コピーしない**。旧機で送り切ってから引っ越す（二重送信を避ける） |
| `/etc/NetworkManager/system-connections/*.nmconnection` | 保守用WiFi の接続定義（PSK を含む） | `600 root:root` | **フェーズ 70 が `secrets.env` から生成**。現行機からのコピーはしない |
| `/etc/modprobe.d/8821au.conf` | `options 8821au rtw_led_ctrl=1 rtw_country_code=JP rtw_power_mgnt=0` | `644 root:root` | フェーズ 30 が配置 |
| `~/.config/fcitx5/profile` | `Default Layout=jp` / `DefaultIM=mozc` | `600 pi:pi` | フェーズ 10 が配置 |

## 9. 既存子Pi の引っ越し（`docs/child-migration.md`）

`AP_SSID` / `WIFI_AP_PSK` / `AP_GW_IP` を現行ハブと同じにすれば、**子側の設定変更は不要**。
子は `send_target_config.json` で `10.42.0.1:1883` を、SSID は `presence-hub` を掴んでいる。

1. **旧ハブで未送信を送り切る** — `record_inbox` の `status='received'` が 0 になるまで待つ
   （ホストから直接 SQLite を開けないため `docker exec presence-bridge` 経由で確認する）
2. **旧ハブの AP を落とす** — `sudo nmcli connection down presence-hub-ap` かつ
   `connection.autoconnect no`。**同一SSIDのAPを2つ生かしたまま新ハブを上げない**
3. 新ハブで AP を起動 → 子が自動で繋ぎ替わる
4. 新ハブで `scripts/fleet-status.sh` → 全子が見え、**exit 0**（`1` は STA_NO 重複、
   `2` は「検査しきれていない」であって安全ではない）
5. `pipeline-monitor.sh` で ②MQTT → ③record_inbox → ④Oracle を同じ event_id で追跡できること

**増設（両ハブ同時稼働）の場合**は上記が成立しない。`AP_SSID` と `AP_GW_IP` を別にし、
新ハブ配下に置く子の `send_target_config.json` の `host` を新しい `AP_GW_IP` へ変更する。
このとき子の `id_names_config.json`（STA_NO）が**全ハブを通じて**重複しないことを確認する。
Oracle の MERGE キーは `MK_DATE + STA_NO1-3 + T1_STATUS` のみで `device_id` を含まないため、
ハブが別でも STA_NO が衝突すればレコードは無警告で欠落する。

## 10. 受入基準

`docs/acceptance-checklist.md` の形式に合わせ、新機で次を確認する。

| # | 確認項目 | 合格条件 |
|---|---|---|
| 1 | 日本語入力 | 再ログイン後、テキストエディタで「ひらがな」を変換入力できる |
| 2 | ドングル | `wlan1` が存在し `iw phy` に `* AP` がある。`rtw_country_code=JP` |
| 3 | 子AP | `nmcli` で `$AP_SSID` の AP プロファイルが active、`$AP_GW_IP` が付き dnsmasq が動いている |
| 4 | 子Pi 接続 | 全子が `$AP_GW_IP` のサブネットで IP を取得し、`scripts/fleet-status.sh` が **exit 0** |
| 5 | コンテナ | mosquitto / bridge / oracle-jdbc が running。**detector は存在しない** |
| 6 | 工場網接続 | 「HIME-H-REAP に接続」で接続でき、`ip route get <ORACLE_HOST>` が `dev wlan0` |
| 7 | デフォルト経路 | 工場接続中もデフォルト経路が奪われない（`never-default`） |
| 8 | 記録モニタ | エラーを出さずに起動し、bridge の書込ログが流れる |
| 9 | パイプライン監視 | ①〜④が表示され、同一 event_id を ②→③→④ で追える |
| 10 | 直近30件 | 既定の絞り込みで **0 件にならない**（子の実レコードが見える） |
| 11 | フリート監視 | `http://localhost:8090` が開き、`ss -ltn` で `127.0.0.1:8090` のみ |
| 12 | WiFi 切替 | `WiFi切替` の各アイコンで切替でき、`warn` 付きは確認プロンプトが出る。`presence-hub` で子AP を復旧できる |
| 13 | 再起動耐性 | `reboot` 後、AP とコンテナが自動復帰する |
| 14 | 切断 | 「HIME-H-REAP を切断」で `HOME_SSID` に戻る |

## 11. リスクと対策

| リスク | 影響 | 対策 |
|---|---|---|
| 同一SSIDのAPが 2 つ同時に生きる | 子がどちらに繋ぐか不定。`DEPLOY.md` 記録の相互切断事故と同じ構図 | 引っ越し手順の 2 番目（旧AP停止）を最優先工程として明記。フェーズ 50 で同一SSIDの検出時に警告 |
| 新機の固定IPが現行機と重複 | 工場網で IP 衝突 | `site.env` 検証で必須化。情シス申請値であることを `NEW-HUB-SETUP.md` に明記 |
| 後からカメラを付けて detector が動く | placeholder の STA_NO が本番テーブルへ書かれる | `PARENT_STA_NO*` を最初から重複しない実値で採番。compose の `profiles:` で明示有効化を要求 |
| DKMS ビルドがカーネル更新で失敗 | `wlan1` が消え AP が落ちる | DKMS は自動再ビルドされる。失敗時は `install-driver.sh NoPrompt` の再実行手順を `NEW-HUB-SETUP.md` に記載 |
| WiFi 切替で保守用ネットワークから離れ、遠隔操作できなくなる | 現地に行くまで復旧できない | `warn` 行に `y/N` 確認を必須化。警告文に `ADMIN_SSID` を明示。`presence-hub` 復旧ランチャーを必ず同梱する |
| `secrets.env` の受け渡しで秘密が漏れる | 認証情報の露出 | Git・コミットメッセージ・ログに出さない。フェーズ 40 は雛形生成と必要キーの提示までに留め、値は人が投入する |
| 旧機のバッファを引き継いで二重送信 | Oracle への重複書込 | `/var/lib/presence-logger/` はコピーしない。旧機で送り切ってから移行する |

## 12. 実装順序

1. `site.env.example` + `scripts/lib/site-env.sh`（検証）
2. フェーズ 10（日本語入力）— **最優先。単体で価値があり、単体で検証できる**
3. フェーズ 20 → 30 → 40 → 50 → 60 → 70
4. 既存資産の改修（7 節）— ハブモード分岐を含む
5. `desktop/wifi-switch/` の Git 取り込みと一般化（4.4 節）
6. `docs/NEW-HUB-SETUP.md` / `docs/child-migration.md`
7. テスト（`scripts/tests/` に site-env / wifi-switch.conf の検証と、生成ロジックの
   ユニットテストを追加。切替スクリプトは `nmcli` をモックして検証する）
