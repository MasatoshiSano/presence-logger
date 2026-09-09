# 新しいハブ Pi のセットアップ — Git に載せられないものをどう再現するか

空の Raspberry Pi OS が入った Raspberry Pi 5 を、**子Pi専用ハブ**として立ち上げるための
運用手順。設計の背景は
[`superpowers/specs/2026-09-09-new-hub-bootstrap-design.md`](superpowers/specs/2026-09-09-new-hub-bootstrap-design.md)
を参照。

> **このドキュメントの状態**: ブートストラップスクリプト（`scripts/bootstrap-hub.sh`）は
> **未実装**（設計確定済み・実装待ち）。そのため各項目に**手動での再現手順を併記**して
> あり、スクリプトが揃う前でもこの文書だけで新機を立ち上げられる。スクリプト実装後は
> 「自動」のコマンド 1 本で置き換わる。
>
> **実装前の制約**: `connect-hime-h-reap.sh` / `disconnect-hime-h-reap.sh` /
> `setup-dongle-ap.sh` は既定値として**現行機の値**（`UFI_103134`・`presence-hub`・
> この拠点の `FACTORY_SUBNETS`）を持つ。新機で値が違う場合は環境変数で上書きして
> 実行するか、スクリプトを直接編集する。`site.env` 駆動になるのは実装後である。

---

## 0. なぜこの文書が要るのか

リポジトリを `git clone` しても、**動く状態にはならない**。次の 3 種類が Git に載って
いないためである。

| 種類 | 例 | 載せない理由 |
|---|---|---|
| **秘密情報** | Oracle パスワード、WiFi PSK | 漏洩リスク |
| **機体固有値** | 固定IP、ホスト名、STA_NO | 機体ごとに違う。共有すると事故る |
| **大きい / 生成物** | IMX500 モデル `.rpk`、`.venv`、SQLite バッファ | リポジトリを肥大させる / 再生成できる |

この文書は、**それぞれを新機のどこに、何を、どうやって置くか**を一覧にしたものである。

---

## 0.5 前提パッケージと実行順序（ここを外すと後段が全部失敗する）

### 導入するもの

```bash
sudo apt-get update
sudo apt-get install -y \
    docker.io docker-compose-plugin \
    python3-yaml \
    mosquitto-clients \
    git rsync \
    dkms build-essential bc raspberrypi-kernel-headers
sudo usermod -aG docker "$USER"      # 反映には再ログイン or reboot が必要
```

**`python3-yaml` は必須。** 次はいずれも **venv ではなくシステムの `python3`** で動く。

| 使う場所 | 用途 |
|---|---|
| `scripts/install.sh` | `profiles.yaml` から SNTP サーバ一覧を組む |
| `connect-hime-h-reap.sh` | root で `profiles.yaml` + `secrets.env` を読む |
| `show-recent-records.sh` | 絞り込みの既定値を読む |
| `pipeline_monitor/config.py` | 設定読み込み（`import yaml`） |

`mosquitto-clients` はパイプライン監視（`mosquitto_sub`）に必須。`lxterminal` と `chromium`
は Raspberry Pi OS Desktop に既定で入っている（ランチャーが使う）。

### 実行順序（3 つの依存関係）

```
① インターネットに繋がっている間に  →  docker compose build
② 子AP(ドングル側) を起動してから   →  docker compose up -d
③ /etc/presence-logger/ を揃えてから →  docker compose up -d
```

1. **ビルドはインターネット接続中に行う。** `services/oracle-jdbc/Dockerfile` は
   `ojdbc11.jar` を `repo1.maven.org` から `curl` で取得する。工場網（HIME-H-REAP）へ
   切り替えた後にビルドすると**必ず失敗する**。
2. **`docker compose up -d` の前に子AP を上げる。** `docker-compose.override.yml` が
   mosquitto を `10.42.0.1:1883` にバインドするため、AP が無いと**そのIPが存在せず
   mosquitto が起動できない**（`restart: unless-stopped` で延々と再試行する）。
