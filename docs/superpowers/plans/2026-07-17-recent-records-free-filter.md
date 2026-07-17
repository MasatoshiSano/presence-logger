# 直近記録ビューア 自由フィルタ化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** デスクトップ「直近N件」ビューアを、拠点 STA_NO・日時範囲・T1_STATUS で自由に絞り込めるようにし、T1_STATUS は数字そのまま表示する。

**Architecture:** 既存3層（対話式シェル → oracle-jdbc サイドカー `/select_recent` → 整形）を維持。Java の `selectRecent` を「渡された条件だけで WHERE を動的に組む」形へ後方互換拡張し、Python クライアント `pipeline_monitor/oracle_reader.py` と対話式シェルから任意条件を送れるようにする。表示は入退室翻訳を撤去。

**Tech Stack:** Java (com.sun.net.httpserver, JDBC ojdbc11)、Python 3（pytest）、Bash、Docker Compose。

## Global Constraints

- SQL の値はすべて **PreparedStatement バインド変数**で渡す（列名・テーブル名は固定リテラル / `isSafeTableName` で検証済み）。動的連結でも値を文字列結合しない。
- `limit` は整数検証し 1..200 にクランプ。`FETCH FIRST <limit>` のみインライン（現行踏襲）。
- `MK_DATE NOT LIKE '2099%'`（verify スモークの番兵行）は常に除外し続ける。
- 応答フォーマットは不変: `count=` / `ora_code=` / `error_message=` / `row=MK_DATE,STA_NO1,STA_NO2,STA_NO3,T1_STATUS,UPCMPFLG`（最新順 DESC）。
- `/select_recent` は後方互換: 既存の「3拠点必須」呼び出しは同じ結果を返すこと。
- テスト実行: `python -m pytest`（設定は `pyproject.toml`、`--import-mode=importlib`）。
- oracle-jdbc の compose サービス名は `oracle-jdbc`、コンテナ名は `presence-oracle-jdbc`、ビルドコンテキストは `./services/oracle-jdbc`。
- **T1_STATUS は入退室(ENTER/EXIT)へ翻訳せず数字そのまま表示**（子Piデータは 1/2 以外に 3,11,14,17,99 等を含む）。

---

## File Structure

- `desktop/presence-tools/pipeline_monitor/oracle_reader.py` — `OracleQuery` に任意フィルタ列を追加、`build_post_body` を空欄フィールド省略に変更（Task 1）
- `tests/desktop/test_oracle_fetch.py` — 上記のテスト追加（Task 1）
- `services/oracle-jdbc/src/Main.java` — `selectRecent` を動的 WHERE に（Task 2）
- `desktop/presence-tools/_render_recent.py` — ENTER/EXIT 翻訳撤去、T1 数値表示。整形本体を import 可能な関数へ抽出（Task 3）
- `tests/desktop/test_render_recent.py` — 新規、整形関数のテスト（Task 3）
- `desktop/presence-tools/show-recent-records.sh` — 対話式プロンプト＋`DRY_RUN` ボディ出力（Task 4）

---

## Task 1: Python クライアントに任意フィルタを追加

**Files:**
- Modify: `desktop/presence-tools/pipeline_monitor/oracle_reader.py:40-63`
- Test: `tests/desktop/test_oracle_fetch.py`

**Interfaces:**
- Produces: `OracleQuery` dataclass に追加フィールド
  `sta_no1/2/3: str = ""`（既定を必須→任意化）, `mk_date_from: str = ""`,
  `mk_date_to: str = ""`, `t1_status: str = ""`。
  `build_post_body(q: OracleQuery, password: str) -> str` は、`url/user/password/table_name/limit`
  を常に含め、`sta_no1/sta_no2/sta_no3/mk_date_from/mk_date_to/t1_status` は
  **値が非空のときだけ**含める。

- [ ] **Step 1: 失敗するテストを書く**

`tests/desktop/test_oracle_fetch.py` の末尾に追記:

