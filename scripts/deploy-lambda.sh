#!/bin/bash
# Build and deploy the Garage61 MCP HTTP server to AWS Lambda.
#
# The HTTP transport is stateless with plain JSON responses, which is exactly
# Lambda's shape; the Lambda Web Adapter layer runs the ASGI app unchanged.
# Requires: aws CLI with credentials, python3.11 with pip.
#
#   ./scripts/deploy-lambda.sh          # package + update code
#
# One honest trade of scale-to-zero: in-memory caches (comparison state,
# telemetry LRU) do not survive cold starts, so after a long idle the first
# comparison refetches from Garage61 and a drill-down may ask for a re-compare.
set -euo pipefail
REGION=${REGION:-us-east-1}
FUNCTION=${FUNCTION:-garage61-mcp}
PY=${PY:-python3}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

$PY -m pip install -q --target "$WORK/pkg" \
  --platform manylinux2014_x86_64 --implementation cp --python-version 3.11 \
  --only-binary=:all: \
  "mcp>=1.0.0,<2.0.0" "httpx>=0.25.0" "pydantic>=2.0.0" \
  "python-dotenv>=1.0.0" "uvicorn>=0.30.0"
cp -r "$ROOT/src" "$WORK/pkg/garage61_mcp"
cat > "$WORK/pkg/run.sh" <<'RUN'
#!/bin/bash
exec python -m uvicorn garage61_mcp.http_server:app --host 0.0.0.0 --port 8080 --no-access-log
RUN
chmod +x "$WORK/pkg/run.sh"
( cd "$WORK/pkg" && find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; zip -qr ../fn.zip . )
aws lambda update-function-code --region "$REGION" --function-name "$FUNCTION" \
  --zip-file "fileb://$WORK/fn.zip" \
  --query '{fn:FunctionName,size:CodeSize,status:LastUpdateStatus}' --output json
