#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8787}"
CODEX_EXECUTABLE="${CODEX_EXECUTABLE:-codex}"

cd "$ROOT_DIR"
exec python3 -m stub_installed_client_service.server \
  --host "$HOST" \
  --port "$PORT" \
  --codex-executable "$CODEX_EXECUTABLE"
