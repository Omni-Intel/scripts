#!/usr/bin/env bash
# Usage: bash download-openneuro.sh [-o OUTPUT] [-j JOBS] [-p DATASET_JOBS] ds002721 [ds003505v1.1.2 ...]
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
image=${OPENNEURO_CONTAINER:-/home/container/download-tools.sif}
output=$PWD
jobs=4
dataset_jobs=1
while (($#)); do
    case "$1" in
        -o|--output) output=${2:?Missing output directory}; shift 2 ;;
        -j|--jobs) jobs=${2:?Missing job count}; shift 2 ;;
        -p|--dataset-jobs) dataset_jobs=${2:?Missing dataset job count}; shift 2 ;;
        -h|--help)
            echo "Usage: bash $0 [-o OUTPUT] [-j JOBS] [-p DATASET_JOBS] ID [ID ...]"
            echo "-p: concurrent datasets (default 1); -j: transfers per dataset (default 4)."
            echo "IDs: ds002721 (latest snapshot), ds002721v1.0.3 (specific version)."
            echo "Set OPENNEURO_CONTAINER to override /home/container/download-tools.sif."
            echo "Interrupted downloads resume automatically in the same output directory."
            exit 0 ;;
        --) shift; break ;;
        -*) echo "Unknown option: $1" >&2; exit 2 ;;
        *) break ;;
    esac
done
(($#)) || { echo 'At least one dataset ID is required.' >&2; exit 2; }
[[ $jobs =~ ^[1-9][0-9]*$ ]] || { echo 'Jobs must be a positive integer.' >&2; exit 2; }
[[ $dataset_jobs =~ ^[1-9][0-9]*$ ]] || { echo "Dataset jobs must be a positive integer." >&2; exit 2; }
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
worker="$script_dir/download_openneuro.py"
if [[ ! -e $worker && ! -L $worker ]]; then
    worker_url='https://raw.githubusercontent.com/Omni-NCC/scripts/main/openneuro/download_openneuro.py'
    echo "Python worker not found; downloading from $worker_url" >&2
    temporary_worker=$(mktemp "$script_dir/.download_openneuro.py.XXXXXX")
    trap 'rm -f -- "$temporary_worker"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    # Use curl and Python from the image; the host only needs Bash and the runtime.
    "$runtime" exec --bind "$script_dir:/download-bootstrap" "$image" \
        curl --fail --location --retry 3 --connect-timeout 20 --max-time 300 \
        --output "/download-bootstrap/$(basename -- "$temporary_worker")" "$worker_url" || {
        echo 'Failed to download Python worker; no worker was installed.' >&2
        exit 1
    }
    [[ -s $temporary_worker ]] || { echo 'Downloaded Python worker is empty.' >&2; exit 1; }
    "$runtime" exec --bind "$script_dir:/download-bootstrap:ro" "$image" \
        python3 -c 'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))' \
        "/download-bootstrap/$(basename -- "$temporary_worker")" || {
        echo 'Downloaded Python worker is not valid Python; no worker was installed.' >&2
        exit 1
    }
    chmod 644 "$temporary_worker"
    # Publish only the complete file and never overwrite a concurrently created one.
    ln -- "$temporary_worker" "$worker" || {
        echo 'Could not install Python worker; check permissions or concurrent runs.' >&2
        exit 1
    }
    rm -f -- "$temporary_worker"
    trap - EXIT INT TERM
fi
[[ -f $worker && -r $worker ]] || { echo "Python worker is not a readable file: $worker" >&2; exit 1; }
exec "$runtime" exec \
    --bind "$output:/downloads" --bind "$script_dir:/download-script:ro" \
    --pwd /downloads "$image" \
    python3 -B /download-script/download_openneuro.py --jobs "$jobs" --dataset-jobs "$dataset_jobs" "$@"
