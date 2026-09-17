#!/usr/bin/env python3
"""job6 — 정산된 매크로 이벤트의 소급 이벤트 스터디 (Predexon 깊이 히스토리).

각 이벤트에 대해: 사다리 함축값의 마감 전 수렴 궤적 + 호가 잔량 궤적 + 정산 캘리브레이션.
설계 노트(파일럿 실측): 칼시는 발표 ~5분 전에 거래를 닫는다. "발표 후 붕괴"는 존재하지
않으므로 측정 대상은 마감까지의 수렴과 정산 정합이다. 스냅샷은 변화 구동이라
행사가별 forward-fill 로 사다리를 복원한다.

필요: PREDEXON_API_KEY (predexon.com 무료. 오더북 히스토리는 무료·무제한, 1 req/s)
"""
import json, gzip, os, pathlib, re, statistics, sys, time, urllib.request, urllib.error
import datetime as dt

HERE=pathlib.Path(__file__).parent
OUT=HERE.parent/"results"; OUT.mkdir(exist_ok=True)
CACHE=HERE/"cache"/"books"; CACHE.mkdir(parents=True, exist_ok=True)
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

SERIES=("KXCPI","KXCPIYOY","KXU3","KXFED","KXFEDDECISION","KXPAYROLLS","KXGDP")
WIN_H=12          # 마감 전 관측 창(시간)
BUCKET=300_000    # 5분

def strike(tk):
    m=re.search(r"-T(-?\d+(?:\.\d+)?)$", tk)
    return float(m.group(1)) if m else None

def books(tk, t1, t2):
    """캐시 우선. Predexon 스냅샷 전체(페이지네이션)."""
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

def mid_of(s):
    yb=s.get("yes_bids") or []; ya=s.get("yes_asks") or []
    bb=max((x["price"] for x in yb), default=None)
    ba=min((x["price"] for x in ya), default=None)
    if bb is None or ba is None: return None
    return (bb+ba)/200

def depth_of(s):
    yb=s.get("yes_bids") or []; ya=s.get("yes_asks") or []
    return sum(x["price"]/100*x["size"] for x in yb)+sum((1-x["price"]/100)*x["size"] for x in ya)

def implied(pk_):
    ks=sorted(pk_); pts=[(k,pk_[k]) for k in ks]
    for (k1,p1),(k2,p2) in zip(pts,pts[1:]):
        if p1>=0.5>=p2 and p1!=p2: return k1+(p1-0.5)/(p1-p2)*(k2-k1)
    return None

# 발표 시각 레지스트리. 거래소 마감 시각(close_time)은 행정적 시각이지
# 실제 지표가 공표되는 시각이 아니다. Kalshi Research(2026) 가 시계를 실제 사건
# 발생 시각으로 재앵커링하자 스포츠 캘리브레이션이 개선되고 선거 편향이 사라짐을
# 보였고, 자기 논문에서도 일부 카테고리만 적용했다고 한계로 밝혔다.
# 거시지표는 발표 시각이 확정돼 있으므로 재앵커링이 정확히 가능하다.
_REG = None
def release_ts(ser, ev, fallback_close):
    """events.json 에 등록된 발표 시각을 우선 사용하고, 없으면 마감 시각으로 되돌린다.
    반환: (기준시각_ms, 출처문자열)"""
    global _REG
    if _REG is None:
        f = HERE.parent/"collect"/"events.json"
        try: _REG = json.load(open(f)).get("events", [])
        except Exception: _REG = []
    for e in _REG:
        if ser in (e.get("kalshi_series") or []):
            try:
                t = dt.datetime.fromisoformat(e["release_utc"].replace("Z","+00:00"))
                return int(t.timestamp()*1000), f"release:{e['id']}"
            except Exception: pass
    return fallback_close, "close_time"

