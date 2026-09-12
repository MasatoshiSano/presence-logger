# 新しいハブ Pi のセットアップ — Git に載せられないものをどう再現するか

空の Raspberry Pi OS が入った Raspberry Pi 5 を、**子Pi専用ハブ**として立ち上げるための
運用手順。設計の背景は
[`superpowers/specs/2026-09-09-new-hub-bootstrap-design.md`](superpowers/specs/2026-09-09-new-hub-bootstrap-design.md)
を参照。

> **推奨**: 親機で USB キットを作り、新機ではデスクトップのアイコンから会話形式で
> 設定する（下記「USB 対話セットアップ」）。`site.env` を手で書く必要はない。
>
> ブートストラップ本体は `scripts/bootstrap-hub.sh`。ウィザードがその入力を作ってから
> 全フェーズを回す。各節の「手動」はフェーズが失敗したときの拠り所。

---

## USB 対話セットアップ（親と共存する 2 台目）

親機はそのまま運転したまま、別のホスト名・固定IP・AP名を持つ兄弟ハブを作る。

### 親機で USB を書く

```bash
bash scripts/pack-hub-usb.sh /media/pi/USBのマウント先
```

キット `presence-hub-kit/` にはリポジトリ、ドングルドライバのソース、
`mosquitto` / `oracle-jdbc` / `bridge` のイメージが入る。
**載せないもの**: 親の `fleet/children.conf`、Oracle パスワード、AP パスワード、
detector イメージ、SQLite バッファ。工場WiFi の PSK は載るので、USB は鍵と同じ扱い。

### 新機（素の Pi OS Desktop）で

1. USB を挿し、`このUSBからコピー` をダブルクリックする
   （開かないときは `bash /media/*/presence-hub-kit/copy-to-this-pi.sh`）
2. デスクトップの **ハブ初期設定** をクリックする
3. 順に答える: ホスト名 / 工場の SSID・固定IP・ゲートウェイ・DNS / Oracle（ホスト・ポート・サービス・ユーザ・テーブル） / 子Pi用ハブAP名 / APパスワード / Oracleパスワード  
   （工場網と Oracle の既定値はコピー元。同居時のハブAP名の既定は「ホスト名-hub」。Enter でそのまま）
4. 終わったら再起動する。
   - デスクトップの **「子をこのハブへ付ける」** をクリックし、質問に答える。
     - **既存の子**: 名前と局番号が残る
     - **新しい子 / 同じハブへのクローン増設**: そこで改名し、局番号は空になる

コピーした直後は AP もコンテナも起動しない（親と衝突しない）。
ウィザードが `site.env` と secrets を書いてから `bootstrap-hub.sh` を回す。

docker 本体と compose プラグインはフェーズ20 が入れる。docker グループへの追加も
そこで行う。残りのフェーズは root で docker を話すので、**再ログインを待たずに**
コンテナまで上がる。実行ビットはコピー時に立て直す（FAT の USB でも
`bash copy-to-this-pi.sh` で動く）。

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

### スクリプトのフェーズと、この文書の節の対応

`scripts/bootstrap-hub.sh` は番号付きのフェーズを順に実行する。各フェーズは、この文書の
どの節を自動化したものかが決まっている。**手動で進める場合は、この順序どおりに節をたどれば
同じ結果になる。**

| フェーズ | 内容 | 対応する節 | 完了後に人がすること |
|---|---|---|---|
| 10 | 日本語入力 | §3.10 | **ログアウト → 再ログイン** |
| 20 | 基盤パッケージ・ホスト名・venv・SSH鍵 | §0.5, §3.5, §3.7 | 再ログイン(docker グループ反映) |
| 30 | ドングルドライバ | §3.9 | `wlan1` の出現確認 |
| 40 | `/etc/presence-logger/` 生成 | §3.3, §3.4 | `secrets.env` に値を入れる |
| 50 | 子AP 構築 | （§3.12 の前提） | 子が IP を取れる確認 |
| 60 | コンテナ起動・常駐化 | §3.12 | — |
| 70 | デスクトップ配置・WiFi切替 | §3.11 | アイコンの「信頼して実行」 |

