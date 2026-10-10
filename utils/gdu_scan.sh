#!/usr/bin/env bash
set -euo pipefail

export_dir="/dev/shm/gdu-exports/$(date +%Y%m%d)"

if (( $# == 0 )); then
    echo "Usage: $0 <path1> [path2 ...]" >&2
    exit 1
fi

current_partial=""
trap 'rm -f -- "$current_partial"' EXIT

date -Is

for scan_path in "$@"; do
    # Normalize to absolute physical path
    scan_path="$(realpath -e -- "$scan_path")"

    # Build output path while preserving directory hierarchy
    if [[ "$scan_path" == "/" ]]; then
        output_file="$export_dir/root.json.zst"
    else
        relative_path="${scan_path#/}"
        output_file="$export_dir/${relative_path}.json.zst"
    fi

    partial_file="$output_file.partial"
    current_partial="$partial_file"

    mkdir -p "$(dirname "$output_file")"

    echo "Scanning: $scan_path"

    sudo -n "$HOME/bin/gdu" "$scan_path" \
        --no-cross \
        --non-interactive \
        --no-progress \
        --no-delete \
        --output-file /dev/stdout |
        zstd -c > "$partial_file"

    mv -- "$partial_file" "$output_file"
    current_partial=""

    sha256sum "$output_file" > "$output_file.sha256"

    echo "Completed: $output_file"
done

date -Is
printf 'SCAN_DONE\n'
