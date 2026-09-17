#!/bin/bash
# hs-research 전체 실행. 재실행 안전 (완료된 항목은 건너뜀).
# 사용:  nohup ./run_all.sh > logs/run_all.log 2>&1 &
cd "$(dirname "$0")"
mkdir -p logs out cache
export PYTHONUNBUFFERED=1
run() {
  echo "── $1 시작: $(date '+%m-%d %H:%M:%S')"
  caffeinate -is python3 "$1" >> "logs/${1%.py}.log" 2>&1 \
    && echo "── $1 완료" || echo "── $1 실패 (logs/${1%.py}.log 확인)"
}
run job5_trades_kalshi.py     # 소멸성(67일 롤링) → 최우선
run job1_flb_kalshi.py
run job2_smile.py
run job3_atten.py
run job4_flb_polymarket.py    # VPN 필요. 없으면 안내만 남기고 통과
python3 report.py
echo "전체 완료: $(date '+%m-%d %H:%M:%S')  →  out/REPORT.md"
