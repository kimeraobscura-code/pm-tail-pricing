#!/usr/bin/env python3
"""hs-research 공용 라이브러리 (표준 라이브러리만 사용).

이 세션에서 실측으로 확인된 함정들이 코드에 박혀 있다:
- Cloudflare 가 파이썬 기본 UA 를 차단한다 → 브라우저 UA 필수
- api.binance.com 은 한국에서 451 → data-api.binance.vision 사용
- 칼시 BTC/ETH 정산은 CF Benchmarks BRTI/ERTI → 코인베이스를 대용치로 사용 (명시)
- 칼시 1분 캔들은 요청 창이 넓으면 400 → 60시간 조각으로 분할
- 칼시 체결 이력은 약 67일 롤링 → 백필은 소멸성 작업
- gamma 이벤트 페이지네이션은 높은 offset 에서 422 → 예외 시 그 페이지에서 중단
- data-api 는 offset 이 1만에서 막힘 → `end` 파라미터로 뒤로 걷기
- 1/rho² 선형화는 작은 rho² 에서 폭증 → 비선형 격자 적합, A<=1 제약
"""
import gzip, json, math, os, pathlib, re, statistics, sys, time, random
import urllib.error, urllib.parse, urllib.request
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT  = pathlib.Path(__file__).parent.resolve()
CACHE = ROOT / "cache"; CACHE.mkdir(exist_ok=True)
OUT   = ROOT / "out";   OUT.mkdir(exist_ok=True)
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
K  = "https://api.elections.kalshi.com/trade-api/v2"

def log(*a):
    print(time.strftime("%m-%d %H:%M:%S"), *a, file=sys.stderr, flush=True)

# ────────────────────────────── HTTP ──────────────────────────────
def jget(url, timeout=45, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read()
                              .decode("utf-8", "replace"), strict=False)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 502, 503, 504) and i < tries-1:
                time.sleep(4*(i+1)); continue
            raise
        except Exception as e:
            last = e
            if i < tries-1: time.sleep(2*(i+1)); continue
            raise
    raise last

def cached(key, fn):
    """디스크 캐시 (gz json). 잡 간에 캔들을 재사용해 API 호출을 줄인다."""
    f = CACHE / (re.sub(r"[^A-Za-z0-9._-]", "_", key)[:200] + ".json.gz")
    if f.exists():
        try:
            with gzip.open(f, "rt") as h: return json.load(h)
        except Exception: pass
    v = fn()
    tmp = f.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as h: json.dump(v, h)
    tmp.rename(f)
    return v

# ────────────────────────────── Kalshi ──────────────────────────────
def tsec(s):
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())

def strike(ticker):
    m = re.search(r"-T(\d+(?:\.\d+)?)$", ticker)
    return float(m.group(1)) if m else None

def event_of(ticker):
    return ticker.rsplit("-T", 1)[0]

def k_markets(series=None, status=None, max_pages=20):
    out, cur = [], None
    for _ in range(max_pages):
        u = f"{K}/markets?limit=1000"
        if series: u += f"&series_ticker={series}"
        if status: u += f"&status={status}"
        if cur:    u += f"&cursor={cur}"
        try: d = jget(u, timeout=60)
        except Exception: break
        out += d.get("markets") or []
        cur = d.get("cursor")
        if not cur: break
    return out

def k_settled_events(series, cap):
    """정산된 이벤트를 최근순으로. [(event, [market,...]), ...]"""
    ev = {}
    for m in k_markets(series, "settled"):
        ev.setdefault(event_of(m["ticker"]), []).append(m)
    return [(e, ev[e]) for e in sorted(ev, reverse=True)[:cap]]

def event_window(arr):
    o = min(tsec(m["open_time"])  for m in arr if m.get("open_time"))
    c = max(tsec(m["close_time"]) for m in arr if m.get("close_time"))
    return o, c

def k_candles_1m(series, ticker, o, c):
    """1분 캔들, 60시간 조각. 반환 {분단위ts: {mid,bid,ask,last,vol}}"""
    key = f"c1m_{series}_{ticker}_{o}_{c}"
    def fetch():
        rows, a = [], o
        step = 60*3600
        while a < c:
            b = min(c, a+step)
            try:
                r = jget(f"{K}/series/{series}/markets/{ticker}/candlesticks"
                         f"?start_ts={a}&end_ts={b}&period_interval=1", timeout=60)
                rows += r.get("candlesticks") or []
            except Exception:
                pass
            if b >= c: break
            a = b
        return rows
    out = {}
    for x in cached(key, fetch):
        t = int(x["end_period_ts"])//60*60
        bd = (x.get("yes_bid") or {}).get("close_dollars")
        ak = (x.get("yes_ask") or {}).get("close_dollars")
        bar = {}
        # ask 는 bid=0 이어도 기록한다. 외가격 1센트 고정(틱 하한)이 정확히 그 케이스다.
        try: akv = float(ak) if ak is not None else None
        except (TypeError, ValueError): akv = None
        try: bdv = float(bd) if bd is not None else None
        except (TypeError, ValueError): bdv = None
        if akv is not None and 0 < akv < 1:
            bar["ask"] = akv
            if bdv is not None and 0 < bdv <= akv:
                bar["bid"] = bdv; bar["mid"] = (akv+bdv)/2
        p = (x.get("price") or {}).get("close_dollars")
        if p: bar["last"] = float(p)
        v = x.get("volume_fp")
        if v: bar["vol"] = float(v)
        if bar: out[t] = bar
    return out

