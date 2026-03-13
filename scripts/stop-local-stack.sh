#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

WIDGET_PORT="${WIDGET_PORT:-8081}"
FITS_UI_PORT="${FITS_UI_PORT:-5173}"

require_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Missing required command: ${command_name}" >&2
    exit 1
  fi
}

stop_port_listener() {
  local port="$1"
  local label="$2"
  local pids=()

  mapfile -t pids < <(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null | awk '!seen[$0]++')

  if (( ${#pids[@]} == 0 )); then
    echo "No ${label} listener found on port ${port}."
    return
  fi

  echo "Stopping ${label} on port ${port}..."
  kill "${pids[@]}" >/dev/null 2>&1 || true
}

require_command docker
require_command lsof

stop_port_listener "${FITS_UI_PORT}" "fits-ui dev server"
stop_port_listener "${WIDGET_PORT}" "chatbot-widget server"

echo "Stopping TAG backend Docker stack from ${REPO_ROOT}..."
(
  cd "${REPO_ROOT}"
  docker compose down
)
