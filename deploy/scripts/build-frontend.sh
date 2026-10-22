#!/usr/bin/env bash
# Personal OS — build the frontend static bundle (M1-D).
# TanStack Start is configured for SPA output and Nitro is disabled.
# Usage: ./deploy/scripts/build-frontend.sh
set -euo pipefail
cd "$(dirname "$0")/../../ui"

if command -v terraform >/dev/null 2>&1; then
  IAC_CLI=terraform
elif command -v tofu >/dev/null 2>&1; then
  IAC_CLI=tofu
else
  echo "Terraform/OpenTofu is required to read frontend auth outputs." >&2
  exit 1
fi

npm ci

COGNITO_DOMAIN="${VITE_COGNITO_HOSTED_UI_DOMAIN:-$("$IAC_CLI" -chdir=../deploy/terraform output -raw cognito_hosted_ui_domain 2>/dev/null || true)}"
COGNITO_CLIENT_ID="${VITE_COGNITO_APP_CLIENT_ID:-$("$IAC_CLI" -chdir=../deploy/terraform output -raw cognito_app_client_id 2>/dev/null || true)}"
FRONTEND_URL="${FRONTEND_URL:-$("$IAC_CLI" -chdir=../deploy/terraform output -raw frontend_url 2>/dev/null || true)}"

if [[ -z "$COGNITO_DOMAIN" || -z "$COGNITO_CLIENT_ID" || -z "$FRONTEND_URL" ]]; then
  if [[ "${ALLOW_EMPTY_AUTH:-false}" == "true" || "${PREFLIGHT:-false}" == "true" ]]; then
    echo "WARNING: Cognito/frontend outputs unavailable; building SPA bundle without pre-configured auth." >&2
  else
    echo "Required Cognito/frontend Terraform outputs are empty. Run terraform apply first, supply VITE_COGNITO_* in env, or pass ALLOW_EMPTY_AUTH=true." >&2
    exit 1
  fi
fi

export VITE_COGNITO_HOSTED_UI_DOMAIN="${COGNITO_DOMAIN:-}"
export VITE_COGNITO_APP_CLIENT_ID="${COGNITO_CLIENT_ID:-}"
if [[ -n "$FRONTEND_URL" ]]; then
  export VITE_COGNITO_REDIRECT_URI="${FRONTEND_URL%/}/auth/callback"
elif [[ -n "${VITE_COGNITO_REDIRECT_URI:-}" ]]; then
  export VITE_COGNITO_REDIRECT_URI="${VITE_COGNITO_REDIRECT_URI}"
else
  export VITE_COGNITO_REDIRECT_URI=""
fi

npm run build

OUT="dist/client"
if [ ! -f "${OUT}/_shell.html" ]; then
  echo "ERROR: ${OUT}/_shell.html missing — build did not produce a static SPA shell." >&2
  exit 1
fi
cp "${OUT}/_shell.html" "${OUT}/index.html"
if [ ! -d "${OUT}/assets" ]; then
  echo "ERROR: ${OUT}/assets missing — static JS/CSS assets were not emitted." >&2
  exit 1
fi

echo "Static frontend bundle ready in ui/${OUT}; SSR output under ui/dist/server is excluded."