```python
from urllib.parse import parse_qs


def test_build_post_body_omits_blank_filter_fields():
    q = OracleQuery(host="h", port="1521", service="S", user="u", table="T",
                    limit=30)  # sta/date/status すべて既定(空)
    body = parse_qs(build_post_body(q, "pw"), keep_blank_values=True)
    # 常に含む
    assert body["table_name"] == ["T"]
    assert body["limit"] == ["30"]
    # 空欄は送らない
    for k in ("sta_no1", "sta_no2", "sta_no3", "mk_date_from", "mk_date_to", "t1_status"):
        assert k not in body


def test_build_post_body_includes_only_provided_filters():
    q = OracleQuery(host="h", port="1521", service="S", user="u", table="T",
                    sta_no1="100", mk_date_from="20260717000000", t1_status="3",
                    limit=50)
    body = parse_qs(build_post_body(q, "pw"), keep_blank_values=True)
    assert body["sta_no1"] == ["100"]
    assert body["mk_date_from"] == ["20260717000000"]
    assert body["t1_status"] == ["3"]
    assert "sta_no2" not in body          # 与えていない
    assert "mk_date_to" not in body
    assert body["limit"] == ["50"]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/desktop/test_oracle_fetch.py -q`
Expected: FAIL（`OracleQuery` に `mk_date_from` 等が無く TypeError、または空欄が本文に混入）

- [ ] **Step 3: 最小実装**

`oracle_reader.py` の `OracleQuery` と `build_post_body` を差し替え:

```python
@dataclass
class OracleQuery:
    host: str
    port: str
    service: str
    user: str
    table: str
    sta_no1: str = ""
    sta_no2: str = ""
    sta_no3: str = ""
    mk_date_from: str = ""
    mk_date_to: str = ""
    t1_status: str = ""
    limit: int = 30


def build_post_body(q: OracleQuery, password: str) -> str:
    fields = {
        "url": f"jdbc:oracle:thin:@{q.host}:{q.port}/{q.service}",
        "user": q.user,
        "password": password,
        "table_name": q.table,
        "limit": str(q.limit),
    }
    # 任意フィルタは値があるときだけ送る（空欄=絞らない）
    for key in ("sta_no1", "sta_no2", "sta_no3", "mk_date_from", "mk_date_to", "t1_status"):
        val = getattr(q, key)
        if val:
            fields[key] = val
    return urllib.parse.urlencode(fields)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m pytest tests/desktop/test_oracle_fetch.py -q`
Expected: PASS（既存 `test_build_post_body_is_urlencoded_and_has_jdbc_url` も、`sta_no1=100` を明示指定しているので引き続き PASS）

- [ ] **Step 5: コミット**

```bash
git add desktop/presence-tools/pipeline_monitor/oracle_reader.py tests/desktop/test_oracle_fetch.py
git commit -m "feat(monitor): optional station/date/status filters in OracleQuery"
```

---

## Task 2: Java サイドカー `/select_recent` を動的 WHERE に

**Files:**
- Modify: `services/oracle-jdbc/src/Main.java:362-448`（`selectRecent` メソッド全体）

**Interfaces:**
- Consumes: Task 1 が送る POST フィールド。必須は `url,user,password,table_name`。
  任意は `sta_no1,sta_no2,sta_no3,mk_date_from,mk_date_to,t1_status,limit,connect_timeout_ms,read_timeout_ms`。
- Produces: 変わらない応答（`count/ora_code/error_message/row=...`）。渡された任意フィールドだけ
  `AND <col> = ?` / `AND MK_DATE >= ?` / `AND MK_DATE <= ?` を WHERE に足す。

- [ ] **Step 1: `selectRecent` を差し替える**

`Main.java` の `selectRecent`（現行 362-448 行）を以下で置換。必須チェックから
sta_no1/2/3 を外し、WHERE と bind を動的に組む:

