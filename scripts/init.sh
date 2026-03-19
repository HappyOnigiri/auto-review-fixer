#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
TEMPLATE_LOCAL="${SCRIPT_DIR:+$SCRIPT_DIR/templates/run-refix.yml}"
TEMPLATE_URL="https://raw.githubusercontent.com/HappyOnigiri/Refix/main/scripts/templates/run-refix.yml"
WORKFLOW_DIR=".github/workflows"
WORKFLOW_FILE="$WORKFLOW_DIR/run-refix.yml"

# Parse flags
FORCE=false
CHANNEL=""    # stable (@v1) or main (@main)
SCHEDULE=""   # 1h, 30m, 6h, disable

for arg in "$@"; do
  case "$arg" in
    -f) FORCE=true ;;
    --channel=*) CHANNEL="${arg#--channel=}" ;;
    --schedule=*) SCHEDULE="${arg#--schedule=}" ;;
  esac
done

# 上書きチェック
if [ -f "$WORKFLOW_FILE" ] && [ "$FORCE" != true ]; then
  if [ ! -r /dev/tty ]; then
    echo "Aborted: non-interactive shell. Re-run with -f to overwrite." >&2
    exit 1
  fi
  printf "⚠️  %s already exists. Overwrite? [y/N] " "$WORKFLOW_FILE" >/dev/tty
  read -r answer </dev/tty
  case "$answer" in
    [yY]*) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
fi

mkdir -p "$WORKFLOW_DIR"

if [ -n "$TEMPLATE_LOCAL" ] && [ -f "$TEMPLATE_LOCAL" ]; then
  cp "$TEMPLATE_LOCAL" "$WORKFLOW_FILE"
else
  tmp=$(mktemp)
  trap 'rm -f "$tmp"' EXIT
  curl -fsSL "$TEMPLATE_URL" -o "$tmp"
  mv "$tmp" "$WORKFLOW_FILE"
fi

# Channel selection (stable @v1 / latest @main)
if [ -z "$CHANNEL" ] && [ -r /dev/tty ]; then
  echo "" >/dev/tty
  echo "Release channel:" >/dev/tty
  echo "  1) stable  — @v1   (recommended: tracks the latest stable major release)" >/dev/tty
  echo "  2) latest  — @main (tracks unreleased development changes)" >/dev/tty
  printf "Select [1]: " >/dev/tty
  read -r ch </dev/tty
  case "${ch:-1}" in
    2) CHANNEL="main" ;;
    *) CHANNEL="stable" ;;
  esac
fi

if [ "${CHANNEL:-stable}" = "main" ]; then
  sed -i.bak 's|HappyOnigiri/Refix@v1|HappyOnigiri/Refix@main|g' "$WORKFLOW_FILE"
  rm -f "${WORKFLOW_FILE}.bak"
fi

# Schedule selection
if [ -z "$SCHEDULE" ] && [ -r /dev/tty ]; then
  echo "" >/dev/tty
  echo "Recovery schedule:" >/dev/tty
  echo "  1) Every hour        (recommended)" >/dev/tty
  echo "  2) Every 30 minutes" >/dev/tty
  echo "  3) Every 6 hours" >/dev/tty
  echo "  4) Disable           (use external cron)" >/dev/tty
  printf "Select [1]: " >/dev/tty
  read -r sch </dev/tty
  case "${sch:-1}" in
    2) SCHEDULE="30m" ;;
    3) SCHEDULE="6h" ;;
    4) SCHEDULE="disable" ;;
    *) SCHEDULE="1h" ;;
  esac
fi

case "${SCHEDULE:-1h}" in
  30m)
    sed -i.bak 's|- cron: "0 \* \* \* \*"|- cron: "*/30 * * * *"|' "$WORKFLOW_FILE"
    rm -f "${WORKFLOW_FILE}.bak"
    ;;
  6h)
    sed -i.bak 's|- cron: "0 \* \* \* \*"|- cron: "0 */6 * * *"|' "$WORKFLOW_FILE"
    rm -f "${WORKFLOW_FILE}.bak"
    ;;
  disable)
    sed -i.bak '/^  schedule:$/,/^    - cron:/d' "$WORKFLOW_FILE"
    rm -f "${WORKFLOW_FILE}.bak"
    ;;
esac

echo "✅ Created $WORKFLOW_FILE"
echo ""
echo "Next steps:"
echo "  1. Add secrets: GH_TOKEN, CLAUDE_CODE_OAUTH_TOKEN"
echo "  2. (Optional) Add .refix.yaml or set vars.REFIX_CONFIG_YAML"
echo "  3. See: https://github.com/HappyOnigiri/Refix"
