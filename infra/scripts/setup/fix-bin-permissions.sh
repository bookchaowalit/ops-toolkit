#!/bin/bash
# Fix executable permissions on node_modules/.bin shims
# This script should run after npm install to ensure all binaries are executable

set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BIN_DIR="$ROOT/infra/node_modules/.bin"

if [ ! -d "$BIN_DIR" ]; then
    echo "No node_modules/.bin directory found. Run npm install first."
    exit 0
fi

echo "Fixing executable permissions in $BIN_DIR..."

# Make all files in .bin executable
find "$BIN_DIR" -type f -exec chmod +x {} \;

echo "✅ Fixed permissions for all binaries in $BIN_DIR"