```java
    private static void selectRecent(HttpExchange ex) throws IOException {
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            sendPlain(ex, 405, "error_message=method not allowed\n");
            return;
        }
        Map<String, String> form;
        try (InputStream is = ex.getRequestBody()) {
            form = parseForm(new String(is.readAllBytes(), StandardCharsets.UTF_8));
        }
        for (String required : new String[]{"url", "user", "password", "table_name"}) {
            if (!form.containsKey(required)) {
                sendPlain(ex, 400, "error_message=missing field: " + required + "\n");
                return;
            }
        }
        String tableName = form.get("table_name");
        if (!isSafeTableName(tableName)) {
            sendPlain(ex, 400, "error_message=invalid table_name\n");
            return;
        }

        // FETCH FIRST N は版差でバインド不可のため小整数として検証しインライン。既定30、1..200。
        int limit = 30;
        try {
            limit = Integer.parseInt(form.getOrDefault("limit", "30").trim());
        } catch (NumberFormatException nfe) {
            sendPlain(ex, 400, "error_message=invalid limit\n");
            return;
        }
        if (limit < 1) limit = 1;
        if (limit > 200) limit = 200;

        // 任意フィルタを動的に WHERE へ。値はすべてバインド。列名は固定リテラル。
        StringBuilder where = new StringBuilder(" WHERE MK_DATE NOT LIKE '2099%'");
        List<String> binds = new ArrayList<>();
        String[][] eqCols = {
            {"sta_no1", "STA_NO1"}, {"sta_no2", "STA_NO2"}, {"sta_no3", "STA_NO3"},
            {"t1_status", "T1_STATUS"},
        };
        for (String[] c : eqCols) {
            String v = form.get(c[0]);
            if (v != null && !v.isEmpty()) {
                where.append(" AND ").append(c[1]).append(" = ?");
                binds.add(v);
            }
        }
        String from = form.get("mk_date_from");
        if (from != null && !from.isEmpty()) {
            where.append(" AND MK_DATE >= ?");
            binds.add(from);
        }
        String to = form.get("mk_date_to");
        if (to != null && !to.isEmpty()) {
            where.append(" AND MK_DATE <= ?");
            binds.add(to);
        }

        Properties props = new Properties();
        props.setProperty("user", form.get("user"));
        props.setProperty("password", form.get("password"));
        props.setProperty("oracle.net.CONNECT_TIMEOUT",
            form.getOrDefault("connect_timeout_ms", "10000"));
        props.setProperty("oracle.jdbc.ReadTimeout",
            form.getOrDefault("read_timeout_ms", "30000"));

        String sql =
            "SELECT MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS, UPCMPFLG FROM " + tableName
            + where
            + " ORDER BY MK_DATE DESC FETCH FIRST " + limit + " ROWS ONLY";

        int count = 0;
        Integer oraCode = null;
        String errorMessage = "";
        StringBuilder rows = new StringBuilder();

        try (Connection conn = DriverManager.getConnection(form.get("url"), props);
             PreparedStatement stmt = conn.prepareStatement(sql)) {
            for (int i = 0; i < binds.size(); i++) {
                stmt.setString(i + 1, binds.get(i));
            }
            try (ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    rows.append("row=")
                        .append(rs.getString(1)).append(',')
                        .append(rs.getString(2)).append(',')
                        .append(rs.getString(3)).append(',')
                        .append(rs.getString(4)).append(',')
                        .append(rs.getInt(5)).append(',')
                        .append(rs.getInt(6)).append('\n');
                    count++;
                }
            }
        } catch (SQLException sqlEx) {
            oraCode = sqlEx.getErrorCode() != 0 ? sqlEx.getErrorCode() : null;
            errorMessage = sanitizeOneLine(sqlEx.getMessage());
        } catch (Exception other) {
            errorMessage = sanitizeOneLine(other.getClass().getSimpleName() + ": " + other.getMessage());
        }

        StringBuilder sb = new StringBuilder();
        sb.append("count=").append(count).append('\n');
        sb.append("ora_code=").append(oraCode == null ? "" : oraCode.toString()).append('\n');
        sb.append("error_message=").append(errorMessage).append('\n');
        sb.append(rows);
        sendPlain(ex, 200, sb.toString());
    }
```

- [ ] **Step 2: import を追加**

`Main.java` 冒頭の import 群に、未追加なら以下を足す（`java.util.List` / `java.util.ArrayList`）:

```java
import java.util.ArrayList;
import java.util.List;
```

確認: `grep -n "import java.util" services/oracle-jdbc/src/Main.java`

- [ ] **Step 3: docstring を更新**

