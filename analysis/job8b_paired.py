#!/usr/bin/env python3
"""job8b — 호가 편의 대 체결 편의: 완전 짝지은 검정 + 분해 (랩탑 실행용).

배경: job1(호가)과 job5(체결)는 계약군이 달라 두 추정치를 비교할 수 없었고,
신뢰구간도 겹쳤다(롱샷 [-1.39,-0.74] 공유). 칼시 1분 캔들은 같은 분에
bid/ask(호가)와 price(마지막 체결가)를 함께 주므로, 같은 계약·같은 시각에서
두 다리를 완전히 짝지을 수 있다.

분해 (같은 계약·같은 스냅샷 집합 위에서):
  Δ = Bias_q(B) - Bias_t(B)
    = E[l - m | m∈B]                          (위치 항 — Y 소거. 체결이 중간가 대비
                                               어디서 찍히는지의 순수 미시구조)
    + E[Y-l | m∈B] - E[Y-l | l∈B]             (선택 항 — 조건화 변수 차이. 평균회귀)
여기서 m=호가 중간값, l=마지막 체결가, Y=실현(0/1), B=가격 구간.

사전 판정 규칙 (실행 전 고정, job8 과 동일):
  두 구간(저가·고가) 모두에서 Δ 의 95% CI 가 0 을 포함하면
  "호가 편의가 체결보다 크다"는 방향성 주장을 철회한다.

체결가 정의: 스냅샷 분 기준 과거 120분 내 거래량>0 인 마지막 분봉의 close.
그보다 오래된 체결은 스냅샷에서 제외(양 다리 모두) — 정체된 가격을 체결로
취급하면 위치 항이 0 쪽으로 희석된다. 정체 시간은 기록한다.
"""
import json, random, statistics, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from lib import k_settled_events, k_candles_1m, tsec, OUT

CFG = [   # (시리즈, 이벤트 상한)
    ("KXGOLDD", 45), ("KXBTCD", 6), ("KXETHD", 6),
    ("KXCPI", 4), ("KXCPIYOY", 4), ("KXU3", 4), ("KXPAYROLLS", 4),
]
FRACS = (0.25, 0.50, 0.75)
STALE_MAX = 120          # 분
LOW, HIGH = (0.0, 0.15), (0.85, 1.0)
B = 2000

def log(*a): print(*a, file=sys.stderr, flush=True)

def snapshots(series, m):
    """계약 하나 → [(mid, last, stale_min), ...] (스냅샷별). 캔들 1회 호출."""
    try:
        o = tsec(m["open_time"]); c = tsec(m["close_time"])
    except Exception:
        return []
    if not o or not c or c - o < 3600:
        return []
    bars = k_candles_1m(series, m["ticker"], o, c)
    if not bars: return []
    ts = sorted(bars)
    out = []
    for f in FRACS:
        t0 = int(o + (c - o) * f) // 60 * 60
        # t0 이전 30분 내 mid 있는 분봉
        mid = None
        for t in range(t0, t0 - 1800, -60):
            b = bars.get(t)
            if b and "mid" in b:
                mid = b["mid"]; tmid = t; break
        if mid is None: continue
        # tmid 이전 STALE_MAX 분 내 거래량>0 인 마지막 분봉의 last
        last = stale = None
        for t in range(tmid, tmid - STALE_MAX * 60, -60):
            b = bars.get(t)
            if b and b.get("vol") and b.get("last") and 0 < b["last"] < 1:
                last = b["last"]; stale = (tmid - t) // 60; break
        if last is None: continue
        out.append((mid, last, stale))
    return out

