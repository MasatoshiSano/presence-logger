# 設計: 「このUSBからコピー」の後にハブ初期設定を自動で始める

- 日付: 2026-09-29
- 状態: 設計のみ（まだ実装していない）
- 依頼: 「コピー後にウィザードを自動起動して」
- 担当: 設計 Opus → 実装 Sonnet（TDD）→ レビュー Codex

## 0. 結論（先に）

仮案の 1〜5 はおおむね採用する。ただし、コードを読んで仮案と違う決定を **3 点**した。

1. **事前点検(preflight)の「repo」判定は、コピーの後では意味がない。**
   `preflight_repo_verdict` は `~/projects/presence-logger` に別アプリの中身があると BLOCK を出す。
   ところがコピーした後で点検すると、そこには presence-logger の印になる3ファイル
   （`docker-compose.yml`, `services/bridge`, `scripts/bootstrap-hub.sh`）が必ずあるので、
   いつも OK になる。そしてコピー(`rsync -a`)は同じ名前のファイルを上書きするので、
   別アプリのファイルはもう壊れている。
   → **コピーを始める前に、コピー先を確認する仕組みをコピー自体に入れる**
   （`copy_dest_verdict`。preflight と同じ3つの印で判定し、別物ならコピーしない）。
   ウィザードを自動で始めるようになると、利用者が README の手順1（先に点検する）を飛ばしやすくなる。
   だからこの確認はこの変更に含める。
2. **root で動いたときの SUDO_USER の扱い。** `sudo -u pi` でウィザードを起動すると、
   ウィザードの中では `SUDO_USER=root` になる。ウィザードは `user="${SUDO_USER:-$USER}"` の持ち主で
   `chown` するので、site.env・secrets.env・setup-complete が **root の持ち物になる**。
   → root のときは `runuser -u <利用者> -- env -u SUDO_USER -u SUDO_UID -u SUDO_GID -u SUDO_COMMAND HOME=… USER=… LOGNAME=…`
   で起動する（sudo は使わない）。利用者が決まらない（本物の root でログインしている）ときは自動では始めない。
3. **点検が途中で落ちたとき（戻り値が 0 でも 2 でもない）は、ウィザードを始めない。**
   安全側に倒す（[[project_silent_success_pattern]] の教訓：うまくいかなかったのに成功扱いにしない）。

## 1. コードを読んで分かったこと（仮案の前提の確認）

| 確認したこと | 結果 | 根拠 |
|---|---|---|
| コピーで `.kit/setup-complete` が消えるか | **消えない**。`kit_copy_dir` は `rsync -a src/ dst/`（`--delete` なし）か、tar で重ねるだけ | `scripts/lib/kit-copy.sh:5-13` |
| キットに `setup-complete` が入ることはあるか | **入らない**。payload からは `.kit` を除外しており、キットの `.kit` は pack が毎回新しく作る（origin.env / 雛形 / driver / images だけ） | `scripts/pack-hub-usb.sh:16-31, 326-364` |
| もう一度コピーしたとき site.env / secrets.env を上書きするか | しない。site.env は payload から除外。キットにあるのは secrets.env.template だけ | 同上 |
| 利用者が止めないと setup-complete は残るか | 残る。ウィザードは bootstrap が成功した後にだけ `date > marker` を書く | `scripts/setup-hub-wizard.sh:650-651` |
| setup-complete があるときのウィザード | 「既に設定済み」＋ `p`（AP パスワードだけやり直す）メニューになる | `setup-hub-wizard.sh:337-355` |
| ウィザードが入力を読む場所 | 標準入力から `read -r`。tty が無いと先へ進めない | `setup-hub-wizard.sh:303-320` |
| ウィザードの最後 | 自分で「Enterで閉じる」を待つ | `setup-hub-wizard.sh:665-667` |
| コピーを呼ぶところ | `.desktop` から lxterminal の中で、**利用者本人（pi）として** `bash copy-to-this-pi.sh`。終わった後に `.desktop` 側でも `read 'Enterで閉じる'` | `pack-hub-usb.sh:304` |
| `copy-hub-from-usb.sh` を他から source しているか | **している**。`scripts/bootstrap/70-desktop.sh:12` が関数（`copy_trust_desktop` など）を使うために source している。main は `[[ BASH_SOURCE == $0 ]]` の時だけ動く | `70-desktop.sh:11-12` |
| preflight の戻り値 | BLOCK があれば 2、WARN だけなら 0（見分けるには出力の `^\[WARN\]` を数える） | `preflight-new-hub.sh:259-273` |
| preflight が見るホーム | `$HOME/projects/presence-logger` と `$HOME/Desktop/presence-tools`。root で走ると `/root` を見てしまう | `preflight-new-hub.sh` main |
| preflight はどこにあるか | 新しいキット: `$kit/preflight-new-hub.sh`。payload の中の `scripts/preflight-new-hub.sh` にもある（コピー後は `$dest/scripts/`） | `pack-hub-usb.sh:357` |
| アイコンを置けなかったとき | `copy_install_setup_icon` の失敗を見ていないので「✅ コピーしました／アイコンをクリック」と出る（**失敗しても成功と表示してしまう**） | `copy-hub-from-usb.sh:73-77` |

