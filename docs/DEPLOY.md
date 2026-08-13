# デプロイ / バージョンアップ手順（親 Pi 5 ＋ 子 Zero2）

2層WiFi構成（Oracle ⇄ 親 wlan0、親 wlan1(AP 10.42.0.1) ⇄ 子 10.42.0.51）での
アプリ・MLモデルの更新を、**親から1コマンド**で・**失敗時は自動ロールバック**で行う。

## なぜ K3s ではないのか

- 子は **Pi Zero 2 W（RAM 416MB / 空き ~85MB）**。K3s agent 常駐だけで大半を食い、
  カメラ推論と共存できない。Docker すら載っていない。
- 子は親のAP配下で**外部レジストリに出られない**。K3s なら親に registry + 常時接続が必要で、
  2ノードには重すぎる。
- ノード2台では K3s の旨み（多数ノード・自己修復・宣言的ロールアウト）より運用コストが勝る。

→ 実態（親=git+docker / 子=素ファイル+systemd）に合わせ、**SSH+rsync+systemd の
pull型スクリプト**を採用。台数が 3〜5 に増えたら Ansible へ無理なく発展できる。

## 構成と"正"の置き場

| 対象 | 実行環境 | ソース・オブ・トゥルース | 配布方法 |
|---|---|---|---|
| 親アプリ | git + docker compose | **親ローカルの git ツリー**（tag） | `deploy-parent.sh`（local checkout + compose） |
| 子アプリ | 素python + systemd | `child/`（git管理） | `deploy-child.sh`（rsync + restart） |
| 子モデル | IMX500 `.rpk` | `models/<name>/<version>/`（親ローカル） | `deploy-model.sh`（rsync + restart） |

共通ロジック（バックアップ / ヘルスチェック / ロールバック）は
`scripts/lib/deploy-common.sh`。

> **ネットワーク前提**：親は普段 **社内ネットで GitHub に接続できない**。よってどの経路も
> GitHub を必須にしない。子はそもそも親からの rsync のみ（GitHub 非依存）。親は稼働ツリー
> 兼 git ツリーなので、**親上で `git tag` → checkout でネット無しに完結**する。GitHub は
> 接続できる時だけの「オフサイトバックアップ / 取り込み(`--fetch`)」に留める。

## 手順

### 子アプリを更新する
```bash
# 1. child/ の中を編集（Picamera.py など）
scripts/deploy-child.sh --dry-run      # 送る差分を確認
scripts/deploy-child.sh                # 配布 → 再起動 → 検証 →(失敗時)自動戻し
```

**機体固有設定は配布されない。** `id_names_config.json`（region_id→STA_NO割当）・
`threshold_config.json`・`recognition_config.json`・`save_config.json`・
`model_config.json`・`crop_config.json` は、子のWeb UI(:8080)でオペレーターが
設定する端末の状態であり、配布対象から外してある。設定は各子のWeb UIで行う。

フリート共通の設定（`status_code_config.json`・`send_target_config.json`）を
配りたいときだけ `--with-shared-config` を付ける。現場で `send_target_config` の
`enabled` を一時的にoffにしている子があると再有効化される点に注意。

### 子のMLモデルを更新する
```bash
# 1. models/<name>/<新version>/ に network.rpk / labels.txt / packerOut.zip を置く
scripts/deploy-model.sh --list
scripts/deploy-model.sh signal_tower <新version>   # 配布 → picamera再起動 → 検証
# 戻すとき
scripts/deploy-model.sh signal_tower <旧version>
```

### 複数の子へまとめて配る（フリート）

対象一覧は `fleet/children.conf`（1行1台、SSH到達名のみ。IPは書かない）。

```bash
scripts/deploy-fleet.sh app --dry-run                 # 全子ぶんの差分を確認
scripts/deploy-fleet.sh app --only zero2              # canary を1台だけ先行
scripts/deploy-fleet.sh app                           # 全子へ 1台ずつ順に配布
scripts/deploy-fleet.sh model signal_tower 20260422   # 全子へモデル配布
```

**既定は fail-fast**。1台で失敗したら以降へは配らない（不良リリースをフリート全体へ
広げないため）。あえて続けるときだけ `--keep-going`。各子の配布・ヘルスチェック・
自動ロールバックは `deploy-child.sh` / `deploy-model.sh` がそのまま担う。

