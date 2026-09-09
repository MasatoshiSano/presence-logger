#!/usr/bin/env bash
# kit-copy.sh — rsync が無い環境でもディレクトリをコピーする。
#
# USB / FAT では実行ビットが落ちる。権限の立て直しは呼び出し側。
kit_copy_dir() {
    local src="$1" dst="$2"
    mkdir -p "$dst"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a "$src/" "$dst/" || return 1
    else
        tar -C "$src" -cf - . | tar -C "$dst" -xf - || return 1
    fi
}

# stdin: 1 行 1 exclude パターン（rsync / tar 共通）
kit_copy_tree_filtered() {
    local src="$1" dst="$2"
    mkdir -p "$dst"
    local tar_ex=() rsync_ex=() ex
    while IFS= read -r ex; do
        [ -n "$ex" ] || continue
        tar_ex+=(--exclude="$ex")
        rsync_ex+=(--exclude="$ex")
    done
    if command -v rsync >/dev/null 2>&1; then
        rsync -rltD "${rsync_ex[@]}" "$src/" "$dst/" || return 1
    else
        tar -C "$src" "${tar_ex[@]}" -cf - . | tar -C "$dst" -xf - || return 1
    fi
}
