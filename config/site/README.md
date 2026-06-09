# `config/site/` — HIME-H-REAP 拠点の実設定スナップショット

`/etc/presence-logger/` に配置する**非秘密の実設定（本番値）**をバージョン管理する場所。
`config/*.example`（汎用テンプレート）と違い、ここは**この拠点の実運用構成そのもの**で、
新しい Pi への移行や設定破損時の復元元になる。

> **秘密は含まない**: パスワード・PSK は `${VAR}` 参照のみ。実値は
> `/etc/presence-logger/secrets.env`（Git管理外）。ここに入っている IP・
> ホスト名・ステーション番号・Oracleユーザは `config/profiles.yaml.example`
> でも既に公開済みの内容で、追加の機微情報はない。

## 収録ファイルと本番値

| ファイル | 内容 | 本番値の要点 |
|---|---|---|
| `profiles.yaml` | HIME-H-REAP プロファイル | 固定IP 172.22.13.17、Oracle 10.166.5.93/HHC001、station 996/995/994、`unknown_ssid_policy: drop` |
| `device.yaml` | 端末既定 | station 996/995/994（profile 上書きと整合） |
| `detector.yaml` | 検知パラメータ | **debounce 3.0/3.0 秒**（本番想定） |
| `bridge.yaml` | bridge 動作 | 実運用と同一 |

## ⚠ 実Pi の現在値との差（意図的）

このスナップショットは**本番想定値**。一方、現在の実 Pi は**検証用の値**で
稼働しており、こことは意図的に異なる:

| 項目 | このスナップショット(本番) | 実Piの現在値(検証中) |
|---|---|---|
| `detector.yaml` debounce | 3.0 / 3.0 秒 | 0.1 / 0.2 秒 |
| `device.yaml` station | 996 / 995 / 994 | TST / T / 00 |

実 Pi を本番運用へ切り替える際は、このスナップショットを `/etc/` へ反映する
（下記）か、該当値を手で戻すこと。

## 復元・反映手順

```bash
# 非秘密の実設定を /etc へ反映（本番値で上書き）
sudo cp config/site/profiles.yaml  /etc/presence-logger/profiles.yaml
sudo cp config/site/device.yaml    /etc/presence-logger/device.yaml
sudo cp config/site/detector.yaml  /etc/presence-logger/detector.yaml
sudo cp config/site/bridge.yaml    /etc/presence-logger/bridge.yaml
sudo chown root:root /etc/presence-logger/*.yaml
sudo chmod 644       /etc/presence-logger/*.yaml

# 秘密は別途（Git管理外）。secrets.env と wallets/ を安全な経路でコピー:
#   /etc/presence-logger/secrets.env   (600 root:docker) — WIFI_PSK_HIMEREAP + ORACLE_PASSWORD_*
#   /etc/presence-logger/wallets/      (700 root:docker) — wallet 利用時のみ

# 反映後、コンテナを作り直して再読込
sudo docker compose --project-directory /opt/presence-logger up -d --force-recreate
```

各ファイルの仕様は [`../../docs/etc-presence-logger.md`](../../docs/etc-presence-logger.md) を参照。
