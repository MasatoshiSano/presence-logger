# 既存子Pi の引っ越し・増設

新ハブへ既存の子を移す手順。セットアップ本体は
[`NEW-HUB-SETUP.md`](NEW-HUB-SETUP.md)。設計の背景は
[`superpowers/specs/2026-09-09-new-hub-bootstrap-design.md`](superpowers/specs/2026-09-09-new-hub-bootstrap-design.md)
§9 / §11。

**引っ越し**（旧ハブを止めて新ハブへ子を移す）なら `AP_SSID` / `WIFI_AP_PSK` /
`AP_GW_IP` を現行ハブと同じにする。子側の設定変更は不要。子は
`~/send_target_config.json` で `10.42.0.1:1883` を、SSID は `presence-hub` を掴んでいる。

PSK や Oracle パスワードの**実値は書かない**。現行機の画面で確認し、手入力する。

---

## 引っ越し（1〜6）

工程の順を守ること。**2 を省くと、このあと全部失敗する。**

### 1. 旧ハブで未送信を送り切る

ホストから SQLite を直接開けないので、`docker exec presence-bridge` 経由で確認する。
`status='received'` が 0 になるまで待つ。

```bash
# 旧ハブで
docker exec presence-bridge python3 -c "
import sqlite3
c=sqlite3.connect('/var/lib/presence-logger/bridge_record_buf.db')
print('未送信:', c.execute(\"select count(*) from record_inbox where status='received'\").fetchone()[0])"
```

0 になる前に切り替えると、旧バッファの行が新機へ引き継がれない（
`/var/lib/presence-logger/` はコピーしない）。

### 2. 新ハブの公開鍵を各子へ配る（旧親が到達できるうちに）

既存の子は**旧親の公開鍵しか知らない**。新ハブで鍵を作っただけでは
`fleet-status.sh` / `deploy-child.sh` / フリート管理の登録ウィザードが全部失敗する
（`BatchMode=yes` のためパスワードを聞かない）。

**旧ハブの AP を落とす前に**配る。子が新 AP へ移った後では、旧親からも新ハブからも
入れられなくなる。

```bash
# 新ハブで公開鍵を控える
cat ~/.ssh/id_ed25519.pub

# 旧親で実行（子がまだ旧 AP に繋がっている間に）
for h in zero2 pizero2w-2.local; do
  ssh "$h" 'cat >> ~/.ssh/authorized_keys' <<< '<上で控えた公開鍵1行>'
done
```

ホスト名は `fleet/children.conf` の実機に読み替える。パターン B（SD クローン）で
旧親の鍵をそのまま使う場合は、この工程を省略できる。

### 3. 旧ハブの AP を落とす

同一 SSID の AP を 2 つ生かしたまま新ハブを上げない。接続名は
`site.env` の `AP_SSID` に `-ap` を付けたもの（既定なら `presence-hub-ap`）。

```bash
# 旧ハブで
sudo nmcli connection down presence-hub-ap
sudo nmcli connection modify presence-hub-ap connection.autoconnect no
```

### 4. 新ハブで AP を起動する

子は同じ SSID / PSK を掴んでいるので、自動で繋ぎ替わる。

```bash
# 新ハブで（フェーズ50。既に bootstrap 済みなら nmcli connection up でも可）
sudo bash scripts/bootstrap-hub.sh 50
```

同じ SSID がまだ見えているとフェーズ50 は警告して止まる。そのときは 3 に戻る。

### 5. `scripts/fleet-status.sh` が exit 0

```bash
# 新ハブで
scripts/fleet-status.sh
echo $?
```

| exit | 意味 |
|---|---|
| `0` | 全機体を検査できて STA_NO 重複なし |
| `1` | STA_NO 重複あり（検査不能な子があってもこちらを優先する） |
| `2` | 検査しきれていない（到達できない / 割当を読めない子がある） |

**`2` を `0` と混同しない。** 「安全」ではなく「確かめられていない」。
到達できない子が残っているときは、2 の公開鍵配布か AP の繋ぎ替わりを疑う。

### 6. `pipeline-monitor.sh` で同一 event_id を追う

デスクトップの「パイプライン監視」、または:

```bash
bash desktop/presence-tools/pipeline-monitor.sh
```

①子Pi別受信 / ②MQTT生ログ / ③record_inbox / ④Oracle。
同じ event_id を **② → ③ → ④** で辿れること。④の工場網確認は HIME-H-REAP 接続中。

---

## 7. 増設（両ハブ同時稼働）の差分

引っ越し手順の 3（旧 AP 停止）は**やらない**。両ハブが同時に生きる。

1. 新ハブの `site.env` で **`AP_SSID` と `AP_GW_IP` を現行機と別の値**にする
   （同じだと子がどちらに繋ぐか不定になる）
2. 新ハブ配下に置く子の `~/send_target_config.json` の `"host"` を、新しい
   `AP_GW_IP` へ変更する（既定の `10.42.0.1` のままでは旧ハブへ送り続ける）
3. 子の STA_NO（`id_names_config.json`）が**全ハブを通じて一意**であること。
   Oracle の MERGE キーは `MK_DATE + STA_NO1-3 + T1_STATUS` のみで `device_id` を
   含まない。ハブが別でも STA_NO が衝突すればレコードは無警告で欠落する

確認は引っ越しと同じ 5・6（`fleet-status.sh` が exit 0、pipeline-monitor で ②→③→④）。
増設後は**両方のハブ**で `fleet-status.sh` を通し、ハブを跨いだ STA_NO も目視で突合する。
