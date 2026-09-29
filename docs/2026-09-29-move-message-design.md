# 設計: 「旧親から移す」の完了メッセージで名簿の変更を正しく伝える（2026-09-29）

対象: `fleet_ui/migrate.py` の `take_child()` 末尾（旧親の名簿を書き換える部分）。
名簿を書き換える処理そのものは変えない。**書き換えたあとで旧親の名簿を読み直し、読み直した内容をもとに何を伝えるかを決める**。

## 0. 現状の事実（コードで確認済み）

| 項目 | 事実 | 場所 |
|---|---|---|
| 書き換え | `ssh_pi(old_host, "printf %s <stripped> > <remote_inventory>")`。**戻り値を捨てている** | `migrate.py:490-491` |
| 実行器 | `run_cmd_long` は終了コードを捨て、stdout だけを返す（失敗しても空文字） | `migrate.py:94-109` |
| 読み込み | `_read_remote_inventory` は sentinel(`CAT_OK`/`MISSING`) で `ok` / `missing` / `fail` を区別する | `migrate.py:161-183` |
| 既存の分岐 | `status != "ok"`（`missing` を含む）のとき「旧親の名簿は更新できませんでした。旧親で手動削除してください。」、ok=True | `migrate.py:479-489` |
| 成功の文言 | `"{entry} をこのハブへ移しました（ホスト名と局番号はそのまま）"`。名簿のことは言わない | `migrate.py:495-499` |
| 新ハブ側 | 先に `validate_hostname` で重複を弾くので、`add_to_inventory` の「既に登録済みです」には実際はほぼ来ない。失敗したら早く返る（`if not added.ok: return added`） | `migrate.py:350-356, 450-452` |
| `output` | 成功時は `waited.output`（子の IP）。ブラウザ画面は **失敗時だけ** `<pre>` で `output` を出す。ウィザードは `message` だけを表示する。`child_cli take` は JSON をそのまま出す | `static/index.html:287-297`, `setup-children-wizard.sh:98-107,188`, `child_cli.py:58-61` |
| 表示の改行 | ウィザードは `print(msg)` なので `\n` がそのまま改行になる。JSON は `\n` がエスケープされるので問題ない。**ブラウザの `<p id="migrate-msg">` には `white-space` の指定が無いので、`\n` は空白に潰れる**（文は「。」で終わるので読めるが、行は分かれない） | `index.html:55,223-228` |
| Git | `deploy-parent.sh` は `git diff --quiet` が偽だと実行を拒否する | `deploy-parent.sh:56-57` |

**ついでに見つけたこと（今回は直さない。別タスクにする）**: `_read_remote_inventory` が返す本文には、sentinel の前の
`printf '\n...'` が足した `\n` が1つ付いている。それをそのまま書き戻すので、**1回書き換えるごとに旧親の名簿の末尾に空行が1行増える**
（`'# old\nzero2\nother.local\n'` → `'# old\nother.local\n\n'` を確認済み）。そのため、逆向きに移し直しても旧親の
`git diff` はきれいに戻らない。直すと書き換えの振る舞いが変わるので、今回の制約（書き換えの振る舞いは変えない）の外にある。§4 の資料では「並び順と空行の差分が残ることがある」とだけ書く。

## 1. 結果の場合分けと完了メッセージ案（a）

前提: ここに来た時点で、子はこのハブの AP に居て、送り先も揃っている。どの場合も **`ok=True`** のままにする
（移すこと自体は済んでいる。`ok=False` にすると、ウィザードが「失敗しました。1 → 3 で取り込んで」と誤った案内を出す）。
利用者に何かしてもらう場合は、2行目を `要対応:` で始めて目立たせる。

共通の1行目: `{entry} をこのハブへ移しました（ホスト名と局番号はそのまま）。`