**大事な事実:** 自動で始める処理は `copy-to-this-pi.sh`（＝キットに入っているこのスクリプトのコピー）に入る。
そのため、自動で始まるのは **この変更の後に作ったキットだけ**。そういうキットには必ず `$kit/preflight-new-hub.sh` も入っている。
「点検スクリプトが無い」状態になるのは、リポジトリから手で動かしたときか、キットが壊れているときくらいしかない。

## 2. (a) 決めた挙動の表

判定は上から順に行い、最初に当てはまった行で決まる。「コピー」の列は、コピーそのものを実行するかどうか。

| # | 状況 | コピー | ウィザード | 終了コード | 利用者に出す文言（案） |
|---|---|---|---|---|---|
| 1 | キットが無い／`origin.env` が無い（今と同じ） | しない | 始めない | 1 | 今と同じ |
| 2 | **コピー先に別アプリの中身がある**（新規） | **しない** | 始めない | 1 | `❌ <dest> に presence-logger ではない中身があります（先頭: a b c）`<br>`   コピーすると元のアプリのファイルと混ざるので、何もせずに止めました。`<br>`   別の場所へ退避してから、もう一度「このUSBからコピー」を開いてください。` |
| 3 | コピーが途中で失敗（rsync/tar） | 途中まで | 始めない | 1 | 今と同じ（stderr）。自動では始めない |
| 4 | アイコンを置けなかった（テンプレートが無い） | した | 始めない | **1** | `⚠ デスクトップにアイコンを置けませんでした: <理由>`（「クリックしてください」は出さない） |
| 5 | コピー成功 → 共通で出す行 | した | — | — | `✅ <dest> へコピーしました`<br>`   （この時点では AP もコンテナも起動していません）` |
| 6 | `COPY_NO_WIZARD=1` | した | 始めない | 0 | `   デスクトップの「ハブ初期設定」をクリックして設定を始めてください` |
| 7 | 画面で操作していない（stdin か stdout が tty でない：SSH のパイプ・テスト・リダイレクト） | した | 始めない | 0 | 6 と同じ |
| 8 | `<dest>/.kit/setup-complete` がある（もう一度コピーした） | した | 始めない | 0 | `   このハブは設定済みです（<dest>/.kit/setup-complete）。自動では始めません。`<br>`   AP パスワードだけやり直すときは、デスクトップの「ハブ初期設定」から p を選んでください。` |
| 9 | 利用者が root しか決まらない（`SUDO_USER` が無い root、または user=root） | した | 始めない | 0 | `   root で動いているため自動では始めません。pi でログインしてデスクトップの「ハブ初期設定」をクリックしてください。` |
| 10 | `<dest>/scripts/setup-hub-wizard.sh` が無い | した | 始めない | 0 | 6 と同じ |
| 11 | 点検スクリプトが見つからない | した | **始める** | 0 | `   事前点検（preflight-new-hub.sh）がキットに無いため、点検せずに初期設定を始めます。` → 14 へ |
| 12 | 点検の結果が **BLOCK**（戻り値 2） | した | **始めない** | 0 | （点検結果を全部表示した後に）<br>`🛑 事前点検で BLOCK が出たため、初期設定は始めません。`<br>`   上の [BLOCK] の行を解消してから、デスクトップの「ハブ初期設定」をクリックしてください。`<br>`   （コピーしたファイルはそのまま残っています。AP もコンテナも起動していません）` |
| 12b | 点検が途中で落ちた（戻り値が 0/2 以外） | した | 始めない | 0 | `🛑 事前点検を最後まで実行できませんでした（戻り値 N）。初期設定は始めません。`<br>`   手で bash <preflight> を実行して確認してから、デスクトップの「ハブ初期設定」をクリックしてください。` |
| 13 | 点検の結果に **WARN** がある（戻り値 0 で `[WARN]` が1件以上） | した | **Enter を待つ** | 0 | `⚠ 上の WARN を読んで、了承できるなら Enter で初期設定を始めます（やめるときは Ctrl+C。後でアイコンからも始められます）: `<br>入力が読めない（EOF）ときは始めない（9 の文言に準じた案内を出す） |
| 14 | 問題なし（OK/INFO だけ）または 11・13 を通った | した | **始める（画面の手前で）** | 0 | 始める前に: `▶ このままハブ初期設定を始めます（ウィンドウを閉じれば中断できます。後でアイコンからやり直せます）` |
| 15 | ウィザードが 0 で終わった | — | — | 0 | 何も追加しない（ウィザードが自分で完了を知らせる） |
| 16 | ウィザードが 0 以外で終わった | — | — | **0** | `⚠ ハブ初期設定は完了していません（戻り値 N）。デスクトップの「ハブ初期設定」からやり直せます。` |

