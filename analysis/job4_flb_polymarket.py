#!/usr/bin/env python3
"""JOB4 — 폴리마켓 FLB 전수 (정산 이진마켓 ~1,400개, 체결 걷기, 계약 단위 부트스트랩).
※ 한국에서 *.polymarket.com 은 451 지역차단 → 이 잡은 VPN(미국 출구)이 필요하다.
451 이면 명확히 알리고 정상 종료한다 (체인을 깨지 않는다)."""
import sys, json, collections, statistics
sys.path.insert(0, ".")
from lib import *

MIN_VOL   = float(os.environ.get("HSR_PM_MINVOL", "10000"))
MAX_PAGES = int(os.environ.get("HSR_PM_PAGES", "12"))
WORKERS   = int(os.environ.get("HSR_WORKERS", "8"))
OUTF      = OUT/"job4_flb_pm.jsonl"

def main():
    try:
        jget("https://gamma-api.polymarket.com/markets?limit=1", timeout=20)
    except Exception as e:
        log(f"폴리마켓 접근 불가 ({e}). VPN(미국 출구)을 켠 뒤 이 잡만 다시 실행하라:")
        log("  python3 job4_flb_polymarket.py")
        return
    evs = []
    for tag in ["crypto","economics","politics","business","tech"]:
        for off in range(0, 500, 100):
            try: b = jget(f"https://gamma-api.polymarket.com/events?closed=true&limit=100"
                          f"&offset={off}&tag_slug={tag}")
            except Exception: break
            if not b: break
            evs += b
    seen, mkts = set(), []
    for e in evs:
        if e.get("slug") in seen: continue
        seen.add(e.get("slug"))
        for m in e.get("markets") or []:
            try:
                op = json.loads(m.get("outcomePrices") or "[]")
                if len(op) != 2 or {op[0], op[1]} != {"1", "0"}: continue
                if float(m.get("volumeNum") or 0) < MIN_VOL: continue
                mkts.append({"cid": m.get("conditionId"), "q": (m.get("question") or "")[:60],
                             "tick": m.get("orderPriceMinTickSize"),
                             "result": 1 if op[0] == "1" else 0,
                             "vol": float(m.get("volumeNum") or 0)})
            except Exception: continue
    log(f"정산 이진마켓 {len(mkts)}개 (vol>=${MIN_VOL:,.0f})")
    done = load_done(OUTF, "cid")
    todo = [m for m in mkts if m["cid"] not in done]
    log(f"재개: 남은 마켓 {len(todo)}개")
    def snaps(m):
        tr, end = [], None
        for _ in range(MAX_PAGES):
            u = (f"https://data-api.polymarket.com/trades?market={m['cid']}&limit=500"
                 + (f"&end={end}" if end else ""))
            try: b = jget(u)
            except Exception: break
            if not b: break
            tr += b; end = min(t["timestamp"] for t in b)
            if len(b) < 500: break
        if len(tr) < 30: return None
        tr.sort(key=lambda t: t["timestamp"])
        ps = []
        for f in (0.25, 0.5, 0.75):
            t = tr[int(len(tr)*f)]
            p = float(t["price"])
            if (t.get("outcome") or "").lower() == "no": p = 1-p
            ps.append(round(p, 4))
        return {**m, "snaps": ps, "n_trades": len(tr)}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        n = 0
        for f in as_completed([ex.submit(snaps, m) for m in todo]):
            n += 1
            try: r = f.result()
            except Exception: r = None
            if r: append_jsonl(OUTF, r)
            if n % 100 == 0: log(f"  {n}/{len(todo)}")
    obs, by_c = [], collections.defaultdict(list)
    for ln in open(OUTF, errors="replace"):
        try: r = json.loads(ln)
        except Exception: continue
        for p in r["snaps"]:
            obs.append((p, r["result"])); by_c[r["cid"]].append((p, r["result"]))
    summ = {"n_obs": len(obs), "n_contracts": len(by_c), "bins": flb_bins(obs),
            "low":  cluster_boot(by_c, (0, 0.15)),
            "high": cluster_boot(by_c, (0.85, 1.0)),
            "note": "체결가 스냅샷(수명 25/50/75%). 틱 0.001 지배 표본. VPN 경유 수집."}
    json.dump(summ, open(OUT/"job4_summary.json","w"), indent=1, ensure_ascii=False)
    log("JOB4 완료:", json.dumps({k: summ[k] for k in ("n_obs","low","high")}, ensure_ascii=False)[:300])

if __name__ == "__main__": main()
