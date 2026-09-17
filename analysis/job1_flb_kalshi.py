#!/usr/bin/env python3
"""JOB1 — 칼시 FLB 대규모 (호가 스냅샷 기반).
사다리 시리즈를 자동 발견하고, 정산 이벤트의 계약별 수명 25/50/75% 호가 중간값을
함축확률로 놓아 실현 결과와 대조한다. 유의성은 계약 단위 부트스트랩.
탐색적 분석 (사전등록 아님) — 기존 3시리즈 4,017관측의 확장판."""
import sys, json, collections, statistics
sys.path.insert(0, ".")
from lib import *

CORE       = ["KXGOLDD","KXGOLDW","KXGOLDH","KXBTCD","KXETHD"]
MAX_EVENTS = int(os.environ.get("HSR_MAX_EVENTS", "30"))
MAX_SERIES = int(os.environ.get("HSR_MAX_SERIES", "12"))
WORKERS    = int(os.environ.get("HSR_WORKERS", "8"))
OUTF       = OUT/"job1_flb_kalshi.jsonl"

def discover_ladders():
    """전 마켓 settled 스캔에서 사다리형 시리즈를 찾는다 (-T숫자 스트라이크가 이벤트당 5개 이상)."""
    def fetch():
        return [{"ticker": m["ticker"]} for m in k_markets(None, "settled", max_pages=15)]
    ms = cached("settled_global_scan_v1", fetch)
    by_ser = collections.defaultdict(lambda: collections.defaultdict(set))
    for m in ms:
        tk = m["ticker"]; k = strike(tk)
        if k is None: continue
        by_ser[tk.split("-")[0]][event_of(tk)].add(k)
    out = []
    for ser, evs in by_ser.items():
        med = statistics.median(len(v) for v in evs.values())
        if med >= 5: out.append((ser, sum(len(v) for v in evs.values())))
    out.sort(key=lambda x: -x[1])
    return [s for s, _ in out]

def main():
    env_ser = os.environ.get("HSR_SERIES")
    if env_ser:
        series = env_ser.split(",")
    else:
        disc = discover_ladders()
        series = list(dict.fromkeys(CORE + disc))[:MAX_SERIES]
        json.dump(disc, open(OUT/"job1_series_discovered.json","w"))
    log("대상 시리즈:", series)
    done = load_done(OUTF, "ticker")
    log(f"재개: 이미 처리된 계약 {len(done)}개")
    for ser in series:
        evs = k_settled_events(ser, MAX_EVENTS)
        log(f"{ser}: 정산 이벤트 {len(evs)}개")
        for e, arr in evs:
            todo = [m for m in arr if m["ticker"] not in done and m.get("result") in ("yes","no")]
            if not todo: continue
            try: o, c = event_window(arr)
            except ValueError: continue
            def one(m):
                q = k_candles_1m(ser, m["ticker"], o, c)
                if len(q) < 30: return None
                ts = sorted(t for t in q if q[t].get("mid") is not None)
                if len(ts) < 30: return None
                snaps = {f: q[ts[int(len(ts)*f)]]["mid"] for f in (0.25, 0.5, 0.75)}
                return {"series": ser, "event": e, "ticker": m["ticker"],
                        "K": strike(m["ticker"]), "result": 1 if m["result"]=="yes" else 0,
                        "snaps": snaps, "vol": float(m.get("volume_fp") or 0)}
            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                for f in as_completed([ex.submit(one, m) for m in todo]):
                    try: r = f.result()
                    except Exception: r = None
                    if r: append_jsonl(OUTF, r)
        log(f"{ser} 완료")
    # ── 요약 ──
    obs = []; by_c = collections.defaultdict(list)
    for ln in open(OUTF, errors="replace"):
        try: r = json.loads(ln)
        except Exception: continue
        for p in r["snaps"].values():
            obs.append((p, r["result"])); by_c[r["ticker"]].append((p, r["result"]))
    summ = {"n_obs": len(obs), "n_contracts": len(by_c), "bins": flb_bins(obs),
            "low":  cluster_boot(by_c, (0, 0.15)),
            "high": cluster_boot(by_c, (0.85, 1.0)),
            "note": "호가 중간값 기반. 계약당 3스냅샷. CI 는 계약 단위 부트스트랩(B=2000)."}
    json.dump(summ, open(OUT/"job1_summary.json","w"), indent=1, ensure_ascii=False)
    log("JOB1 완료:", json.dumps({k: summ[k] for k in ("n_obs","n_contracts","low","high")},
                                  ensure_ascii=False)[:300])

if __name__ == "__main__": main()
