#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
TEMPLATE_LOCAL="${SCRIPT_DIR:+$SCRIPT_DIR/templates/run-refix.yml}"
TEMPLATE_URL="https://raw.githubusercontent.com/HappyOnigiri/Refix/main/scripts/templates/run-refix.yml"
WORKFLOW_DIR=".github/workflows"
WORKFLOW_FILE="$WORKFLOW_DIR/run-refix.yml"

# 上書きチェック
if [ -f "$WORKFLOW_FILE" ] && [ "${1:-}" != "-f" ]; then
  printf "⚠️  %s already exists. Overwrite? [y/N] " "$WORKFLOW_FILE" >/dev/tty
  if [ ! -r /dev/tty ]; then
    echo "Aborted: non-interactive shell. Re-run with -f to overwrite." >&2
    exit 1
  fi
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

echo "✅ Created $WORKFLOW_FILE"
echo ""
echo "Next steps:"
echo "  1. Add secrets: GH_TOKEN, CLAUDE_CODE_OAUTH_TOKEN"
echo "  2. (Optional) Add .refix.yaml or set vars.REFIX_CONFIG_YAML"
echo "  3. See: https://github.com/HappyOnigiri/Refix"
