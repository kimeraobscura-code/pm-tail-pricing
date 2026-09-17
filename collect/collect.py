#!/usr/bin/env python3
"""hs-collect — 예측시장 소멸성 데이터 수집기 (표준 라이브러리만 사용)

받는 것: 재구성이 불가능한 데이터만.
  1. 호가창 깊이   어디에도 과거 데이터가 없다
  2. 유니버스      상장됐다 사라진 마켓을 못 세면 생존편향이 생긴다
  3. 칼시 체결     공개 이력이 약 67일 롤링이라 그 뒤는 영구 소멸

안 받는 것: 나중에 백필되는 것.
  바이낸스 klines(전체 이력 공개) / 폴리마켓 온체인 체결(data-api로 과거분 조회 가능)

사용:
  python3 collect.py            수집 시작
  python3 collect.py --status   상태 점검
  python3 collect.py --once     1회만 돌고 종료 (동작 확인용)
"""
import datetime as dt
import gzip, json, os, pathlib, queue, shutil, signal, sys, threading, time
import urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(os.environ.get("HS_ROOT", pathlib.Path(__file__).parent)).resolve()
RAW  = ROOT / "raw"
UA   = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# ── 주기 (초). 유니버스는 최단 마켓(Limitless 5분)보다 반드시 짧아야 한다 ──
BOOK_EVERY  = int(os.environ.get("HS_BOOK_EVERY",  "60"))
UNI_EVERY   = int(os.environ.get("HS_UNI_EVERY",   "60"))
TRADE_EVERY = int(os.environ.get("HS_TRADE_EVERY", "10"))
HEALTH_EVERY= int(os.environ.get("HS_HEALTH_EVERY","300"))
UNI_FULL_EVERY = int(os.environ.get("HS_UNI_FULL_EVERY", "7200"))
# 유니버스는 두 갈래다.
#   탐색(UNI_EVERY, 60s)  : 각 베뉴 첫 페이지만. Limitless 5분 마켓을 놓치지 않기 위한 것
#   전수(UNI_FULL_EVERY)  : 페이지네이션 전량 크롤. 무거워서 자주 못 돈다
TOP_N       = int(os.environ.get("HS_TOP_N", "400"))   # 베뉴당 호가창 추적 마켓 수
PIN_CAP     = int(os.environ.get("HS_PIN_CAP", "600"))  # 고정 추적 상한 (우선순위 순으로 자름)
LITE_CAP    = int(os.environ.get("HS_LITE_CAP", "2500")) # 정치·문화 bbo 전용 티어 상한
# 체결 스트림은 기본 OFF. 칼시 체결은 API 로 67일 백필이 가능해 실시간 수집이 필수가 아니고,
# 실측에서 저장의 60%(390MB/일)를 차지했다. 켜려면 HS_TRADES=1.
TRADES_ON   = os.environ.get("HS_TRADES", "0") == "1"
WORKERS     = int(os.environ.get("HS_WORKERS", "12"))

# 스트림별 최소 기대 처리량 (건/5분). 미달하면 데이터로 경고를 남긴다.
# 조용한 스트림과 조용한 시장은 로그만 봐서는 구분되지 않는다.
EXPECTED = {"pmus_book": 100, "kalshi_book": 50, "limitless_book": 20, "kalshi_trades": 5}
if not TRADES_ON: EXPECTED.pop("kalshi_trades", None)

STOP = threading.Event()
STAT = {}
LOCK = threading.Lock()
def bump(k, n=1):
    with LOCK: STAT[k] = STAT.get(k, 0) + n

# ────────────────────────────── HTTP ──────────────────────────────
def http(url, timeout=30, method="GET", body=None):
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"User-Agent": UA, "Accept": "application/json",
                 **({"Content-Type": "application/json"} if body is not None else {})})
    return urllib.request.urlopen(req, timeout=timeout).read()

def jget(url, **kw):
    return json.loads(http(url, **kw).decode("utf-8", "replace"), strict=False)

