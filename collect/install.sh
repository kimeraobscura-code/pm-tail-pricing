#!/bin/bash
# hs-collect 설치. 맥미니에서 한 번만 실행한다.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
echo "1) 절전 끄기 (sudo 필요). 안 하면 밤에 수집이 끊긴다."
sudo pmset -a sleep 0 disablesleep 1 autorestart 1 powernap 0
echo "2) launchd 등록"
sed "s|__DIR__|$DIR|g" "$DIR/com.hscollect.plist" > ~/Library/LaunchAgents/com.hscollect.plist
launchctl unload ~/Library/LaunchAgents/com.hscollect.plist 2>/dev/null || true
launchctl load  ~/Library/LaunchAgents/com.hscollect.plist
echo "3) 확인"
sleep 5
launchctl list | grep hscollect || echo "  (등록 실패)"
python3 "$DIR/collect.py" --status
