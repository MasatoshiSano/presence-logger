# パターンB — SDカード完全コピーから2台目のハブを作る

クリーンインストールから積み上げる[パターンA](NEW-HUB-SETUP.md)に対し、現行機の SD カードを
`dd` 等で丸ごと複製して2台目を作る手順。**速いが危険**で、変更漏れは「動かない」ではなく
**「動いているように見えて壊れている」**形で現れる。

---

## 0. どちらを選ぶか

| | パターンA（クリーンインストール） | パターンB（SDクローン） |
|---|---|---|
| 所要時間 | 長い（apt・DKMS ビルド・設定投入） | 短い（複製 + 変更のみ） |
| 機体固有値 | **「無い」状態から積む**ので取り違えようがない | **全部複製される**。消し忘れが事故になる |
| ドングルドライバ | ビルドが要る | 既にある |
| 失敗の見え方 | その場で止まる（分かりやすい） | **無警告で壊れる**（レコード欠落・相互切断） |
| 推奨 | ◎ | △（急ぎのときのみ。本章を全部消化できるなら） |

> `docs/DEPLOY.md` に記録されている「2台目投入時の事故」は、**子Pi の SD 完全コピー**で
> 実際に起きたものである（ホスト名・STA_NO・mDNS 名が同時に衝突し、2台が互いを蹴り合う
> 無限ループになった）。親でも同じ構図が成立する。

---

## 1. 【最重要】電源を入れる前にやること

**クローンしたカードを、変更前に既存機の電波圏内で起動してはならない。** 初回起動で
次が自動的に起きるためである（すべて現行機の実測値）。

| 起動時に起きること | 実測の根拠 |
|---|---|
| **SSID `presence-hub` の AP を立てる** | `presence-hub-ap` は `autoconnect=yes`。子がどちらに繋ぐか不定になる |
| コンテナが起動し、**クローンされたバッファの未送信行を送ろうとする** | `presence-logger.service` は `enabled` |
| mDNS で同じ名前を名乗り、**既存機の avahi が自分を勝手に改名する** | `DEPLOY.md` に記録済みの実害 |
| 保存済み WiFi に勝手に繋ぐ | `UFI_103134` / `F660P-sDcS-A` / `GallaxyS23FE` はいずれも `autoconnect=yes` |

安全な進め方は次のいずれか。

- **推奨**: カードを別マシン（PC / もう1台の Pi）にマウントし、**オフラインで**§3 の編集を行う
- 次善: **ドングルを外し、既存機の電波が届かない場所で**初回起動して §3 を行う
  （内蔵 wlan0 が保存済み WiFi に繋ぐのは止められないが、AP の二重立ち上げは防げる）

---

## 2. 変更が必要なもの（実測に基づく完全一覧）

危険度 **致命的** = 既存の本番稼働を壊す / **高** = 誤ったデータや到達不能を生む /
**中** = 後で効いてくる / **低** = 実害は限定的。

