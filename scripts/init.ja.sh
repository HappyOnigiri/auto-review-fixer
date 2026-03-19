#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
TEMPLATE_LOCAL="${SCRIPT_DIR:+$SCRIPT_DIR/templates/run-refix.ja.yml}"
TEMPLATE_URL="https://raw.githubusercontent.com/HappyOnigiri/Refix/main/scripts/templates/run-refix.ja.yml"
WORKFLOW_DIR=".github/workflows"
WORKFLOW_FILE="$WORKFLOW_DIR/run-refix.yml"

# フラグ解析
FORCE=false
CHANNEL=""    # stable (@v1) または main (@main)
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
    echo "中断: 非インタラクティブシェルです。上書きするには -f を付けて再実行してください。" >&2
    exit 1
  fi
  printf "⚠️  %s はすでに存在します。上書きしますか？ [y/N] " "$WORKFLOW_FILE" >/dev/tty
  read -r answer </dev/tty
  case "$answer" in
    [yY]*) ;;
    *) echo "中断しました。"; exit 1 ;;
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

# チャンネル選択 (stable @v1 / latest @main)
if [ -z "$CHANNEL" ] && [ -r /dev/tty ]; then
  echo "" >/dev/tty
  echo "リリースチャンネル:" >/dev/tty
  echo "  1) stable  — @v1   （推奨: v1 メジャーバージョンの最新安定リリースを追跡）" >/dev/tty
  echo "  2) latest  — @main （未リリースの開発中の変更を追跡）" >/dev/tty
  printf "選択 [1]: " >/dev/tty
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

# スケジュール選択
if [ -z "$SCHEDULE" ] && [ -r /dev/tty ]; then
  echo "" >/dev/tty
  echo "リカバリースケジュール:" >/dev/tty
  echo "  1) 1時間ごと        （推奨）" >/dev/tty
  echo "  2) 30分ごと" >/dev/tty
  echo "  3) 6時間ごと" >/dev/tty
  echo "  4) 無効化           （外部 cron を使用）" >/dev/tty
  printf "選択 [1]: " >/dev/tty
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

echo "✅ $WORKFLOW_FILE を作成しました"
echo ""
echo "次のステップ:"
echo "  1. シークレットを追加する: GH_TOKEN, CLAUDE_CODE_OAUTH_TOKEN"
echo "  2. （任意）.refix.yaml を追加するか、vars.REFIX_CONFIG_YAML を設定する"
echo "  3. 詳細: https://github.com/HappyOnigiri/Refix"
