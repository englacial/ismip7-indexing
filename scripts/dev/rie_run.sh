#!/usr/bin/env bash
# Build the Lambda image and run the handler once under the Lambda Runtime Interface
# Emulator the way Lambda runs it: non-root, /dev/shm read-only, 10 GB / 900 s env.
#
#   uv run python scripts/dev/rie_smoke.py /tmp/rie      # synthetic files, repo, event
#   scripts/dev/rie_run.sh /tmp/rie                       # build, invoke, check "verified"
#
# The AWS base image ships the emulator: its entrypoint starts it whenever
# AWS_LAMBDA_RUNTIME_API is unset, so nothing is downloaded and the binary
# always matches the image's architecture. PLATFORM=linux/arm64 builds and
# runs the arm64 image (natively on an arm64 host; through qemu elsewhere).
# CONTAINER_CLI=podman|docker, default docker.
set -euo pipefail
WORK=${1:?workdir prepared by rie_smoke.py}
PLATFORM=${PLATFORM:-linux/amd64}
CLI=${CONTAINER_CLI:-docker}
cd "$(dirname "$0")/../.."
uv export --extra lambda --no-emit-project --no-hashes --no-dev -o requirements-lambda.txt
if [ "$CLI" = podman ]; then
  podman build --platform "$PLATFORM" --format docker --build-arg GIT_SHA="$(git rev-parse HEAD)" \
      -f Dockerfile.lambda -t ismip7-virtualize:rie .
  LABEL=(--security-opt label=disable)  # bind mounts under SELinux (Fedora)
else
  docker buildx build --platform "$PLATFORM" --provenance=false --build-arg GIT_SHA="$(git rev-parse HEAD)" \
      -f Dockerfile.lambda -t ismip7-virtualize:rie --load .
  LABEL=()
fi
chmod -R a+rwX "$WORK/target"
$CLI rm -f ismip7-rie >/dev/null 2>&1 || true
$CLI run -d --name ismip7-rie --platform "$PLATFORM" --user 1000 -p 9000:8080 "${LABEL[@]}" \
    --mount type=tmpfs,dst=/dev/shm,tmpfs-mode=0555,readonly \
    -v "$WORK/src":/mnt/src:ro -v "$WORK/target":/mnt/target \
    -e AWS_LAMBDA_FUNCTION_MEMORY_SIZE=10240 -e AWS_LAMBDA_FUNCTION_TIMEOUT=900 -e ISMIP7_WORKERS=2 \
    ismip7-virtualize:rie
for _ in $(seq 1 60); do
  [ "$($CLI inspect -f '{{.State.Running}}' ismip7-rie 2>/dev/null)" = true ] || { $CLI logs ismip7-rie; exit 1; }
  curl -s -o /dev/null http://127.0.0.1:9000/ && break
  sleep 1
done
OUT=$(curl -sS -m 900 -XPOST http://127.0.0.1:9000/2015-03-31/functions/function/invocations -d @"$WORK/event.json")
echo "$OUT" | python3 -m json.tool
$CLI logs ismip7-rie 2>&1 | tail -20
$CLI rm -f ismip7-rie >/dev/null
echo "$OUT" | python3 -c 'import json,sys; r=json.load(sys.stdin); assert r.get("status")=="verified", r; print("smoke test: verified", r["n_files"], "files,", r["nrefs"], "refs, workers", r.get("n_workers"), "cpu_count", r.get("cpu_count"))'