def paged(fn, cap=40, pages=None):
    """페이지네이션 헬퍼. 중간에 실패해도 그때까지 받은 건 반환한다.
    (gamma는 특정 offset을 넘기면 422를 던진다)"""
    out = []
    for i in range(pages if pages is not None else cap):
        try:
            b = fn(i)
        except Exception:
            break
        if not b: break
        out += b
    return out

# ─────────────────── 원시층: 평문 JSONL, 회전 시 압축 ───────────────────
# gzip 스트림에 직접 쓰면 크래시 때 버퍼가 통째로 날아간다. 평문으로 쓰고 회전할 때 압축한다.
class Sink:
    def __init__(self, src):
        self.src, self.hour, self.f, self.n = src, None, None, 0
        self.lock = threading.Lock()
    def _path(self, h):
        d = RAW / self.src / h[:8]; d.mkdir(parents=True, exist_ok=True)
        return d / f"{h[8:]}.jsonl"
    def _roll(self, h):
        if self.f:
            self.f.close()
            old = self._path(self.hour)
            if old.exists() and old.stat().st_size:
                try:
                    with open(old, "rb") as a, gzip.open(f"{old}.gz", "wb", 6) as b:
                        shutil.copyfileobj(a, b)
                    old.unlink()
                except Exception as e:
                    log("health", "compress_err", {"file": str(old), "err": str(e)[:200]})
        self.hour = h
        self.f = open(self._path(h), "a", encoding="utf-8")
    def write(self, obj):
        h = time.strftime("%Y%m%d%H", time.gmtime())
        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        with self.lock:
            if h != self.hour: self._roll(h)
            self.f.write(line + "\n"); self.f.flush()
            self.n += 1
    def close(self):
        with self.lock:
            if self.f: self.f.close(); self.f = None

SINKS = {}   # kalshi_book / pmus_book / pmus_bbo / limitless_book / kalshi_trades / universe / health
def sink(name):
    if name not in SINKS: SINKS[name] = Sink(name)
    return SINKS[name]

def log(src, kind, payload, **extra):
    """captured_at(관측 시각)을 페이로드와 분리해 기록한다. 이시점 재구성의 전제."""
    sink(src).write({"captured_at": time.time(), "kind": kind, **extra, "d": payload})

# ────────────────────────── 유니버스 (델타) ──────────────────────────
STATE = {}                      # venue -> 추적할 식별자 리스트 (PINNED + FILL)
PINNED, FILL = {}, {}
def merge(venue, pinned, fill, full, cap=None, pin_cap=None):
    """PINNED 는 절대 축출되지 않는다 — 우선순위 규칙(카테고리/시리즈)으로만 채운다.
    FILL 은 남는 슬롯이며 신규 발견에 밀려날 수 있다.
    이전 FIFO 는 신규 스포츠 마켓이 경제 마켓을 계속 밀어내
    경제 마켓이 36분에 1회(전수 스캔 주기)만 잡히는 버그를 만들었다."""
    pin_cap = PIN_CAP if pin_cap is None else pin_cap
    cap = TOP_N if cap is None else cap
    pinned = list(dict.fromkeys(pinned))[:pin_cap]
    if full or venue not in PINNED:
        PINNED[venue] = pinned
        pset = set(pinned)
        FILL[venue] = [x for x in dict.fromkeys(fill) if x not in pset]
    else:
        have = set(PINNED[venue])
        add = [x for x in pinned if x not in have]
        if add: PINNED[venue] = (PINNED[venue] + add)[:pin_cap]
        havef = set(PINNED[venue]) | set(FILL[venue])
        newf = [x for x in fill if x not in havef]
        if newf: FILL[venue] = newf + FILL[venue]
    room = max(0, cap - len(PINNED[venue]))
    FILL[venue] = FILL[venue][:room]
    STATE[venue] = PINNED[venue] + FILL[venue]
_PREV, _FULL_AT = {}, {}

