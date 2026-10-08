#!/usr/bin/env bash
# Usage: bash download-openneuro.sh [-o OUTPUT] [-j JOBS] ds002721 [ds003505v1.1.2 ...]
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
image=${OPENNEURO_CONTAINER:-/home/container/download-tools.sif}
output=$PWD
jobs=4
while (($#)); do
    case "$1" in
        -o|--output) output=${2:?Missing output directory}; shift 2 ;;
        -j|--jobs) jobs=${2:?Missing job count}; shift 2 ;;
        -h|--help)
            echo "Usage: bash $0 [-o OUTPUT] [-j JOBS] ID [ID ...]"
            echo "IDs: ds002721 (latest snapshot), ds002721v1.0.3 (specific version)."
            echo "Set OPENNEURO_CONTAINER to override /home/container/download-tools.sif."
            exit 0 ;;
        --) shift; break ;;
        -*) echo "Unknown option: $1" >&2; exit 2 ;;
        *) break ;;
    esac
done
(($#)) || { echo 'At least one dataset ID is required.' >&2; exit 2; }
[[ $jobs =~ ^[1-9][0-9]*$ ]] || { echo 'Jobs must be a positive integer.' >&2; exit 2; }
for id in "$@"; do
    [[ $id =~ ^ds[0-9]{6}(v[0-9]+\.[0-9]+\.[0-9]+)?$ ]] || {
        echo "Invalid dataset ID: $id" >&2; exit 2;
    }
done
[[ -f $image ]] || { echo "Container not found: $image" >&2; exit 2; }
runtime=$(command -v apptainer || command -v singularity) || {
    echo 'Apptainer or Singularity is required.' >&2; exit 2;
}
mkdir -p -- "$output"
output=$(cd -- "$output" && pwd)
# Bind specifications cannot safely represent these path characters.
[[ $output != *:* && $output != *,* && $script_dir != *:* && $script_dir != *,* ]] || {
    echo 'Output/script paths must not contain commas or colons.' >&2; exit 2;
}
exec "$runtime" exec \
    --bind "$output:/downloads" --bind "$script_dir:/download-script:ro" \
    --pwd /downloads "$image" \
    python3 -B /download-script/download_openneuro.py --jobs "$jobs" "$@"