`selectRecent` 直前の Javadoc の "POST fields" 行を実態に合わせて更新:

```java
     * POST fields: url, user, password, table_name (required)
     *   optional filters (each ANDed when non-empty): sta_no1, sta_no2, sta_no3,
     *     t1_status, mk_date_from (MK_DATE>=), mk_date_to (MK_DATE<=)
     *   optional: limit (default 30, capped 200), connect_timeout_ms, read_timeout_ms
     * 2099% sentinel rows are always excluded. No filter => latest N across the table.
```

- [ ] **Step 4: コンテナをビルドして起動（構文/コンパイル検証）**

Run:
```bash
docker compose build oracle-jdbc
```
Expected: `javac` がエラーなく完了しイメージが焼ける（コンパイルエラーがあればここで露見）。

```bash
docker compose up -d oracle-jdbc && docker ps --filter name=presence-oracle-jdbc
```
Expected: `presence-oracle-jdbc` が healthy/up。

- [ ] **Step 5: 後方互換のスモーク（DBに繋がる場合のみ）**

> 注: DB照会は HIME-H-REAP 網からのみ到達可能。`presence-hub` AP 接続中は ORA-12170 になる。
> 接続できない場合はこの Step を「接続後に実施」として保留し、Step 4 のビルド成功をもって
> コンパイル検証済みとする。

接続時 Run: `bash scripts/check_himereap_recent.sh` 相当、または
`bash desktop/presence-tools/show-recent-records.sh 5`（Task 4 後）で
既存の3拠点指定が従来どおり件数を返すことを確認。
Expected: 既存挙動と同じ行が返る（後方互換）。

- [ ] **Step 6: コミット**

```bash
git add services/oracle-jdbc/src/Main.java
git commit -m "feat(oracle-jdbc): dynamic optional filters in /select_recent"
```

---

## Task 3: 整形を数値表示に（ENTER/EXIT 翻訳撤去）

**Files:**
- Modify: `desktop/presence-tools/_render_recent.py`
- Test: `tests/desktop/test_render_recent.py`（新規）

**Interfaces:**
- Produces: `render(text: str) -> str` — サイドカー応答テキストを受け取り、表示用の
  複数行文字列を返す純関数。`main()` は `sys.stdin.read()` を渡して結果を print する薄い
  ラッパにする。T1_STATUS 列は数値そのまま（翻訳・凡例なし）。

- [ ] **Step 1: 失敗するテストを書く**

`tests/desktop/test_render_recent.py`（新規）:

```python
import importlib.util
from pathlib import Path

# ハイフン付きディレクトリのスクリプトをファイルパスから読み込む
_P = Path(__file__).resolve().parents[2] / "desktop" / "presence-tools" / "_render_recent.py"
_spec = importlib.util.spec_from_file_location("_render_recent", _P)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
render = _mod.render


def test_render_shows_raw_t1_status_not_enter_exit():
    text = (
        "count=2\nora_code=\nerror_message=\n"
        "row=20260717090000,HIME,ABC,001,1,0\n"
        "row=20260717090005,HIME,ABC,001,11,0\n"
    )
    out = render(text)
    assert "ENTER" not in out and "EXIT" not in out
    assert "🟢" not in out and "🔴" not in out
    # 生の数値が出る（1 と 11）
    assert "20260717090000" not in out  # 整形済み日時になる
    assert "2026-07-17 09:00:00" in out
    assert " 1 " in out or "\t1" in out or "  1  " in out  # T1=1 が数値で見える
    assert "11" in out


def test_render_reports_error():
    out = render("count=0\nora_code=12170\nerror_message=timeout\n")
    assert "12170" in out


def test_render_empty():
    out = render("count=0\nora_code=\nerror_message=\n")
    assert "0" in out  # 0件でも壊れない
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python -m pytest tests/desktop/test_render_recent.py -q`
Expected: FAIL（`render` 属性が無い / ENTER が出る）

- [ ] **Step 3: `_render_recent.py` を実装**

`STATUS` マップと凡例を撤去し、`render(text)` 純関数へ抽出。全文を以下で置換:

