# 子の Oracle ACK 完了契約（2026-09-23、実装前設計）

## 根拠と適用範囲
タスクAでは ORA-00001 の896行が、同一MK_DATE+STAで異なるT1_STATUSのsent行と衝突。
Oracle定義未確認なのでMERGE・照合キー・failed判定は変更しない。再送は同一イベントの
配送回復だけを目的とし、failedを成功に変えない。CLIとカメラWeb内蔵senderの双方を変更。
実機配備・既存sentの回収・/etc/systemd/NetworkManager変更・コミットは実施しない。

## 契約・状態・再送
- 既存 `presence/record/ack`（既定は送信topic + `/ack`）をQoS2で購読。
  device_idを含む従来の決定的event_idとmk_date_committedで照合。SUBACK後に送信し、
  reconnect時は再購読。MQTT PUBCOMPだけでは完了しない。retained ACKは受理しない。
- 子は小さいSQLite台帳（標準ライブラリのみ）で宛先＋event_id＋payloadを永続管理。
  pending → ACK待ち → acked。publish前にpendingを保存し、ACK受信後にackedを保存。
  再起動後もCSVと台帳から再開。宛先変更時はACKを流用しない。
- ACK待機は1回5秒、未ACKは10分後に同一event_idで再送、期限による破棄なし。
  1巡30秒を上限とし、CSVを行単位で読み、巨大バックログをメモリに展開しない。
  不正行・末尾未完行は保持し、完了扱いにしない。他の正常行は処理可能。
- CLIは全行ACK済みかつ読み取り前後のファイル属性が同一の場合だけsentへ。
  archiveは既存ファイルを上書きしない。CSV生成者は完成ファイルをatomic renameで投入する。
  WebはCSVを移動せず台帳でACK完了を数え、未ACK/旧送信未確認を表示する。
- Web旧sent_linesはOracle確認の証拠ではない。初回に該当行をlegacy_unverifiedとして
  記録し、自動再送もしないしACK済み表示もしない。元JSONは保存し回収判断を運用に委ねる。

## ブリッジ
sent再受信時はevent_idに加えてdevice_id・日時・STA・T1_STATUSを比較し、一致する
Oracle commit済み記録だけACKを再送。内容不一致は拒否しログ化。received/failedにはACKしない。
sent保持期限後は通常の受信→既存MERGE→commit→ACKを通る。ora_code=Noneでも
error_messageが存在すれば失敗とし、成功ACKは禁止。Oracleキーは変更しない。

## 検証
先に失敗テストを実行して記録、その後に実装。ACK喪失・再起動・再接続・誤ACK・不正行・
複数行・更新・archive衝突・旧状態移行・失敗Oracle結果・sent消去後再送をfakeで検証。
全体pytestとruff既存違反との差分を記録（開始時697件）。新規依存・常駐プロセスなし。
