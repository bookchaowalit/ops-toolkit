#!/usr/bin/env bash
# Create the local, uncommitted Book Platform repository boundaries.
# This script is intentionally offline: it does not create remotes, commit,
# push, provision infrastructure, or alter a product database.
set -euo pipefail

script_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_root/../../.." && pwd)"
template_root="$repo_root/infra/scaffolds/platform-repository"
github_root="${SOLO_BOOK_DEV_GITHUB_ROOT:-$repo_root/../../..}"
platform_root="$github_root/bookchaowalit-backend/public/book-backend"

if [[ ! -d "$template_root" ]]; then
  echo "missing scaffold template: $template_root" >&2
  exit 1
fi

# id|repository|display name|capabilities|current source paths (;-separated, or none)|source status|data owner
# Keep the source paths, source status and data owner equal to
# repository-catalog/registries/platforms.yaml (checked by
# the platform_repository_guard tests in the registry repository).
platforms=(
  "api-gateway|book-api-gateway|API Gateway|routing, request identity, rate limiting, ingress policy|infra/api/server.ts;infra/api/rate_limit.ts|current-implementation-in-solo-empire|none"
  "identity|book-identity-platform|Identity|authentication, tenant, membership, RBAC, API keys|infra/api/server.ts;infra/api/auth.ts|current-implementation-in-solo-empire|book-identity-platform"
  "payment|book-payment-platform|Payment|payment intent, checkout, billing, ledger, reconciliation|infra/api/stripe.ts;infra/api/server.ts|current-implementation-in-solo-empire|book-payment-platform"
  "commerce|book-commerce-platform|Commerce|catalog, cart, pricing, order, promotion|projects/product/engineering/book-dev/github/bookchaowalit/public/book-apps/tools/book-ecommerce-scraping|adjacent-domain-repository|book-commerce-platform"
  "logistics|book-logistics-platform|Logistics|carrier adapters, shipment, shipping rates, labels, tracking|none|new-boundary|book-logistics-platform"
  "communication|book-communication-platform|Communication|email, SMS, push, templates, delivery status|bookchaowalit/solo-notifications|adjacent-domain-repository|book-communication-platform"
  "media|book-media-platform|Media|signed upload, file metadata, object storage, image processing|book_media_platform/graphics|implemented-in-this-repository|book-media-platform"
  "data|book-data-platform|Data|event ingress, lake contract, query, analytics, lineage|infra/scripts/data_lake;systems/ai-data/data-lake-architecture.md|current-implementation-in-solo-empire|book-data-platform"
  "ai|book-ai-platform|AI|LLM gateway, model routing, budget, prompt policy, RAG|bookchaowalit/solo-ai-gateway|adjacent-domain-repository|book-ai-platform"
  "workflow|book-workflow-platform|Workflow|jobs, queues, schedules, retries, approvals, execution history|infra/tasks;infra/scripts|current-implementation-in-solo-empire|book-workflow-platform"
  "security|book-security-platform|Security|secret references, policy, audit, WAF controls, privacy|infra/platform;infra/scripts/utils/secret_resolver.py|current-implementation-in-solo-empire|book-security-platform"
  "observability|book-observability-platform|Observability|logs, metrics, traces, alerts, status|infra/self-hosted-stack;infra/tests/platform|current-implementation-in-solo-empire|book-observability-platform"
  "developer|book-developer-platform|Developer|repository templates, CI/CD, config, feature flags|.github;infra/tasks|current-implementation-in-solo-empire|none"
)

escape_sed() {
  printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}

render_token() {
  local file="$1"
  local token="$2"
  local value="$3"
  local escaped
  escaped="$(escape_sed "$value")"
  sed -i "s|$token|$escaped|g" "$file"
}

# Write current_implementation.source_paths as a JSON list and record who owns
# the current implementation.  Only freshly rendered contracts reach this.
render_contract_sources() {
  python3 - "$1" "$2" "$3" <<'PY'
import json
import sys

path, joined, status = sys.argv[1:4]
with open(path, encoding="utf-8") as handle:
    contract = json.load(handle)
paths = [] if joined == "none" else [item for item in joined.split(";") if item]
implementation = contract["current_implementation"]
implementation["source_path"] = paths[0] if paths else "none"
implementation["source_paths"] = paths
implementation["source_status"] = status
if status == "implemented-in-this-repository":
    implementation["repository"] = contract["repository"]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(contract, handle, indent=2)
    handle.write("\n")
PY
}

copy_if_missing() {
  local source="$1"
  local destination="$2"
  if [[ ! -e "$destination" ]]; then
    mkdir -p "$(dirname -- "$destination")"
    cp "$source" "$destination"
  fi
}

mkdir -p "$platform_root"
created=0
for record in "${platforms[@]}"; do
  IFS='|' read -r platform_id repository platform_name capabilities source_paths source_status data_owner <<< "$record"
  source_path="${source_paths%%;*}"
  destination="$platform_root/$repository"
  mkdir -p "$destination/scripts"

  copy_if_missing "$template_root/README.md" "$destination/README.md"
  copy_if_missing "$template_root/contract.json" "$destination/contract.json"
  copy_if_missing "$template_root/scripts/check.py" "$destination/scripts/check.py"
  copy_if_missing "$template_root/scripts/check.sh" "$destination/scripts/check.sh"
  copy_if_missing "$template_root/.gitignore" "$destination/.gitignore"

  for file in "$destination/README.md" "$destination/contract.json"; do
    if grep -q '__' "$file"; then
      render_token "$file" '__PLATFORM_ID__' "$platform_id"
      render_token "$file" '__REPOSITORY__' "$repository"
      render_token "$file" '__PLATFORM_NAME__' "$platform_name"
      render_token "$file" '__CAPABILITIES__' "$capabilities"
      render_token "$file" '__SOURCE_PATH__' "$source_path"
      render_token "$file" '__DATA_OWNER__' "$data_owner"
      render_token "$file" '__SOURCE_STATUS__' "$source_status"
      render_token "$file" '__TARGET_URL__' "https://github.com/bookchaowalit-backend/$repository.git"
      if [[ "$file" == */contract.json ]]; then
        render_contract_sources "$file" "$source_paths" "$source_status"
      fi
    fi
  done

  if [[ ! -e "$destination/.git" ]]; then
    git init -q -b main "$destination" 2>/dev/null || git -C "$destination" init -q
    created=$((created + 1))
  fi
  chmod +x "$destination/scripts/check.sh" "$destination/scripts/check.py"
done

echo "Created or prepared $created new local Git boundary(ies)."
echo "Platform boundaries: ${#platforms[@]}"
echo "No remotes, commits, pushes, provider calls, or database writes were performed."
