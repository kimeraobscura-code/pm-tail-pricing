#!/usr/bin/env python3
"""JOB3 — 감쇠 곡선 c 적합 (탐색적, 사전등록2 의 규모 확장판. 사전등록 판정을 대체하지 않음).
사다리 함축가격 vs 기초자산의 rho² 를 지평별로 재서 rho²(h)=A/(1+c/h) 를 적합한다."""
import sys, json, math, collections, statistics
sys.path.insert(0, ".")
from lib import *

SERIES     = ["KXGOLDH","KXGOLDD","KXGOLDW","KXBTCD","KXETHD"]
MAX_EVENTS = int(os.environ.get("HSR_ATTEN_EVENTS", "40"))
HORIZ_MIN  = [5, 15, 30, 60, 120, 240, 480]
MIN_EFF    = 30
WORKERS    = int(os.environ.get("HSR_WORKERS", "8"))

def rho2(expo, cand, step_min):
    ts = sorted(set(expo) & set(cand))
    if len(ts) < step_min + 3: return None
    sub = ts[::step_min]
    r, dp = [], []
    for a, b in zip(sub, sub[1:]):
        if b - a > step_min*60*2: continue
        ga, gb = expo[a]["mid"], expo[b]["mid"]
        ca, cb = cand[a]["mid"], cand[b]["mid"]
        if ga == 0: continue
        r.append((gb-ga)/ga); dp.append(cb-ca)
    n = len(r)
    if n < 3: return None
    stale = sum(1 for x in dp if x == 0)/n
    if n*(1-stale) < MIN_EFF: return None
    mr, mp = sum(r)/n, sum(dp)/n
    vr = sum((x-mr)**2 for x in r)/(n-1); vp = sum((x-mp)**2 for x in dp)/(n-1)
    if vr <= 0 or vp <= 1e-12: return None
    cv = sum((x-mr)*(y-mp) for x, y in zip(r, dp))/(n-1)
    return (cv/math.sqrt(vr*vp))**2

def main():
    RES = {}
    for ser in SERIES:
        pts = collections.defaultdict(list); nev = 0; stales = []
        evs = k_settled_events(ser, MAX_EVENTS)
        # 열린 이벤트도 포함
        op = {}
        for m in k_markets(ser, "open"):
            op.setdefault(event_of(m["ticker"]), []).append(m)
        evs += [(e, op[e]) for e in sorted(op, reverse=True)[:3]]
        for e, arr in evs:
            try: o, c = event_window(arr)
            except ValueError: continue
            spot = spot_1m(ser, o-120, c+120)
            if len(spot) < 20: continue
            byt = {}
            def one(m):
                q = k_candles_1m(ser, m["ticker"], o, c)
                return (m["ticker"], q) if len(q) >= 20 else None
            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                for f in as_completed([ex.submit(one, m) for m in arr]):
                    try: r = f.result()
                    except Exception: r = None
                    if r: byt[r[0]] = r[1]
            if len(byt) < 4: continue
            for q in byt.values():
                ts = sorted(t for t in q if q[t].get("mid") is not None)
                d = [q[b]["mid"]-q[a]["mid"] for a, b in zip(ts, ts[1:])]
                if len(d) >= 20: stales.append(sum(1 for x in d if x == 0)/len(d))
            imp = ladder_implied(byt)
            if len(imp) < 30: continue
            nev += 1
            for hm in HORIZ_MIN:
                v = rho2(spot, imp, hm)
                if v is not None: pts[hm].append(v)
        curve = [(hm/60.0, statistics.median(v)) for hm, v in sorted(pts.items()) if v]
        fit = atten_fit(curve) if len(curve) >= 3 else None
        RES[ser] = {"events_used": nev, "curve": curve, "fit": fit,
                    "stale_raw": statistics.median(stales) if stales else None,
                    "n_per_horizon": {hm: len(v) for hm, v in sorted(pts.items())}}
        log(f"{ser}: 이벤트 {nev} | " +
            (f"c={fit['c']:.4f}h A={fit['A']:.3f} R2={fit['fit_r2']:.3f}" if fit else "적합실패"))
    RES["_note"] = "탐색적 규모확장. 사전등록2(sha256:29af48fc3a0ef75a) 판정을 대체하지 않는다."
    json.dump(RES, open(OUT/"job3_atten.json","w"), indent=1, ensure_ascii=False)
    log("JOB3 완료")

if __name__ == "__main__": main()
