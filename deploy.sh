#!/usr/bin/env bash
# Syncs this directory to a remote host over ssh. Prompts for anything not
# supplied as an argument or environment variable.
#
# This deliberately only ever syncs FILES - it never runs `docker` on the
# remote host itself. Instead it prints the `docker compose up -d --build`
# command at the end for you to run there yourself. The ssh user doing the
# file sync may not be in that host's `docker` group (or have passwordless
# sudo for it) even when it can write to the deploy directory fine - a
# common split on hosts where container lifecycle is deliberately gated
# behind a login/sudo prompt, and non-interactive `ssh host "docker ..."`
# often can't see docker even when an interactive login can (PATH/profile
# differences, or a stale group membership that only refreshes on a fresh
# login). Rather than chase every way that can fail non-interactively, this
# just doesn't try.
#
# Usage:
#   ./deploy.sh [user@host] [remote_dir]
# Both are optional - omitted ones are prompted for. REMOTE_TARGET and
# REMOTE_DIR env vars work too, for non-interactive use (e.g. CI, though
# that requires key-based auth since nothing will be there to type a
# password):
#   REMOTE_TARGET=user@host REMOTE_DIR=fileplayer ./deploy.sh
#
# Auth: tries key-based ssh first (fast, silent); if that's not set up, you
# get a normal interactive password prompt instead - same as running ssh
# directly. The connectivity check and the rsync transfer both need their
# own ssh connection, so whichever auth succeeds first opens a shared
# connection (ControlMaster/ControlPersist) the transfer reuses, rather
# than prompting you for your password twice.
#
# Requires locally: rsync, ssh. Requires on the remote host: ssh access
# (key or password) for the given user, and (for you to run yourself
# afterwards) docker with the compose plugin.
#
# A .env file (see .env.example - at minimum WEBSOCKET_URL) must exist next
# to this script before deploying; it's copied over so compose has it on
# the remote side too.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REMOTE_TARGET="${1:-${REMOTE_TARGET:-}}"
if [[ -z "$REMOTE_TARGET" ]]; then
  read -rp "Remote target (user@host): " REMOTE_TARGET
fi
if [[ -z "$REMOTE_TARGET" ]]; then
  echo "error: a remote target is required" >&2
  exit 1
fi

REMOTE_DIR="${2:-${REMOTE_DIR:-}}"
if [[ -z "$REMOTE_DIR" ]]; then
  read -rp "Remote directory [fileplayer]: " REMOTE_DIR
fi
REMOTE_DIR="${REMOTE_DIR:-fileplayer}"

if [[ ! -f "${SCRIPT_DIR}/.env" ]]; then
  echo "error: ${SCRIPT_DIR}/.env not found - copy .env.example to .env and fill it in first" >&2
  exit 1
fi

CONTROL_PATH="$(mktemp -u -t fileplayer-deploy-XXXXXX.sock)"
cleanup() {
  ssh -o ControlPath="${CONTROL_PATH}" -O exit "${REMOTE_TARGET}" >/dev/null 2>&1 || true
  rm -f "${CONTROL_PATH}"
}
trap cleanup EXIT

# Purely informational - deliberately does NOT use ControlPath. If this
# touches the (not-yet-existing) control socket at all, even on a failed
# auth attempt, the real attempt below can find a broken socket left
# behind and fail outright ("getsockname failed: Not a socket") without
# ever reaching a password prompt. So this probe's outcome only decides
# which message to print; the real attempt just below is the one and
# only call that ever references ControlPath.
echo "==> checking ssh access to ${REMOTE_TARGET}"
if ssh -o BatchMode=yes -o ConnectTimeout=10 "${REMOTE_TARGET}" true 2>/dev/null; then
  echo "    key-based auth OK"
else
  echo "    key-based auth not available - enter your password when prompted"
fi

SSH_OPTS=(-o "ControlMaster=auto" -o "ControlPath=${CONTROL_PATH}" -o "ControlPersist=60s")
if ! ssh "${SSH_OPTS[@]}" -o ConnectTimeout=30 "${REMOTE_TARGET}" true; then
  echo "error: could not authenticate to ${REMOTE_TARGET}" >&2
  exit 1
fi

echo "==> syncing ${SCRIPT_DIR}/ to ${REMOTE_TARGET}:${REMOTE_DIR}/"
export RSYNC_RSH="ssh ${SSH_OPTS[*]}"
rsync -az --delete \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='tests' \
  --exclude='demo' \
  --exclude='fileplayer_state*.json' \
  --exclude='logs' \
  "${SCRIPT_DIR}/" "${REMOTE_TARGET}:${REMOTE_DIR}/"

echo ""
echo "==> files synced. Run this on the remote host to build the image and start the container"
echo "    (prefix with sudo if your remote user is not in the docker group):"
echo ""
echo "    ssh ${REMOTE_TARGET}"
echo "    cd ${REMOTE_DIR} && docker compose up -d --build"
echo ""
echo "    then tail logs with:"
echo "    docker compose logs -f"