def uni_write(venue, items, keyf, volatile=(), full=True):
    """전수 스캔만 스냅샷/델타를 기록한다.
    탐색(부분 목록)으로 델타를 계산하면 못 본 마켓이 전부 'removed'로 잡혀 기록이 폭증한다."""
    now = time.time()
    cur = {keyf(x): x for x in items}
    def stable(v): return {k: x for k, x in v.items() if k not in volatile}
    if not full:
        prev = _PREV.get(venue) or {}
        new = [v for k, v in cur.items() if k not in prev]
        if new:
            log("universe", "discovered", new, venue=venue, n=len(new))
            bump(f"uni_new_{venue}")
        return
    if now - _FULL_AT.get(venue, 0) >= UNI_FULL_EVERY or venue not in _PREV:
        log("universe", "full", list(cur.values()), venue=venue, n=len(cur))
        _FULL_AT[venue] = now; _PREV[venue] = cur; bump(f"uni_full_{venue}"); return
    prev = _PREV[venue]
    added   = [v for k, v in cur.items() if k not in prev]
    removed = [k for k in prev if k not in cur]
    changed = [v for k, v in cur.items() if k in prev and stable(v) != stable(prev[k])]
    if added or removed or changed:
        log("universe", "delta", {"added": added, "removed": removed, "changed": changed},
            venue=venue, n_cur=len(cur))
    _PREV[venue] = cur; bump(f"uni_delta_{venue}")

# ─────────────────────────── 베뉴별 수집 ───────────────────────────
K = "https://api.elections.kalshi.com/trade-api/v2"
G = "https://gateway.polymarket.us"

FIELDS_PMUS = ("id","slug","question","category","endDate","startDate","status","closed",
               "active","marketType","outcomes","outcomePrices","feeCoefficient",
               "orderPriceMinTickSize","minimumTradeQty","comboEnabled")
FIELDS_K    = ("ticker","event_ticker","title","status","open_time","close_time")

def uni_pmus(full=False):
    ms = paged(lambda i: jget(f"{G}/v1/markets?closed=false&limit=500&offset={i*500}").get("markets"),
               pages=None if full else 1)
    uni_write("pmus", [{k: m.get(k) for k in FIELDS_PMUS} for m in ms], lambda x: x["slug"],
              volatile=("outcomePrices","status"), full=full)
    ECON = ("finance","macro","crypto","technology","science","climate","geopolitics")
    LITE = ("politics","culture")
    SKIP = ("sports",)
    econ = [m["slug"] for m in ms if m.get("category") in ECON]
    rest = [m["slug"] for m in ms if m.get("category") not in ECON + LITE + SKIP]
    merge("pmus", econ, rest, full)
    # lite: 정치·문화. 전통 쌍둥이가 없는 가장 깊은 마켓들이 여기 있고(스패닝 갭 증거),
    # 폴리마켓 US 는 사후 백필이 불가능하다 — 지금 안 받으면 영원히 없다.
    lite = [m["slug"] for m in ms if m.get("category") in LITE]
    merge("pmus_lite", lite, [], full, cap=LITE_CAP, pin_cap=LITE_CAP)

K_NEAR_H = float(os.environ.get("HS_KALSHI_NEAR_H", "8"))   # 근일 창(시간)

def near_dated(ms):
    """마감이 K_NEAR_H 이내인 마켓만 남긴다. 마감시각이 없으면 보수적으로 남긴다.
    창 안에 하나도 없으면(장 마감 등) 마감 임박순 앞에서 200개를 취해 추적이 0 이 되는 것을 막는다."""
    now = time.time(); out = []
    for m in ms:
        ct = m.get("close_time")
        if not ct: out.append((0.0, m)); continue
        try:
            t = dt.datetime.fromisoformat(str(ct).replace("Z", "+00:00")).timestamp()
        except Exception:
            out.append((0.0, m)); continue
        if t >= now:
            out.append((t - now, m))
    out.sort(key=lambda x: x[0])
    near = [m for d, m in out if d <= K_NEAR_H * 3600]
    return near if near else [m for _, m in out[:200]]

SERIES = ("KXGOLDD","KXGOLDW","KXGOLDH","KXCPI","KXFED","KXOIL","KXBTCD","KXETHD","KXBTC15M")

