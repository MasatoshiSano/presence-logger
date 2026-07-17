# 直近記録ビューアの自由フィルタ化 — 設計

- 日付: 2026-07-17
- 対象: デスクトップ「直近N件」ビューア（`desktop/presence-tools/show-recent-records.sh`）と
  oracle-jdbc サイドカー（`services/oracle-jdbc/src/Main.java` の `/select_recent`）

## 背景 / 課題

デスクトップの「直近30件」ビューアは、この拠点の `STA_NO1/2/3` を profiles.yaml から読んで
**固定**で照会するため、その拠点の入退室行しか確認できない。STA_NO を固定は
2層で効いている:

1. シェル `show-recent-records.sh` が profiles.yaml の station を固定で POST する
2. Java `selectRecent` が `sta_no1/2/3` を**必須**とし、常に
   `WHERE STA_NO1=? AND STA_NO2=? AND STA_NO3=?` で AND 連結する

利用者は任意の条件で自由に絞り込みたい。

## 前提知識: 子Piから上がって書かれるデータは2種類

`services/bridge/src/record.py` が2経路を明確に分けている。

| 種別 | トピック | 発生源 | Oracle書込 | T1_STATUS |
|---|---|---|---|---|
| ① presence event（入退室） | `presence/event` | detector の FSM が在/不在検知 | bridge が ENTER/EXIT 判定し profiles の STA_NO で行を組み立て MERGE | `1`=ENTER / `2`=EXIT のみ |
| ② record（CSV由来の完成行） | `presence/record` | 子Pi `child-csv-to-mqtt.py`（CSV 1行=1件） | bridge は判定も profile 参照もせず CSV値をそのまま MERGE | CSV値そのまま（`3` などもあり得る） |

**設計含意**: record 経路のため `T1_STATUS` は入退室(1/2)に限らない。よって
「種別で絞る」は入室/退室の2択ではなく**任意コード指定**にする。表示側は未知コードを
`?(<n>)` で見せる既存挙動を維持する。

## フィルタ軸（確定）

- **拠点 STA_NO1/2/3**（各列を独立に指定可 / 部分指定可 / 全拠点可）
- **日時範囲 MK_DATE**（from / to、片側だけでも可）
- **状態 T1_STATUS**（任意の1コード / 空欄=絞らない）
- 件数 limit（既定30、1〜200）

## 操作方式（確定）: 対話式プロンプト

デスクトップからダブルクリック起動する前提のため、起動時に端末で順に質問し、
Enter（空欄）で「絞らない」を表現する。

拠点の既定挙動（確定）:

- **STA_NO 各列で空欄（Enter）= profiles の拠点値を使う（＝現行動作）**
- 明示的に値を入力 = その列をその値で絞る
- `*` を入力 = その列は絞らない（全拠点/全値）

これにより、Enter連打すれば従来どおり自拠点の直近N件が出る。誤操作で全件が
出る事故を防ぎつつ、`*` で明示すれば全拠点横断もできる。

## 設計

既存の3層構成を維持し、Java の `selectRecent` を「渡された条件だけで WHERE を
動的に組む」形へ後方互換で拡張する。新エンドポイントは作らない
（`/select_recent` の呼び出し元は `show-recent-records.sh` のみ）。

### ① Java サイドカー `services/oracle-jdbc/src/Main.java` — `selectRecent`

- `sta_no1/2/3` を**必須 → 任意**に変更。
  値が渡された列だけ `AND STA_NO_x = ?`（バインド変数）を WHERE に追加。
  渡されない/空欄の列は絞らない。
- 任意の `mk_date_from` / `mk_date_to` を追加。
  それぞれ渡されれば `AND MK_DATE >= ?` / `AND MK_DATE <= ?`（バインド）。
- 任意の `t1_status` を追加。渡されれば `AND T1_STATUS = ?`（バインド）。
- 不変部分:
  - `MK_DATE NOT LIKE '2099%'`（verify スモークの番兵行を常に除外）
  - `ORDER BY MK_DATE DESC`
  - `FETCH FIRST <limit> ROWS ONLY`（limit は整数検証してインライン、1..200 にクランプ。現行踏襲）