def k_trades(ticker, max_pages=5):
    out, cur = [], None
    for _ in range(max_pages):
        u = f"{K}/markets/trades?ticker={ticker}&limit=1000" + (f"&cursor={cur}" if cur else "")
        try: d = jget(u, timeout=60)
        except Exception: break
        tr = d.get("trades") or []
        if not tr: break
        out += tr
        cur = d.get("cursor")
        if not cur: break
    return out

# ─────────────────────── 기초자산 (스팟 대용치) ───────────────────────
# KXGOLD*: 정산은 현물 금. PAXG 대조 결과 |차| 중앙 $3.54 (0.08%), 체계적 +$3.15 → 대용 정당
# KXBTCD/KXETHD: 정산은 CF Benchmarks BRTI/ERTI → 최대 구성 거래소 코인베이스로 대용
SPOT_PROXY = {"KXGOLDD": ("binance", "PAXGUSDT"), "KXGOLDW": ("binance", "PAXGUSDT"),
              "KXGOLDH": ("binance", "PAXGUSDT"),
              "KXBTCD": ("coinbase", "BTC-USD"), "KXETHD": ("coinbase", "ETH-USD")}

def binance_1m(symbol, start_ts, end_ts):
    out = {}
    cur = start_ts*1000; end_ms = end_ts*1000
    while cur < end_ms:
        key = f"bv1m_{symbol}_{cur}"
        def fetch(cur=cur):
            return jget(f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}"
                        f"&interval=1m&startTime={cur}&limit=1000")
        try: rows = cached(key, fetch)
        except Exception: break
        if not rows: break
        for r in rows: out[int(r[0])//1000//60*60] = {"mid": float(r[4])}
        nxt = int(rows[-1][0]) + 60000
        if nxt <= cur: break
        cur = nxt
    return out

def coinbase_1m(product, start_ts, end_ts):
    out = {}
    cur = start_ts
    while cur < end_ts:
        b = min(end_ts, cur + 300*60)
        key = f"cb1m_{product}_{cur}_{b}"
        def fetch(cur=cur, b=b):
            s = dt.datetime.fromtimestamp(cur, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            e = dt.datetime.fromtimestamp(b,  dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return jget(f"https://api.exchange.coinbase.com/products/{product}/candles"
                        f"?granularity=60&start={s}&end={e}")
        try: rows = cached(key, fetch)
        except Exception: rows = []
        for r in rows or []: out[int(r[0])//60*60] = {"mid": float(r[4])}
        cur = b
    return out

def spot_1m(series, start_ts, end_ts):
    src, sym = SPOT_PROXY[series]
    return (binance_1m if src == "binance" else coinbase_1m)(sym, start_ts, end_ts)

# ─────────────────────── 사다리 함축가격 ───────────────────────
def ladder_implied(by_ticker, price="mid"):
    """{ticker:{ts:bar}} → {ts:{'mid':함축중앙값,'n_strikes':k}}.
    p(K)=P(>K) 는 K 에 단조감소. p=0.5 교차점을 선형보간한다."""
    ks = {tk: strike(tk) for tk in by_ticker}
    ks = {tk: k for tk, k in ks.items() if k is not None}
    if len(ks) < 4: return {}
    ts_all = set()
    for tk in ks: ts_all |= set(by_ticker[tk])
    out = {}
    for t in sorted(ts_all):
        pts = []
        for tk, k in ks.items():
            b = by_ticker[tk].get(t)
            if b and b.get(price) is not None: pts.append((k, b[price]))
        if len(pts) < 4: continue
        pts.sort()
        med = None
        for (k1, p1), (k2, p2) in zip(pts, pts[1:]):
            if p1 >= 0.5 >= p2 and p1 != p2:
                med = k1 + (k2-k1)*(p1-0.5)/(p1-p2); break
        if med is None:
            tot = sum(p for _, p in pts)
            if tot <= 0: continue
            med = sum(k*p for k, p in pts)/tot
        out[t] = {"mid": med, "n_strikes": len(pts)}
    return out

# ─────────────────────── 통계 ───────────────────────
def ols(xs, ys):
    n = len(xs)
    if n < 3: return None
    mx, my = sum(xs)/n, sum(ys)/n
    sxx = sum((a-mx)**2 for a in xs)
    if sxx <= 0: return None
    b = sum((a-mx)*(c-my) for a, c in zip(xs, ys))/sxx
    a0 = my - b*mx
    ss = sum((c-my)**2 for c in ys)
    rs = sum((c-(a0+b*x))**2 for x, c in zip(xs, ys))
    return {"a": a0, "b": b, "r2": 1-rs/ss if ss > 0 else 0.0, "n": n}

_ND = statistics.NormalDist()
def fit_contract(K_, close_ts, quotes, spot, min_obs=60):
    """Φ⁻¹(p) = a + b·ln(S/K)/√T 회귀. σ=1/b. 등가격 R²=0.967 로 검증된 방법."""
    xs, ys = [], []
    for t in sorted(set(quotes) & set(spot)):
        p = quotes[t].get("mid"); S = spot[t].get("mid")
        if p is None or S is None: continue
        T = (close_ts - t)/(365*86400)
        if T <= 1e-7 or not (0.005 < p < 0.995): continue
        xs.append(math.log(S/K_)/math.sqrt(T)); ys.append(_ND.inv_cdf(p))
    if len(xs) < min_obs: return None
    r = ols(xs, ys)
    if not r or r["b"] <= 0: return None
    mids = [quotes[t]["mid"] for t in quotes if quotes[t].get("mid") is not None]
    r.update(sigma=1.0/r["b"], mid=statistics.median(mids))
    return r

def atten_fit(points):
    """rho²(h)=A/(1+c/h) 비선형 격자 적합, A<=1. points=[(h시간, rho²),...]"""
    pts = [(h, r) for h, r in points if r is not None and r > 0 and h > 0]
    if len(pts) < 3: return None
    ys = [r for _, r in pts]; my = sum(ys)/len(ys)
    ss = sum((r-my)**2 for r in ys) or 1e-12
    best = (None, None, float("inf"))
    lo_c, hi_c = 1e-3, 1e3
    for _ in range(6):
        for i in range(60):
            c = lo_c*(hi_c/lo_c)**(i/59)
            num = sum(r/(1+c/h) for h, r in pts)
            den = sum(1/(1+c/h)**2 for h, _ in pts)
            if den <= 0: continue
            A = min(1.0, num/den)
            e = sum((r - A/(1+c/h))**2 for h, r in pts)
            if e < best[2]: best = (A, c, e)
        if best[1] is None: return None
        lo_c, hi_c = best[1]/4, best[1]*4
    A, c, e = best
    return {"A": A, "c": c, "fit_r2": 1-e/ss, "n": len(pts)}

def flb_bins(obs, bins=None):
    """obs=[(implied, result01), ...] → 구간표"""
    bins = bins or [(0,.05),(.05,.10),(.10,.20),(.20,.35),(.35,.5),(.5,.65),(.65,.8),(.8,.9),(.9,.95),(.95,1)]
    rows = []
    for lo, hi in bins:
        s = [(p, y) for p, y in obs if lo <= p < hi]
        if len(s) < 15: continue
        rows.append({"bin": f"{lo:.2f}~{hi:.2f}", "n": len(s),
                     "implied": statistics.mean(p for p, _ in s),
                     "realized": statistics.mean(y for _, y in s)})
    return rows

def cluster_boot(by_contract, band, B=2000, seed=11):
    """계약 단위 부트스트랩. by_contract={cid:[(p,y),...]}, band=(lo,hi).
    반환: 편차(실현-함축)의 점추정과 95% CI. 계약 내 상관을 존중한다."""
    cids = [cid for cid, rows in by_contract.items()
            if any(band[0] <= p < band[1] for p, _ in rows)]
    if len(cids) < 10: return None
    def dev(sample):
        ps, ys = [], []
        for cid in sample:
            for p, y in by_contract[cid]:
                if band[0] <= p < band[1]: ps.append(p); ys.append(y)
        return (statistics.mean(ys)-statistics.mean(ps)) if ps else None
    point = dev(cids)
    rnd = random.Random(seed)
    ds = []
    for _ in range(B):
        d = dev([cids[rnd.randrange(len(cids))] for _ in range(len(cids))])
        if d is not None: ds.append(d)
    ds.sort()
    return {"dev": point, "ci_lo": ds[int(len(ds)*0.025)], "ci_hi": ds[int(len(ds)*0.975)],
            "n_contracts": len(cids), "n_obs": sum(1 for cid in cids for p, _ in by_contract[cid]
                                                   if band[0] <= p < band[1])}

# ─────────────────────── 재개 가능한 JSONL 출력 ───────────────────────
def load_done(path, key):
    done = set()
    p = pathlib.Path(path)
    if p.exists():
        for ln in open(p, errors="replace"):
            try: done.add(json.loads(ln)[key])
            except Exception: pass
    return done

def append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