| # | 条件（どう判定するか） | 2行目以降（案） |
|---|---|---|
| A | 書き換える前の読み込みが `ok`、子の行が有った。書き換えのあと読み直しが `ok` で、子の行が無く、**他の行は変わっていない**（`parse_children_conf(読み直し) == parse_children_conf(stripped)`） | `旧親({old_host})の名簿 fleet/children.conf から {entry} を外しました（読み直して確認済み）。`<br>`このハブと旧親の fleet/children.conf が変わっています。commit してから deploy-parent.sh を実行してください。`<br>`試しに移しただけなら、元のハブで 1 → 1 を選び、旧親にこのハブを指定して逆向きに移すと名簿も戻ります。` |
| B1 | 書き換える前の読み込みが `fail`（既存の分岐） | `要対応: 旧親({old_host})の名簿を読めなかったので、{entry} の行は旧親に残っています。旧親で手動削除してください。` |
| B2 | 書き換えのあとの読み直しが `fail` / `missing` | `要対応: 旧親({old_host})の名簿を書き換えましたが、読み直せなかったので {entry} が外れたかは確かめられていません。旧親で fleet/children.conf を開き、{entry} の行が残っていれば手動削除してください。` |
| B3 | 読み直しが `ok` だが、子の行がまだ有る（書けていない）。**今日の検証と同じ「書いたつもりで消えていない」状態** | `要対応: 旧親({old_host})の名簿から {entry} を外せませんでした（読み直すと行が残っています）。旧親で手動削除してください。` |
| B4 | 読み直しが `ok`、子の行は無いが、**他の行も変わった**（想定外の書き換え） | `要対応: 旧親({old_host})の名簿が想定と違う形になりました（{entry} 以外の行も変わっています）。旧親で git diff fleet/children.conf を確認して直してください。` |
| C1 | 書き換える前の読み込みが `missing`（名簿のファイルが無い） | `旧親({old_host})には名簿 fleet/children.conf がありませんでした。旧親の名簿は変えていません。` |
| C2 | 読み込みは `ok` だが、子の行が元から無い（`entry` と一致する行が無い）。**書き換えをしない**（中身が変わらない書き換えを省くだけで、書き換えの結果は同じ） | `旧親({old_host})の名簿には {entry} が載っていませんでした。旧親の名簿は変えていません。` |
| C3 | C2 のうち、`.local` を付けた／外した形（`inventory_name(entry)` か `.local` を除いた名前）の行なら有る | `要対応: 旧親({old_host})の名簿には {entry} ではなく {見つかった形} として載っているので、外していません。旧親で手動削除してください。` |

方針のまとめ:
- **「外しました」と言ってよいのは A だけ**（読み直して、行が無いことを確かめた場合）。書き換えの SSH が何を返したかは判定に使わない（`run_cmd_long` は失敗しても空文字を返すので、返事からは何も分からない）。
- 「手動削除してください」は、行が残っている、または残っているかもしれない場合（B1/B2/B3/C3）だけに付ける。B4 は削除ではなく差分を確認してもらう。
- Git の注意と戻し方の一言は A だけに付ける（旧親のファイルが実際に変わったのは A と B4。B4 は git diff を確認してもらうので、そこで足りる）。
- 1行目は全部の場合で同じにする（ウィザードで何台か続けて移すとき、先頭を見れば移せたかが分かる）。
- 新ハブ側（`add_to_inventory`）: 失敗したらすでに早く返っている。「既に登録済みです」は前の重複チェックで実質来ないので、メッセージには足さない（足すと毎回の表示が長くなるだけ）。代わりに A の2行目で「このハブと旧親の」と両方の名簿に触れる。

## 2. 実装方針（b）

### 2.1 読み直し

`_read_remote_inventory` をそのまま使う（sentinel で `ok`/`missing`/`fail` を区別するので、読み直しにも使える）。
判定用に小さな関数を1つ足す（どちらも SSH を使わない純粋関数なので、単体テストしやすい）:

```python
def _entry_forms(entry: str) -> set[str]:
    short = entry[: -len(".local")] if entry.endswith(".local") else entry
    return {entry, short, f"{short}.local"}
```

一致の判定は `parse_children_conf(text)` の結果と比べる（`strip_inventory_entry` と同じ「`#` の前を strip した値との完全一致」）。

### 2.2 `take_child` 末尾の差分（擬似コード）