```python
#!/usr/bin/env python3
"""show-recent-records.sh 用の整形フィルタ。

oracle-jdbc サイドカーの /select_recent 応答（key=value テキスト）を
人が読める一覧表に整形する。

応答フォーマット:
    count=N
    ora_code=        (空 or ORA番号)
    error_message=
    row=MK_DATE,STA_NO1,STA_NO2,STA_NO3,T1_STATUS,UPCMPFLG
    ...(最新順 / DESC)

T1_STATUS は入退室(1/2)に限らない任意コードなので、ENTER/EXIT へ翻訳せず
数値そのまま表示する。
"""
import sys


def fmt_mk(mk: str) -> str:
    # 14桁 YYYYMMDDHHMMSS を素直に整形。桁が違えば生値のまま返す（壊さない）。
    if len(mk) == 14 and mk.isdigit():
        return f"{mk[0:4]}-{mk[4:6]}-{mk[6:8]} {mk[8:10]}:{mk[10:12]}:{mk[12:14]}"
    return mk


def render(text: str) -> str:
    count = None
    ora_code = ""
    error_message = ""
    rows = []
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("count="):
            count = line[len("count="):]
        elif line.startswith("ora_code="):
            ora_code = line[len("ora_code="):]
        elif line.startswith("error_message="):
            error_message = line[len("error_message="):]
        elif line.startswith("row="):
            rows.append(line[len("row="):].split(",", 5))

    out = []
    if ora_code or error_message:
        out.append("")
        out.append("  ❌ DB照会でエラーが発生しました")
        if ora_code:
            out.append(f"     ORA-{ora_code}")
        if error_message:
            out.append(f"     {error_message}")
        out.append("")
        out.append("  ヒント: HIME-H-REAP に接続中か確認してください（未接続だとDBに届きません）。")
        return "\n".join(out)

    if not rows:
        out.append("")
        out.append("  📭 該当する記録がありません（この条件の行は0件）。")
        out.append("")
        return "\n".join(out)

    out.append("")
    out.append(f"  {'#':>3}  {'日時 (JST)':<19}  {'T1':>4}  {'STA(1/2/3)':<16}  UPCMPFLG")
    out.append(f"  {'-'*3}  {'-'*19}  {'-'*4}  {'-'*16}  {'-'*8}")
    for i, r in enumerate(rows, 1):
        mk, s1, s2, s3, t1 = r[0], r[1], r[2], r[3], r[4]
        upc = r[5] if len(r) > 5 else ""
        sta = f"{s1}/{s2}/{s3}"
        out.append(f"  {i:>3}  {fmt_mk(mk):<19}  {t1:>4}  {sta:<16}  {upc}")
    out.append("")
    out.append(f"  合計 {count} 件（最新が上）。 T1=T1_STATUS(数値そのまま)")
    out.append("")
    return "\n".join(out)


def main() -> int:
    print(render(sys.stdin.read()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m pytest tests/desktop/test_render_recent.py -q`
Expected: PASS

- [ ] **Step 5: 手動スモーク（パイプ動作確認）**

Run:
```bash
printf 'count=1\nora_code=\nerror_message=\nrow=20260717090047,HIME,ABC,001,11,0\n' \
  | python3 desktop/presence-tools/_render_recent.py
```
Expected: `T1` 列に `11` が数値で出る。ENTER/EXIT/絵文字が出ない。

- [ ] **Step 6: コミット**

```bash
git add desktop/presence-tools/_render_recent.py tests/desktop/test_render_recent.py
git commit -m "feat(desktop): render T1_STATUS as raw number, drop ENTER/EXIT"
```

---

## Task 4: 対話式プロンプト付きシェル

**Files:**
- Modify: `desktop/presence-tools/show-recent-records.sh`

**Interfaces:**
- Consumes: profiles.yaml の `sta_no1/2/3`（Enter 時の既定値）、Task 2 の任意フィールド仕様。
- Produces: 起動時に STA_NO1/2/3・期間 from/to・T1_STATUS・件数 を対話取得し、
  POST ボディを組み立てて `/select_recent` を叩き、`_render_recent.py` に渡す。
  `DRY_RUN=1` のとき docker exec を実行せず、組み立てたフィールド一覧を出力して終了。