def uni_kalshi(full=False):
    if not full:
        # 탐색: 관심 시리즈만 직접 조회한다. 전체 첫 페이지는 콤보 마켓이라 무의미하다.
        ms = []
        for ser in SERIES:
            try:
                ms += (jget(f"{K}/markets?series_ticker={ser}&limit=200&status=open").get("markets") or [])
            except Exception: pass
        if ms:
            uni_write("kalshi", [{k: m.get(k) for k in FIELDS_K} for m in ms],
                      lambda x: x["ticker"], full=False)
            merge("kalshi", [m["ticker"] for m in near_dated(ms)], [], True)
        return
    ms, cur = [], None
    for _ in range(20 if full else 1):
        try:
            d = jget(f"{K}/markets?limit=1000&status=open" + (f"&cursor={cur}" if cur else ""), timeout=45)
        except Exception:
            break
        ms += d.get("markets") or []; cur = d.get("cursor")
        if not cur: break
    uni_write("kalshi", [{k: m.get(k) for k in FIELDS_K} for m in ms], lambda x: x["ticker"], volatile=("status",), full=full)
    # 전수 스캔은 유니버스 기록(생존편향 제거)용으로만 쓴다.
    # 추적 재구성은 탐색(uni_kalshi full=False, 시리즈 직접 조회)이 60초마다 한다.
    # 전역 크롤 결과로 merge 하면 첫 페이지들(콤보 마켓)에 시리즈가 없어 추적이 일시 0 이 된다.


def uni_limitless(full=False):
    # limit 상한이 25다. 50 이상은 400을 반환한다.
    # 첫 페이지가 만기 임박순이라 5분 마켓은 탐색 주기만으로도 잡힌다.
    ms = paged(lambda i: (jget(f"https://api.limitless.exchange/markets/active?page={i+1}&limit=25")
                          .get("data")), cap=60, pages=None if full else 1)
    uni_write("limitless", [{k: m.get(k) for k in
              ("id","slug","title","expirationTimestamp","status","expired","volume","categories")}
              for m in ms], lambda x: x["slug"],
              volatile=("volume","status"), full=full)
    merge("limitless", [], [m["slug"] for m in ms], full)

# ── 호가창 ──
def book_pmus(slug):
    d = jget(f"{G}/v1/markets/{slug}/book", timeout=20).get("marketData") or {}
    if d.get("bids") or d.get("offers"):
        log("pmus_book", "book", d, slug=slug); bump("pmus_book")

def bbo_pmus(slug):
    d = jget(f"{G}/v1/markets/{slug}/bbo", timeout=20).get("marketData") or {}
    if d: log("pmus_bbo", "bbo", d, slug=slug); bump("pmus_bbo")

def book_kalshi(tk):
    ob = jget(f"{K}/markets/{tk}/orderbook?depth=20", timeout=20).get("orderbook_fp") or {}
    if ob.get("yes_dollars") or ob.get("no_dollars"):
        log("kalshi_book", "book", ob, ticker=tk); bump("kalshi_book")


def book_limitless(slug):
    d = jget(f"https://api.limitless.exchange/markets/{slug}/orderbook", timeout=20)
    if d.get("bids") or d.get("asks"):
        log("limitless_book", "book", {"bids": d.get("bids"), "asks": d.get("asks")}, slug=slug)
        bump("limitless_book")

# ── 칼시 체결: 커서 증분. 전량 재요청하면 72%가 중복이다. ──
SEEN, SEEN_Q = set(), []
def trades_kalshi():
    cur, pages, new = None, 0, 0
    while pages < 6:
        d = jget(f"{K}/markets/trades?limit=1000" + (f"&cursor={cur}" if cur else ""), timeout=40)
        tr = d.get("trades") or []
        if not tr: break
        fresh = [t for t in tr if t["trade_id"] not in SEEN]
        for t in fresh:
            SEEN.add(t["trade_id"]); SEEN_Q.append(t["trade_id"])
        if fresh:
            log("kalshi_trades", "trades", fresh, n=len(fresh)); new += len(fresh); bump("kalshi_trades", len(fresh))
        if len(fresh) < len(tr): break          # 이미 본 구간에 닿았다
        cur = d.get("cursor"); pages += 1
        if not cur: break
    while len(SEEN_Q) > 400_000:                # 메모리 상한
        SEEN.discard(SEEN_Q.pop(0))
    return new