3. **`/etc/presence-logger/` を先に全部揃える。** 特に `upcmpflg.override`（§3.4 の罠）。

### ハブでは `--build` を無条件に打たない

```bash
# ✗ 失敗する（detector も一緒にビルドしようとする）
docker compose up -d --build

# ○ ハブで動かす 3 つだけを明示する
docker compose up -d --build mosquitto oracle-jdbc bridge
```

`services/detector/Dockerfile` は `COPY models/efficientdet_lite0.tflite` を含むが、この
`.tflite` は `.gitignore` されており **clone 直後には存在しない**。カメラ無しのハブでは
detector 自体が不要なので、サービスを明示して除外する
（設計書 §6 フェーズ60 の実装後は compose の `profiles:` で自動的に外れる）。

---

## 1. 事前に現行機で採取するもの

新機を触る前に、現行機（`raspberrypi5`）で次を控えておく。**新機に持っていくのは値であって、
ファイルではない**（`/etc/NetworkManager/system-connections/` の丸ごとコピーはしない）。

```bash
# --- 秘密（画面に出す。ファイルに保存しない・チャットに貼らない）---
sudo cat /etc/presence-logger/secrets.env

# --- 工場プロファイルの実値（新機用に固定IPだけ変える）---
sudo cat /etc/presence-logger/profiles.yaml

# --- 現行機の局番（新機で重複させないため）---
sudo grep -A3 station /etc/presence-logger/device.yaml

# --- 子Piの一覧と、各子の STA_NO（重複チェックの基準）---
cat fleet/children.conf
scripts/fleet-status.sh

# --- WiFi 切替に並べる接続名 ---
nmcli -t -f NAME,TYPE connection show | grep wireless

# --- 配布中のモデル（新機へ rsync する対象）---
find models -name '*.rpk' -o -name 'packerOut.zip' | sort
```

---

## 2. 一覧 — どこに何を置くか

パスは実ユーザーが `pi`、リポジトリが `/home/pi/projects/presence-logger` の場合。

| # | 絶対パス | 中身 | 権限 | 入手方法 |
|---|---|---|---|---|
| 1 | `~/projects/presence-logger/site.env` | 機体固有値 | `600 pi:pi` | 手入力（§3.1） |
| 2 | `~/projects/presence-logger/wifi-switch.conf` | WiFi切替の定義 | `600 pi:pi` | 手入力（§3.2） |
| 3 | `/etc/presence-logger/secrets.env` | 全パスワード・PSK | `600 root:docker` | 現行機から**手入力**（§3.3） |
| 4 | `/etc/presence-logger/profiles.yaml` | 工場プロファイル | `640 root:root` | site.env から生成（§3.4） |
| 5 | `/etc/presence-logger/device.yaml` | device_id + 親STA_NO | `644 root:root` | site.env から生成（§3.4） |
| 6 | `/etc/presence-logger/bridge.yaml` | bridge 動作 | `644 root:root` | `config/site/` から複製（Gitにある） |
| 7 | `/etc/presence-logger/detector.yaml` | detector 動作（ハブでは未使用） | `644 root:root` | `config/site/` から複製 |
| 8 | `/etc/presence-logger/upcmpflg.override` | UPCMPFLG 上書き | pi 書込可 | §3.4 で生成 |
| 9 | `/etc/presence-logger/wallets/` | Oracle Wallet | `700 root:docker` | wallet 認証時のみ（HHC001 は不要） |
| 10 | `~/projects/presence-logger/.venv/` | Python 仮想環境 | `755 pi:pi` | 新機で再生成（§3.5） |
| 11 | `~/projects/presence-logger/models/**/*.rpk` `packerOut.zip` | 子PiのIMX500モデル | `644 pi:pi` | 現行機から rsync（§3.6） |
| 12 | `~/.ssh/id_ed25519` `.pub` | 子Piへの SSH 鍵 | `600` / `644` | 新機で新規生成（§3.7） |
| 13 | `~/.ssh/config` | 子の到達名定義 | `600` | 手入力（§3.7） |
| 14 | `~/.ssh/known_hosts` | 子の host key | `600` | `ssh-keyscan`（§3.7） |
| 15 | `~/projects/presence-logger/fleet/known_macs.json` | TOFU学習MAC | `644` | **空でよい**（運用で自動生成） |
| 16 | `/var/lib/presence-logger/` | bridge の SQLite バッファ | `755 root` | **コピーしない**（§3.8） |
| 17 | `/etc/modprobe.d/8821au.conf` | ドングルのドライバ設定 | `644 root:root` | §3.9 |
| 18 | `~/.config/fcitx5/profile` | 日本語入力の設定 | `600 pi:pi` | §3.10 |
| 19 | `/etc/NetworkManager/system-connections/*.nmconnection` | 保守用WiFiの接続定義 | `600 root:root` | secrets.env から生成（§3.11） |