```bash
sudo bash scripts/bootstrap-hub.sh            # 全フェーズ
sudo bash scripts/bootstrap-hub.sh 10         # フェーズ10 だけ
sudo bash scripts/bootstrap-hub.sh 30 60      # 30〜60（両端を含む）
bash scripts/bootstrap-hub.sh --list          # 一覧
```

**途中で失敗しても、そのフェーズから再開できる。** 全フェーズは冪等なので、範囲を指定して
流し直しても壊れない。

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

**IPアドレスの書式（`FACTORY_IP` / `FACTORY_GW` / `AP_GW_IP`）**

| 項目 | 決まり | 弾かれる例 |
|---|---|---|
| 各オクテット | 0〜255。**先頭ゼロは不可** | `172.008.13.18`（`008` は不可）/ `999.1.1.1` |
| プレフィックス長 | 0〜32。先頭ゼロ不可 | `/99` / `/024` |
| 単独の `0` | 可（`10.0.0.5/24` は正しい） | — |

**先頭ゼロを禁止しているのは実害があるからである。** `172.008.13.18` のように桁を揃えて
書くと、シェルはこれを8進数として解釈しようとして失敗する。以前はその失敗が握り潰され、
**子APが工場網と衝突していても検証が通ってしまう**状態だった（`172.022.0.1` に至っては
エラーすら出ず `172.18.0.1` と同じ値に化けていた）。現在は書式の時点で弾き、どの項目の
どこが悪いかを名指しする。

**書いたら、その場で検証する（インストールを始める前に）**

```bash
cd ~/projects/presence-logger
bash -c 'source scripts/lib/site-env.sh; site_env_require && echo "✅ site.env は妥当です"'
```

固定IPの衝突・ホスト名の重複・子APと工場網のサブネット重複は、**動かなくなる**のではなく
**無警告でレコードが欠落する**形で現れる。ここで弾くのが唯一の防波堤なので、必ず通してから
次へ進むこと。エラーは1行1件で、問題のある項目名がそのまま出る。

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
> 作っただけでは、`fleet-status.sh` も `deploy-child.sh` も**子の付け替えも
> 全工程が失敗する**（`fleet_ui/provision.py` が `ssh -o BatchMode=yes` を使うため、
> パスワードを聞くことすらしない）。
>
> **親機の子を新ハブへ移す**ときは、新規登録（STA_NO を空にする / 改名）を使わない。
> デスクトップの **「子をこのハブへ付ける」** で「すでに動いている子を移す」を選ぶ。
>
> - **旧親が同じ工場網でまだ動いている**: 旧親のアドレスを入れる。先に `ssh-copy-id`。
> - **旧親が止まっている・別工場網 / 子のSDをクローンした**: カードリーダに子SDを挿し、
>   公開鍵と AP を書いてもらう。起動後にもう一度ウィザードで AP 上の子を取り込む。
> - ハブ初期設定で「親機はもう使わない／別工場」を選ぶと、親と同じ AP 名を許可する
>   （`ORIGIN_ALLOW_SAME_AP=1`）。クローンした子が自動で付く。鍵は SD 書き込みが必要。
>
> 新しい子のクローン増設だけ、同じアイコンで「新しい子を増やす」を選ぶ。
>
> 既にセットアップ済みのハブでファイルが無いときは、root でフェーズ40を再実行するか、
> `site.env` の `AP_SSID` と `/etc/presence-logger/secrets.env` の `WIFI_AP_PSK` から
> `.kit/ap-join.env` を 600/`pi` で作る。

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

### 3.13 デスクトップのアイコンを置く（現地オペレーターの入り口）

**この工程を飛ばすと、コンテナが動いていても現地で誰も操作できない。** §4 の完了確認は
これらのアイコンが動くことを前提にしている。