# ─────────────────────────────── 실행 ───────────────────────────────
TASKS_UNI  = [("pmus", uni_pmus), ("kalshi", uni_kalshi), ("limitless", uni_limitless)]
BBO_EVERY  = int(os.environ.get("HS_BBO_EVERY",  "300"))
LITE_EVERY = int(os.environ.get("HS_LITE_EVERY", "600"))
_last_bbo, _last_lite = [0.0], [0.0]
LITE_WORKERS = int(os.environ.get("HS_LITE_WORKERS", "8"))
_lite_lock = threading.Lock()
def start_lite_scan():
    """정치·문화 bbo 는 2,500건이라 메인 루프에서 돌리면 호가창 주기를 망친다
    (실측: 사이클이 60초에서 200초로 늘어났다). 별도 스레드 + 전용 풀로 분리한다."""
    if not _lite_lock.acquire(blocking=False): return   # 이전 패스가 아직 도는 중이면 건너뜀
    def worker():
        try:
            t0 = time.time(); items = STATE.get("pmus_lite", [])
            with ThreadPoolExecutor(max_workers=LITE_WORKERS) as lex:
                run_all(lex, bbo_pmus, items)
            log("health", "lite_scan", {"secs": round(time.time()-t0, 1), "n": len(items)})
        finally:
            _lite_lock.release()
    threading.Thread(target=worker, daemon=True, name="litescan").start()

def pmus_cycle(ex):
    run_all(ex, book_pmus, STATE.get("pmus", []))
    now = time.time()
    if now - _last_bbo[0] >= BBO_EVERY:      # openInterest 용도라 자주 받을 필요 없다
        run_all(ex, bbo_pmus, STATE.get("pmus", []))
        _last_bbo[0] = now
    if now - _last_lite[0] >= LITE_EVERY:    # 정치·문화: 가격 시계열만 확보한다
        _last_lite[0] = now
        start_lite_scan()

TASKS_BOOK = [("pmus", pmus_cycle),
              ("kalshi",    lambda ex: run_all(ex, book_kalshi,    STATE.get("kalshi", []))),
              ("limitless", lambda ex: run_all(ex, book_limitless, STATE.get("limitless", [])))]


def run_all(ex, fn, items):
    def safe(x):
        try: fn(x)
        except Exception: bump(f"err_{fn.__name__}")
    list(ex.map(safe, items))

_full_lock = threading.Lock()
def start_full_scan():
    """전수 크롤은 수천 건 페이지네이션이라 수 분이 걸린다.
    메인 루프(호가창 수집)를 막지 않도록 별도 스레드에서 돈다."""
    if not _full_lock.acquire(blocking=False): return       # 이미 도는 중이면 건너뜀
    def worker():
        try:
            t0 = time.time()
            for n, f in TASKS_UNI: safe_call(f"unifull_{n}", f, True)
            log("health", "full_scan", {"secs": round(time.time()-t0, 1),
                                        "tracked": {k: len(v) for k, v in STATE.items()}})
        finally:
            _full_lock.release()
    threading.Thread(target=worker, daemon=True, name="fullscan").start()

def safe_call(name, fn, *a):
    try: fn(*a)
    except Exception as e:
        bump(f"err_{name}"); log("health", "task_err", {"task": name, "err": f"{type(e).__name__}: {str(e)[:200]}"})

def health(window):
    """자기 다운타임도 데이터로 남긴다. 기록되지 않은 공백은 '거래 없음'으로 오독된다."""
    snap = dict(STAT)
    log("health", "stat", snap, files={k: v.n for k, v in SINKS.items()},
        universe={k: len(v) for k, v in STATE.items()},
        pinned={k: len(v) for k, v in PINNED.items()},
        fill={k: len(v) for k, v in FILL.items()})
    for stream, exp in EXPECTED.items():
        got = snap.get(stream, 0) - window.get(stream, 0)
        if got < exp:
            log("health", "below_expected", {"stream": stream, "got": got, "expected_min": exp})
    return snap