```diff
     remote_text, status = _read_remote_inventory(
         old_host, remote_inventory, runner=runner
     )
-    if status != "ok" or remote_text is None:
-        return StepResult(
-            ok=True,
-            message=(
-                f"{entry} をこのハブへ移しました（ホスト名と局番号はそのまま）。"
-                "旧親の名簿は更新できませんでした。旧親で手動削除してください。"
-            ),
-            output=waited.output,
-        )
-    stripped = strip_inventory_entry(remote_text, entry)
-    write_remote = f"printf %s {shlex.quote(stripped)} > {remote_inventory}"
-    ssh_pi(old_host, write_remote, runner=runner)
-
-    return StepResult(
-        ok=True,
-        message=f"{entry} をこのハブへ移しました（ホスト名と局番号はそのまま）",
-        output=waited.output,
-    )
+    head = f"{entry} をこのハブへ移しました（ホスト名と局番号はそのまま）。"
+    where = f"旧親({old_host})"
+
+    def done(*lines: str) -> StepResult:
+        return StepResult(ok=True, message="\n".join((head, *lines)), output=waited.output)
+
+    if status == "missing":                                   # C1
+        return done(f"{where}には名簿 fleet/children.conf がありませんでした。旧親の名簿は変えていません。")
+    if status != "ok" or remote_text is None:                 # B1（既存の文言を活かす）
+        return done(f"要対応: {where}の名簿を読めなかったので、{entry} の行は旧親に残っています。"
+                    "旧親で手動削除してください。")
+    before = parse_children_conf(remote_text)
+    if entry not in before:
+        other = sorted((_entry_forms(entry) - {entry}) & set(before))
+        if other:                                             # C3
+            return done(f"要対応: {where}の名簿には {entry} ではなく {other[0]} として載っているので、"
+                        "外していません。旧親で手動削除してください。")
+        return done(f"{where}の名簿には {entry} が載っていませんでした。旧親の名簿は変えていません。")  # C2
+
+    stripped = strip_inventory_entry(remote_text, entry)
+    write_remote = f"printf %s {shlex.quote(stripped)} > {remote_inventory}"
+    ssh_pi(old_host, write_remote, runner=runner)             # 返事は判定に使わない
+
+    after_text, after_status = _read_remote_inventory(old_host, remote_inventory, runner=runner)
+    if after_status != "ok" or after_text is None:            # B2
+        return done(f"要対応: {where}の名簿を書き換えましたが、読み直せなかったので {entry} が外れたかは"
+                    "確かめられていません。旧親で fleet/children.conf を開き、"
+                    f"{entry} の行が残っていれば手動削除してください。")
+    after = parse_children_conf(after_text)
+    if entry in after:                                        # B3
+        return done(f"要対応: {where}の名簿から {entry} を外せませんでした（読み直すと行が残っています）。"
+                    "旧親で手動削除してください。")
+    if after != parse_children_conf(stripped):                # B4
+        return done(f"要対応: {where}の名簿が想定と違う形になりました（{entry} 以外の行も変わっています）。"
+                    "旧親で git diff fleet/children.conf を確認して直してください。")
+    return done(                                              # A
+        f"{where}の名簿 fleet/children.conf から {entry} を外しました（読み直して確認済み）。",
+        "このハブと旧親の fleet/children.conf が変わっています。commit してから deploy-parent.sh を実行してください。",
+        "試しに移しただけなら、元のハブで 1 → 1 を選び、旧親にこのハブを指定して逆向きに移すと名簿も戻ります。",
+    )
```

注意:
- C2 で書き換えを省くのは「中身が変わらない書き換えをしない」だけ。行の有る場合の書き換え（`strip_inventory_entry` → `printf %s ... >`）は1文字も変えない。
- 読み直しの SSH が1回増える（`ConnectTimeout=8`、`run_cmd_long` の既定 45 秒）。旧親へはこの直前にも読み込みで繋がっているので、実際は1秒ほど。
- `old_host` は `validate_old_host` を通った値なので、メッセージに入れてよい。秘密値（PSK）はどの文にも入れない。

### 2.3 `message` と `output` の使い分け

- `message`: 利用者に見せる文（上の表）。複数行にしてよい。
- `output`: **今のまま `waited.output`（子の IP）にする**。成功時の `output` はどの画面も表示していない（ブラウザは失敗時だけ、ウィザードは使わない）。
  JSON の形も変わらないので、`child_cli` / `server.py` / 既存テストへの影響は無い。読み直した名簿の中身は `output` に入れない（形を変える理由が無い。原因の調べはメッセージの指示で旧親を直接見ればよい）。

### 2.4 複数行の表示

| 表示先 | 結果 | 対応 |
|---|---|---|
| ウィザード `children_json_message` | `print(msg)` なので改行どおりに出る | 不要 |
| `child_cli take` | JSON の `\n`。崩れない | 不要 |
| ブラウザ（`index.html` の `#migrate-msg`） | 改行が空白に潰れる | `#migrate-msg { white-space: pre-line; }` を1行足す（CSS だけ。JS は変えない） |

## 3. テストケース一覧（c）

すべて `fleet_ui/tests/test_migrate.py`。ダミー値だけを使う（`172.22.13.17`、`zero2`、`pskpskpsk` など既存と同じ）。

### 3.1 偽ランナーの準備

**状態を持つ旧親の名簿**を作る補助関数を足す（書き込みを本当に反映するかどうかを切り替えられる）:

