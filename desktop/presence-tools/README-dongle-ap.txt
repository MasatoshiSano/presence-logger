ドングル(USB WiFi) 運用ツール — dual-WiFi / 子Pi用AP / 死活監視
================================================================

このPi(ハブ)は無線が2枚:
  wlan0 = 内蔵(brcmfmac, 許可MAC)   … 工場網 HIME-H-REAP / Oracle 専用
  wlan1 = USBドングル(rtl8821au)    … 用途を切替（インターネット子機 or 親機AP）

無線は2枚しかないので「wlan1常時AP + wlan0をUFI⇔工場で切替」で運用する。
詳細な背景は docs/wifi-dongle-dual-wifi.md を参照。

--- 初回だけ（ドライバ導入） -------------------------------------
ELECOM WDC-433DU2H2-B (RTL8811AU, 056e:4010) はカーネル非対応のため外部ドライバが要る。
  1) ドライバ取得＋USB ID追記:
       git clone --depth=1 https://github.com/morrownr/8821au-20210708.git ~/8821au
       # ~/8821au/os_dep/linux/usb_intf.c の RTL8821 セクションに1行追加:
       #   {USB_DEVICE(0x056E, 0x4010), .driver_info = RTL8821}, /* ELECOM WDC-433DU2H2-B */
  2) DKMS導入＋必須オプション(JP/省電力OFF):
       sudo bash desktop/presence-tools/setup-dongle-driver.sh
     → wlan1 が出ればOK（DKMSなのでカーネル更新時は自動再ビルド）

--- AP(親機)を作る（子Pi用の独自WiFi） -------------------------
PSK は secrets.env の WIFI_AP_PSK に入れておく（ファイルに直書きしない）。
       sudo bash desktop/presence-tools/setup-dongle-ap.sh
  → SSID "presence-hub" / 2.4GHz ch6 / WPA2 / 10.42.0.1(DHCP=dnsmasq)
  子Piは presence-hub に接続すれば 10.42.0.x を自動取得し、ハブ=10.42.0.1。

--- 日常のモード切替（wlan1は常にAP） --------------------------
  開発:  sudo bash desktop/presence-tools/mode-dev.sh
         wlan0=UFI(インターネット/Claude) + wlan1=AP   … ネットあり・子Piテスト可
  本番:  sudo bash desktop/presence-tools/mode-prod.sh
         wlan0=HIME-H-REAP(工場/Oracle) + wlan1=AP     … ネット無し（SSH/Claudeは切断）

--- 子Pi → ハブ → Oracle のデータ経路 --------------------------
ハブで mosquitto を子Piへ公開するには hub override を併用:
       docker compose -f docker-compose.yml -f docker-compose.hub.yml up -d
(セキュリティ: 1883 は 10.42.0.1 だけにバインド＝工場網には晒さない)
(注: 「HIME-H-REAP 接続」アイコンを使えば、AP起動とこの mosquitto 公開まで自動)

経路は2種類:
 (A) 在席イベント: 子Pi(detector)を MQTT_HOST=10.42.0.1 で起動 → presence/event
     (ENTER/EXIT) → bridge が profile の席番号で HHC001 へ。
 (B) CSVレコード: 子Piの CSV 行(YYYYMMDDhhmmss,STA_NO1,STA_NO2,STA_NO3,T1_STATUS)を
     child-csv-to-mqtt.py が JSON 化して presence/record へ発行 → bridge が
     「行の値そのまま」HHC001 へ MERGE(席番号もT1_STATUSもCSV側の値)。
     子Piでの起動例:
       MQTT_HOST=10.42.0.1 DEVICE_ID=child-01 WATCH_DIR=~/outbox \
         python3 child-csv-to-mqtt.py
     ~/outbox に *.csv を置くと1行ずつ送信し、~/sent へ退避。冪等(再送しても
     二重記録されない)。bridge側設定は bridge.yaml の record: セクション。

--- 子Piの死活監視 ---------------------------------------------
detector は接続時に presence/status/<device_id>=online(retained)＋Last-Will offline、
20秒ごとに presence/heartbeat/<device_id> を発行。ハブの bridge が購読して
"liveness" をログし、offline/stale を "device_down" 警告で出す。設定は
bridge.yaml / detector.yaml の liveness: セクション（未指定でも既定で動作）。

簡易確認（ハブ側）:
       sudo iw dev wlan1 station dump                       # 接続中の子PiのMAC
       cat /var/lib/NetworkManager/dnsmasq-wlan1.leases     # MAC↔IP↔hostname
       docker logs --since 2m presence-bridge | grep liveness