補足の決定:

- **tty の判定は `[ -t 0 ] && [ -t 1 ]`**（入力も出力も端末のとき）。lxterminal から開いたときは両方とも tty になる。
  `bash copy-to-this-pi.sh | tee log` のように出力をパイプに流したときは始めない（ウィザードの問いかけが見えなくなるため）。
- **点検は必ずコピーの後、利用者本人として、`HOME=<利用者のホーム>` で行う。** 点検の repo 判定はコピーの後なので必ず OK になるが、
  それは 2 の `copy_dest_verdict` をコピーの前に済ませているので問題ない。コピーの前に点検しない理由は、
  点検には数秒かかり（dpkg-query / apt-cache）、自動で始めない経路（6〜10）では時間の無駄になるから。
- **点検は自動で始める経路（6〜10 を通り抜けたとき）でだけ行う。** 非対話・無効化・設定済みのときは点検しない
  （今と同じ速さ・今と同じ出力のまま）。
- **点検スクリプトを探す順番:** `$kit/preflight-new-hub.sh` → `$dest/scripts/preflight-new-hub.sh`。
  どちらも読み取り専用。`$dest/scripts/` 版の source 判定（`../desktop/presence-tools` と `bootstrap-hub.sh`）は
  コピーした後の `$dest` で成り立ち、`$dest/.kit/site.env.template` も読める。
- **終了コードは「コピーとアイコン」だけで決める**（仮案 5）。ウィザードや点検の結果は文言で知らせる。
  例外として 4（アイコンを置けなかった）は 1 にする。これまでは失敗しても成功と表示していた。
- **アイコンは今まで通り、いつも置く**（仮案 4）。やり直しや、自動で始まらなかったときの入口になる。
- **Enter が2回になる件:** ウィザードの最後の「Enterで閉じる」と、`.desktop` 側の「Enterで閉じる」が続けて出る。
  古いキットとの互換を優先して、`.desktop` の Exec は変えない。README と手順書に「最後に Enter を2回押して閉じる」とは書かず、
  ウィザードが終わった後にコピー側で `（もう一度 Enter を押すとこのウィンドウが閉じます）` と1行出して、何を求めているかを分かるようにする。
- **Ctrl+C:** 13 の待ちで押すと、コピーのスクリプトごと終わる（コピーはもう終わっている）。
  lxterminal の `bash -c` も SIGINT を受けてウィンドウが閉じるが、害はない。`trap` は入れない（増やすと、ウィザードの中で押した Ctrl+C の扱いが複雑になる）。
- **`70-desktop.sh` から source される:** 新しく足す処理はすべて関数にし、`main` からだけ呼ぶ。
  source したときに何も起きないこと（副作用が無いこと）をテストで確かめる（T12）。

## 3. (b) `scripts/copy-hub-from-usb.sh` の変更（差分の形の擬似コード）

### シーム（テストで本物と差し替える口）

| 環境変数 | 意味 | 既定 |
|---|---|---|
| `COPY_NO_WIZARD` | `1` なら自動では始めない（利用者向けの無効化スイッチ。README に書く） | 未設定 |
| `COPY_FORCE_TTY` | `1` なら tty とみなす。`0` なら tty でないとみなす。未設定なら `[ -t 0 ] && [ -t 1 ]` で判定する（テスト専用） | 未設定 |
| `COPY_WIZARD_CMD` | 設定されていれば、`bash <dest>/scripts/setup-hub-wizard.sh` の代わりにこれを実行する（テスト専用。`bash -c` には渡さず、1つのパスとして実行する） | 未設定 |
| `COPY_PREFLIGHT_CMD` | 設定されていれば、点検スクリプトを探す代わりにこれを使う。値が `none` なら「見つからない」扱いにする（テスト専用） | 未設定 |

`COPY_DEST` と `COPY_DESKTOP` は今もある（変えない）。

