#!/usr/bin/env bash
set -Eeuo pipefail

# Synthetic smoke test for vLLM and, optionally, the Solo Empire AI Gateway.
# It never sends repository or personal data.

VLLM_BASE_URL="${RUNPOD_VLLM_BASE_URL:-http://127.0.0.1:18000/v1}"
VLLM_HEALTH_URL="${RUNPOD_VLLM_HEALTH_URL:-${VLLM_BASE_URL%/v1}/health}"
EXPECTED_MODEL="${RUNPOD_VLLM_EXPECTED_MODEL:-${VLLM_SERVED_MODEL_NAME:-qwen3.8-27b-nvfp4}}"
REQUEST_TIMEOUT_SEC="${RUNPOD_VLLM_REQUEST_TIMEOUT_SEC:-120}"
GATEWAY_URL="${AI_GATEWAY_SMOKE_URL:-}"
GATEWAY_TOKEN="${AI_GATEWAY_SMOKE_TOKEN:-}"
REQUIRE_GATEWAY="${RUNPOD_SMOKE_REQUIRE_GATEWAY:-false}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: RUNPOD_VLLM_BASE_URL=http://127.0.0.1:18000/v1 $0"
  echo "Optional: AI_GATEWAY_SMOKE_URL, AI_GATEWAY_SMOKE_TOKEN, RUNPOD_SMOKE_REQUIRE_GATEWAY=true"
  exit 0
fi

die() {
  echo "runpod-vllm-smoke: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

require_command curl
require_command python3

echo "[1/4] vLLM health"
health_status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 10 "$VLLM_HEALTH_URL")"
[[ "$health_status" == "200" ]] || die "vLLM health returned HTTP $health_status"

echo "[2/4] served model"
models_json="$(curl --fail --silent --show-error --max-time 10 "$VLLM_BASE_URL/models")"
served_model="$(printf '%s' "$models_json" | python3 -c 'import json,sys; p=json.load(sys.stdin); e=sys.argv[1]; m=[x.get("id") for x in p.get("data",[])]; sys.exit(f"expected model {e!r}; got {m!r}") if e not in m else print(e)' "$EXPECTED_MODEL")"
echo "model=$served_model"

echo "[3/4] synthetic coding completion"
request_json="$(python3 -c 'import json,sys; print(json.dumps({"model":sys.argv[1],"messages":[{"role":"user","content":"Write a Python function named add(a, b) that returns a + b. Return code only."}],"max_tokens":64,"temperature":0}))' "$EXPECTED_MODEL")"
completion_json="$(curl --fail --silent --show-error --max-time "$REQUEST_TIMEOUT_SEC" -H 'Content-Type: application/json' --data "$request_json" "$VLLM_BASE_URL/chat/completions")"
printf '%s' "$completion_json" | python3 -c 'import json,sys; p=json.load(sys.stdin); c=p.get("choices",[]); content=c[0].get("message",{}).get("content","") if c else ""; ok=isinstance(content,str) and "def add" in content and "return" in content; sys.exit("coding completion did not contain an add function") if not ok else None'
echo "coding=ok"

if [[ -n "$GATEWAY_URL" || "$REQUIRE_GATEWAY" == "true" ]]; then
  [[ -n "$GATEWAY_URL" ]] || die "RUNPOD_SMOKE_REQUIRE_GATEWAY=true but AI_GATEWAY_SMOKE_URL is empty"
  [[ -n "$GATEWAY_TOKEN" ]] || die "AI_GATEWAY_SMOKE_TOKEN is required for gateway smoke"
  echo "[4/4] Solo Empire AI Gateway"
  gateway_status="$(curl --fail --silent --show-error --max-time 10 -H "Authorization: Bearer $GATEWAY_TOKEN" --write-out '\n%{http_code}' "$GATEWAY_URL/api/ai/status")"
  gateway_code="${gateway_status##*$'\n'}"
  gateway_payload="${gateway_status%$'\n'*}"
  [[ "$gateway_code" == "200" ]] || die "AI Gateway status returned HTTP $gateway_code"
  configured="$(printf '%s' "$gateway_payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("configured", ""))')"
  [[ "$configured" == "True" || "$configured" == "true" ]] || die "AI Gateway reports configured=$configured"
  gateway_request='{"route":"runpod-smoke","prompt_version":"runpod-smoke-v1","profile":"fast","privacy_class":"public","token_budget":512,"max_output_tokens":64,"messages":[{"role":"user","content":"Write a Python function named add(a, b) that returns a + b. Return code only."}]}'
  gateway_response="$(curl --fail --silent --show-error --max-time "$REQUEST_TIMEOUT_SEC" -H "Authorization: Bearer $GATEWAY_TOKEN" -H 'Content-Type: application/json' --data "$gateway_request" "$GATEWAY_URL/api/ai/generate")"
  printf '%s' "$gateway_response" | python3 -c 'import json,sys; p=json.load(sys.stdin); content=p.get("content",""); sys.exit("AI Gateway contract mismatch") if p.get("contract") != "solo-empire.ai-gateway.v1" else None; sys.exit("AI Gateway coding response missing add function") if not isinstance(content,str) or "def add" not in content or "return" not in content else None'
  echo "gateway=coding_ok"
else
  echo "[4/4] Solo Empire AI Gateway skipped (set AI_GATEWAY_SMOKE_URL and AI_GATEWAY_SMOKE_TOKEN to enable)"
fi

echo "runpod-vllm-smoke: PASS"