**入力解決ルール（確定仕様）:**
- STA_NO 各列: 空欄(Enter)= profile 値を使う / `*` = その列を送らない（絞らない）/ それ以外 = その値で絞る
- 期間 from/to、T1_STATUS: 空欄 = 送らない / 値あり = その値
- 件数: 空欄 = 30

- [ ] **Step 1: 対話プロンプトと DRY_RUN を実装**

`show-recent-records.sh` の「非秘密の接続情報を読む」ブロック（`eval "$raw"` の直後、
現行 66-75 行あたり。station 空チェックの手前）に、対話取得と解決ロジックを挿入する。
`STA_NO ... echo` の固定表示は解決後の値表示に置き換える。

まず対話取得（プロンプト。`read` は tty から）:

```bash
# --- 対話式フィルタ入力（Enter=既定/絞らない、* =その列を絞らない）---
echo
echo "  フィルタ条件を入力（Enter で既定 / 詳細は各行の括弧）"
read -rp "  STA_NO1（Enter=${PCFG[sta_no1]} / *=すべて）: " IN_S1
read -rp "  STA_NO2（Enter=${PCFG[sta_no2]} / *=すべて）: " IN_S2
read -rp "  STA_NO3（Enter=${PCFG[sta_no3]} / *=すべて）: " IN_S3
read -rp "  期間 from YYYYMMDDhhmmss（Enter=指定なし）: " IN_FROM
read -rp "  期間 to   YYYYMMDDhhmmss（Enter=指定なし）: " IN_TO
read -rp "  T1_STATUS（Enter=すべて / 例 1 や 3）: " IN_T1
read -rp "  件数（Enter=${LIMIT}、最大200）: " IN_LIMIT
```

次に解決（`resolve_sta` は Enter→profile、`*`→空、他→そのまま）:

```bash
resolve_sta() {  # $1=入力 $2=profile既定
    case "$1" in
        "")  printf '%s' "$2" ;;   # Enter → profile値
        "*") printf '' ;;          # * → 絞らない(空)
        *)   printf '%s' "$1" ;;   # 明示指定
    esac
}
F_S1="$(resolve_sta "$IN_S1" "${PCFG[sta_no1]}")"
F_S2="$(resolve_sta "$IN_S2" "${PCFG[sta_no2]}")"
F_S3="$(resolve_sta "$IN_S3" "${PCFG[sta_no3]}")"
F_FROM="$IN_FROM"
F_TO="$IN_TO"
F_T1="$IN_T1"
[[ -n "$IN_LIMIT" ]] && LIMIT="$IN_LIMIT"

echo "===================================================================="
echo "   絞込: STA=${F_S1:-*}/${F_S2:-*}/${F_S3:-*}  期間=${F_FROM:-…}..${F_TO:-…}  T1=${F_T1:-すべて}  件数=${LIMIT}"
echo "   接続先: ${PCFG[oracle_host]}:${PCFG[oracle_port]}/${PCFG[oracle_service]}  user=${PCFG[oracle_user]}"
echo "===================================================================="
```

- [ ] **Step 2: 全件取得ガードを緩和**

現行の「station が空ならエラー終了」ブロック（69-72 行）を削除する。全拠点横断は
`*` 明示時の意図的動作になったため。profiles 読み込み失敗自体のエラーは残す。

- [ ] **Step 3: POST ボディ組み立てを条件付きに変更**

現行のボディ組み立て（86-99 行の python urlencode）を、空値を省くよう置換:

```bash
BODY="$(python3 - <<PY
import urllib.parse
fields = {
    "url":        "jdbc:oracle:thin:@${PCFG[oracle_host]}:${PCFG[oracle_port]}/${PCFG[oracle_service]}",
    "user":       "${PCFG[oracle_user]}",
    "password":   """${ORACLE_PW}""",
    "table_name": "${PCFG[oracle_table]}",
    "limit":      "${LIMIT}",
}
opt = {
    "sta_no1": "${F_S1}", "sta_no2": "${F_S2}", "sta_no3": "${F_S3}",
    "mk_date_from": "${F_FROM}", "mk_date_to": "${F_TO}", "t1_status": "${F_T1}",
}
for k, v in opt.items():
    if v:
        fields[k] = v
print(urllib.parse.urlencode(fields))
PY
)"
```

