#!/usr/bin/env python3
"""job7 — 틱 바닥의 잔량 집중 측정.

기존 결과(job2)는 외가격 ask 의 '빈도'가 1틱에 몰린다는 것이었다(KXBTCD 57.6%).
이 잡은 '물량'을 잰다: 심외가격 책에서 1~2틱 가격대에 얹힌 잔량 대 그 외 가격대 잔량.
job6 이 받아둔 책 캐시를 재사용하고, BTC/금 일물 표본을 추가로 당긴다.
"""
import json, gzip, os, pathlib, re, statistics, sys, time, urllib.request, urllib.error
import datetime as dt

HERE=pathlib.Path(__file__).parent
OUT=HERE.parent/"results"; CACHE=HERE/"cache"/"books"; CACHE.mkdir(parents=True, exist_ok=True)
KEY=os.environ.get("PREDEXON_API_KEY") or ""
if not KEY:
    env=pathlib.Path.home()/"apyx/research/.env"
    if env.exists():
        for ln in env.read_text().splitlines():
            if ln.startswith("PREDEXON_API_KEY="): KEY=ln.split("=",1)[1].strip()
assert KEY, "PREDEXON_API_KEY 필요"
K="https://api.elections.kalshi.com/trade-api/v2"
P="https://api.predexon.com/v2/kalshi/orderbooks"
def log(*a): print(*a, file=sys.stderr, flush=True)
def get(url, key=True, tries=4):
    h={"User-Agent":"Mozilla/5.0"}
    if key: h["X-API-Key"]=KEY
    for i in range(tries):
        try: return json.loads(urllib.request.urlopen(urllib.request.Request(url,headers=h), timeout=45).read())
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(15*(i+1)); continue
            raise
        except Exception:                     # 타임아웃/일시 네트워크 오류도 재시도
            time.sleep(5*(i+1)); continue
    raise RuntimeError("재시도 소진")

def books_cached(tk, t1, t2):
    cf=CACHE/f"{tk}.json.gz"
    if cf.exists(): return json.load(gzip.open(cf,"rt"))
    snaps=[]; pk=None
    while True:
        u=f"{P}?ticker={tk}&start_time={t1}&end_time={t2}&limit=2000"
        if pk: u+=f"&pagination_key={pk}"
        d=get(u); s=d.get("snapshots",[]); snaps+=s
        pk=(d.get("pagination") or {}).get("pagination_key")
        time.sleep(1.05)
        if not pk or not s: break
    json.dump(snaps, gzip.open(cf,"wt"))
    return snaps

FLOOR=2   # 1~2센트 = 바닥 대역
def measure(snaps):
    """외가격 방향(호가 자체가 싼 쪽)의 잔량을 바닥/비바닥으로 나눈다.
    yes_asks 의 낮은 가격대 = yes 외가격 매도호가. yes_bids 의 98~99 = no 외가격 상당."""
    fl, non = [], []
    for s in snaps:
        for lv in s.get("yes_asks") or []:
            (fl if lv["price"]<=FLOOR else non).append(lv["size"])
        for lv in s.get("yes_bids") or []:
            (fl if lv["price"]>=100-FLOOR else non).append(lv["size"])
    return fl, non

def main():
    # 표본 1: job6 캐시 (매크로 사다리 전체)
    groups={}
    for cf in CACHE.glob("*.json.gz"):
        tk=cf.stem.replace(".json","")
        ser=tk.split("-")[0]
        snaps=json.load(gzip.open(cf,"rt"))
        fl,non=measure(snaps)
        g=groups.setdefault(ser,{"fl":[],"non":[]})
        g["fl"]+=fl; g["non"]+=non
    # 표본 2: 정산된 BTC/금 일물 20개씩 (마감 전 6시간)
    for ser in ("KXBTCD","KXGOLDD"):
        try: d=get(f"{K}/markets?series_ticker={ser}&status=settled&limit=100", key=False)
        except Exception as e: log(f"{ser}: {e}"); continue
        ms=sorted(d.get("markets",[]), key=lambda m:m.get("close_time",""))[-20:]
        g=groups.setdefault(ser,{"fl":[],"non":[]})
        for m in ms:
            ct=dt.datetime.fromisoformat(m["close_time"].replace("Z","+00:00"))
            t2=int(ct.timestamp()*1000)
            sn=books_cached(m["ticker"], t2-6*3600_000, t2)
            fl,non=measure(sn)
            g["fl"]+=fl; g["non"]+=non
            log(f"  {m['ticker']}: {len(sn)} snaps")
    res={}
    print(f"\n{'시리즈':<12}{'바닥관측':>9}{'바닥중앙(계약)':>13}{'비바닥중앙':>10}{'집중배율':>9}{'바닥물량비중':>11}")
    for ser,g in sorted(groups.items()):
        if not g["fl"] or not g["non"]: continue
        mf=statistics.median(g["fl"]); mn=statistics.median(g["non"])
        share=sum(g["fl"])/(sum(g["fl"])+sum(g["non"]))
        res[ser]={"n_floor":len(g["fl"]),"med_floor":mf,"med_non":mn,
                  "ratio":round(mf/mn,1) if mn else None,"floor_size_share":round(share,4)}
        print(f"{ser:<12}{len(g['fl']):>9,}{mf:>13,.0f}{mn:>10,.0f}"
              f"{(mf/mn if mn else 0):>8.1f}x{share*100:>10.1f}%")
    json.dump(res, open(OUT/"floor_size.json","w"))
    print(f"\n저장: {OUT/'floor_size.json'}")

if __name__=="__main__": main()