---

## 3. 再現手順

### 3.1 `site.env`（#1）

```bash
cd ~/projects/presence-logger
cp site.env.example site.env
chmod 600 site.env
nano site.env
```

各値の決め方:

| 変数 | 決め方 |
|---|---|
| `HUB_HOSTNAME` | 既存の親・子と重複しない名前。`device_id` と MQTT `client_id` を決めるので**重複は事故**になる |
| `HUB_MODE` | カメラ無しなら `1` |
| `FACTORY_SSID` 等 | 現行機の `profiles.yaml` から転記 |
| `FACTORY_IP` | **情シスへ申請した新機の固定IP**。現行機（`172.22.13.17/24`）と必ず別。内蔵 wlan0 の MAC を許可登録してもらう必要がある |
| `PARENT_STA_NO1-3` | ハブでは Oracle に書かれないが**必須項目**。将来カメラを付けたときに備え、**他機・全子Piと重複しない値**を採番する。現行機と同じ値を入れてはならない |
| `AP_SSID` / `AP_GW_IP` | **引っ越し**なら現行機と同じ値（子は無変更で繋ぎ替わる）。**増設**なら別の値（子側の変更が要る） |
| `ADMIN_SSID` | ここから離れると遠隔操作できなくなる接続。現行機では `F660P-sDcS-A` |

内蔵 WiFi の MAC は次で確認する（申請に使う）:

```bash
cat /sys/class/net/wlan0/address
```

### 3.2 `wifi-switch.conf`（#2）

```bash
cp wifi-switch.conf.example wifi-switch.conf
chmod 600 wifi-switch.conf
nano wifi-switch.conf
```

`<PSKキー名>` を `-` にした行は nmcli プロファイルを作らない。**`presence-hub-ap` は必ず `-`
にする**（AP は子AP構築の工程が作るため、ここで二重に作ると設定が食い違う）。

### 3.3 `secrets.env`（#3）— 最重要

**現行機の画面に表示させ、新機で手入力する。** ファイルをネットワーク越しにコピーしたり、
チャット・チケット・コミットに貼ったりしない。

現行機:
```bash
sudo cat /etc/presence-logger/secrets.env
```

新機:
```bash
sudo install -d -m 0755 /etc/presence-logger
sudo touch /etc/presence-logger/secrets.env
sudo chown root:docker /etc/presence-logger/secrets.env
sudo chmod 600 /etc/presence-logger/secrets.env
sudo nano /etc/presence-logger/secrets.env
```

必要なキー:

| キー | 用途 | 必須 |
|---|---|---|
| `ORACLE_PASSWORD_HHC` | HIME-H-REAP の Oracle(HHC001) | ◎ |
| `WIFI_PSK_HIMEREAP` | 工場WiFi の PSK（接続スクリプトが root で読む） | ◎ |
| `WIFI_AP_PSK` | 子Pi用 AP の PSK。**引っ越しなら現行機と同じ値**（違うと子が繋げない） | ◎ |
| `WIFI_PSK_*` | WiFi切替に並べる各接続の PSK（`wifi-switch.conf` の 5 列目で参照する名前） | 切替を使うなら |
| `ORACLE_PASSWORD_A` / `_B` / `_D`、`WALLET_PASSWORD_B` | 他拠点プロファイルを使う場合のみ | － |

