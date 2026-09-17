#!/usr/bin/env python3
"""JOB5 — 칼시 체결 백필 (소멸성: 약 67일 롤링 → 가장 먼저 돌린다)
+ 체결가 기반 거래량가중 FLB (호가 스냅샷과 독립적인 재현).
대상: 사다리 시리즈의 정산 마켓 전부. 티커별 조회라 파이어호스를 받지 않는다."""
import sys, json, gzip, collections, statistics
sys.path.insert(0, ".")
from lib import *

CORE    = ["KXGOLDD","KXGOLDW","KXGOLDH","KXBTCD","KXETHD","KXCPI","KXFED"]
WORKERS = int(os.environ.get("HSR_WORKERS", "8"))
TRD     = OUT/"trades"; TRD.mkdir(exist_ok=True)
DONEF   = OUT/"job5_done.jsonl"

def main():
    series = os.environ.get("HSR_TRADE_SERIES", ",".join(CORE)).split(",")
    done = load_done(DONEF, "ticker")
    log(f"대상 {series} | 재개: {len(done)}개 완료됨")
    results = {}          # ticker -> result01 (FLB 용)
    tasks = []
    for ser in series:
        ms = k_markets(ser, "settled")
        for m in ms:
            if m.get("result") in ("yes","no"):
                results[m["ticker"]] = 1 if m["result"] == "yes" else 0
        tasks += [(ser, m["ticker"]) for m in ms if m["ticker"] not in done]
        log(f"{ser}: 정산 마켓 {len(ms)}개")
    log(f"체결 백필 대상 {len(tasks)}개 티커")
    sinks = {}
    def sink(ser):
        if ser not in sinks:
            sinks[ser] = gzip.open(TRD/f"{ser}.jsonl.gz", "at", encoding="utf-8")
        return sinks[ser]
    def one(job):
        ser, tk = job
        tr = k_trades(tk, max_pages=5)
        return ser, tk, tr
    n = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for f in as_completed([ex.submit(one, j) for j in tasks]):
            n += 1
            try: ser, tk, tr = f.result()
            except Exception: continue
            for t in tr:
                sink(ser).write(json.dumps(t, separators=(",", ":")) + "\n")
            append_jsonl(DONEF, {"ticker": tk, "n": len(tr)})
            if n % 200 == 0:
                for s in sinks.values(): s.flush()
                log(f"  {n}/{len(tasks)}")
    for s in sinks.values(): s.close()
    # ── 체결가 기반 거래량가중 FLB ──
    obs, by_c = [], collections.defaultdict(list)
    for ser in series:
        p = TRD/f"{ser}.jsonl.gz"
        if not p.exists(): continue
        for ln in gzip.open(p, "rt", errors="replace"):
            try: t = json.loads(ln)
            except Exception: continue
            tk = t.get("ticker"); y = results.get(tk)
            if y is None: continue
            try:
                px = float(t["yes_price_dollars"]); cnt = float(t.get("count_fp") or 1)
            except Exception: continue
            if not (0 < px < 1): continue
            w = min(int(cnt), 50)               # 거래량 가중 (초대형 체결 상한)
            obs += [(px, y)]*max(1, w//10 or 1)
            by_c[tk].append((px, y))
    summ = {"n_trades_obs": len(obs), "n_contracts": len(by_c), "bins": flb_bins(obs),
            "low":  cluster_boot(by_c, (0, 0.15)),
            "high": cluster_boot(by_c, (0.85, 1.0)),
            "note": "체결가 기반(거래량 근사가중, 체결당 상한). CI 는 계약 단위 부트스트랩."}
    json.dump(summ, open(OUT/"job5_summary.json","w"), indent=1, ensure_ascii=False)
    log("JOB5 완료:", json.dumps({k: summ[k] for k in ("n_trades_obs","low","high")}, ensure_ascii=False)[:300])

if __name__ == "__main__": main()