| # | 対象 | 複製されるとどうなるか | 危険度 |
|---|---|---|---|
| 1 | `/etc/hostname`、`/etc/hosts` の `127.0.1.1` 行 | `device_id` と MQTT `client_id` が同一になり、**ブローカーが既存接続を切断して2台が互いを蹴り合う** | **致命的** |
| 2 | NM プロファイル `presence-hub-ap`（`autoconnect=yes` / SSID `presence-hub` / mode ap / `wlan1` 固定） | **同一SSIDのAPが2つ**立ち、子がどちらに繋ぐか不定になる | **致命的** |
| 3 | 工場網の MAC 許可登録 | **複製されない**（ハードウェア）。新機の内蔵 wlan0 の MAC を申請しないと工場網に繋がらない | **致命的** |
| 4 | NM プロファイル `HIME-H-REAP`（`wlan0` 固定 / `manual` / **`172.22.13.17/24`** / hidden / `never-default` / `autoconnect=no`） | **固定IP衝突**。`autoconnect=no` なので即座には起きないが、接続ボタンを押した瞬間に衝突する | **高** |
| 5 | `connection.interface-name` の割り当て（`HIME-H-REAP`→`wlan0`、`presence-hub-ap`→`wlan1`） | 新機でドングルが `wlan0` として現れると、**工場プロファイルがドングルに乗り MAC 許可制で弾かれる**。AP は内蔵に乗ろうとして失敗する | **高** |
| 6 | `/etc/ssh/ssh_host_*_key*` | 2台が**同じホスト鍵**を持つ。`known_hosts` が両者を区別できず、なりすまし検知が働かない | **高** |
| 7 | `/var/lib/presence-logger/*.db`（`bridge_record_buf.db` 他） | **未送信行を抱えたまま複製される**。クローンが既知SSIDに繋ぐと、そのプロファイルの Oracle へ流し込む | **高**（別拠点時）／中（同一拠点） |
| 8 | `/etc/presence-logger/device.yaml` の `station` | ハブモードでは Oracle に書かれないが、**カメラを挿した瞬間に既存機と衝突**する（無警告のレコード欠落） | **中**（遅延爆弾） |
| 9 | `/etc/presence-logger/profiles.yaml` の `wifi.static_ipv4` | #4 と同じ値の二重管理。片方だけ直すと食い違う | **中** |
| 10 | `/etc/machine-id`（`/var/lib/dbus/machine-id` はこれへのシンボリックリンク） | systemd / journal の識別子。DHCPv6 の DUID や DHCP client-id の生成に使われることがあり、**同じリースを掴む可能性**がある | **中** |
| 11 | `fleet/children.conf`、`fleet/known_macs.json` | **2台の親が同じ子を自分のものだと思う**。両方から `deploy-child.sh` を打つと配布が競合する | **中** |
| 12 | `~/.ssh/id_ed25519`（秘密鍵） | 子から見て2台を区別できない。片方の鍵を失効させると**両方が失効**する | **中** |
| 13 | `/etc/presence-logger/secrets.env` | **同一拠点ならそのままが正しい**（`WIFI_AP_PSK` が違うと子が繋げない）。別拠点なら全面差し替え | 用途次第 |
| 14 | PARTUUID（`dc29b882-01` / `-02`） | `dd` クローンだと同一。別マシンで使う限り実害はないが、両方のカードを1台に挿すと曖昧になる | 低 |
| 15 | デスクトップ資産（`WiFi切替` 等の `/home/pi` 前提パス） | ユーザー名が同じなら実害なし | 低 |

### #7 について — どこまで危険か

`bridge` の書き込みは `MERGE ... ON (MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS)`
`WHEN NOT MATCHED THEN INSERT` のみで、**`WHEN MATCHED` 節を持たない**
（`services/bridge/src/oracle_client.py:70-79`）。したがって**同じ行の再送は Oracle 側では
何も起こらない**（冪等）。同一拠点で2台目を作る限り、二重書き込みにはならない。

**危険なのは別拠点へ持って行く場合である。** `record_inbox` の行は「どのプロファイル向けか」
を持たない。`record_sender.run_once()` は**その時点で繋がっている既知プロファイル**の Oracle へ
未送信行を流す。よって**現行拠点の記録が、移設先の別の Oracle へ書き込まれる**。
`unknown_ssid_policy: drop` は新規受信を守るだけで、**既にバッファに入っている行は守らない**。

→ **別拠点へ持って行くなら、バッファの消去は必須。**

---

## 3. 変更手順（オフライン編集）

カードを別マシンにマウントした前提。`$ROOT` をルートパーティションのマウント先とする。

```bash
ROOT=/mnt/clone            # 例。実際のマウント先に読み替える
```

### 3.1 ホスト名（#1）

```bash
echo 'presence-hub-2' | sudo tee $ROOT/etc/hostname
sudo sed -i 's/^127\.0\.1\.1[[:space:]].*/127.0.1.1\tpresence-hub-2/' $ROOT/etc/hosts
grep 127.0.1.1 $ROOT/etc/hosts        # 確認
```