```python
def _old_parent(body, *, apply_write=True, reread="ok"):
    """旧親の children.conf を1つ持つ。write を反映しない＝「書けたように見えて消えていない」。"""
    state = {"body": body, "reads": 0, "writes": []}
    def handle(joined, remote):
        if " > " in remote and "children.conf" in remote:      # 書き換え
            state["writes"].append(remote)
            if apply_write:
                state["body"] = shlex.split(remote)[2]          # printf %s <本文> > <path>
            return ""                                           # run_cmd_long は常に空を返しうる
        if "cat " in remote and "children.conf" in remote:      # 読み込み・読み直し
            state["reads"] += 1
            if state["reads"] >= 2 and reread == "fail":
                return ""
            return state["body"] + f"\n{CAT_OK}\n"
        return None
    return state, handle
```

これを `_take_script` と組み合わせ、`_take(...)` で実行する（`None` のときは `_take_script` へ回す）。

### 3.2 新しいテスト（TDD の順）

| # | テスト名（案） | 準備 | 検証 |
|---|---|---|---|
| T1 | `test_take_child_does_not_claim_removed_when_write_did_not_apply` | `apply_write=False`（書き換えの返事は空＝成功に見えるが、名簿は変わらない） | `res.ok` / `"外しました" not in res.message` / `"外せませんでした" in res.message` / `"手動削除" in res.message` / 読み込みが2回（`state["reads"] == 2`）。**修正前の実装では、名簿のことを何も言わずに成功と返すので、`"外せませんでした"` の検証で落ちる**（修正前は失敗することを確かめてから直す） |
| T2 | `test_take_child_reports_removal_only_after_reread` | `apply_write=True`、`"# old\nzero2\nother.local\n"` | `"旧親(172.22.13.17)の名簿" in res.message` / `"外しました（読み直して確認済み）" in res.message` / `"commit" in res.message and "deploy-parent.sh" in res.message` / `"逆向き" in res.message` / 書き換え後の本文に `other.local` と `# old` が残る / `"zero2\n"` が残らない |
| T3 | `test_take_child_reread_failure_is_not_reported_as_removed` | `apply_write=True`、`reread="fail"` | `res.ok` / `"確かめられていません" in res.message` / `"外しました" not in res.message` / `"手動削除" in res.message` |
| T4 | `test_take_child_reports_unexpected_rewrite_of_other_lines` | 書き込みのとき、本文を `""` にする（`other.local` まで消える）偽 | `"想定と違う形" in res.message` / `"git diff fleet/children.conf" in res.message` / `"外しました" not in res.message` |
| T5 | `test_take_child_skips_write_when_entry_not_on_old_parent` | 旧親の本文 `"other.local\n"`（zero2 無し） | `state["writes"] == []` / `"載っていませんでした" in res.message` / `"変えていません" in res.message` / `"手動削除" not in res.message` |
| T6 | `test_take_child_flags_local_suffix_mismatch_on_old_parent` | 旧親の本文 `"zero2.local\n"`、`entry="zero2"` | `state["writes"] == []` / `"zero2.local として載っている" in res.message` / `"手動削除" in res.message` |
| T7 | `test_take_child_missing_old_inventory_is_not_manual_delete` | `cat` への返事を `f"{MISSING}\n"` にする | `"ありませんでした" in res.message` / `"手動削除" not in res.message` / 書き換え無し |
| T8 | `test_take_child_message_first_line_is_stable` | T1・T2・T5 と同じ準備を `pytest.mark.parametrize` で | どの場合も `res.message.splitlines()[0] == "zero2 をこのハブへ移しました（ホスト名と局番号はそのまま）。"` |
| T9 | `test_take_child_output_stays_child_ip` | T2 と同じ | `res.output == "10.42.0.9"`（`output` の形を変えていないこと） |
| T10 | `test_take_child_message_never_contains_psk` | T1〜T7 の準備で | `"pskpskpsk" not in res.message` |

### 3.3 既存テストの洗い出しと書き換え

完了メッセージの文字列を固定しているテストは1件だけ。他は `res.ok` だけを見ている。

