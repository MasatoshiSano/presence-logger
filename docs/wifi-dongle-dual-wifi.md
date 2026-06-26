# WiFiドングルで dual-WiFi（工場網＋インターネット同時接続）を構築する

USB WiFiドングルを足して、**内蔵WiFiで工場網（Oracle記録）／ドングルでインターネット**を
**同時に**使えるようにする手順。別のラズパイでも再現できるよう、工場網に依存する値
（SSID・IP・サブネット）はすべて**その拠点の `profiles.yaml` 側**に置き、ここでは固定しない。

実績環境: Raspberry Pi 5 / Raspberry Pi OS (Debian 13) / kernel 6.12 /
ドングル ELECOM **WDC-433DU2H2-B**（Realtek **RTL8811AU**, USB ID `056e:4010`）。

---

## 0. 全体像（なぜこの役割分担か）

```
[内蔵WiFi  wlan0] ── 工場AP（例: taden-ot-ap） ── Oracle / NTP   ★never-default
[ドングル wlan1] ── スマホ等のテザリング     ── インターネット   ★default route
```

- 多くの工場OT網は **MACアドレス許可制（ホワイトリスト）**。ラズパイ導入時に登録されるのは
  **内蔵WiFiのMAC**なので、**工場網は内蔵 wlan0 に固定**し、ドングル wlan1 はインターネット用にする
  （= 役割を入れ替える）。MAC許可制でない工場網なら逆でもよいが、本手順は「内蔵=工場」を基本にする。
- ルーティングは「**デフォルト経路＝インターネット側(wlan1)**」「**工場の宛先サブネットだけ wlan0 経由**」。
  工場接続には `ipv4.never-default yes` を付け、デフォルト経路を奪わせない。

> **別ラズパイでの注意**: 工場網のSSID・固定IP・ゲートウェイ・宛先サブネット・Oracle/NTPのIPは
> 拠点ごとに違う。**`taden-ot-ap` という名前や 172.29.1.x / 10.168.252.x をハードコードしない**こと。
> これらは `profiles.yaml`（と接続スクリプトの `PROFILE_NAME` / `FACTORY_SUBNETS`）で与える。

---

## 1. ドライバのインストール（RTL8811AU）

### 1-1. 症状の見分け方
ドングルを挿しても `wlan1` が出ない場合、たいてい次のどれか。

```bash
lsusb | grep -i elecom            # 056e:4010 WDC-433DU2H2-B が見えるか
ip link | grep wlan               # wlan1 が無い
dmesg | grep -iE "pegasus|056e"   # "pegasus ... probe ... failed with error -110" が出る
```

- `056e:4010` は USB-ID DB 上は別物（Elecom LD-USB20＝有線LAN）として登録されているため、
  **`pegasus`（USB有線LANドライバ）が誤マッチして失敗(-110)** し、WiFiとして使えない。
- カーネル内蔵 `rtw88` は 8821**cu**/8821**c** 系のみで、**RTL8811AU は非対応** → 外部ドライバが必要。

### 1-2. 前提パッケージ
```bash
sudo apt-get install -y dkms build-essential git bc raspberrypi-kernel-headers
# ヘッダが現行カーネルと一致しているか確認（build シンボリックリンクがあること）
uname -r ; ls -d /lib/modules/$(uname -r)/build
```

### 1-3. ドライバ取得・USB ID 追加・ビルド
```bash
cd ~
git clone --depth=1 https://github.com/morrownr/8821au-20210708.git ~/8821au
cd ~/8821au
```

`os_dep/linux/usb_intf.c` の **RTL8821 セクション**（既に Elecom の 4007/400E/400F が並んでいる箇所）に、
このドングルの VID:PID を1行追加する。

```c
{USB_DEVICE(0x056E, 0x4010), .driver_info = RTL8821}, /* ELECOM WDC-433DU2H2-B */
```

> **別のドングルを使う場合**: `lsusb` で VID:PID を確認し、その値を入れる。RTL8811AU/8821AU 系なら
> `.driver_info = RTL8821`。チップが違うと別ドライバ（例: RTL8812AU → morrownr/8812au）になる。

ビルド＆DKMS導入（非対話）:
```bash
sudo ./install-driver.sh NoPrompt
```

### 1-4. ドライバオプション（重要：これが無いと association に失敗する）
`/etc/modprobe.d/8821au.conf` の `options` 行を次にする:

```
options 8821au rtw_led_ctrl=1 rtw_country_code=JP rtw_power_mgnt=0
```

- `rtw_country_code=JP` … 未設定だと内部の地域テーブルが壊れ、5GHzで
  **`CTRL-EVENT-ASSOC-REJECT status_code=1`**（AP拒否）になる。日本は `JP`。
- `rtw_power_mgnt=0` … 省電力ONだと associate 直後に
  **4-way handshake を取りこぼして `no-secrets` で切断**する。OFF necessary。

反映:
```bash
sudo modprobe -r 8821au ; sudo modprobe 8821au
cat /sys/module/8821au/parameters/rtw_country_code   # JP と出ること
ip -br link show wlan1                                # wlan1 が出ること
```

NetworkManager 側でも省電力を無効化（任意・保険）:
```bash
sudo tee /etc/NetworkManager/conf.d/wifi-powersave-wlan1.conf >/dev/null <<'EOF'
[connection-wlan1-nopowersave]
match-device=interface-name:wlan1
wifi.powersave=2
EOF
sudo systemctl reload NetworkManager
```

> DKMS 導入なので**カーネル更新時は自動で再ビルド**される。大型カーネル更新の前後で
> `wlan1` が出ないときは `cd ~/8821au && sudo ./install-driver.sh NoPrompt` で入れ直す。

---