### 3.2 AP を自動起動させない（#2）

**引っ越し（既存の子を新ハブへ移す）の場合も、まずは止めておく。** 旧ハブの AP を落として
から新ハブの AP を上げる、という順序を守るためである。

```bash
sudo sed -i 's/^autoconnect=true/autoconnect=false/' \
    "$ROOT/etc/NetworkManager/system-connections/presence-hub-ap.nmconnection"
# autoconnect 行が無い場合は [connection] セクションへ追記する
sudo grep -A2 '\[connection\]' "$ROOT/etc/NetworkManager/system-connections/presence-hub-ap.nmconnection"
```

**増設（2台のハブを同時稼働）の場合は、SSID とサブネットも変える。**

```bash
# SSID を変える（子側の接続先変更が必要になる）
sudo sed -i 's/^ssid=presence-hub$/ssid=presence-hub-2/' \
    "$ROOT/etc/NetworkManager/system-connections/presence-hub-ap.nmconnection"
```

### 3.3 工場網の固定IP（#4・#9）

```bash
# NM プロファイル側
sudo sed -i 's|^address1=172\.22\.13\.17/24.*|address1=172.22.13.18/24|' \
    "$ROOT/etc/NetworkManager/system-connections/HIME-H-REAP.nmconnection"

# アプリ側（同じ値の二重管理になっている。両方直す）
sudo sed -i 's|172\.22\.13\.17/24|172.22.13.18/24|' $ROOT/etc/presence-logger/profiles.yaml
grep -n "172.22.13" $ROOT/etc/presence-logger/profiles.yaml
```

**この IP は情シスへ申請した新機用の値であること。** 併せて新機の内蔵 wlan0 の MAC を
登録してもらう（#3）。MAC は起動後に `cat /sys/class/net/wlan0/address` で確認する。

### 3.4 SSH ホスト鍵を作り直す（#6）

削除しておけば、初回起動時に `ssh-keygen` が自動で新規生成する。

```bash
sudo rm -f $ROOT/etc/ssh/ssh_host_*
```

既存機の `~/.ssh/known_hosts` からは、新機用の項目を後で登録し直す。

### 3.5 machine-id を作り直す（#10）

**空ファイルにする**（削除ではない）。systemd が初回起動時に生成する。

```bash
sudo truncate -s 0 $ROOT/etc/machine-id
ls -l $ROOT/var/lib/dbus/machine-id     # /etc/machine-id へのシンボリックリンクであること
```

### 3.6 親自身の STA_NO（#8）

ハブモードでは Oracle に書かれないが、**将来カメラを付けたときの遅延爆弾**になる。今のうちに
既存機・全子Piと重複しない値へ変える。

```bash
sudo nano $ROOT/etc/presence-logger/device.yaml     # station を新機用の値に
```

### 3.7 記録バッファを消す（#7）

```bash
# 別拠点へ持って行く場合は必須。同一拠点でも、他機の履歴が監視画面に出て紛らわしいので推奨
sudo rm -f $ROOT/var/lib/presence-logger/*.db \
           $ROOT/var/lib/presence-logger/*.db-wal \
           $ROOT/var/lib/presence-logger/*.db-shm \
           $ROOT/var/lib/presence-logger/*.bak-*
```

> **同一拠点の引っ越しで、旧機に未送信行が残っている場合**は、消す前に旧機側で送り切ること
> （[NEW-HUB-SETUP.md §3.8](NEW-HUB-SETUP.md) の確認コマンド）。クローン側で消すのは
> 「二重に持たない」ためであって、送信義務は旧機に残る。

### 3.8 フリート情報を整理する（#11）

```bash
# 増設（子を分ける）場合: 新ハブが担当しない子を children.conf から外す
sudo nano $ROOT/home/pi/projects/presence-logger/fleet/children.conf
# TOFU の学習MACは作り直させる
sudo rm -f $ROOT/home/pi/projects/presence-logger/fleet/known_macs.json
```