> `docker` グループが無いとエラーになる。docker 導入後に実行するか、先に
> `sudo groupadd -f docker` しておく。

### 3.4 `/etc/presence-logger/` の YAML 群（#4〜#8）

**自動（実装後）**: `sudo bash scripts/bootstrap-hub.sh 40`

**手動（現在）**:
```bash
cd ~/projects/presence-logger
sudo bash scripts/install.sh                      # ひな型を配置 + timesyncd 設定
sudo cp config/site/bridge.yaml   /etc/presence-logger/bridge.yaml
sudo cp config/site/detector.yaml /etc/presence-logger/detector.yaml
sudo cp config/site/profiles.yaml /etc/presence-logger/profiles.yaml
sudo cp config/site/device.yaml   /etc/presence-logger/device.yaml
sudo chown root:root /etc/presence-logger/*.yaml
sudo chmod 644 /etc/presence-logger/*.yaml
sudo chmod 640 /etc/presence-logger/profiles.yaml

# ★ここから新機用に必ず書き換える
sudo nano /etc/presence-logger/profiles.yaml   # wifi.static_ipv4.address を新機のIPに
sudo nano /etc/presence-logger/device.yaml     # station を PARENT_STA_NO1-3 に

# UPCMPFLG の実行時上書き（pi が sudo 無しで変えられるファイル）
sudo touch /etc/presence-logger/upcmpflg.override
sudo chown pi:pi /etc/presence-logger/upcmpflg.override
```

> **罠**: `scripts/install.sh` は `upcmpflg.override` を作らないが、`docker-compose.yml` は
> これを**ファイルとして**マウントする。先に作っておかないと **Docker が同名のディレクトリを
> 勝手に作り**、bridge が上書き値を読めなくなる。できてしまったら
> `sudo rmdir /etc/presence-logger/upcmpflg.override` → `touch` し直し →
> `docker compose up -d --force-recreate bridge`。

**`config/site/profiles.yaml` をそのまま使ってはいけない。** これは現行機の実値
（固定IP `172.22.13.17/24`）であり、同一ネットワークで 2 台が同じ IP を名乗ると衝突する。

配置後の検証:
```bash
sudo python3 -c "import yaml;print(yaml.safe_load(open('/etc/presence-logger/profiles.yaml')).keys())"
ls -l /etc/presence-logger/
```

### 3.5 `.venv`（#10）

`fleet-ui.service` が `/home/pi/projects/presence-logger/.venv/bin/python` を**絶対パスで**
起動するため、無いとフリート監視が動かない。

`fleet_ui` の依存は**標準ライブラリだけ**（`json` / `subprocess` / `shlex` / `http.server` /
`pathlib` 等）。したがって空の venv で足りる。システムの `python3-yaml` も見えるように
しておくと、後から他のツールを venv 側で動かしたくなったときに困らない。

```bash
cd ~/projects/presence-logger
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -U pip

# テストを流す場合のみ（本番稼働には不要）
.venv/bin/pip install -r requirements-dev.txt
```

検証:
```bash
.venv/bin/python -c "import fleet_ui.server; print('fleet_ui OK')"
```

### 3.6 IMX500 モデル（#11）

`.rpk` と `packerOut.zip` は `.gitignore` されている（親ローカル保管）。現行機から運ぶ。

```bash
# 新機で実行（現行機の到達名を <現行機> に読み替える）
rsync -av --progress <現行機>:~/projects/presence-logger/models/ \
                     ~/projects/presence-logger/models/
scripts/deploy-model.sh --list      # 版が見えることを確認
```

現行機にある版（2026-09-09 時点）: `signal_tower/20260422`、`signal_tower/20260730`、
`object_detection/20260422`。