def main():
    t0 = time.time()
    contracts = {}   # cid -> {"y":0/1, "rows":[(m,l,stale)]}
    jobs = []
    for ser, cap in CFG:
        try: evs = k_settled_events(ser, cap)
        except Exception as e:
            log(f"{ser}: {type(e).__name__} {e}"); continue
        n = sum(len(ms) for _, ms in evs)
        log(f"{ser}: 이벤트 {len(evs)}개 / 계약 {n}개")
        for _, ms in evs:
            for m in ms:
                if m.get("result") in ("yes", "no"):
                    jobs.append((ser, m))
    log(f"대상 계약 {len(jobs):,}개 — 캔들 수집 시작")
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(snapshots, ser, m): (ser, m) for ser, m in jobs}
        for fu in as_completed(futs):
            ser, m = futs[fu]
            done += 1
            if done % 300 == 0: log(f"  {done}/{len(jobs)}  ({time.time()-t0:.0f}s)")
            try: rows = fu.result()
            except Exception: continue
            if rows:
                contracts[m["ticker"]] = {
                    "y": 1.0 if m["result"] == "yes" else 0.0, "rows": rows, "ser": ser}
    log(f"스냅샷 확보 계약 {len(contracts):,}개  ({time.time()-t0:.0f}s)")
    stales = [s for c in contracts.values() for _, _, s in c["rows"]]
    log(f"체결 정체시간: 중앙 {statistics.median(stales):.0f}분 / p90 "
        f"{sorted(stales)[int(len(stales)*0.9)]}분")

    def terms(cids, band):
        """풀링 통계: (Δ, 위치항, 선택항, bias_q, bias_t, n_q, n_t)"""
        lo, hi = band
        qy, qm, lm_, ty, tl = [], [], [], [], []
        for cid in cids:
            c = contracts[cid]
            for m_, l_, _ in c["rows"]:
                if lo <= m_ < hi:
                    qy.append(c["y"]); qm.append(m_); lm_.append(l_ - m_)
                if lo <= l_ < hi:
                    ty.append(c["y"]); tl.append(l_)
        if len(qm) < 10 or len(tl) < 10: return None
        bq = statistics.mean(qy) - statistics.mean(qm)
        bt = statistics.mean(ty) - statistics.mean(tl)
        loc = statistics.mean(lm_)
        # 선택항 = Δ - (-loc)?  유도: bq - bt = E[Y-m|m] - E[Y-l|l]
        #   = E[l-m|m] + E[Y-l|m] - E[Y-l|l]  → 선택항 = (bq - bt) - loc... 부호 주의:
        #   E[Y-m|m] = E[Y-l|m] + E[l-m|m]  ⇒ Δ = loc + (E[Y-l|m∈B] - E[Y-l|l∈B])
        yl_m = statistics.mean(y - l for y, l in
                               [(contracts[cid]["y"], l_) for cid in cids
                                for m_, l_, _ in contracts[cid]["rows"]
                                if lo <= m_ < hi]) if qm else 0.0
        sel = (bq - bt) - loc
        return (bq - bt, loc, sel, bq, bt, len(qm), len(tl))

    cids = sorted(contracts)
    rnd = random.Random(11)
    result = {}
    for name, band in (("저가(롱샷)", LOW), ("고가(즐겨찾기)", HIGH)):
        pt = terms(cids, band)
        if pt is None:
            print(f"{name}: 표본 부족"); continue
        boots = {"d": [], "loc": [], "sel": []}
        for _ in range(B):
            samp = [cids[rnd.randrange(len(cids))] for _ in range(len(cids))]
            r = terms(samp, band)
            if r: boots["d"].append(r[0]); boots["loc"].append(r[1]); boots["sel"].append(r[2])
        def ci(v):
            v = sorted(v); return v[int(0.025*len(v))], v[int(0.975*len(v))]
        d_lo, d_hi = ci(boots["d"]); l_lo, l_hi = ci(boots["loc"]); s_lo, s_hi = ci(boots["sel"])
        sig = "유의" if (d_lo > 0 or d_hi < 0) else "비유의(0 포함)"
        result[name] = dict(delta=pt[0], delta_ci=[d_lo, d_hi],
                            loc=pt[1], loc_ci=[l_lo, l_hi],
                            sel=pt[2], sel_ci=[s_lo, s_hi],
                            bias_q=pt[3], bias_t=pt[4], n_q=pt[5], n_t=pt[6], sig=sig)
        print(f"\n■ {name}  (호가 관측 {pt[5]:,} / 체결 관측 {pt[6]:,})")
        print(f"  Bias_호가 {pt[3]*100:+.2f}%p   Bias_체결 {pt[4]*100:+.2f}%p")
        print(f"  Δ(차이)   {pt[0]*100:+.2f}%p  [{d_lo*100:+.2f}, {d_hi*100:+.2f}]  → {sig}")
        print(f"    위치 항  {pt[1]*100:+.2f}%p  [{l_lo*100:+.2f}, {l_hi*100:+.2f}]"
              f"   (체결이 중간가 대비 찍히는 위치, Y 소거)")
        print(f"    선택 항  {pt[2]*100:+.2f}%p  [{s_lo*100:+.2f}, {s_hi*100:+.2f}]"
              f"   (조건화 변수 차이)")
    json.dump({"contracts": len(contracts), "results": result},
              open(OUT / "job8b_paired.json", "w"), default=float)
    print(f"\n계약 {len(contracts):,}개 | 저장: out/job8b_paired.json | {time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()
