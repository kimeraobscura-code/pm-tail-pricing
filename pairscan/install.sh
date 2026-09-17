#!/bin/bash
# pair-scan 설치 (맥미니). GUI 로그인 세션 필요 (cme_fetch 가 브라우저를 연다).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
for L in pairscan cmefetch; do
  sed "s|__DIR__|$DIR|g" "$DIR/com.$L.plist" > ~/Library/LaunchAgents/com.$L.plist
  launchctl unload ~/Library/LaunchAgents/com.$L.plist 2>/dev/null || true
  launchctl load  ~/Library/LaunchAgents/com.$L.plist
done
echo "등록 확인:"; launchctl list | grep -E "pairscan|cmefetch"
echo "첫 스캔 즉시 실행:"; python3 "$DIR/pairscan.py"