```diff
@@ copy_install_setup_icon の後に追加 @@
+# $1: コピー先。無い・空・presence-logger なら 0。別物なら理由を出して 1。
+# 印は preflight_repo_verdict と同じ3点（docker-compose.yml / services/bridge / scripts/bootstrap-hub.sh）。
+copy_dest_verdict() {
+    local dir="$1" listing
+    [ -e "$dir" ] || [ -L "$dir" ] || return 0
+    if [ -d "$dir" ] && [ -z "$(ls -A "$dir" 2>/dev/null)" ]; then return 0; fi
+    if [ -e "$dir/docker-compose.yml" ] && [ -e "$dir/services/bridge" ] \
+        && [ -e "$dir/scripts/bootstrap-hub.sh" ]; then return 0; fi
+    listing="$(ls -A "$dir" 2>/dev/null | head -5 | tr '\n' ' ')"
+    echo "❌ $dir に presence-logger ではない中身があります（先頭: $listing）" >&2
+    echo "   コピーすると元のアプリのファイルと混ざるので、何もせずに止めました。" >&2
+    echo "   別の場所へ退避してから、もう一度「このUSBからコピー」を開いてください。" >&2
+    return 1
+}

@@ copy_hub_from_usb @@
     if [ ! -f "$kit/.kit/origin.env" ]; then ... return 1; fi
+    copy_dest_verdict "$dest" || return 1
     mkdir -p "$dest" "$desk"
     ...
     copy_fix_permissions "$dest"
-    copy_install_setup_icon "$dest" "$desk"
-    echo "✅ $dest へコピーしました"
-    echo "   デスクトップの「ハブ初期設定」をクリックしてください"
-    echo "   （この時点では AP もコンテナも起動していません）"
+    echo "✅ $dest へコピーしました"
+    echo "   （この時点では AP もコンテナも起動していません）"
+    if ! copy_install_setup_icon "$dest" "$desk"; then
+        echo "⚠ デスクトップにアイコンを置けませんでした" >&2
+        return 1
+    fi
 }
```

注: 「クリックしてください」の行は `copy_hub_from_usb` から消し、後で出す（自動で始めないと決まったときに、理由と一緒に出す）。
`copy_fix_permissions` の chown は今と同じ（`main` はユーザーを渡していない。これは今のままにする。範囲の外）。