### 3.7 SSH 鍵と子への到達設定（#12〜#14）

**現行機の鍵をコピーせず、新機で新しく作る**（鍵の複製は追跡性を失う）。ただし
**順序が決定的に重要**である。

> **⚠ 引っ越しの成否を分ける点**: 既存の子は**旧親の公開鍵しか知らない**。新ハブで新しい鍵を
> 作っただけでは、`fleet-status.sh` も `deploy-child.sh` も**フリート管理の登録ウィザードも
> 全工程が失敗する**（`fleet_ui/provision.py:44` が `ssh -o BatchMode=yes` を使うため、
> パスワードを聞くことすらしない）。**旧親がまだ子に到達できるうちに**、新ハブの公開鍵を
> 各子へ入れておくこと。子が新APへ移った後では、旧親からも新ハブからも入れられなくなる。

```bash
# ① 新ハブで鍵を作り、公開鍵を控える
ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub

# ② 旧親で実行（子がまだ旧APに繋がっている間に！）
for h in zero2 pizero2w-2.local; do
  ssh "$h" 'cat >> ~/.ssh/authorized_keys' <<< '<①で控えた公開鍵1行>'
done

# ③ 新ハブで到達名を定義
cat >> ~/.ssh/config <<'EOF'
Host zero2
    HostName 10.42.0.52
    User pi
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking accept-new
EOF
chmod 600 ~/.ssh/config

# ④ 子が新ハブのAPに繋がった後で host key を登録
ssh-keyscan -H pizero2w-2.local >> ~/.ssh/known_hosts

# ⑤ 検証（パスワードを聞かれずに応答すること）
ssh -o BatchMode=yes zero2 'hostname'
```

②を飛ばしてしまった場合の復旧は、子にキーボードとモニタを繋いで直接
`authorized_keys` に追記するか、子のパスワード認証が有効なら
`ssh-copy-id -i ~/.ssh/id_ed25519.pub pi@<子のIP>` を試す（既定で無効な場合がある）。

`~/.ssh/config` の `HostName` は**現時点のIP**でしかない。子のIPは AP の DHCP で変わるため、
到達しなくなったら `ip -4 neigh | grep <APサブネット>` で今のIPを調べて書き換える。

### 3.8 `/var/lib/presence-logger/`（#16）— コピーしない

bridge の SQLite バッファ（未送信レコードのカーソル）。**現行機からコピーすると同じ
レコードを二重に Oracle へ書く。**

正しい順序:

```bash
# 1. 現行機で未送信が捌けたことを確認（0 になるまで待つ）
docker exec presence-bridge python3 -c "
import sqlite3
c=sqlite3.connect('/var/lib/presence-logger/bridge_record_buf.db')
print('未送信:', c.execute(\"select count(*) from record_inbox where status='received'\").fetchone()[0])"

# 2. 0 になってから新ハブへ切り替える（子の引っ越し手順へ）
```

新機側は空のディレクトリでよい（`scripts/install.sh` が作る）。

### 3.9 ドングルのドライバ設定（#17）

素の Raspberry Pi OS では `wlan1` が出てこない（`pegasus` が `056e:4010` に誤マッチする）。

**自動（実装後）**: `sudo bash scripts/bootstrap-hub.sh 30`

**手動（現在）**: [`wifi-dongle-dual-wifi.md`](wifi-dongle-dual-wifi.md) の §1 に従う。要点のみ:

```bash
sudo apt-get install -y dkms build-essential git bc raspberrypi-kernel-headers
git clone --depth=1 https://github.com/morrownr/8821au-20210708.git ~/8821au
# os_dep/linux/usb_intf.c の RTL8821 セクションに1行追加:
#   {USB_DEVICE(0x056E, 0x4010), .driver_info = RTL8821}, /* ELECOM WDC-433DU2H2-B */
cd ~/8821au && sudo ./install-driver.sh NoPrompt

sudo tee /etc/modprobe.d/8821au.conf >/dev/null <<'EOF'
options 8821au rtw_led_ctrl=1 rtw_country_code=JP rtw_power_mgnt=0
EOF
sudo modprobe -r 8821au; sudo modprobe 8821au

# 検証（3つとも通ること）
cat /sys/module/8821au/parameters/rtw_country_code       # JP
ip -br link show wlan1                                    # wlan1 が出る
iw phy $(cat /sys/class/net/wlan1/phy80211/name) info | grep -- '\* AP'
```

