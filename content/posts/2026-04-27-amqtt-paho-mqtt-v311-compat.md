---
title: "amqtt と paho-mqtt が繋がらない原因 — IndexError の正体は MQTT v5/v3.1.1 の不一致"
emoji: "🐛"
type: "tech"
topics: ["MQTT", "Python", "paho-mqtt", "amqtt", "Debugging"]
published: true
category: "Debugging"
date: "2026-04-27"
description: "Python製MQTTブローカー amqtt と paho-mqtt クライアントを組み合わせたとき、CONNACK 応答で `IndexError: bytearray index out of range` が起きる。原因は MQTT プロトコルバージョンの不一致と、Python の定数エイリアスでは回避できないという落とし穴。"
coverImage: "/images/posts/amqtt-paho-mqtt-v311-compat-cover.jpg"
---

## やりたかったこと

Raspberry Pi 上でフルスタックの end-to-end テストを書きたかった。`docker` も `mosquitto` もシステムに入っておらず、`sudo` も使えない環境だったので、Python製の MQTT ブローカー [amqtt](https://github.com/Yakifo/amqtt) を `pip install` で venv に導入し、それを相手に [paho-mqtt](https://eclipse.dev/paho/) クライアントから publish/subscribe する構成にした。

production コードは `paho.Client(client_id=..., protocol=paho.MQTTv5)` で MQTT v5 を使っていた。

## こんな人向け

- Python の MQTT クライアント `paho-mqtt` で `amqtt` ブローカーに繋ごうとしている
- MQTT 接続直後に `IndexError: bytearray index out of range` が出て困っている
- 「ブローカーがエラーを吐いていない（Invalid protocol だけ）」のに、クライアント側が Connection アボートで死ぬ
- `amqtt` の v5 サポート状況を調べている

## ❌ 最初の指示 / アプローチ

production コードは v5 で書いていたので、テスト用に「ブローカーだけ amqtt に差し替えれば動くはず」と考えた。

```python
# services/bridge/src/mqtt_listener.py（抜粋・production コード）
client = paho.Client(client_id=self._client_id, protocol=paho.MQTTv5)
```

amqtt の起動コードは公式 README そのまま：

```python
from amqtt.broker import Broker

BROKER_CONFIG = {
    "listeners": {"default": {"type": "tcp", "bind": "127.0.0.1:1883"}},
    "auth": {"allow-anonymous": True},
    "topic-check": {"enabled": False},
}

broker = Broker(BROKER_CONFIG)
await broker.start()
```

これで `127.0.0.1:1883` に paho クライアント（v5）から繋ぎに行った。

## 何が起きたか

ブローカー側のログ：

```
Invalid connection from (client @=127.0.0.1:36295)
amqtt.errors.MQTTError: Invalid protocol from (client @=127.0.0.1:36295): 5
Failed to initialize client session: Invalid protocol from (client @=127.0.0.1:36295): 5
```

ここまでは「amqtt は v5 を弾く」とわかる。

ところがクライアント側は別のもっと不可解なエラーで落ちた：

```
Exception in thread paho-mqtt-client-presence-bridge-livetest:
    return self._handle_connack()
    properties.unpack(self._in_packet['packet'][2:])
    propslen, VBIlen = VariableByteIntegers.decode(buffer)
    digit = buffer[0]
            ~~~~~~^^^
IndexError: bytearray index out of range
```

`_handle_connack()` が CONNACK パケットの properties セクションを decode しようとしてバッファが空、で `bytearray[0]` が `IndexError`。CONNACK の解析を v5 のロジックでやろうとしている。

### なぜこうなるのか

amqtt 0.11 系は **MQTT v3.1.1 のみサポート**。v5 の CONNECT パケットを受け取ると上記の `Invalid protocol: 5` で内部的にエラー扱いするが、TCP レベルでは中途半端に応答を返してしまう（v3.1.1 互換の短い CONNACK 風バイト列）。

paho クライアント側はクライアント生成時に `protocol=paho.MQTTv5` で初期化されているので、応答を **v5 形式の CONNACK として decode** しようとする。v5 CONNACK には properties セクション（可変長）があるが、ブローカーが返したのは v3.1.1 形式の短いバイト列なので properties オフセット位置でバッファが尽きて `IndexError`。

つまり：

- **ブローカー**: 「v5 はサポートしてない」と認識しているが、TCPは閉じきらず短い応答を出す
- **クライアント**: 自分が v5 で繋ぎに行ったので、応答も v5 と仮定して parse → 失敗

エラーが「IndexError」というプロトコルとは無関係そうな見た目になるのが厄介。

## ✅ 解決した指示 / アプローチ

最初は **Python の定数を上書き**して回避できないか試した：

```python
# テストスクリプトの先頭
import paho.mqtt.client as paho
paho.MQTTv5 = paho.MQTTv311  # 構成定数の上書き
```

これは効かなかった。`paho.Client(protocol=...)` は **コンストラクタに渡された値で内部の protocol handler を決める** ため、後で定数を書き換えても、すでに import 済みの class が参照しているハンドラは v5 のまま。

正解は **production コード自体を v3.1.1 に切り替える** こと：

```python
# services/bridge/src/mqtt_listener.py
client = paho.Client(client_id=self._client_id, protocol=paho.MQTTv311)

# services/detector/src/mqtt_client.py
client = paho.Client(client_id=self._client_id, protocol=paho.MQTTv311)
```

これに変更した結果、amqtt と問題なく握手し、QoS=2 の publish/subscribe/ACK 往復が動作。134 件のユニットテストもすべて通過（テストはプロトコル番号を assert していなかった）。

### なぜこれで解決するのか

- amqtt が話せるのは v3.1.1 のみ → クライアントを v3.1.1 に揃えれば握手成立
- 自分のアプリが v5 の固有機能（Properties、Reason Codes、Shared Subscriptions、Topic Aliases 等）を使っていなければ、v3.1.1 への退行は機能損失ゼロ
- Mosquitto・HiveMQ・EMQX などの本番ブローカーも v3.1.1 を完全サポートしているので、production の互換性も悪化しない

## 比較まとめ

| | ❌ 最初 | ✅ 改善後 |
|---|---------|-----------|
| クライアントプロトコル | `paho.MQTTv5` | `paho.MQTTv311` |
| 定数アップデート | `paho.MQTTv5 = paho.MQTTv311` で監修 | `paho.Client(protocol=paho.MQTTv311)` で実値渡し |
| amqtt との握手 | `IndexError` で abort | 成功 |
| production 互換性 | Mosquitto などは v5 対応で問題なし | v3.1.1 はあらゆるブローカで OK |
| 機能差分 | v5 features を使っていない場合は無し | 同左 |

## バイブコーディングで実装する

この記事の内容を踏まえた、最初から正しく実装させるためのプロンプト：

> Python で MQTT を使うアプリを書く。クライアントは paho-mqtt、ブローカーは Mosquitto を本番、開発時は amqtt を Python venv に入れて使う。
>
> アプリ側でMQTT v5 固有機能（Properties、Reason Codes、Shared Subscriptions 等）は使わないので、`paho.Client(protocol=paho.MQTTv311)` で MQTT v3.1.1 を指定すること。amqtt は v5 を受け付けないため、ここで v5 を選ぶと CONNACK 解析時に `IndexError: bytearray index out of range` になる（ブローカー側ログには "Invalid protocol: 5" が出る）。
>
> なお `paho.MQTTv5 = paho.MQTTv311` のように定数を上書きするだけでは、すでに class に紐づいているハンドラには反映されないので無効。コンストラクタに実値を渡すこと。

### AIに指示するときのポイント

- 「MQTT を使う」と言うと AI は最新の v5 を選びがち。**ブローカー側の対応バージョンを先に明示**する
- `IndexError: bytearray index out of range` は MQTT 関連と気付きにくいエラー。AI に「これは何のエラー？」と聞くときは、**呼び出し元が `_handle_connack` だったこと**まで添えると当てやすい
- `paho.Client(protocol=...)` の値は**起動時に固定される**ので、後から定数を書き換えても効かない。runtime での切替が必要なら、Client インスタンスを作り直す