```diff
+copy_is_interactive() {
+    case "${COPY_FORCE_TTY:-}" in
+        1) return 0 ;;
+        0) return 1 ;;
+    esac
+    [ -t 0 ] && [ -t 1 ]
+}
+
+# 利用者本人として argv を実行する前置きを、1行に1語で出す（純粋関数。テストで直接確かめる）。
+# $1: 今の uid  $2: 利用者  $3: 利用者のホーム
+copy_as_user_prefix() {
+    local uid="$1" user="$2" home="$3"
+    if [ "$uid" -eq 0 ] && [ "$user" != "root" ]; then
+        printf '%s\n' runuser -u "$user" -- env \
+            -u SUDO_USER -u SUDO_UID -u SUDO_GID -u SUDO_COMMAND \
+            "HOME=$home" "USER=$user" "LOGNAME=$user"
+    else
+        printf '%s\n' env "HOME=$home"
+    fi
+}
+
+copy_find_preflight() {
+    local kit="$1" dest="$2"
+    if [ -n "${COPY_PREFLIGHT_CMD:-}" ]; then
+        [ "$COPY_PREFLIGHT_CMD" = none ] && return 1
+        printf '%s\n' "$COPY_PREFLIGHT_CMD"; return 0
+    fi
+    local f
+    for f in "$kit/preflight-new-hub.sh" "$dest/scripts/preflight-new-hub.sh"; do
+        [ -f "$f" ] && { printf '%s\n' "$f"; return 0; }
+    done
+    return 1
+}
+
+# 戻り値: 0=始めてよい  1=始めない（理由は表示済み）
+copy_preflight_gate() {
+    local kit="$1" dest="$2" user="$3" home="$4" pf out rc
+    local -a pre
+    if ! pf="$(copy_find_preflight "$kit" "$dest")"; then
+        echo "   事前点検（preflight-new-hub.sh）がキットに無いため、点検せずに初期設定を始めます。"
+        return 0
+    fi
+    mapfile -t pre < <(copy_as_user_prefix "$(id -u)" "$user" "$home")
+    echo "── 事前点検（読み取りのみ）──"
+    out="$("${pre[@]}" bash "$pf" 2>&1)"; rc=$?
+    printf '%s\n' "$out"
+    case "$rc" in
+        0) ;;
+        2) echo "🛑 事前点検で BLOCK が出たため、初期設定は始めません。"
+           echo "   上の [BLOCK] の行を解消してから、デスクトップの「ハブ初期設定」をクリックしてください。"
+           echo "   （コピーしたファイルはそのまま残っています。AP もコンテナも起動していません）"
+           return 1 ;;
+        *) echo "🛑 事前点検を最後まで実行できませんでした（戻り値 $rc）。初期設定は始めません。"
+           echo "   手で bash $pf を実行して確認してから、デスクトップの「ハブ初期設定」をクリックしてください。"
+           return 1 ;;
+    esac
+    if grep -q '^\[WARN\]' <<< "$out"; then
+        read -r -p "⚠ 上の WARN を読んで、了承できるなら Enter で初期設定を始めます（やめるときは Ctrl+C。後でアイコンからも始められます）: " _ \
+            || { echo; echo "   入力を読めなかったので始めません。デスクトップの「ハブ初期設定」をクリックしてください。"; return 1; }
+    fi
+    return 0
+}
+
+# コピーが成功した後に呼ぶ。戻り値は使わない（いつも 0）。
+copy_maybe_launch_wizard() {
+    local kit="$1" dest="$2" user="$3" home="$4" wrc
+    local click="   デスクトップの「ハブ初期設定」をクリックして設定を始めてください"
+    local -a pre cmd
+    if [ "${COPY_NO_WIZARD:-}" = 1 ] || ! copy_is_interactive; then echo "$click"; return 0; fi
+    if [ -f "$dest/.kit/setup-complete" ]; then
+        echo "   このハブは設定済みです（$dest/.kit/setup-complete）。自動では始めません。"
+        echo "   AP パスワードだけやり直すときは、デスクトップの「ハブ初期設定」から p を選んでください。"
+        return 0
+    fi
+    if [ -z "$user" ] || [ "$user" = root ]; then
+        echo "   root で動いているため自動では始めません。pi でログインしてデスクトップの「ハブ初期設定」をクリックしてください。"
+        return 0
+    fi
+    if [ -n "${COPY_WIZARD_CMD:-}" ]; then cmd=("$COPY_WIZARD_CMD")
+    elif [ -f "$dest/scripts/setup-hub-wizard.sh" ]; then cmd=(bash "$dest/scripts/setup-hub-wizard.sh")
+    else echo "$click"; return 0; fi
+    copy_preflight_gate "$kit" "$dest" "$user" "$home" || return 0
+    echo
+    echo "▶ このままハブ初期設定を始めます（ウィンドウを閉じれば中断できます。後でアイコンからやり直せます）"
+    mapfile -t pre < <(copy_as_user_prefix "$(id -u)" "$user" "$home")
+    ( cd "$dest" && "${pre[@]}" "${cmd[@]}" ); wrc=$?
+    if [ "$wrc" -ne 0 ]; then
+        echo "⚠ ハブ初期設定は完了していません（戻り値 $wrc）。デスクトップの「ハブ初期設定」からやり直せます。"
+    fi
+    echo "（もう一度 Enter を押すとこのウィンドウが閉じます）"
+    return 0
+}

@@ main @@
-    copy_hub_from_usb "$kit" "$dest" "$desk"
+    copy_hub_from_usb "$kit" "$dest" "$desk" || return $?
+    copy_maybe_launch_wizard "$kit" "$dest" "$user" "$home"
+    return 0
 }
```

実装のときの注意:

- ウィザードは **引数なし**で起動する（アイコンの Exec と同じ）。`WIZARD_WORKDIR` は渡さない（ウィザードが自分の場所から `$dest` を求める）。
- テストでは「何が起動されたか」をファイルに記録する偽スクリプトを `COPY_WIZARD_CMD` に渡す。偽スクリプトは
  `printf '%s\n' "$@" "PWD=$PWD" "USER=$USER" "HOME=$HOME" "SUDO_USER=${SUDO_USER-unset}" > "$FAKE_LOG"` のように書く。
- テストは root で動かないので、root のときの経路は `copy_as_user_prefix 0 pi /home/pi` の出力だけで確かめる（本当に runuser を実行はしない）。
- `main` のテストでキットの場所を渡すときは、位置引数（`main "$kit"`）と `COPY_DEST` / `COPY_DESKTOP` を使う。`SUDO_USER` は消しておく
  （`env` から取り除く）。`user` にはテストを動かしている利用者が入る。
- `mapfile -t pre < <(...)` の中で `id -u` を呼ぶ。偽の `id` を PATH に置くと `copy_fix_permissions` にも効いてしまうので、使わない。
- `grep -q '^\[WARN\]'` は preflight の出力の形（`[LEVEL] area: msg`）に頼っている。T8 と T9 で形を固定する。

## 4. (c) テストケース一覧（`scripts/tests/test_copy_hub_from_usb.py` に追加。今ある3件は変えずに通ること）

