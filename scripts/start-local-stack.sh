#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

WIDGET_DIR="${WIDGET_DIR:-/home/deepakrajb/Desktop/REMP ChatBot/chatbot-widget}"
FITS_UI_DIR="${FITS_UI_DIR:-/home/deepakrajb/Desktop/REMP ChatBot/fits-ui}"
WIDGET_PORT="${WIDGET_PORT:-8081}"
FITS_UI_PORT="${FITS_UI_PORT:-5173}"

widget_pid=""

require_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Missing required command: ${command_name}" >&2
    exit 1
  fi
}

require_directory() {
  local dir_path="$1"
  if [[ ! -d "${dir_path}" ]]; then
    echo "Directory not found: ${dir_path}" >&2
    exit 1
  fi
}

cleanup() {
  if [[ -n "${widget_pid}" ]] && kill -0 "${widget_pid}" >/dev/null 2>&1; then
    echo
    echo "Stopping chatbot-widget server..."
    kill "${widget_pid}" >/dev/null 2>&1 || true
    wait "${widget_pid}" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

require_command docker
require_command npm
require_command npx

require_directory "${REPO_ROOT}"
require_directory "${WIDGET_DIR}"
require_directory "${FITS_UI_DIR}"

COMPOSE_ENV_FILE=".env"

echo "Starting TAG backend Docker stack from ${REPO_ROOT}..."
echo "Using compose env file: ${REPO_ROOT}/${COMPOSE_ENV_FILE}"
(
  cd "${REPO_ROOT}"
  docker compose --env-file "${COMPOSE_ENV_FILE}" up --build -d
)

echo "Building chatbot-widget in ${WIDGET_DIR}..."
(
  cd "${WIDGET_DIR}"
  npm run build
)

echo "Serving chatbot-widget on port ${WIDGET_PORT}..."
(
  cd "${WIDGET_DIR}"
  npx serve -l "${WIDGET_PORT}" --config dev/serve.json
) &
widget_pid=$!

echo "Starting fits-ui dev server from ${FITS_UI_DIR}..."
cd "${FITS_UI_DIR}"
npm run dev -- --port "${FITS_UI_PORT}"
