#!/usr/bin/env bash
# Personal OS — deployment smoke test (M1-D).
# Exercises the public HTTPS entry point without credentials: health,
# readiness, version, and frontend. Run after deploy-frontend.sh.
# Usage: ./deploy/scripts/smoke-test.sh <frontend_url>
set -euo pipefail

BASE="${1:?usage: smoke-test.sh https://<cloudfront-domain-or-custom-domain>}"
FAIL=0

check() {
  local name="$1" url="$2" expect="$3"
  code="$(curl -s -o /tmp/smoke-body -w '%{http_code}' --max-time 15 "$url")"
  if [ "$code" = "$expect" ]; then
    echo "PASS  ${name} (${code})"
  else
    echo "FAIL  ${name}: expected ${expect}, got ${code}"
    head -c 300 /tmp/smoke-body || true
    FAIL=1
  fi
}

# 1. HTTPS + certificate sanity
check "frontend index"        "${BASE}/"                  200
check "API liveness"          "${BASE}/api/v1/health"     200
check "API readiness (DB)"    "${BASE}/api/v1/ready"      200
check "API version"           "${BASE}/api/v1/version"    200

# 2. HTTP must redirect to HTTPS (CloudFront viewer_protocol_policy)
http_url="${BASE/https:/http:}"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$http_url/")"
if [ "$code" = "301" ] || [ "$code" = "308" ]; then
  echo "PASS  HTTP→HTTPS redirect (${code})"
else
  echo "FAIL  HTTP did not redirect: ${code}"
  FAIL=1
fi

# 3. TLS version floor
proto="$(curl -s -o /dev/null --max-time 10 -w '%{ssl_version}' "$BASE/" || true)"
if [ "$proto" = "TLSv1.3" ] || [ "$proto" = "TLSv1.2" ]; then
  echo "PASS  TLS: ${proto}"
else
  echo "WARN  TLS: ${proto}"
fi

# 4. Direct ALB access must be blocked (origin-verify rule).
echo "Manual check (run once, from a non-CloudFront network):"
echo "  curl https://<alb_dns>/api/v1/health   # must NOT return 200"

if [ "$FAIL" -eq 0 ]; then
  echo "SMOKE TEST: ALL PASS"
else
  echo "SMOKE TEST: FAILURES DETECTED"
  exit 1
fi
