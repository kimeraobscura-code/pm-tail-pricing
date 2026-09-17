#!/usr/bin/env python3
"""JOB2 — 가격모형 역공학 + 스마일 + 틱 고정 히스토그램.
Φ⁻¹(p)=a+b·ln(S/K)/√T 를 계약별로 적합해 함축 σ 를 뽑는다 (등가격 R²=0.967 검증됨).
외가격 ask 분포로 틱 하한 고정을 정량화한다 (BTC 57.6% @0.01 재확인·확장)."""
import sys, json, math, collections, statistics
sys.path.insert(0, ".")
from lib import *

MAX_EVENTS = {"KXGOLDD": 20, "KXGOLDW": 10, "KXGOLDH": 40, "KXBTCD": 40, "KXETHD": 40}
MIN_OBS    = {"KXGOLDD": 60, "KXGOLDW": 60, "KXGOLDH": 25, "KXBTCD": 25, "KXETHD": 25}
WORKERS    = int(os.environ.get("HSR_WORKERS", "8"))
OUTF       = OUT/"job2_fits.jsonl"

def realized_sigma(series):
    """실현 변동성(연율). binance.vision 1h, 175일."""
    sym = {"KXGOLDD":"PAXGUSDT","KXGOLDW":"PAXGUSDT","KXGOLDH":"PAXGUSDT",
           "KXBTCD":"BTCUSDT","KXETHD":"ETHUSDT"}[series]
    key = f"rvol_{sym}"
    def fetch():
        now = int(time.time()); out = []
        cur = (now-175*86400)*1000
        while cur < now*1000:
            rows = jget(f"https://data-api.binance.vision/api/v3/klines?symbol={sym}"
                        f"&interval=1h&startTime={cur}&limit=1000")
            if not rows: break
            out += [float(r[4]) for r in rows]
            nxt = int(rows[-1][0])+3600000
            if nxt <= cur: break
            cur = nxt
        return out
    p = cached(key, fetch)
    r = [(b-a)/a for a, b in zip(p, p[1:])]
    return statistics.pstdev(r)*math.sqrt(24*365)

def main():
    done = load_done(OUTF, "ticker")
    pin  = collections.defaultdict(collections.Counter)   # 시리즈별 외가격 ask 분포
    for ser in MAX_EVENTS:
        for e, arr in k_settled_events(ser, MAX_EVENTS[ser]):
            todo = [m for m in arr if m["ticker"] not in done]
            try: o, c = event_window(arr)
            except ValueError: continue
            spot = spot_1m(ser, o-120, c+120)
            if len(spot) < 20: continue
            def one(m):
                K_ = strike(m["ticker"])
                if K_ is None: return None
                q = k_candles_1m(ser, m["ticker"], o, c)
                for bar in q.values():                     # 틱 고정 히스토그램 재료
                    a = bar.get("ask")
                    if a and a <= 0.15: pin[ser][round(a, 2)] += 1
                f = fit_contract(K_, c, q, spot, MIN_OBS[ser])
                if not f: return None
                return {"series": ser, "event": e, "ticker": m["ticker"], "K": K_,
                        "sigma": f["sigma"], "r2": f["r2"], "n": f["n"], "mid": f["mid"]}
            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                for fu in as_completed([ex.submit(one, m) for m in todo]):
                    try: r = fu.result()
                    except Exception: r = None
                    if r: append_jsonl(OUTF, r)
        log(f"{ser} 적합 완료")
    # ── 요약 ──
    rows = [json.loads(ln) for ln in open(OUTF, errors="replace")]
    summ = {}
    for ser in MAX_EVENTS:
        rs = [r for r in rows if r["series"] == ser]
        if not rs: continue
        atm = [r for r in rs if .35 <= r["mid"] < .65]
        otm = [r for r in rs if r["mid"] < .15 or r["mid"] >= .85]
        s = {"n_contracts": len(rs), "realized_sigma": realized_sigma(ser)}
        if atm: s.update(atm_sigma=statistics.median(r["sigma"] for r in atm),
                         atm_r2=statistics.median(r["r2"] for r in atm))
        if otm: s["otm_sigma"] = statistics.median(r["sigma"] for r in otm)
        tot = sum(pin[ser].values())
        if tot: s["ask_hist_le15"] = {f"{p:.2f}": {"n": n, "pct": round(n/tot*100, 1)}
                                       for p, n in sorted(pin[ser].items())[:10]}
        summ[ser] = s
    json.dump(summ, open(OUT/"job2_summary.json","w"), indent=1, ensure_ascii=False)
    log("JOB2 완료")

if __name__ == "__main__": main()
