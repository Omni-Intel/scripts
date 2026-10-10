#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  move-remote.sh <remote:source> <remote:destination> [--execute]

The default is dry-run. --execute actually moves files.
Moves allow an existing destination but reject overlapping paths.
EOF
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

# Normalize separators for lexical overlap checks. Reject dot segments rather
# than relying on backend-specific normalization.
remote_path() {
    local value="$1" remote path
    [[ "$value" == *:* ]] || fail "expected remote:path: $value"
    remote="${value%%:*}"
    path="${value#*:}"
    [[ -n "$remote" && "$remote" != */* ]] || fail "invalid remote: $value"
    while [[ "$path" == /* ]]; do path="${path#/}"; done
    while [[ "$path" == */ ]]; do path="${path%/}"; done
    while [[ "$path" == *//* ]]; do path="${path//\/\//\/}"; done
    case "/$path/" in
        */./*|*/../*) fail "dot path segments are not supported: $value" ;;
    esac
    printf '%s:%s' "$remote" "$path"
}

if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
    usage
    exit 0
fi
[[ $# -ge 2 && $# -le 3 ]] || { usage; exit 1; }
SRC_ROOT="$(remote_path "$1")"
DST_ROOT="$(remote_path "$2")"
OPTION="${3:-}"

DRY_RUN=(--dry-run)
case "$OPTION" in
    '') echo '=== DRY-RUN MODE ===' ;;
    --execute) DRY_RUN=(); echo '=== EXECUTE MODE ===' ;;
    *) fail "unknown option: $OPTION" ;;
esac

[[ "$SRC_ROOT" != "$DST_ROOT" ]] || fail 'source and destination are identical'
src_remote="${SRC_ROOT%%:*}"
dst_remote="${DST_ROOT%%:*}"
src_path="${SRC_ROOT#*:}"
dst_path="${DST_ROOT#*:}"
if [[ "$src_remote" == "$dst_remote" ]]; then
    if [[ -z "$src_path" || -z "$dst_path" ||
          "$dst_path" == "$src_path/"* || "$src_path" == "$dst_path/"* ]]; then
        fail 'source and destination overlap'
    fi
fi

printf '\nSource      : %s\nDestination : %s\n\n' "$SRC_ROOT" "$DST_ROOT"

# Capture a complete listing before moving. Listing errors must abort rather
# than being interpreted as an empty source or a missing destination.
if ! LISTING="$(rclone lsf "$SRC_ROOT" --max-depth 1)"; then
    fail "cannot list source: $SRC_ROOT"
fi
if [[ -z "$LISTING" ]]; then
    echo 'Source is empty. Nothing to move.'
    exit 0
fi

echo 'Destination contents, if present, will be merged using rclone rules.'
rclone move "$SRC_ROOT" "$DST_ROOT" --delete-empty-src-dirs "${DRY_RUN[@]}" -P

if [[ "$OPTION" == --execute ]]; then
    printf '\nMove completed.\nDestination: %s\n' "$DST_ROOT"
else
    printf '\nDry-run completed. No files were changed.\nTo execute:\n'
    printf '  %q %q %q --execute\n' "$0" "$SRC_ROOT" "$DST_ROOT"
fi