共通のフィクスチャ `_kit(tmp_path, *, preflight=None)`: 今あるテスト2と同じ最小キット（origin.env / payload/hello.txt /
launchers/ハブ初期設定.desktop）を作る。`preflight` を渡すと、`$kit/preflight-new-hub.sh` に指定の本文を書く。
payload に `scripts/setup-hub-wizard.sh`（中身は `exit 99`。**本物は使わない**）を置く。
`fake_wizard(tmp_path)`: 引数・PWD・USER・HOME・SUDO_USER をログに書いて `exit ${FAKE_WIZ_RC:-0}` する偽スクリプトを作り、パスを返す。
実行は `run_bash('source scripts/copy-hub-from-usb.sh; main "<kit>"', env=…, stdin=…)` の形。
env には `COPY_DEST`・`COPY_DESKTOP` を入れ、`SUDO_USER` を取り除く。

| ID | テスト名 | 条件 | 確かめること |
|---|---|---|---|
| T1 | `test_no_tty_does_not_launch_wizard` | `COPY_FORCE_TTY=0`, `COPY_WIZARD_CMD=fake` | rc=0 ／ 偽ウィザードのログが空 ／ stdout に「ハブ初期設定」をクリック ／ 点検が呼ばれていない（偽の preflight のログも空） |
| T2 | `test_no_wizard_env_disables_launch` | `COPY_FORCE_TTY=1`, `COPY_NO_WIZARD=1` | rc=0 ／ 起動していない ／ 「クリック」の案内がある |
| T3 | `test_setup_complete_skips_launch_and_is_preserved` | dest に `.kit/setup-complete` を先に置く（dest は presence-logger の3つの印を持たせる）, `COPY_FORCE_TTY=1` | rc=0 ／ 起動していない ／ 「設定済み」と「p を選んで」が出る ／ コピーの後も setup-complete の中身が変わっていない |
| T4 | `test_preflight_block_does_not_launch_and_explains` | preflight の本文 `echo '[BLOCK] docker: x'; exit 2`, TTY=1 | rc=0 ／ 起動していない ／ stdout に `[BLOCK] docker: x` と「BLOCK が出たため」「解消してから」がある |
| T5 | `test_preflight_crash_does_not_launch` | preflight の本文 `exit 7`, TTY=1 | rc=0 ／ 起動していない ／ 「戻り値 7」が出る |
| T6 | `test_preflight_warn_waits_for_enter_then_launches` | preflight の本文 `echo '[WARN] hostname: y'; exit 0`, TTY=1, stdin=`"\n"` | rc=0 ／ 「Enter で初期設定を始めます」の問いかけが出る ／ 起動している |
| T7 | `test_preflight_warn_eof_does_not_launch` | T6 と同じで stdin=`""`（EOF） | rc=0 ／ 起動していない ／ 「入力を読めなかった」が出る |
| T8 | `test_preflight_ok_launches_without_prompt` | preflight の本文 `echo '[OK] repo: z'; echo '        [WARN] 字下げした行は数えない'; exit 0`, TTY=1, stdin=`""` | 起動している（先頭が `[WARN]` でない行は数えない。入力を待たない） |
| T9 | `test_missing_preflight_launches_with_notice` | `COPY_PREFLIGHT_CMD=none`, TTY=1 | 起動している ／ 「点検せずに初期設定を始めます」が出る |
| T10 | `test_launch_command_args_cwd_and_user` | preflight OK, TTY=1, env に `SUDO_USER` は無い | 偽ウィザードのログ: 引数が無い ／ `PWD=<dest>` ／ `USER=<今の利用者>` ／ `HOME=<利用者のホーム>` |
| T11 | `test_default_command_is_bash_repo_wizard` | `COPY_WIZARD_CMD` 無し。payload の `scripts/setup-hub-wizard.sh` を「ログを書いて exit 0」にする, TTY=1, preflight OK | ログが書かれた（＝`bash <dest>/scripts/setup-hub-wizard.sh` が動いた）／ 本物のウィザードは使っていない |
| T12 | `test_sourcing_has_no_side_effects` | `source scripts/copy-hub-from-usb.sh; echo ok`（`COPY_FORCE_TTY=1`, `COPY_WIZARD_CMD=fake`） | stdout が `ok` だけ ／ 起動していない（`70-desktop.sh` が source しても何も起きない） |
| T13 | `test_as_user_prefix_root_drops_sudo_env` | `copy_as_user_prefix 0 pi /home/pi` | 出力の語が `runuser -u pi -- env -u SUDO_USER -u SUDO_UID -u SUDO_GID -u SUDO_COMMAND HOME=/home/pi USER=pi LOGNAME=pi` の順になっている |
| T14 | `test_as_user_prefix_non_root_is_plain_env` | `copy_as_user_prefix 1000 pi /home/pi` | `env HOME=/home/pi` だけで、`runuser` も `sudo` も含まない |
| T15 | `test_root_user_does_not_launch` | `copy_maybe_launch_wizard <kit> <dest> root /root` を直接呼ぶ, TTY=1 | 起動していない ／ 「root で動いているため」が出る |
| T16 | `test_wizard_failure_does_not_change_exit_code` | preflight OK, TTY=1, `FAKE_WIZ_RC=5` | main の rc=0 ／ 「完了していません（戻り値 5）」が出る |
| T17 | `test_copy_failure_does_not_launch` | kit に `payload/presence-logger` を作らない, TTY=1 | rc=1 ／ 起動していない ／ 点検も呼ばれていない |
| T18 | `test_foreign_dest_refuses_copy` | dest に `notes.txt` だけを置く, TTY=1 | rc=1 ／ `hello.txt` がコピーされていない ／ `notes.txt` はそのまま ／ 「presence-logger ではない中身」が出る ／ 起動していない |
| T19 | `test_existing_presence_logger_dest_is_updated` | dest に3つの印を置く | rc=0 ／ `hello.txt` がコピーされた |
| T20 | `test_empty_dest_is_ok` | dest を空のディレクトリにする | rc=0 ／ コピーされた |
| T21 | `test_missing_icon_template_fails_loudly` | launchers の .desktop を置かない | rc=1 ／ stdout に「クリックしてください」が無い ／ stderr に「アイコンを置けませんでした」がある ／ 起動していない |
| T22 | `test_preflight_runs_with_user_home` | preflight の本文 `echo "[OK] home: $HOME"; exit 0`, TTY=1 | 出力に利用者のホームがある（`/root` などではない） |
| T23 | `test_preflight_prefers_kit_then_dest_scripts` | キットに preflight を置かず、payload の `scripts/preflight-new-hub.sh`（`echo from-dest; exit 0`）だけを置く | `from-dest` が出る ／ 起動している |

