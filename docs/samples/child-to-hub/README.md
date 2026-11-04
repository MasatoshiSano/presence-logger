# 子ラズパイ → 親ラズパイ(ハブ) のデータ形式サンプル

親に実際に届くのは **CSV ではなく JSON** です。CSV は子ラズパイの中だけにあり、
子が 1 行ずつ JSON に変換して MQTT(トピック `presence/record`, QoS 2) で送ります。

```
信号灯の色(on/blink/off) → 状態コード(数字) → 送信用CSV → JSON → MQTT → 親(bridge) → Oracle HF1RCM01
```

| ファイル | 内容 |
|---|---|
| `sample.csv` | 子ラズパイの送信用 CSV(`/home/pi/send_logs/T1_<IP>_<日付>.csv`)。ヘッダー行なし |
| `sample.json` | `sample.csv` の全行(3行)を JSON にしたもの。読みやすさのため配列にまとめている |

> 実際は配列ではなく、**CSV 1行につき 1 つの JSON メッセージ**を別々に送ります(`sample.json` の配列の要素 1 つ分が 1 メッセージ)。
>
> 値は説明用のサンプルです(`device_id` やタイムスタンプは例)。

## CSV の列(ヘッダーなし)

`日時, STA_NO1, STA_NO2, STA_NO3, T1_STATUS`

| 列 | 例 | 意味 |
|---|---|---|
| 日時 | `20261104130307` | YYYYMMDDhhmmss(日本時間・14桁) |
| STA_NO1〜3 | `HIME,ABC,001` | 設備を表す 3 項目(子の `id_names_config.json` で設定) |
| T1_STATUS | `1` | 状態コード(整数)。信号灯の色の組み合わせから `status_code_config.json` で決まる |

## JSON の項目

| 項目 | 内容 |
|---|---|
| `event_id` | `device_id|日時,STA_NO1,STA_NO2,STA_NO3,T1_STATUS` の SHA-1。同じ行を再送しても同じ値(二重登録防止) |
| `mk_date` | CSV の日時(文字列) |
| `sta_no1`〜`sta_no3` | CSV の STA_NO1〜3(文字列) |
| `t1_status` | CSV の T1_STATUS(整数) |
| `device_id` | 送信元の子ラズパイ(環境変数 `DEVICE_ID`、無ければホスト名) |
| `schema_version` | 常に `1` |

Oracle の `HF1RCM01` には `MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS` の 5 列が入ります。