## 2. dual-WiFi のルーティング設定

役割分担:

| インターフェース | 用途 | nmcli 接続 |
|---|---|---|
| `wlan0`（内蔵） | 工場網（許可済みMAC） | 工場プロファイル（`never-default` + 工場サブネットのみ個別ルート）|
| `wlan1`（ドングル）| インターネット | テザリング等。**デフォルト経路**を持つ |

### 2-1. 工場接続（内蔵 wlan0・`connect-<profile>.sh` が自動でこの設定にする）
本リポジトリの `desktop/presence-tools/connect-taden-ot-ap.sh` は次の nmcli 設定を作る:

- `connection.interface-name wlan0`（内蔵に固定）
- `ipv4.method manual` / `ipv4.addresses <静的IP>`（拠点の値）
- `ipv4.gateway ""` ＋ `ipv4.never-default yes`（デフォルト経路を奪わない）
- `ipv4.routes "<工場サブネット> <GW>, ..."`（Oracle・DNS/NTP のサブネットだけ wlan0 経由）
- `ipv4.dns ""` ＋ `ipv4.ignore-auto-dns yes`（インターネットのDNSは wlan1 のまま）

**別ラズパイへの適用**: 値はスクリプト内蔵の以下で決まり、拠点ごとに差し替える:
- `PROFILE_NAME`（= 工場SSID = `profiles.yaml` のキー）
- 静的IP / ゲートウェイ … `profiles.yaml` の `wifi.static_ipv4`
- `FACTORY_SUBNETS`（環境変数で上書き可。既定はこの拠点用の値）
  → **その工場のOracle/NTPが属するサブネット**を `"a.b.c.0/24 d.e.f.0/24"` 形式で指定する。

### 2-2. インターネット接続（ドングル wlan1）
テザリング等の接続は **インターフェース固定をしない**でおくと、ドングルが無いときに自動で
内蔵(wlan0)へフォールバックして使える:

```bash
sudo nmcli connection modify "<テザリングSSID>" connection.interface-name ""
```

両方を同時に上げると、工場接続が wlan0 を占有するのでテザリングは自動的に wlan1 へ載る。

### 2-3. 期待されるルーティング（確認）
```bash
ip route
# default via <テザリングGW> dev wlan1            ← インターネット
# <工場サブネット> via <工場GW> dev wlan0  proto static  ← 工場宛だけ
nmcli -t -f DEVICE,STATE,CONNECTION device | grep wlan   # 両方 connected
ping -c2 8.8.8.8                                          # ネット (wlan1)
ip route get <OracleのIP>                                 # ... dev wlan0 になること
```

---

## 3. bridge の dual-WiFi 対応（コード側・対応済み）

bridge は「今どのSSIDか」で工場網か判定する。dual接続では**アクティブSSIDが2つ**になるため、
最初の1つ（インターネット側）を拾うと全イベントを `drop_unknown_ssid` で捨ててしまう。

→ `services/bridge/src/network_watcher.py` を「**アクティブSSIDのうち、設定プロファイルに
一致するもの（=工場SSID）を優先**」するよう修正済み（`parse_active_ssids` ＋ `preferred_ssids`）。
別ラズパイでも、その拠点の工場SSIDが `profiles.yaml` にあれば自動でそれを優先する（追加設定不要）。

確認:
```bash
docker logs --since 2m presence-bridge | grep current_ssid   # 工場SSID になること
docker logs --since 2m presence-bridge | grep merge_committed # Oracleへ書けていること
```

---

## 4. ハマりどころ早見表

| 症状（ログ） | 原因 | 対処 |
|---|---|---|
| `wlan1` が出ない / `pegasus ... error -110` | 誤ドライバ・8811AU非対応 | §1 のドライバ導入 |
| `CTRL-EVENT-ASSOC-REJECT status_code=1` | `rtw_country_code` 未設定（5GHz拒否） | `rtw_country_code=JP` |
| associate直後に切断 / NMが `no-secrets` | 省電力で4-way取りこぼし | `rtw_power_mgnt=0` ＋ NM powersave無効 |
| associate→`reason=1`で即切断（既知の正常網には繋がる）| **AP側のMACフィルタ** | 工場網は内蔵wlan0(許可済みMAC)で使う（本手順の役割分担）。または工場管理者にドングルのMACを登録 |
| 繋がるが `drop_unknown_ssid` で記録されない | dual接続でSSID誤判定 | §3（対応済み）。古いbridgeなら再ビルド |

切り分けの定石: **ドングルを既知の正常網（テザリング等）に繋いでみる**。
繋がれば「ドングルは正常 → 工場AP側の拒否（MACフィルタ）」と確定できる。

---

## 5. 別ラズパイへの適用チェックリスト

1. ドングルを挿し、§1 でドライバ導入（USB ID は `lsusb` で確認。同型 WDC-433DU2H2-B なら `056e:4010`）。
2. その拠点用の `profiles.yaml` を用意（SSID・静的IP・GW・Oracle/NTP）。**taden-ot-ap の値は流用しない**。
3. 接続スクリプトを用意。工場SSIDは `PROFILE_NAME`、宛先サブネットは `FACTORY_SUBNETS` で
   **その工場の値**にする（taden-ot-ap 用の `10.168.252.0/24 192.168.250.0/24` は固定しない）。
4. テザリング接続は `connection.interface-name ""` でフォールバック可にしておく。
5. 接続 → §2-3 と §3 の確認コマンドで、ネット(wlan1)・Oracle(wlan0)・記録の3点を確認。

> **ドングルは presence-logger に必須ではない**。抜いても工場網は内蔵wlan0で繋がり記録できる。
> ドングルは「工場記録中にインターネットも同時に使う」ための追加機能。