引っ越し（既存の子をそのまま引き継ぐ）なら `children.conf` は**そのままでよい**。

### 3.9 SSH ユーザー鍵（#12）

同一拠点の引っ越しなら、**複製された鍵をそのまま使うのが実務的**である（子が既にこの鍵を
信頼しているため、[NEW-HUB-SETUP.md §3.7](NEW-HUB-SETUP.md) の鍵配布工程を丸ごと省ける）。
これがパターンB の数少ない明確な利点である。

追跡性を優先して作り直す場合は、**旧親がまだ子に到達できるうちに**新しい公開鍵を各子へ
配ること。配る前に旧鍵を消すと、どちらの親からも子に入れなくなる。

```bash
# 作り直す場合のみ
sudo rm -f $ROOT/home/pi/.ssh/id_ed25519 $ROOT/home/pi/.ssh/id_ed25519.pub
```

---

## 4. 起動後にやること

### 4.1 インターフェース名の確認（#5）— 最初に見る

```bash
ip -br link show                       # wlan0 / wlan1 が何か
cat /sys/class/net/wlan0/address       # 内蔵の MAC（工場網の申請に使う）
ethtool -i wlan0 2>/dev/null | grep driver    # brcmfmac なら内蔵
ethtool -i wlan1 2>/dev/null | grep driver    # rtl8821au ならドングル
```

**`wlan0` が `rtl8821au`（ドングル）になっていたら、そのまま進めてはいけない。**
NM プロファイルは `connection.interface-name` でインターフェースを固定しているため、
工場プロファイルがドングルに乗って MAC 許可制で弾かれる。次のいずれかで対処する。

- 各プロファイルの `interface-name` を実態に合わせて入れ替える
- udev で命名を固定する（`/etc/udev/rules.d/` で MAC → 名前を固定）

### 4.2 一意性の確認

既存機と新機の両方で実行し、**すべて異なる**ことを確認する。

```bash
hostname
cat /etc/machine-id
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
cat /sys/class/net/wlan0/address
grep -n "address1=" /etc/NetworkManager/system-connections/HIME-H-REAP.nmconnection
```

### 4.3 AP が二重に立っていないこと

```bash
# 新機で
nmcli -t -f NAME,DEVICE connection show --active | grep presence-hub
# 手元の別端末や子Piから
nmcli dev wifi list | grep presence-hub      # 同名が2つ見えないこと
```

### 4.4 既存機側の健全性

```bash
# 既存機で実行。avahi が勝手に改名していないこと
hostname
avahi-resolve -n "$(hostname).local"
scripts/fleet-status.sh                       # exit 0
```

### 4.5 以降は パターンA と同じ

[NEW-HUB-SETUP.md §4 完了確認](NEW-HUB-SETUP.md) の 14 項目をそのまま実施する。
クローンなのでパッケージ導入（§0.5）とドライバ導入（§3.9）は不要。

---

## 5. パターンB 特有の落とし穴

- **「変更したつもり」が最も危険。** §4.2 の一意性確認を、既存機と新機を**並べて**実行すること。
  片方だけ見ても気づけない。
- **フリート管理の登録ウィザードは親のクローンには使えない。** あれは**子**を登録するための
  ものである（`fleet_ui/server.py:174-176` はインベントリに載っていない端末しか受け付けない）。
- **`WIFI_AP_PSK` も複製される。** 子Pi の Web UI（`child/web_server.py`）は全インターフェースに
  bind し**認証を持たない**ため、`/wifi_connect` や `/shutdown` を誰でも叩ける。これを守って
  いるのは AP の WPA2 PSK だけである。**カードを複製するということは、この鍵を複製すること**
  でもある。廃棄するカードは物理的に破壊するか、少なくとも `secrets.env` を消去すること。
- **クローン元が「検証中の値」で動いていることがある。** `config/site/README.md` が記録して
  いるとおり、実機の `detector.yaml`（debounce）や `device.yaml`（station）は本番想定値と
  意図的に異なる場合がある。クローンはその状態ごと複製する。
