#!/bin/bash
# solo-ops — idempotent local setup (safe to run repeatedly).
#
# Usage (from the solo-ops checkout):
#   bash infra/scripts/setup/setup.sh
#
# What it does: verifies the toolchain, creates infra/.env from the template (never overwrites),
# and installs the Node dependencies of the owner CLI. MCP server registration belongs to the
# solo-mcp repository (see its README); nothing here writes outside this checkout.

set -e
set -o pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# setup.sh is at infra/scripts/setup/setup.sh: setup -> scripts -> infra -> checkout root
PROJECT_DIR="$( cd "$SCRIPT_DIR/../../.." && pwd )"

echo "solo-ops setup — checkout: $PROJECT_DIR"

if [ ! -f "$PROJECT_DIR/infra/package.json" ]; then
    echo "Error: cannot find infra/package.json" >&2
    exit 1
fi

bash "$SCRIPT_DIR/toolchain-preflight.sh" || {
    echo "Toolchain preflight failed; fix the reported tools and rerun." >&2
    exit 1
}

if [ ! -f "$PROJECT_DIR/infra/.env" ]; then
    cp "$PROJECT_DIR/infra/.env.example" "$PROJECT_DIR/infra/.env"
    echo "Created infra/.env from infra/.env.example — add your own tokens (never commit this file)."
else
    echo "infra/.env already exists (left untouched)."
fi

if [ ! -d "$PROJECT_DIR/infra/node_modules" ]; then
    (cd "$PROJECT_DIR/infra" && npm ci)
else
    echo "infra/node_modules already installed."
fi

echo "Done. Next: task test   (offline checks)   |   task --list"
echo "Siblings (optional): solo-mcp, solo-architecture, solo-knowledge, solo-skills next to this checkout."