`rtw_country_code=JP` が無いと 5GHz で AP に拒否され、`rtw_power_mgnt=0` が無いと
4-way handshake を取りこぼして切断する。**どちらも省略できない。**

### 3.10 日本語入力の設定（#18）

**自動（実装後）**: `sudo bash scripts/bootstrap-hub.sh 10`

**手動（現在）**:
```bash
sudo apt-get install -y fcitx5 fcitx5-mozc fcitx5-frontend-gtk3 fcitx5-frontend-gtk4 \
    fcitx5-frontend-qt5 fcitx5-frontend-qt6 fcitx5-config-qt mozc-utils-gui fonts-noto-cjk

sudo sed -i 's/^XKBLAYOUT=.*/XKBLAYOUT="jp"/' /etc/default/keyboard
sudo setupcon      # コンソール側へ反映。デスクトップ側は再ログイン/再起動で反映される

im-config -n fcitx5          # ★ 現行機の ~/.xinputrc をコピーしないこと（下記）

mkdir -p ~/.config/fcitx5 && chmod 700 ~/.config/fcitx5
cat > ~/.config/fcitx5/profile <<'EOF'
[Groups/0]
Name=Default
Default Layout=jp
DefaultIM=mozc

[Groups/0/Items/0]
Name=keyboard-jp
Layout=

[Groups/0/Items/1]
Name=mozc
Layout=

[GroupOrder]
0=Default
EOF
chmod 600 ~/.config/fcitx5/profile
```

**ログアウト → 再ログインするまで日本語入力はできない。** labwc(Wayland) セッションでは
`GTK_IM_MODULE` 等を撒くのがセッション開始時の `im-launch` だからである。

再ログイン後、**実際に確認する**（設定しただけでは確かめたことにならない）:

```bash
env | grep -E 'GTK_IM_MODULE|QT_IM_MODULE|XMODIFIERS'   # fcitx が出ること
pgrep -a fcitx5                                          # fcitx5 -d が動いていること
```

- テキストエディタで「ひらがな」を変換入力できること
- **記号キーが刻印どおり入ること**（`@` `:` `_` など）。ずれている場合は `/etc/default/keyboard`
  の反映が不完全なので、`raspi-config` の *Localisation Options → Keyboard* で日本語配列を
  選び直して再起動する

fcitx5 が既に動いている状態で `profile` を書いた場合は、再ログインの代わりに `fcitx5 -r`
で読み直させてもよい。

> **現行機の `~/.xinputrc` をコピーしてはならない。** 中身は `run_im fcitx`（fcitx **4**）
> だが fcitx4 は入っておらず、im-config の auto フォールバックが偶然 fcitx5 を拾って
> 動いているだけの残骸である。新機では `im-config -n fcitx5` に生成させる。

### 3.11 保守用 WiFi の接続定義（#19）

**現行機の `/etc/NetworkManager/system-connections/` を丸ごとコピーしない。** 全SSIDの PSK が
平文で入っており、不要な古い接続も混ざるため。

**自動（実装後）**: `sudo bash scripts/bootstrap-hub.sh 70` が `wifi-switch.conf` と
`secrets.env` から必要な分だけ作る。

**手動（現在）**: 接続ごとに 1 回だけ作る。
```bash
sudo nmcli connection add type wifi con-name "F660P-sDcS-A" ifname wlan0 \
    ssid "F660P-sDcS-A" \
    802-11-wireless-security.key-mgmt wpa-psk \
    802-11-wireless-security.psk "<secrets.env の WIFI_PSK_F66>" \
    connection.autoconnect yes
```