**自動（実装後）**: `sudo bash scripts/bootstrap-hub.sh 70`

**手動（現在）**:

```bash
cd ~/projects/presence-logger
DESK="$HOME/Desktop"
mkdir -p "$DESK/presence-tools" "$DESK/WiFi切替"

# 1. ツール本体
cp -r desktop/presence-tools/. "$DESK/presence-tools/"
chmod +x "$DESK/presence-tools"/*.sh "$DESK/presence-tools"/*.py

# 2. ランチャー（パスを自分の $HOME に置き換える）
for f in desktop/launchers/*.desktop; do
    sed "s|/home/pi/Desktop|$DESK|g" "$f" > "$DESK/$(basename "$f")"
done
chmod +x "$DESK"/*.desktop
```

初回はアイコンを右クリックして「**信頼して実行**」を選ぶ（Raspberry Pi OS の既定動作）。

**置かれるもの**

| アイコン | 実体 | sudo | ハブでの動作 |
|---|---|---|---|
| HIME-H-REAP に接続 | `connect-hime-h-reap.sh` | 要 | 工場網へ接続。**カメラ無しなので検知は始まらない** |
| HIME-H-REAP を切断 | `disconnect-hime-h-reap.sh` | 要 | `HOME_SSID` へ戻す |
| 記録モニタ | `watch-records.sh` | 不要 | bridge の書込ログを流す |
| 直近30件の記録 | `show-recent-records.sh` | 不要 | JDBCサイドカー経由で Oracle を SELECT |
| パイプライン監視 | `pipeline-monitor.sh` | 不要 | 子Pi→MQTT→inbox→Oracle を1画面で追う |
| 子をこのハブへ付ける | `setup-children-wizard.sh` | 一部 | 既存の子の引っ越し / 新しい子の登録（会話形式） |
| WiFi切替（`WiFi切替/` 内） | `switch-wifi.sh` | 要 | §3.11 で nmcli プロファイルを作った接続だけ |

**ハブ構成（カメラ無し）での注意 3 点**

1. **「接続」を押しても検知は始まらない。** 元々は接続と同時に detector コンテナを起動する
   設計だが、ハブには detector が無い。記録は子Pi から届く。
2. **「記録モニタ」は bridge のログだけを表示する。** detector のログは存在しない。
3. **「直近30件」の絞り込み既定値は「すべて」にする。** 既定では親自身の局番で絞るが、ハブの
   局番は placeholder なので、そのまま Enter を押すと**常に0件**になる。プロンプトで `*` を
   入力するか、`site.env` に `HUB_MODE=1` を設定しておく。

> **`HUB_MODE=1` を `site.env` に入れておくこと。** 上記 1〜3 はこの値で自動的に切り替わる。
> 入れ忘れると、存在しない detector を掴もうとしてエラーになる。

**確認**

```bash
ls ~/Desktop/*.desktop ~/Desktop/WiFi切替/*.desktop
# 「子をこのハブへ付ける」があること。ブラウザのフリート管理は置かない。
```

### 3.14 運用上の注意（新機で最初に踏みやすい罠）

- **`children.conf` は Git 追跡ファイル。** 子を登録すると作業ツリーが汚れ、
  `scripts/deploy-parent.sh` が「作業ツリーに未コミット変更があります」で**実行を拒否する**。
  子を登録したら `git add fleet/children.conf && git commit` してから親の更新を行う。
- **既存の子が1台でも到達不能だと、未登録の候補を出さない。** これは仕様である
  （稼働中の子がIPドリフトで「未登録の端末」に見えたとき、本番機を誤って再プロビジョニング
  しないための保護）。引っ越し中は **「子をこのハブへ付ける」→ 既存の子を移す** を使う。
- **AP の装置名は環境変数 `AP_DEV`（既定 `wlan1`）。** 新機でドングルが `wlan0` として現れると**子が1台も
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