#### 状態確認と STA_NO 重複チェック

```bash
scripts/fleet-status.sh          # 全子の稼働状況 + STA_NO割当 + 重複判定
scripts/fleet-status.sh --only zero2
```

**子を増やしたら必ず実行する。** Oracle の MERGE キーは `MK_DATE + STA_NO1-3 +
T1_STATUS` のみで `device_id` を含まないため、子同士で STA_NO が重複すると
同時刻・同ステータスのレコードが**無警告で欠落**する。

終了コード:

| コード | 意味 |
|---|---|
| 0 | 全機体を検査できて重複なし |
| 1 | STA_NO 重複あり（最も実行可能な合図なので、検査不能な子があっても優先） |
| 2 | 検査しきれていない（到達できない子がある／割当を読めない子がある） |

**`2` を `0` と混同しないこと。**「安全」ではなく「確かめられていない」を意味する。
`2` のまま子を追加すると、未検査の機体と衝突している可能性が残る。

#### 子を増やす（3台目以降も同じ手順）

> **SDカードのコピーで増やす場合は、この順序を必ず守ること。**
> 2台目の投入時に実際に事故が起きた（下の「なぜこの順序か」参照）。
> 電源を入れる前に読むこと。

**手順**

1. **電源を入れる前に、既存機の一覧を確認しておく**
   ```bash
   scripts/fleet-status.sh          # 既存が exit 0 であることを確認
   ip -4 neigh | grep 10.42.0       # 今いる子のIPを控える
   ```

2. **新機を親APに接続し、電源投入**。ここで新機は**まだ既存機と同じ名前**なので、
   長時間放置しない。次の手順3をすぐ行う。
   ```bash
   ip -4 neigh | grep 10.42.0       # 手順1と比べて増えたIPが新機
   ```

3. **送信を止める（最優先）**
   ```bash
   ssh pi@<新機IP> 'sudo systemctl stop child-csv-to-mqtt.service'
   ```
   クローンだと MQTT の `client_id` が既存機と同一になり、
   **2台が互いの接続を切断し合う**。まず止める。

4. **STA_NO を空にする**（誤った局番号で記録させないため）
   ```bash
   ssh pi@<新機IP> 'cp -a ~/id_names_config.json ~/id_names_config.json.clone-backup'
   ssh pi@<新機IP> 'python3 -c "
   import json; p=\"/home/pi/id_names_config.json\"
   d=json.load(open(p)); json.dump({\"id_names\":{k:[\"\",\"\",\"\"] for k in d[\"id_names\"]}}, open(p,\"w\"))"'
   ```

5. **ホスト名を一意にして再起動**
   ```bash
   ssh pi@<新機IP> 'sudo hostnamectl set-hostname pizero2w-3'
   ssh pi@<新機IP> 'sudo sed -i "s/\bpizero2w\b/pizero2w-3/g" /etc/hosts'
   ssh pi@<新機IP> 'sudo reboot'
   ```
   ホスト名が `device_id` と MQTT `client_id` の両方を決める。ここが分かれれば競合は終わる。

6. **mDNS を正常化**（クローン時は既存機の avahi が衝突回避で勝手に改名していることがある）
   ```bash
   for h in <既存機...> ; do ssh $h 'sudo systemctl restart avahi-daemon'; done
   ssh pi@<新機IP> 'sudo systemctl restart avahi-daemon'
   getent hosts pizero2w-3.local    # 新機のIPを指すこと
   ```

7. **SSH の host key を登録**（しないと `fleet-status.sh` が「到達できません」になる）
   ```bash
   ssh-keyscan -H pizero2w-3.local >> ~/.ssh/known_hosts
   ```

8. **インベントリに追加**
   ```bash
   echo 'pizero2w-3.local' >> fleet/children.conf
   scripts/fleet-status.sh          # 新機が見えること。STA_NO は「未割当」でよい
   ```

9. **STA_NO を設定**（新機のWeb UI `http://<新機IP>:8080`）
   他機と**重複しない**値を入れる。設定後:
   ```bash
   scripts/fleet-status.sh          # exit 0 を確認。1 なら重複しているので直す
   ```

10. **入力途中のゴミレコードを掃除**（下記の注意を参照）

**なぜこの順序か**

2台目投入時、SDカード完全コピーだったため次が同時に起きた。