- WHERE は「必ず真の基点」から動的に連結する。実装は
  `WHERE MK_DATE NOT LIKE '2099%'` を土台に、指定された条件を順に AND する。
  バインド値は追加順にリストへ積み、`stmt.setString(i++, ...)` で束縛する。
- テーブル名は既存の `isSafeTableName` で検証済み。列名は固定リテラルなので
  動的連結でも SQL インジェクションの余地はない（値はすべてバインド）。
- `url/user/password/table_name` は引き続き必須。
- **要再ビルド/再デプロイ**: oracle-jdbc コンテナの作り直しが必要。

応答フォーマットは現行維持（`count=`, `ora_code=`, `error_message=`,
`row=MK_DATE,STA_NO1,STA_NO2,STA_NO3,T1_STATUS,UPCMPFLG`）。

### ② シェル `desktop/presence-tools/show-recent-records.sh`

- profiles.yaml から拠点値を読む処理は維持（既定値として使う）。
- POST 組立の前に対話式プロンプトを追加。順に:
  1. `STA_NO1（Enter=<profile値> / *=すべて）:`
  2. `STA_NO2（同上）:`
  3. `STA_NO3（同上）:`
  4. `期間 from YYYYMMDDhhmmss（Enter=指定なし）:`
  5. `期間 to   YYYYMMDDhhmmss（Enter=指定なし）:`
  6. `T1_STATUS（Enter=すべて / 例 1 や 3 を単一指定）:`
  7. `件数（Enter=30、最大200）:`
- 入力の解決:
  - 空欄 → その列は profile 値（拠点3列）、日時/status は「指定なし」でフィールド自体を送らない
  - `*` → その列は「絞らない」。フィールドを空文字で送るのではなく**送らない**
    （Java 側で「キー無し = 絞らない」を判定できるようにする）
- 現行の「station が空ならエラー」ガード（誤って全件取得を防ぐ目的）は、
  対話式の既定が profile 値になったため撤去/緩和する。全件横断は `*` 明示時のみ。
- 適用した条件をヘッダに1行で表示（例:
  `絞込: STA=100/*/300  期間=20260701000000..  status=すべて  件数=30`）。

### ③ 整形 `desktop/presence-tools/_render_recent.py`

- 変更は最小。未知 `T1_STATUS` の `?(n)` 表示は既存維持。
- 必要なら適用フィルタの見出し追記のみ（シェル側で出すなら不要）。

## テスト（TDD、先に書く）

- Java の SQL 組立をユニットで検証しづらいため、`services/bridge` 側の JDBC
  クライアントテスト（`services/bridge/tests/test_oracle_jdbc_client.py`）と
  `tests/desktop/` の該当テストに、以下ケースを追加:
  - 3列すべて指定 → 現行と同じ WHERE
  - 一部の列のみ指定（例 STA_NO1 のみ）→ その列だけ AND される
  - 拠点なし（全列 `*`）→ STA_NO 条件なし、番兵除外と ORDER/limit のみ
  - 日時範囲 from のみ / to のみ / 両方
  - t1_status 指定
  - limit の下限/上限クランプ（1未満→1、200超→200）
- Java 変更は既存の統合/スモーク（`scripts/verify_himereap_oracle.sh`,
  `scripts/check_himereap_recent.sh` など）で E2E 確認。

## 影響範囲 / リスク

- `/select_recent` を後方互換拡張（フィールド追加のみ、既存3列指定の呼び出しは同結果）。
- 全拠点横断（STA_NO 無し）は `FETCH FIRST N` で行数は抑えられるが、`MK_DATE` の
  索引状況によっては ORDER BY DESC が全表走査になり得る。実運用の N は小さい（既定30、
  最大200）ため実害は小さい想定。必要なら後続で索引を確認する。
- oracle-jdbc コンテナの再ビルドが必要。デプロイは既存手順に従う。

## 非対象（YAGNI）

- GUI/HTML 化はしない（対話式CLIのまま）。
- T1_STATUS の複数値 IN 指定は初版では対象外（単一値 or 空欄）。必要になれば拡張。
- presence event（①）側の経路には手を入れない。ビューアは Oracle を直接読むため
  経路①/②を区別せず、書き込まれた行をそのまま条件照会する。
