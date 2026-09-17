#!/usr/bin/env bash
# Claude Code PostToolUse 훅. 원고 마크다운을 고칠 때마다 Vale 을 그 파일에만 돌린다.
# error 가 있으면 종료코드 2 로 stderr 를 돌려보내 그 자리에서 고치게 한다.
# 그 아래 등급은 보여주기만 한다.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 0
command -v vale >/dev/null 2>&1 || exit 0

f=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -n "$f" ] || exit 0
case "$f" in
  *README.md|*README.ko.md|*/prereg/*.md) ;;
  *) exit 0 ;;
esac
[ -f "$f" ] || exit 0

out=$(vale --minAlertLevel=warning --output=line "$f" 2>/dev/null)
[ -n "$out" ] || exit 0

if printf '%s\n' "$out" | grep -q ':error:'; then
  printf '%s\n' "$out" | grep ':error:' >&2
  echo "Vale error — 위 줄을 고칠 것 (규칙: styles/PaperVoice/)" >&2
  exit 2
fi
printf '%s\n' "$out"
exit 0