def main():
    RAW.mkdir(parents=True, exist_ok=True)
    once = "--once" in sys.argv
    log("health", "start", {"book_every": BOOK_EVERY, "uni_every": UNI_EVERY,
                            "trade_every": TRADE_EVERY, "top_n": TOP_N, "pid": os.getpid()})
    print(f"hs-collect 시작 | raw={RAW} | book {BOOK_EVERY}s / uni {UNI_EVERY}s / trades {TRADE_EVERY}s",
          file=sys.stderr, flush=True)
    ex = ThreadPoolExecutor(max_workers=WORKERS)
    for n, f in TASKS_UNI: safe_call(f"uni_{n}", f, False)   # 즉시 생산 시작
    start_full_scan()                                         # 전수 크롤은 배후에서
    t_uni = t_book = t_trade = (0 if once else time.time())
    t_full = time.time()
    t_health, window = time.time(), dict(STAT)
    while not STOP.is_set():
        now = time.time()
        if now - t_uni >= UNI_EVERY:
            for n, f in TASKS_UNI: safe_call(f"uni_{n}", f, False)
            t_uni = time.time()
        if now - t_full >= UNI_FULL_EVERY:
            start_full_scan(); t_full = now
        if now - t_book >= BOOK_EVERY:
            c0 = time.time(); durs = {}
            for n, f in TASKS_BOOK:
                d0 = time.time(); safe_call(f"book_{n}", f, ex); durs[n] = round(time.time() - d0, 1)
            cyc = time.time() - c0
            if cyc > BOOK_EVERY:      # 주기보다 오래 걸리면 영구히 밀린다
                log("health", "cycle_overrun", {"cycle_s": round(cyc, 1),
                    "budget_s": BOOK_EVERY, "per_venue": durs,
                    "tracked": {k: len(v) for k, v in STATE.items()}})
            t_book = time.time()
        if TRADES_ON and now - t_trade >= TRADE_EVERY:
            safe_call("trades_kalshi", trades_kalshi); t_trade = now
        if now - t_health >= HEALTH_EVERY:
            window = health(window); t_health = now
            print(f"  {time.strftime('%H:%M:%S')} {json.dumps(dict(STAT), ensure_ascii=False)}",
                  file=sys.stderr, flush=True)
        if once:
            for _, f in TASKS_BOOK: pass
            health(window); break
        STOP.wait(1)
    health(window); log("health", "stop", dict(STAT))
    for s in SINKS.values(): s.close()
    print("종료", file=sys.stderr, flush=True)

def status():
    if not RAW.exists(): print("raw 디렉터리 없음. 아직 수집 안 함."); return
    print(f"{'스트림':<16}{'파일':>6}{'레코드':>10}{'용량':>10}{'최근 기록':>22}")
    print("-" * 66)
    tot = 0
    for d in sorted(RAW.iterdir()):
        if not d.is_dir(): continue
        fs = list(d.rglob("*.jsonl")) + list(d.rglob("*.jsonl.gz"))
        if not fs: continue
        sz = sum(f.stat().st_size for f in fs); tot += sz
        n = sum(sum(1 for _ in (gzip.open(f, "rt", errors="replace") if f.suffix == ".gz"
                                else open(f, errors="replace"))) for f in fs)
        last = max(f.stat().st_mtime for f in fs)
        print(f"{d.name:<16}{len(fs):>6}{n:>10,}{sz/1e6:>9.1f}M"
              f"{time.strftime('%m-%d %H:%M:%S', time.localtime(last)):>22}")
    print("-" * 66)
    print(f"{'합계':<16}{'':>6}{'':>10}{tot/1e6:>9.1f}M")
    days = tot / 1e6 / max(1e-9, (time.time() - min(
        (f.stat().st_mtime for d in RAW.iterdir() if d.is_dir() for f in d.rglob("*")), default=time.time())) / 86400)
    if days > 0: print(f"\n추정 증가율 ≈ {days:,.0f} MB/일  →  1000시간 ≈ {days*1000/24/1024:,.1f} GB")

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT,  lambda *_: STOP.set())
    if "--status" in sys.argv: status()
    else:
        try: main()
        except KeyboardInterrupt: pass