- ホスト名が同一 → `child-csv-to-mqtt.py` の `client_id=f"child-csv-{DEVICE_ID}"` が衝突。
  MQTT は同一 client_id の新規接続時に既存接続を切断するため、
  **2台が互いを蹴り合う無限ループ**になり、両方の送信サービスがクラッシュ→再起動を反復した。
  20秒間に `presence/status/<id> offline` を16回以上観測。
- STA_NO も同一 → 送信が安定していれば Oracle でレコードが無警告に欠落していた。
- mDNS 名も奪い合い、既存機の avahi が自分を `pizero2w-2` に自動改名していた。

手順3（送信停止）を最初に置くのは、この競合を止めるのが最優先だからである。

**STA_NO 設定時の注意 — 入力途中の値が記録される**

Web UI は1文字入力するたびに全体を保存する
（`index.html` の `input.addEventListener('input', ... saveIdNames())`）。
一方 `Picamera.py` の `SendLogger` は「3項目のうち1つでも入っていれば送信対象」と判定するため、
`004020` と打つ途中の `[HIME][][]` や `[HIME][T120][]` が**そのままレコードになる**。

設定直後に不完全なレコードを掃除する:
```bash
docker exec presence-bridge python3 -c "
import sqlite3
c=sqlite3.connect('/var/lib/presence-logger/bridge_record_buf.db')
n=c.execute(\"delete from record_inbox where status='received' and (sta_no2='' or sta_no3='')\").rowcount
c.commit(); print('削除:', n, '件')"
```
`status='received'`（Oracle未送信）だけを対象にしているので、送信済みには触れない。

### 親を更新する（GitHub 非接続前提）
```bash
# 親の上で直接（社内ネット・ネット無しでOK）
git add -A && git commit -m "..."          # 親で編集→コミット
git tag v1.2.3                             # ローカルにタグ
scripts/deploy-parent.sh v1.2.3            # local checkout + compose + 検証 →(失敗時)戻し

# GitHub に繋がる時だけ：バックアップ push / 取り込み
git push origin --tags                     # 繋がった時にまとめて退避
scripts/deploy-parent.sh --fetch v1.2.3    # 別マシンで打ったタグを取得してから更新
```

## 安全設計

- **runtime 状態と機体固有設定を絶対に触らない**：`send_target_state.json`（送信カーソル）や
  `logs/`・`raw_images/` に加え、機体固有設定（`id_names_config.json` 等、
  `CHILD_DEVICE_OWNED_FILES`）も配布対象外。上書きすると送信巻き戻り/実データ消失、
  および STA_NO 誤送信になるため。
- **配布前バックアップ**：上書き対象の現行版を子の `~/.deploy-backups/<ts>/` に退避
  （直近 `KEEP_BACKUPS=5` 世代を保持）。
- **ヘルスチェック（3層）**：
  1. **即時**：サービス `active` + 数秒の安定確認（crash-loop 検出）+ web_server `:8080` 応答。
  2. **readiness**：`/model_status` が `ready` かつ、同じ応答の `model_type` が期待モデルと一致
     （`/current_model` は機体によっては `network`/`labels` しか返さず `model_type` を
     持たないため見ない。見ると正常な配布でも必ず不一致になりロールバックする）
     （実トラフィックを待たず、モデル未ロード/誤モデルを決定論的に検出）。
     ※レコードはイベント駆動のため「実レコード到達待ち」は無人時に誤ロールバックを招く。だから採らない。
  3. **E2E（任意, `VERIFY_E2E=1`）**：`/send_logs_now` で1回送信 → `/send_target_status` の
     `last_result` が成功か確認（600s 待たずにデータ経路まで検証）。
- **自動ロールバック**：チェック失敗で直前版へ復元 → 再起動 → 再チェック（readiness 含む）。
  `--no-rollback` で無効化可。

## 上書き可能な設定（環境変数）

`CHILD_SSH`(既定 zero2) / `CHILD_AP_IP`(既定は空＝子から自動取得) /
`CHILD_WEB_PORT`(8080) / `KEEP_BACKUPS`(5) / `HEALTH_STABLE_WAIT`(6秒)
— 詳細は `scripts/lib/deploy-common.sh`。

子のIPは親APのDHCP動的割当のため、既定では**デプロイ時に子自身から取得**する
（`hostname -I` の先頭IPv4）。固定したい場合のみ `CHILD_AP_IP` を明示指定する。