> 注: `ORACLE_PW` 取得（docker exec printenv）は DRY_RUN 時に失敗し得るので Step 4 で分岐する。

- [ ] **Step 4: DRY_RUN 分岐を追加**

`ORACLE_PW` 取得の**前**に DRY_RUN を判定し、パスワード無し・docker exec 無しで
組み立て結果を出して終了する。DRY_RUN では password をダミーにする:

```bash
if [[ "${DRY_RUN:-0}" == "1" ]]; then
    ORACLE_PW="DRYRUN"
    BODY_DUMP=1
fi
```

を `ORACLE_PW="$(docker exec ...)"` の直前に置き、その取得行を
`if [[ "${DRY_RUN:-0}" != "1" ]]; then ... fi` で囲う。ボディ組み立て後、
サイドカー POST の直前に:

```bash
if [[ "${BODY_DUMP:-0}" == "1" ]]; then
    echo "[DRY_RUN] POST body fields:"
    printf '%s\n' "$BODY" | tr '&' '\n'
    exit 0
fi
```

- [ ] **Step 5: DRY_RUN でプロンプト→ボディ対応を検証**

Run（profiles.yaml.example にフォールバックする前提で、拠点は Enter=既定、期間指定あり、STA_NO2 を全件、T1=3）:
```bash
printf '\n*\n\n20260717000000\n20260717235959\n3\n5\n' \
  | DRY_RUN=1 bash desktop/presence-tools/show-recent-records.sh
```
Expected: `[DRY_RUN] POST body fields:` の下に
`sta_no1=<profile値>`、`sta_no3=<profile値>` はあるが `sta_no2` は無い、
`mk_date_from=20260717000000`、`mk_date_to=20260717235959`、`t1_status=3`、`limit=5` が並ぶ。

- [ ] **Step 6: 実照会スモーク（HIME-H-REAP 接続時のみ）**

> `presence-hub` AP 接続中は DB 未到達（ORA-12170）。接続後に実施。

Run: `bash desktop/presence-tools/show-recent-records.sh`（Enter 連打）
Expected: 従来どおり自拠点の直近30件が、T1 列に数値で表示される。

- [ ] **Step 7: コミット**

```bash
git add desktop/presence-tools/show-recent-records.sh
git commit -m "feat(desktop): interactive free-filter prompts for recent-records viewer"
```

---

## Self-Review

**1. Spec coverage:**
- 拠点 STA_NO（部分/全/既定） → Task 1(body), Task 2(WHERE), Task 4(prompt+resolve) ✓
- 日時範囲 MK_DATE → Task 1, Task 2, Task 4 ✓
- T1_STATUS 任意コード絞込 → Task 1, Task 2, Task 4 ✓
- 件数 limit（1..200） → Task 2(クランプ既存維持), Task 4(prompt) ✓
- T1_STATUS 数値表示 → Task 3 ✓
- 後方互換 → Task 2 Step 5、Task 4 Step 6 ✓
- 全条件バインド/番兵除外/応答不変 → Global Constraints + Task 2 ✓

**2. Placeholder scan:** コード各ステップに実コードあり。TBD/TODO なし。

**3. Type consistency:** `OracleQuery` の新フィールド名（`mk_date_from/mk_date_to/t1_status`, `sta_no*`）は
Task 1・Task 2（Java の form キー）・Task 4（POST フィールド）で一致。応答パーサ `render`/`parse_select_recent`
は列順（MK_DATE,STA_NO1..3,T1_STATUS,UPCMPFLG）を変えないため整合。

**注意点（実装者向け）:**
- Java 変更の DB 実照会検証は HIME-H-REAP 網からのみ可能。未接続時は「ビルド成功＋DRY_RUN＋ローカル整形テスト」までを完了条件とし、実照会は接続後に回す。
- 既存の別バグ（同一 MK_DATE+STA で T1_STATUS 違いが Oracle PK と衝突し ORA-00001）は本計画の対象外。フィルタ機能とは独立の課題として別途対応する。
