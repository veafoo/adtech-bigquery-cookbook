#!/usr/bin/env bash
# Load the synthetic tables into BigQuery with explicit schemas (no autodetect).
#   synth/load.sh <project>.<dataset>        e.g. synth/load.sh my-project.adtech_synth
# Creates the dataset in US if needed and replaces the seven tables. Then:
#   COOKBOOK_SYNTH_DATASET=<project>.<dataset> python3 run_cookbook.py --only 14,15
set -euo pipefail

DATASET="${1:?usage: synth/load.sh <project>.<dataset>}"
[[ "$DATASET" =~ ^[A-Za-z0-9_.:-]+\.[A-Za-z0-9_]+$ ]] || { echo "not a <project>.<dataset> name: $DATASET" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
REF="${DATASET/./:}"   # bq wants project:dataset

if ! ls "$HERE"/*.ndjson >/dev/null 2>&1; then
  python3 "$HERE/generate.py" --out "$HERE"
fi

bq mk -f --dataset --location=US "$REF"
for schema in "$HERE"/schemas/*.json; do
  table="$(basename "$schema" .json)"
  bq load --source_format=NEWLINE_DELIMITED_JSON --replace "$REF.$table" "$HERE/$table.ndjson" "$schema"
done
