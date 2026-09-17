#!/bin/bash
# CME 이벤트 컨트랙트 체결 CSV 자동 다운로드.
# CME 는 스크립트(curl)를 403 으로 차단하지만 실제 브라우저는 통과한다.
# → GUI 세션의 기본 브라우저를 열어 받게 하고, ~/Downloads 에서 수거해 cme_drop/ 으로 옮긴다.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
DROP="$DIR/cme_drop"; mkdir -p "$DROP"
LOG(){ echo "$(date '+%m-%d %H:%M:%S') $*"; }

PAGE="https://www.cmegroup.com/markets/event-contracts.html"
CSV="https://www.cmegroup.com/reports/Event_Contract_Swaps_TS.csv"
DL="$HOME/Downloads"

# 받기 전 기존 잔재 정리 (재다운로드 시 "(1).csv" 로 쌓이는 것 방지)
rm -f "$DL"/Event_Contract_Swaps_TS*.csv 2>/dev/null

LOG "브라우저로 쿠키 수립 (Akamai 통과용)"
open "$PAGE"
sleep 25
LOG "CSV 다운로드 요청"
open "$CSV"

# 다운로드 완료 대기 (최대 90초)
GOT=""
for i in $(seq 1 30); do
  sleep 3
  F=$(ls -t "$DL"/Event_Contract_Swaps_TS*.csv 2>/dev/null | head -1)
  if [ -n "$F" ] && [ ! -f "$F.download" ] && [ "$(wc -c < "$F")" -gt 1000 ]; then GOT="$F"; break; fi
done

if [ -z "$GOT" ]; then
  LOG "실패: 90초 내 다운로드 없음 (Akamai 챌린지 또는 브라우저 설정 확인)"
  exit 1
fi
head -1 "$GOT" | grep -q "trade_date" || { LOG "실패: CSV 형식 아님 (챌린지 페이지 저장됨)"; rm -f "$GOT"; exit 1; }
DEST="$DROP/TS_$(date '+%Y%m%d_%H%M').csv"
mv "$GOT" "$DEST"
LOG "성공: $(wc -l < "$DEST" | tr -d ' ')행 → $DEST"

# 연 탭 정리 (실패해도 무시)
osascript -e 'tell application "Google Chrome" to close tabs of front window whose URL contains "cmegroup"' 2>/dev/null
osascript -e 'tell application "Safari" to close (tabs of front window whose URL contains "cmegroup")' 2>/dev/null
exit 0
