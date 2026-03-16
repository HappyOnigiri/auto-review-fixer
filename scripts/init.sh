#!/usr/bin/env bash
set -euo pipefail

WORKFLOW_DIR=".github/workflows"
WORKFLOW_FILE="$WORKFLOW_DIR/run-refix.yml"

# 上書きチェック
if [ -f "$WORKFLOW_FILE" ] && [ "${1:-}" != "-f" ]; then
  echo "⚠️  $WORKFLOW_FILE already exists. Use '-f' to overwrite."
  exit 1
fi

mkdir -p "$WORKFLOW_DIR"

cat > "$WORKFLOW_FILE" << 'WORKFLOW_EOF'
# ==========================================
# Refix — Auto-fix CodeRabbit review comments
# ==========================================
#
# Setup:
#
# 1. Add the following GitHub Secrets:
#    - GH_TOKEN: GitHub token with write access to this repository
#    - CLAUDE_CODE_OAUTH_TOKEN: Claude Code OAuth token
#
# 2. Configuration (optional):
#    You can customize Refix behavior using either:
#
#    a) Place a `.refix.yaml` file at the repository root
#    b) Set YAML content in GitHub Repository Variables as `REFIX_CONFIG_YAML`
#
#    When both are present, the repository variable (vars.REFIX_CONFIG_YAML)
#    takes precedence over the `.refix.yaml` file.
#
#    For available options, see:
#    - README: https://github.com/HappyOnigiri/Refix
#    - Sample config: https://github.com/HappyOnigiri/Refix/blob/main/.refix.sample.yaml
#
name: Run Refix

on:
  check_suite:
    types: [completed]
  pull_request:
    types: [opened, synchronize, reopened, labeled]
  issue_comment:
    types: [created, edited]
  workflow_dispatch:
    inputs:
      pr-number:
        description: "PR number to process"
        required: true
        type: number

concurrency:
  group: run-refix-pr-${{ github.event.pull_request.number || github.event.check_suite.pull_requests[0].number || github.event.issue.number || inputs.pr-number || 'dispatch' }}
  cancel-in-progress: false

permissions:
  contents: read

jobs:
  run-refix:
    if: |
      (github.event_name != 'check_suite' || github.event.check_suite.pull_requests[0].number != 0) &&
      !(github.event_name == 'pull_request' && github.event.pull_request.state == 'closed')
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4

      - name: Run Refix
        uses: HappyOnigiri/Refix@main
        with:
          gh-token: ${{ secrets.GH_TOKEN }}
          claude-code-oauth-token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          config-yaml: ${{ vars.REFIX_CONFIG_YAML }}
WORKFLOW_EOF

echo "✅ Created $WORKFLOW_FILE"
echo ""
echo "Next steps:"
echo "  1. Add secrets: GH_TOKEN, CLAUDE_CODE_OAUTH_TOKEN"
echo "  2. (Optional) Add .refix.yaml or set vars.REFIX_CONFIG_YAML"
echo "  3. See: https://github.com/HappyOnigiri/Refix"