| 既存テスト | 今の検証 | 修正後にどうなるか | 書き換え |
|---|---|---|---|
| `test_take_child_does_not_wipe_old_inventory_when_cat_fails`（349行〜） | `writes == []` / `"手動削除" in res.message` | B1 に入る。文言に「手動削除」は残るので通る | 検証を1つ足す: `"外しました" not in res.message`。そのほかはそのまま |
| `test_take_child_installs_key_switches_wifi_keeps_identity`（170行〜） | `res.ok` のみ。偽は書き込みを反映しない（`old_inv_text` は辞書だが書き換えていない） | B3 に入る（`ok=True` なので通る） | 偽の書き込み分岐で `old_inv_text["body"] = shlex.split(remote)[2]` として反映し、`"外しました（読み直して確認済み）" in res.message` を足す（A の通しの確認になる） |
| `_take_script` を使うテスト群（745行〜: 送り先を揃える B2-T8〜） | `res.ok` と揃える呼び出しだけ | 読み込みは毎回 `zero2` を返すので B3 に入るが `ok=True` のまま | 変更不要。送り先の確認が主目的なので、名簿の文言には触れない |
| `test_take_child_keyscans_ip_...` / `..._wifi_ack_missing_...` | `res.ok` のみ | B3 に入るが通る | 変更不要 |
| `test_server_http.py` / `test_child_cli.py` / `scripts/tests/test_setup_children_wizard.py` | `take_payload` を差し替えている | 影響なし | 変更不要 |

ブラウザの CSS を足すなら、`fleet_ui/tests/test_server.py` に `"white-space: pre-line"` が `#migrate-msg` の近くにある（html 文字列に含まれる）ことを確かめる1行を足してよい（任意）。

確認のコマンド: `pytest fleet_ui/tests/test_migrate.py -q`、そのあと `pytest fleet_ui -q` と `ruff check fleet_ui`。

## 4. 資料の直し方（d）

### `docs/NEW-HUB-SETUP.md` §3.14（754行）

- 今: 「（`fleet_ui/migrate.py` の `take_child`）。完了メッセージにはこのことが出ない。」
- 案: 「（`fleet_ui/migrate.py` の `take_child`）。完了メッセージには、旧親の名簿を読み直して外れたことを確かめた場合だけ
  「旧親(…)の名簿 fleet/children.conf から … を外しました（読み直して確認済み）」と出る。
  読めなかった・外せなかった・別の形で載っていた場合は「要対応:」で始まる行が出るので、その指示どおり旧親で直す。」
- 「正しい戻し方」の項の末尾に1文足す: 「逆向きに移しても、旧親の `fleet/children.conf` は元の並び順に戻らず、末尾に空行が増えていることがある。`git diff` が並び順と空行だけなら `git checkout -- fleet/children.conf` で戻してよい。」
  （空行が増える件は §0 の「ついでに見つけたこと」。直したらこの文を消す。）

### `docs/child-migration.md`「名簿の扱い」（113〜114行）

- 今: 「（旧親の `fleet/children.conf` が SSH で書き換わる。Git 追跡ファイルなので旧親の作業ツリーが汚れる。完了メッセージには出ない）」
- 案: 「（旧親の `fleet/children.conf` が SSH で書き換わる。Git 追跡ファイルなので旧親の作業ツリーが汚れる。
  完了メッセージの2行目に、外せたか（読み直して確認済み）と、外せなかったときの対応（「要対応:」の行）が出る）」

どちらも、**実装がマージされたあとで**直す（先に直すと、今の動きと資料が食い違う）。

## 5. 実装タスクの所有範囲（e）

実装ワーカー1人で足りる（ファイル同士が密につながっているので分けない）。

| ファイル | 触ってよい範囲 |
|---|---|
| `fleet_ui/migrate.py` | `take_child` の末尾（旧親の名簿を読む所から `return` まで）と、小さな補助関数 `_entry_forms` の追加。`_read_remote_inventory`・`strip_inventory_entry`・`run_cmd_long`・書き換えのコマンドは**変えない** |
| `fleet_ui/tests/test_migrate.py` | §3 のテスト追加と、§3.3 の既存テスト2件の書き換え |
| `fleet_ui/static/index.html` | `#migrate-msg { white-space: pre-line; }` の1行だけ |
| `fleet_ui/tests/test_server.py` | （任意）上の CSS の確認を1行 |
| `docs/NEW-HUB-SETUP.md` | §3.14 の §4 に書いた2か所だけ |
| `docs/child-migration.md` | 「名簿の扱い」の §4 に書いた1か所だけ |

触らないもの: `child/`、`services/`、`scripts/bootstrap/`、`scripts/setup-children-wizard.sh`（変更不要）、`fleet_ui/child_cli.py`・`fleet_ui/server.py`（変更不要）、`fleet_ui/provision.py`。
実機・USB・sudo は使わない。

別タスクの候補（今回の範囲外）: `_read_remote_inventory` の本文から sentinel 用の `\n` を1つ除き、書き換えのたびに空行が増えないようにする（書き換えの振る舞いが変わるので、別に設計とテストを立てる）。
