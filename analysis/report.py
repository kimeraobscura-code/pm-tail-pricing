#!/usr/bin/env python3
"""잡 요약들을 하나의 REPORT.md 로 모은다."""
import json, pathlib, time
OUT = pathlib.Path(__file__).parent/"out"
L = [f"# hs-research 결과 요약", f"생성: {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
def sec(title, path):
    p = OUT/path
    L.append(f"## {title}")
    if not p.exists(): L.append("_(미실행 또는 실패)_\n"); return
    L.append("```json"); L.append(json.dumps(json.load(open(p)), indent=1, ensure_ascii=False)[:6000])
    L.append("```\n")
sec("JOB5 — 칼시 체결 FLB (거래량가중)", "job5_summary.json")
sec("JOB1 — 칼시 호가 FLB (대규모)",   "job1_summary.json")
sec("JOB2 — 역공학·스마일·틱 고정",    "job2_summary.json")
sec("JOB3 — 감쇠 상수 c (탐색적)",      "job3_atten.json")
sec("JOB4 — 폴리마켓 FLB (전수)",       "job4_summary.json")
open(OUT/"REPORT.md","w").write("\n".join(L))
print(f"작성됨: {OUT/'REPORT.md'}")