pack 側（`scripts/tests/test_pack_hub_usb.py`）:

| ID | 確かめること |
|---|---|
| P1 | README.txt の手順3が新しい文面になっている（「自動で始まります」と「始まらないときはデスクトップの「ハブ初期設定」をクリック」の両方が含まれる） |
| P2 | README.txt に古い文面「現れるのでクリックする」が**無い** |
| P3 | README.txt に `COPY_NO_WIZARD=1` の説明がある |

最後に `pytest scripts/tests/test_copy_hub_from_usb.py scripts/tests/test_pack_hub_usb.py scripts/tests/test_bootstrap_desktop.py` と
`ruff check .` を通す（bootstrap_desktop は source 経由で影響を受けるので確認だけする。直さない）。

## 5. (d) 文言・資料の更新箇所

| ファイル | 場所 | 新しい文面（案） |
|---|---|---|
| `scripts/copy-hub-from-usb.sh` | 先頭のコメント（2〜5行目） | 2行目の後に「コピーの後、画面で操作しているときは事前点検をしてから、ハブ初期設定をそのまま始める（`COPY_NO_WIZARD=1` で止められる）。」を足す |
| `scripts/copy-hub-from-usb.sh` | 最後のメッセージ | §2 の表の 5・6・8・9・12・12b・13・14・16 のとおり |
| `scripts/pack-hub-usb.sh` | `pack_write_readme` の手順2〜3（285〜288行） | 手順3を次のように変える:<br>`3. コピーが終わると、同じ画面で点検が走り、続けて「ハブ初期設定」が自動で始まる`<br>`   ・点検に BLOCK が出たときは始まりません。表示に従って直してから、デスクトップの「ハブ初期設定」をクリックする`<br>`   ・WARN が出たときは内容を読み、了承できるなら Enter`<br>`   ・自動で始まらないとき（ターミナルから流したとき・古い USB）は、デスクトップの「ハブ初期設定」をクリックする`<br>`   ・自動で始めたくないときは: COPY_NO_WIZARD=1 bash …/copy-to-this-pi.sh` |
| `scripts/pack-hub-usb.sh` | `pack_write_readme` の手順1（279〜283行） | 点検の案内はそのまま残す。コピー先に別のアプリがあるときはコピー自体が止まると1行足す:<br>`   （~/projects/presence-logger に別のアプリがある場合は、コピーの段階で止まります）` |
| `scripts/pack-hub-usb.sh` | `pack_write_copy_desktop` の `Comment=` | `この Raspberry Pi にハブ一式をコピーし、続けて初期設定を始めます` （**Exec は変えない**） |
| `scripts/tests/test_pack_hub_usb.py` | README の確認（439行付近） | P1〜P3 を足す |
| `docs/NEW-HUB-SETUP.md` | 「新機（素の Pi OS Desktop）で」の手順 1〜2（52〜54行） | `1. USB を挿し、`このUSBからコピー` をダブルクリックする`<br>`   （開かないときは `bash /media/pi/*/presence-hub-kit/copy-to-this-pi.sh`）`<br>`2. コピーが終わると事前点検が走り、問題がなければそのまま **ハブ初期設定** が始まる。`<br>`   - BLOCK が出たら始まらない。直してからデスクトップの **ハブ初期設定** をクリックする。`<br>`   - WARN が出たら読んで Enter。`<br>`   - 始まらないとき（古い USB・SSH から流したとき・`COPY_NO_WIZARD=1`）もデスクトップの **ハブ初期設定** をクリックする。`<br>`   - 設定済みのハブにもう一度コピーしたときは自動で始まらない。` |
| `docs/NEW-HUB-SETUP.md` | 同じ節の「コピーした直後は AP もコンテナも起動しない」の段落（65〜66行） | 最後に次を足す: `コピー先の ~/projects/presence-logger に presence-logger ではない中身があると、コピーせずに止まる。` |
| `docs/how-it-works/wizards/index.html` | 210行 `<li><strong>【新機】ハブ初期設定</strong>` | `— コピーが終わると同じ画面で事前点検が走り、続けて自動で始まります（BLOCK が出たら止まるので、直してからデスクトップのアイコンをクリック）。この時点では電波もコンテナも起動しません（親機とぶつからないため）。` |
| `docs/how-it-works/wizards/index.html` | 111行の表 `ハブ用USBを作る → 新機で ハブ初期設定` | `ハブ用USBを作る → 新機でコピー（初期設定が続けて始まる）` |

## 6. 古いキットと新しいキットで手順が食い違わないように

- 古いキット（この変更より前に作った USB）の `copy-to-this-pi.sh` は古いスクリプトなので、**自動では始まらない**。
  古いキットの README.txt も古い手順（アイコンをクリック）を書いているので、**その USB の中では手順は食い違わない**。
- 食い違うのは「新しい手順書（NEW-HUB-SETUP.md / wizards の HTML）＋古い USB」の組み合わせ。そのため新しい手順書には必ず
  「**始まらないときはデスクトップの『ハブ初期設定』をクリック**」を書く（§5 の文面に入れてある）。どちらの USB でも、この一文に従えば進める。
- 古いキットは作り直すことを勧める（`pack-hub-usb.sh` を実行し直すだけでよい）。作り直すと、`preflight-new-hub.sh` と、コピー先の確認（§0-1）の両方が USB に入る。
  NEW-HUB-SETUP.md の「ハブ用USBを作る」節の最後に「2026-09-29 より前に作った USB は、自動で始まらず、コピー先の確認もしない。作り直すことを勧める」と1行足す。
- 新しいキットで「自動で始まらない」のに気づけないことが無いように、始めない経路では**必ず理由を1行出す**（§2 の表の 6〜12b）。

## 7. (e) 実装タスクの所有範囲

実装で変えてよいファイル（これ以外は変えない）:

1. `scripts/copy-hub-from-usb.sh`
2. `scripts/tests/test_copy_hub_from_usb.py`
3. `scripts/pack-hub-usb.sh`（`pack_write_readme` と `pack_write_copy_desktop` の `Comment=` 行だけ）
4. `scripts/tests/test_pack_hub_usb.py`（README の確認だけ）
5. `docs/NEW-HUB-SETUP.md`（§5 に書いた箇所だけ）
6. `docs/how-it-works/wizards/index.html`（§5 に書いた2箇所だけ）

変えてはいけないファイル: `scripts/setup-hub-wizard.sh`, `scripts/preflight-new-hub.sh`, `scripts/lib/kit-copy.sh`,
`scripts/bootstrap/**`（`70-desktop.sh` は source しているだけなので、T12 で影響が無いことを確かめる）,
`scripts/prepare-child-sd.sh`, `fleet_ui/**`, `scripts/lib/site-env.sh`。

進め方（TDD）: T12・T13・T14（純粋関数）→ T18〜T21（コピーの確認とアイコン）→ T1〜T11・T15〜T17・T22・T23（起動するかどうか）
→ P1〜P3 → 資料の順に進める。それぞれ、先に失敗するテストを書く。

## 8. このまま残る問題（範囲の外）

- `main` は `copy_fix_permissions "$dest"` に利用者を渡していないので、root で動いたときに `chown` されない（この変更より前からある）。
  root で動いてもウィザードは自動では始めない（§2 の 9）ので、この変更で悪くはならない。直すなら別のタスクにする。
- `.desktop` の Exec が `find /media /run/media "$HOME" -name copy-to-this-pi.sh | head -1` で最初に見つかったものを使う。
  USB を2本挿していると古い方のキットが選ばれることがある（この変更より前からある）。