デスクトップの WiFi 切替は「**保存済みの接続へ切り替えるだけ**」なので、この登録が済むまで
アイコンを押しても失敗する。

### 3.12 コンテナの起動と常駐化（欠かせない最後の工程）

§0.5 の順序（インターネット中にビルド → 子AP を上げる → `/etc/presence-logger/` を揃える）
を満たしてから実行する。

```bash
cd ~/projects/presence-logger
docker compose up -d --build mosquitto oracle-jdbc bridge
docker ps --format '{{.Names}}  {{.Status}}'      # 3つが Up であること
```

再起動しても復活するようにする。`scripts/install.sh` が置く systemd unit は
`WorkingDirectory=/opt/presence-logger` を指しているので、実ツリーへ向け直す drop-in が要る。

```bash
sudo REPO_DIR=~/projects/presence-logger bash desktop/presence-tools/setup-autostart.sh
systemctl is-enabled presence-logger.service       # enabled
```

> `setup-autostart.sh` は drop-in に `ExecStartPost=-/usr/bin/docker stop presence-detector`
> を書く。カメラ無しのハブに detector は存在しないが、行頭の `-` により失敗しても無視される
> ので害はない（実装後は `HUB_MODE` で出し分ける）。

フリート監視の常駐:

```bash
sudo install -m 644 fleet_ui/systemd/fleet-ui.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now fleet-ui.service
ss -ltn | grep 8090                                # 127.0.0.1:8090 のみ
```

### 3.13 運用上の注意（新機で最初に踏みやすい罠）

- **`children.conf` は Git 追跡ファイル。** フリート管理で子を登録すると作業ツリーが汚れ、
  `scripts/deploy-parent.sh` が「作業ツリーに未コミット変更があります」で**実行を拒否する**。
  子を登録したら `git add fleet/children.conf && git commit` してから親の更新を行う。
- **既存の子が1台でも到達不能だと、フリート管理に登録候補が出ない。** これは仕様である
  （稼働中の子がIPドリフトで「未登録の端末」に見えたとき、本番機を誤って再プロビジョニング
  しないための保護）。引っ越し中は**全子が新APへ繋ぎ替わってから**登録操作を行う。
- **フリート管理は AP を `wlan1` 決め打ちで見ている**（`fleet_ui/discovery.py:32`、
  `fleet_ui/provision.py:134`）。新機でドングルが `wlan0` として現れると**子が1台も
  表示されない**。`ip -br link` で確認し、想定と違う場合は設計書 §7 の `AP_DEV`
  環境変数化を先に実装する。

---

## 4. 完了確認

新機で次が全て通ること（詳細は設計書 §10 の受入基準）。

```bash
# 日本語入力（再ログイン後）
env | grep -E 'GTK_IM_MODULE|XMODIFIERS'      # fcitx が出る

# ドングルと子AP
ip -br link show wlan1
nmcli -t -f DEVICE,STATE,CONNECTION device | grep wlan

# 子Pi
scripts/fleet-status.sh                        # exit 0（1=STA_NO重複 / 2=検査不能）

# コンテナ（detector が居ないこと）
docker ps --format '{{.Names}}  {{.Status}}'

# フリート監視がローカル限定で待ち受けていること
ss -ltn | grep 8090                            # 127.0.0.1:8090 のみ
```

`fleet-status.sh` の **`2` を `0` と混同しないこと**。「安全」ではなく「確かめられていない」
を意味する。

---

## 5. 秘密情報の取り扱い（厳守）

- `secrets.env` の値を **Git・コミットメッセージ・ログ・チャット・チケットに書かない**
- 新機へは**画面表示 → 手入力**で運ぶ。scp / メール / 共有フォルダを経由させない
- 一時ファイルに書き出した場合は `shred -u` で消す
- `site.env` と `wifi-switch.conf` は `.gitignore` 済みだが、**コミット前に `git status` で
  混入していないことを確認する**
