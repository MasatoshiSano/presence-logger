# デスクトップ操作ツール（現地オペレーター用）

工場WiFi「HIME-H-REAP」へ手動で接続し、Oracle(HHC001)への記録を確認するための
GUIランチャーとスクリプト一式。Raspberry Pi のデスクトップにアイコンとして並べ、
クリックで端末を開いて操作する。**検知のON/OFFは「接続/切断」に連動**する
（接続中だけ検知する＝工場にいる時だけ記録する、という設計）。

> このツール群は presence-logger 本体（`services/`）とは独立して動き、
> Docker コンテナ（`presence-detector` / `presence-bridge` / `presence-oracle-jdbc`）を
> 起動・停止・参照する。秘密情報は持たず、PSK/パスワードは実行時に
> `/etc/presence-logger/secrets.env`（root 600）から読む。

## 構成

| ファイル | 役割 | sudo |
|---|---|---|
| `launchers/HIME-H-REAP-接続.desktop` | 工場WiFiに接続 → NTP同期 → 検知開始（繋ぎっぱなし） | 要 |
| `launchers/HIME-H-REAP-切断.desktop` | 検知停止 → 元のWiFi(UFI_103134)へ戻す | 要 |
| `launchers/記録モニタ.desktop` | HHC001 への書込をリアルタイム表示 | 不要 |
| `launchers/直近30件.desktop` | DBを直接SELECTし直近30件を最新順表示 | 不要 |
| `presence-tools/connect-hime-h-reap.sh` | 接続＋時刻同期＋detector起動の実体 | 要 |
| `presence-tools/disconnect-hime-h-reap.sh` | detector停止＋切断の実体 | 要 |
| `presence-tools/watch-records.sh` + `_render.py` | モニタの実体（detector/bridgeログを整形） | 不要 |
| `presence-tools/show-recent-records.sh` + `_render_recent.py` | 直近N件の実体（JDBCサイドカー経由でSELECT） | 不要 |
| `presence-tools/setup-autostart.sh` | 再起動で復活＋起動時は検知OFFにする systemd 設定（1回だけ） | 要 |
| `presence-tools/README.txt` | 現地向けの使い方（凡例・トラブル対応） | － |

## インストール（新しい Pi へ展開する場合）

`.desktop` ランチャーは `/home/pi/Desktop/presence-tools/` の絶対パスを前提にしている。
リポジトリからは次のように配置する:

```bash
# 1. スクリプト本体をデスクトップへ
cp -r desktop/presence-tools ~/Desktop/presence-tools
chmod +x ~/Desktop/presence-tools/*.sh ~/Desktop/presence-tools/*.py

# 2. ランチャーをデスクトップへ
cp desktop/launchers/*.desktop ~/Desktop/
chmod +x ~/Desktop/*.desktop          # 「信頼して実行」を求められたら許可

# 3. （任意・1回だけ）再起動で自動復帰＋起動時は検知OFF
sudo bash ~/Desktop/presence-tools/setup-autostart.sh
```

前提:
- `/etc/presence-logger/secrets.env` に `WIFI_PSK_HIMEREAP` と Oracle パスワードが
  設定済みであること（[`../docs/etc-presence-logger.md`](../docs/etc-presence-logger.md) 参照）。
- Docker コンテナ群がビルド済みであること（リポジトリ本体の README 参照）。
- ユーザ `pi` が `docker` グループに所属していること（sudo無しのモニタ/直近30件に必要）。

## 使い方（おすすめの順番）
1. 「記録モニタ」を起動（先に開いておく）
2. 「HIME-H-REAP に接続」→ パスワード入力（ここで検知も始まる）
3. モニタに `✅ DB書込(NEW)` が流れるのを確認
4. 終わったら「HIME-H-REAP を切断」（検知も止まり元のWiFiへ戻る）

凡例・トラブル対応の詳細は [`presence-tools/README.txt`](presence-tools/README.txt) を参照。