def run_event(ser, ev, markets):
    close=max(m.get("close_time","") for m in markets)
    ct=dt.datetime.fromisoformat(close.replace("Z","+00:00"))
    t_close=int(ct.timestamp()*1000)
    t_anchor, anchor_src = release_ts(ser, ev, t_close)
    # 관측 창은 마감까지만 유효하다(칼시는 발표 전 거래를 닫는다).
    # 기준시각은 발표 시각으로 두되, 데이터 수집 상한은 마감 시각이다.
    t2=t_close; t1=t_anchor-WIN_H*3600_000
    lad={}   # ticker -> [(ts,mid,depth)]
    for m in markets:
        tk=m["ticker"]
        sn=books(tk, t1, t2)
        rows=[(s["timestamp"], mid_of(s), depth_of(s)) for s in sn]
        lad[tk]=[(a,b,c) for a,b,c in rows if b is not None]
        log(f"    {tk}: {len(lad[tk])} obs")
    numeric=all(strike(tk) is not None for tk in lad)
    # forward-fill 사다리
    series_rows=[]
    for b in range(t1, t2+BUCKET, BUCKET):
        pk_={}; dp=0
        for tk,rows in lad.items():
            last=None
            for ts,mid,d_ in rows:
                if ts<=b: last=(mid,d_)
                else: break
            if last:
                if strike(tk) is not None: pk_[strike(tk)]=last[0]
                dp+=last[1]
        v=implied(pk_) if (numeric and len(pk_)>=3) else None
        series_rows.append({"t":b,"rel_min":round((b-t_anchor)/60000,1),
                            "implied":v,"n":len(pk_),"depth":round(dp)})
    # 정산 캘리브레이션: 마지막 함축/mid vs 결과
    res={m["ticker"]: m.get("result") for m in markets}
    calib=[]
    for tk,rows in lad.items():
        if not rows: continue
        final=rows[-1][1]
        y=1.0 if res.get(tk)=="yes" else 0.0
        calib.append({"ticker":tk,"final_mid":round(final,4),"outcome":y})
    imp=[r["implied"] for r in series_rows if r["implied"] is not None]
    log(f"  [{ser}-{ev}] 버킷 {len(series_rows)}, 함축 {len(imp)}, "
        f"수렴 {imp[0]:+.4f}→{imp[-1]:+.4f}" if imp else f"  [{ser}-{ev}] 함축 없음(범주형)")
    return {"series":ser,"event":ev,"close":close,
            "anchor_ms":t_anchor,"anchor_src":anchor_src,
            "close_to_anchor_min":round((t_anchor-t_close)/60000,1),
            "n_markets":len(markets),
            "numeric_ladder":numeric,"trajectory":series_rows,"calibration":calib}

def main():
    out=[]
    for ser in SERIES:
        try: d=get(f"{K}/markets?series_ticker={ser}&status=settled&limit=500", key=False)
        except Exception as e: log(f"{ser}: {e}"); continue
        evs={}
        for m in d.get("markets",[]):
            evs.setdefault(m["ticker"].split("-")[1], []).append(m)
        for ev,ms in sorted(evs.items()):
            log(f"[{ser}-{ev}] 마켓 {len(ms)}개")
            try: out.append(run_event(ser, ev, ms))
            except Exception as e: log(f"  실패: {type(e).__name__} {str(e)[:100]}")
        time.sleep(0.5)
    json.dump(out, open(OUT/"event_studies.json","w"))
    # 요약 표
    print(f"\n{'이벤트':<22}{'마켓':>5}{'내재 시작→끝':>22}{'브라이어':>10}{'앵커':>22}{'마감→발표(분)':>12}")
    for e in out:
        imp=[r["implied"] for r in e["trajectory"] if r["implied"] is not None]
        tr=f"{imp[0]:+.4f}→{imp[-1]:+.4f}" if imp else "(범주형)"
        br=statistics.mean((c["final_mid"]-c["outcome"])**2 for c in e["calibration"]) if e["calibration"] else None
        print(f"{e['series']+'-'+e['event']:<22}{e['n_markets']:>5}{tr:>22}"
              f"{(br if br is None else round(br,4)):>10}{e.get('anchor_src','-'):>22}"
              f"{e.get('close_to_anchor_min',0):>12}")
    print(f"\n저장: {OUT/'event_studies.json'}")

if __name__=="__main__": main()
