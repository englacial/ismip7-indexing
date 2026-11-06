#!/usr/bin/env bash
# Create or update the ISMIP7 Lambda stack (infra/lambda-stack.yaml) and push the image.
#
#   AWS_PROFILE=astera infra/deploy.sh            # full deploy: stack, image, function update
#   AWS_PROFILE=astera infra/deploy.sh --image    # rebuild and push the image, update the function only
#
# Requires: aws CLI signed in to the target account, docker (with buildx) or podman, a clean git checkout.
# Set CONTAINER_CLI=podman|docker to choose; defaults to docker if present, else podman.
# The first run creates the ECR repository with a placeholder, pushes the real
# image, then points the function at it; later runs only rebuild and push.
set -euo pipefail
cd "$(dirname "$0")/.."

STACK=${STACK:-ismip7-virtualize}
FUNCTION=${FUNCTION:-ismip7-virtualize}
REGION=${AWS_REGION:-us-west-2}
# A pre-created execution role (infra/execution-role.json); empty = the stack creates one.
ROLE_ARN=${EXECUTION_ROLE_ARN:-}
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
SHA=$(git rev-parse HEAD)
IMAGE="${ECR}/${FUNCTION}:${SHA}"

CLI=${CONTAINER_CLI:-$(command -v docker >/dev/null 2>&1 && echo docker || echo podman)}

build_and_push() {
  aws ecr get-login-password --region "$REGION" | "$CLI" login --username AWS --password-stdin "$ECR"
  if [[ "$CLI" == podman ]]; then
    # Lambda needs a Docker v2 single-image manifest: build in docker format (not OCI).
    podman build --platform linux/amd64 --format docker \
        --build-arg GIT_SHA="$SHA" -f Dockerfile.lambda -t "$IMAGE" .
    podman push --format v2s2 "$IMAGE"
  else
    docker buildx build --platform linux/amd64 --provenance=false \
        --build-arg GIT_SHA="$SHA" -f Dockerfile.lambda -t "$IMAGE" --load .
    docker push "$IMAGE"
  fi
}

if [[ "${1:-}" == "--image" ]]; then
  build_and_push
  aws lambda update-function-code --region "$REGION" --function-name "$FUNCTION" --image-uri "$IMAGE" >/dev/null
  aws lambda wait function-updated --region "$REGION" --function-name "$FUNCTION"
  echo "function $FUNCTION now runs $IMAGE"
  exit 0
fi

# 1. Stack without the function first: Lambda accepts only images from a private
#    ECR repository, and the repository is created by this stack.
if ! aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" >/dev/null 2>&1; then
  echo "creating stack $STACK (repository, role, secret, log group; no function yet)"
  aws cloudformation deploy --region "$REGION" --stack-name "$STACK" \
      --template-file infra/lambda-stack.yaml --capabilities CAPABILITY_NAMED_IAM \
      --parameter-overrides FunctionName="$FUNCTION" ImageUri="" ExecutionRoleArn="$ROLE_ARN" \
      --no-fail-on-empty-changeset
fi

# 2. Real image.
build_and_push

# 3. Create (or update) the function with the real image.
aws cloudformation deploy --region "$REGION" --stack-name "$STACK" \
    --template-file infra/lambda-stack.yaml --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides FunctionName="$FUNCTION" ImageUri="$IMAGE" ExecutionRoleArn="$ROLE_ARN" \
    --no-fail-on-empty-changeset

aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
    --query 'Stacks[0].Outputs' --output table
echo
echo "Next: put the source.coop credentials into the secret (not committed anywhere):"
echo "  aws secretsmanager put-secret-value --region $REGION --secret-id ismip7/sc_creds --secret-string file://sc_creds.json"
