#!/usr/bin/env bash
# Memory-size sweep: run one group through the Lambda at several memory sizes and
# tabulate open/commit seconds, billed duration and GB-seconds per invocation.
#
#   AWS_PROFILE=personal scripts/dev/profile_lambda.sh INDEX.parquet REGION GROUP SC_CREDS.json [SIZES...]
#   e.g. ... index.parquet AIS CESM2-WACCM/historical/SDBN1-8000m sc_creds.json 2048 4096 6144 10240
#
# Writes into a scratch repository (englacial/ismip7/icechunk-profile-<region>) so the
# production store is untouched, restores the function's memory size at the end, and
# prints a markdown table for docs/lambda.md. Lambda CPU scales with memory
# (1769 MB = 1 vCPU, 10240 MB = 6), and billing is GB-seconds, so a CPU-bound job
# costs about the same at every size; the sweep measures the real process
# efficiency and the single-threaded tail (concat, commit, verify).
set -euo pipefail
INDEX=${1:?index.parquet}; REGION=${2:?AIS|GrIS}; GROUP=${3:?group path}; CREDS=${4:?sc_creds.json}; shift 4
SIZES=${*:-2048 4096 6144 10240}
FUNCTION=${FUNCTION:-ismip7-virtualize}; AWSR=${AWS_REGION:-us-west-2}
TARGET="s3://us-west-2.opendata.source.coop/englacial/ismip7/icechunk-profile-$(echo "$REGION" | tr A-Z a-z)"
OUT=${OUT:-profile-$(date -u +%Y%m%dT%H%M%SZ)}; mkdir -p "$OUT"

orig=$(aws lambda get-function-configuration --region "$AWSR" --function-name "$FUNCTION" --query MemorySize --output text)
restore() { aws lambda update-function-configuration --region "$AWSR" --function-name "$FUNCTION" --memory-size "$orig" >/dev/null; }
trap restore EXIT

echo "| memory MB | vCPUs | files | open s | s/file | commit s | billed s | GB-s | status |" | tee "$OUT/table.md"
echo "|---:|---:|---:|---:|---:|---:|---:|---:|---|" | tee -a "$OUT/table.md"
for mem in $SIZES; do
  aws lambda update-function-configuration --region "$AWSR" --function-name "$FUNCTION" --memory-size "$mem" >/dev/null
  aws lambda wait function-updated --region "$AWSR" --function-name "$FUNCTION"
  start=$(date -u +%s)
  ismip7-fanout --index "$INDEX" --region "$REGION" --groups "$GROUP" --function "$FUNCTION" \
      --target "$TARGET" --write-creds "$CREDS" --overwrite --verify 0 \
      --max-lambda-seconds 100000 --report "$OUT/run-$mem.json" >"$OUT/run-$mem.log" 2>&1 || true
  # billed duration from the function's REPORT line for this window
  billed=$(aws logs filter-log-events --region "$AWSR" --log-group-name "/aws/lambda/$FUNCTION" \
      --start-time $((start * 1000)) --filter-pattern "REPORT" --query 'events[-1].message' --output text 2>/dev/null \
      | grep -o 'Billed Duration: [0-9]*' | grep -o '[0-9]*' || echo "")
  python3 - "$OUT/run-$mem.json" "$mem" "$billed" <<'EOF' | tee -a "$OUT/table.md"
import json, sys
r = json.load(open(sys.argv[1]))[0]; mem = int(sys.argv[2]); billed_ms = sys.argv[3]
t = r.get("timings") or {}; n = r.get("n_files") or 0
opn = t.get("open"); com = t.get("commit")
billed = int(billed_ms) / 1000 if billed_ms else None
gbs = billed * mem / 1024 if billed else None
f = lambda v, d=1: "" if v is None else f"{v:.{d}f}"
print(f"| {mem} | {r.get('cpu_count','')} | {n} | {f(opn)} | {f(opn / n, 3) if opn and n else ''} | {f(com)} | {f(billed)} | {f(gbs, 0)} | {r.get('status')} {r.get('error') or ''} |")
EOF
done
echo; echo "scratch repository: $TARGET (delete its prefix when done); function memory restored to $orig MB"
