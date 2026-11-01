#!/usr/bin/env bash
# Build the Lambda image and run the handler once under the Lambda Runtime Interface
# Emulator the way Lambda runs it: non-root, /dev/shm read-only, 10 GB / 900 s env.
# Usage: scripts/dev/rie_run.sh <event.json> <target-dir-with-prepared-repo>
# The event's "target" must be local:/mnt/target/<repo> (the dir is mounted there).
set -euo pipefail
EVENT=${1:?event.json}; TARGET=${2:?target dir}
RIE=${RIE:-$HOME/.local/bin/aws-lambda-rie}
[ -x "$RIE" ] || { mkdir -p "$(dirname "$RIE")"; curl -sSL -o "$RIE" https://github.com/aws/aws-lambda-runtime-interface-emulator/releases/latest/download/aws-lambda-rie; chmod +x "$RIE"; }
docker buildx build --platform linux/amd64 --provenance=false --build-arg GIT_SHA="$(git rev-parse HEAD)" \
    -f Dockerfile.lambda -t ismip7-virtualize:rie --load .
chmod -R a+rwX "$TARGET"
docker rm -f ismip7-rie >/dev/null 2>&1 || true
docker run -d --name ismip7-rie --platform linux/amd64 --user 1000 -p 9000:8080 \
    --mount type=tmpfs,dst=/dev/shm,tmpfs-mode=0555,readonly \
    -v "$RIE":/aws-lambda-rie:ro -v "$TARGET":/mnt/target \
    -e AWS_LAMBDA_FUNCTION_MEMORY_SIZE=10240 -e AWS_LAMBDA_FUNCTION_TIMEOUT=900 \
    --entrypoint /aws-lambda-rie ismip7-virtualize:rie /var/lang/bin/python -m awslambdaric ismip7_helper.lambda_handler.handler
sleep 3
curl -sS -XPOST http://127.0.0.1:9000/2015-03-31/functions/function/invocations -d @"$EVENT" | python3 -m json.tool
docker logs ismip7-rie 2>&1 | tail -20
docker rm -f ismip7-rie >/dev/null
