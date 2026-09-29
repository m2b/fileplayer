#!/usr/bin/env bash
# Installs (or updates) the file player as a systemd service on THIS Linux
# host - no docker. Run it on the host itself, from a copy of this repo
# (e.g. one synced over by deploy.sh/deploy.ps1). The service runs straight
# out of this directory, by default using a venv created in ./.venv.
#
# Usage:
#   bash deploy_service.sh [websocket_url]
# The websocket url is required: pass it as the argument, or set
# WEBSOCKET_URL (in the environment or .env), or you're prompted for it.
#
# Same configuration as docker-compose.yml: --playlist, --serve-api and
# --websocket-url, with durable state and a daily-rotating log file. Like
# compose, it reads a .env next to this script (see .env.example), so one
# .env serves both. The same settings are overridable in .env or as env vars,
# which take precedence over .env (defaults in brackets; relative paths are
# relative to this directory):
#   SERVICE_NAME [fileplayer]      SERVICE_USER [you - or SUDO_USER under sudo]
#   ASSETS_DIR [./sample_data]     PLAYLIST_FILE [./sample_playlist.csv]
#   STATE_FILE [./fileplayer_state.json]    LOGS_DIR [./logs]
#   API_PORT [8000]  DEFAULT_SPEED [1.0]  TICK [0.2]
#   WEBSOCKET_RETRY_DELAY [5.0]  LOG_LEVEL [INFO]
#   USE_VENV [1] - set to 0 to skip ./.venv and run with PYTHON instead,
#     which must already have requirements.txt installed (e.g. from distro
#     packages); nothing is pip-installed in that case
#   PYTHON [python3] - only used with USE_VENV=0
# e.g.
#   ASSETS_DIR=/srv/assets PLAYLIST_FILE=/srv/playlist.csv bash deploy_service.sh ws://host:1880/ingest
#   USE_VENV=0 bash deploy_service.sh ws://host:1880/ingest
#
# Safe to re-run (e.g. after redeploying files): it refreshes the venv's
# packages, rewrites the unit and restarts the service.
#
# Requires on this host: systemd, python3 (with the venv module -
# python3-venv on Debian/Ubuntu - unless USE_VENV=0), and sudo for
# installing the unit and running systemctl (unless run as root). The service user must be able to
# write to this directory (the state file is written next to it by default).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Reads ./.env the way docker compose does: KEY=VALUE lines, # comments,
# optional surrounding quotes. It's parsed rather than sourced, since
# compose's .env syntax isn't bash. Variables already set in the
# environment win over the file, as with compose.
if [[ -f .env ]]; then
  echo "==> reading settings from ${SCRIPT_DIR}/.env"
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$ ]] || continue
    key="${BASH_REMATCH[2]}"
    value="${BASH_REMATCH[3]}"
    if [[ "$value" =~ ^\"(.*)\"$ || "$value" =~ ^\'(.*)\'$ ]]; then
      value="${BASH_REMATCH[1]}"
    else
      value="${value%%[[:space:]]#*}"
      value="${value%"${value##*[![:space:]]}"}"
    fi
    [[ -v "$key" ]] || printf -v "$key" '%s' "$value"
  done <.env
fi

WEBSOCKET_URL="${1:-${WEBSOCKET_URL:-}}"
if [[ -z "$WEBSOCKET_URL" ]]; then
  read -rp "Websocket url (e.g. ws://host:1880/ingest): " WEBSOCKET_URL
fi
if [[ ! "$WEBSOCKET_URL" =~ ^wss?:// ]]; then
  echo "error: a websocket url starting with ws:// or wss:// is required" >&2
  exit 1
fi

SERVICE_NAME="${SERVICE_NAME:-fileplayer}"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-$(id -un)}}"

abs_path() {
  case "$1" in
    /*) printf '%s' "$1" ;;
    *) printf '%s/%s' "$SCRIPT_DIR" "${1#./}" ;;
  esac
}
ASSETS_DIR="$(abs_path "${ASSETS_DIR:-sample_data}")"
PLAYLIST_FILE="$(abs_path "${PLAYLIST_FILE:-sample_playlist.csv}")"
STATE_FILE="$(abs_path "${STATE_FILE:-fileplayer_state.json}")"
LOGS_DIR="$(abs_path "${LOGS_DIR:-logs}")"
API_PORT="${API_PORT:-8000}"
DEFAULT_SPEED="${DEFAULT_SPEED:-1.0}"
TICK="${TICK:-0.2}"
WEBSOCKET_RETRY_DELAY="${WEBSOCKET_RETRY_DELAY:-5.0}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
USE_VENV="${USE_VENV:-1}"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "error: systemctl not found - this host doesn't look like it runs systemd" >&2
  exit 1
fi
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "error: service user '${SERVICE_USER}' does not exist" >&2
  exit 1
fi
if [[ ! -d "$ASSETS_DIR" ]]; then
  echo "error: assets directory ${ASSETS_DIR} not found" >&2
  exit 1
fi
if [[ ! -f "$PLAYLIST_FILE" ]]; then
  echo "error: playlist file ${PLAYLIST_FILE} not found" >&2
  exit 1
fi

SUDO=""
if [[ $EUID -ne 0 ]]; then
  SUDO="sudo"
fi

# The venv, logs dir and state file belong to the service user, so create
# them as that user rather than as whoever is running this script.
as_service_user() {
  if [[ "$(id -un)" == "$SERVICE_USER" ]]; then
    "$@"
  else
    sudo -u "$SERVICE_USER" -- "$@"
  fi
}

if ! as_service_user test -w "$SCRIPT_DIR"; then
  echo "error: ${SERVICE_USER} can't write to ${SCRIPT_DIR}" >&2
  exit 1
fi

if [[ "$USE_VENV" == "0" ]]; then
  # systemd needs an absolute path to the interpreter.
  if ! PYTHON_BIN="$(command -v "${PYTHON:-python3}")"; then
    echo "error: ${PYTHON:-python3} not found" >&2
    exit 1
  fi
  echo "==> using ${PYTHON_BIN} (no venv)"
  if ! as_service_user "$PYTHON_BIN" -c "import fastapi, uvicorn, websockets"; then
    echo "error: ${PYTHON_BIN} is missing packages from requirements.txt (fastapi, uvicorn, websockets)" >&2
    echo "       install them for it, or re-run without USE_VENV=0 to use a venv" >&2
    exit 1
  fi
else
  VENV="${SCRIPT_DIR}/.venv"
  echo "==> setting up python venv in ${VENV}"
  if [[ ! -x "${VENV}/bin/python" ]]; then
    if ! as_service_user python3 -m venv "$VENV"; then
      echo "error: couldn't create a venv - on Debian/Ubuntu install python3-venv first" >&2
      exit 1
    fi
  fi
  as_service_user "${VENV}/bin/pip" install --quiet --disable-pip-version-check -r requirements.txt
  PYTHON_BIN="${VENV}/bin/python"
fi
as_service_user mkdir -p "$LOGS_DIR"

# Quotes one ExecStart argument for systemd: double-quoted, with systemd's
# own specifier (%) and variable ($) expansion escaped.
sd_quote() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//%/%%}"
  s="${s//\$/\$\$}"
  printf '"%s"' "$s"
}

EXEC_ARGS=(
  "$PYTHON_BIN" -m publisher.player_cli
  --root "$ASSETS_DIR"
  --playlist "$PLAYLIST_FILE"
  --default-speed "$DEFAULT_SPEED"
  --tick "$TICK"
  --state-file "$STATE_FILE"
  --serve-api
  --api-host 0.0.0.0
  --api-port "$API_PORT"
  --websocket-url "$WEBSOCKET_URL"
  --websocket-retry-delay "$WEBSOCKET_RETRY_DELAY"
  --log-level "$LOG_LEVEL"
  --log-dir "$LOGS_DIR"
)
EXEC_START=""
for arg in "${EXEC_ARGS[@]}"; do
  EXEC_START+="$(sd_quote "$arg") "
done

UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
UNIT_TMP="$(mktemp)"
trap 'rm -f "$UNIT_TMP"' EXIT
cat >"$UNIT_TMP" <<EOF
# Generated by ${SCRIPT_DIR}/deploy_service.sh - re-run that to change it.
[Unit]
Description=Timeseries CSV file player
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${SCRIPT_DIR//%/%%}
Environment=PYTHONUNBUFFERED=1
ExecStart=${EXEC_START% }
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "==> installing ${UNIT_PATH}"
$SUDO install -m 644 "$UNIT_TMP" "$UNIT_PATH"
$SUDO systemctl daemon-reload
$SUDO systemctl enable --quiet "$SERVICE_NAME"
echo "==> (re)starting ${SERVICE_NAME}"
$SUDO systemctl restart "$SERVICE_NAME"

# A bad setting (e.g. a non-numeric TICK) only shows up once the player
# parses its arguments, so give it a moment and check it's still up.
sleep 3
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  echo "error: ${SERVICE_NAME} failed to stay up - recent logs:" >&2
  $SUDO journalctl -u "$SERVICE_NAME" -n 30 --no-pager >&2 || true
  exit 1
fi

echo ""
echo "==> ${SERVICE_NAME} is running as ${SERVICE_USER}, publishing to ${WEBSOCKET_URL}"
echo "    api:     http://$(hostname):${API_PORT}/api/tree"
echo "    status:  systemctl status ${SERVICE_NAME}"
echo "    logs:    journalctl -u ${SERVICE_NAME} -f    (also ${LOGS_DIR}/fileplayer.log)"
echo "    remove:  sudo systemctl disable --now ${SERVICE_NAME} && sudo rm ${UNIT_PATH} && sudo systemctl daemon-reload"
