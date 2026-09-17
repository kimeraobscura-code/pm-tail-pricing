#!/usr/bin/env python3
"""pair-scan — 비교 상품 선택용 페어링 스캐너 (표준 라이브러리만).

목적: 논문의 비교축을 정하기 전에, "폴리마켓 US 의 어떤 마켓이 전통 금융의 어떤
상품과 짝지어질 수 있는가"를 매일 측정한다. 산출물은 페어 후보의 순위표다.

데이터 소스 (전부 무가입·무VPN):
  A. 폴리마켓 US   gateway.polymarket.us    비스포츠 전 마켓 + 호가창 (스크립트 OK)
  B. 전통 옵션      CBOE 공식 지연시세 API   GLD/IBIT/ETHA/SLV/USO/SPY/QQQ/TLT (스크립트 OK)
  C. CME 이벤트     cme_drop/ 폴더           사용자가 원클릭 CSV 를 떨어뜨리면 자동 수집
                                             (CME 는 스크립트 403, 브라우저만 통과)

사용:
  python3 pairscan.py            1회 스캔 → out/scan_YYYYMMDD_HH.json + out/SCOREBOARD.md
  python3 pairscan.py --board    마지막 스캔으로 순위표만 재생성
launchd 로 6시간마다 돌리면 된다 (com.pairscan.plist 동봉).
"""
import csv, glob, gzip, io, json, os, pathlib, re, shutil, statistics, sys, time
import datetime as dt
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).parent.resolve()
OUT  = ROOT/"out";      OUT.mkdir(exist_ok=True)
DROP = ROOT/"cme_drop"; DROP.mkdir(exist_ok=True)
ARCH = ROOT/"archive";  ARCH.mkdir(exist_ok=True)
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
G  = "https://gateway.polymarket.us"
NOW = dt.datetime.now(dt.timezone.utc)

def log(*a): print(time.strftime("%m-%d %H:%M:%S"), *a, file=sys.stderr, flush=True)

def jget(url, timeout=40, tries=3):
    for i in range(tries):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            return json.loads(urllib.request.urlopen(r, timeout=timeout).read()
                              .decode("utf-8", "replace"), strict=False)
        except Exception:
            if i == tries-1: raise
            time.sleep(2*(i+1))

# ───────────────── A. 폴리마켓 US ─────────────────
SKIP_CAT = {"sports"}                       # 페어링 불가능이 자명한 것만 제외
def scan_pmus():
    ms, off = [], 0
    while True:
        try: b = jget(f"{G}/v1/markets?closed=false&limit=500&offset={off}").get("markets") or []
        except Exception: break
        if not b: break
        ms += b; off += 500
    import collections
    st = collections.Counter(m.get("status") for m in ms)
    log(f"PMUS 반환 {len(ms):,}개 | status={dict(st.most_common(4))}")
    # 점검창(HALTED)에서도 호가창은 동결 상태로 읽히므로 후보에 포함한다.
    # 반환이 비정상적으로 적으면(점검창의 필터 오동작) 저하 모드를 기록하고 빈 손으로 돌아간다 —
    # launchd 6시간 주기가 다음 사이클에 자동 복구한다.
    cand = [m for m in ms if m.get("category") not in SKIP_CAT
            and m.get("status") in ("MARKET_STATUS_OPEN", "MARKET_STATUS_HALTED")]
    log(f"비스포츠 OPEN/HALTED {len(cand):,}개")
    if len(cand) < 50:
        log("경고: 후보 <50 — 점검창이거나 필터 오동작. degraded 로 기록하고 종료")
        (OUT/"DEGRADED.flag").write_text(f"{NOW.isoformat()} candidates={len(cand)} status={dict(st)}")
        return []
    if (OUT/"DEGRADED.flag").exists(): (OUT/"DEGRADED.flag").unlink()
    def book(m):
        try:
            d = jget(f"{G}/v1/markets/{m['slug']}/book", 25).get("marketData") or {}
        except Exception: return None
        bids = d.get("bids") or []; asks = d.get("offers") or []
        depth = (sum(float(x["px"]["value"])*float(x["qty"]) for x in bids)
               + sum(float(x["px"]["value"])*float(x["qty"]) for x in asks))
        bb = max((float(x["px"]["value"]) for x in bids), default=None)
        ba = min((float(x["px"]["value"]) for x in asks), default=None)
        q = m.get("question") or ""
        kn = re.search(r'\$?([\d,]+(?:\.\d+)?)', q)
        return {"slug": m["slug"], "q": q[:90], "cat": m.get("category"),
                "end": m.get("endDate"), "tick": m.get("orderPriceMinTickSize"),
                "depth_usd": round(depth), "bid": bb, "ask": ba,
                "spread": round(ba-bb, 4) if (bb is not None and ba is not None) else None,
                "strike_hint": kn.group(1) if kn else None}
    rows = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for f in as_completed([ex.submit(book, m) for m in cand]):
            try: r = f.result()
            except Exception: r = None
            if r: rows.append(r)
    return rows

# ───────────────── B. 전통 옵션 (CBOE) ─────────────────
SYMS = ["GLD","IBIT","ETHA","SLV","USO","SPY","QQQ","TLT"]
def scan_cboe():
    out = {}
    for sym in SYMS:
        try:
            d = jget(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json", 40)
        except Exception as e:
            log(f"  CBOE {sym} 실패: {e}"); continue
        spot = d["data"].get("current_price")
        exps = {}
        for o in d["data"].get("options") or []:
            m = re.match(rf"^{sym}(\d{{6}})([CP])(\d{{8}})$", o["option"])
            if not m: continue
            e = m.group(1); K = int(m.group(3))/1000
            rec = exps.setdefault(e, {"n": 0, "atm_iv": None, "otm_put_relspread": [],
                                      "k_lo": K, "k_hi": K})
            rec["n"] += 1
            rec["k_lo"] = min(rec["k_lo"], K); rec["k_hi"] = max(rec["k_hi"], K)
            if spot:
                mny = K/spot
                if m.group(2) == "C" and abs(mny-1) < 0.01 and o.get("iv"):
                    rec["atm_iv"] = o["iv"]
                if m.group(2) == "P" and 0.85 <= mny <= 0.95 and (o.get("bid") or 0) > 0:
                    mid = (o["bid"]+o["ask"])/2
                    if mid > 0: rec["otm_put_relspread"].append((o["ask"]-o["bid"])/mid)
        for e, rec in exps.items():
            rs = rec.pop("otm_put_relspread")
            rec["otm_relspread"] = round(statistics.median(rs), 3) if rs else None
        out[sym] = {"spot": spot, "expiries": exps}
        log(f"  CBOE {sym}: spot {spot}, 만기 {len(exps)}개")
    return out

# ───────────────── C. CME 드롭 폴더 ─────────────────
def ingest_cme():
    """cme_drop/ 의 CSV 를 아카이브하고 상품별 체결 통계를 만든다.
    같은 날 여러 번 받은 파일의 겹치는 체결은 원문 행 단위로 중복 제거한다."""
    agg = {}
    seen_lines = set()
    for f in sorted(DROP.glob("*.csv")) + sorted(ARCH.glob("*.csv.gz")):
        try:
            if f.suffix == ".gz":
                text = gzip.open(f, "rt", errors="replace").read()
            else:
                text = f.read_text(errors="replace")
        except Exception: continue
        lines = text.splitlines()
        if not lines: continue
        body = []
        for ln in lines[1:]:
            if ln and ln not in seen_lines:
                seen_lines.add(ln); body.append(ln)
        for row in csv.DictReader(io.StringIO("\n".join([lines[0]] + body))):
            name = (row.get("instrument_long_name") or "").strip()
            if not name: continue
            a = agg.setdefault(name, {"trades": 0, "qty": 0, "days": set()})
            a["trades"] += 1
            try: a["qty"] += int(row.get("qty") or 0)
            except ValueError: pass
            a["days"].add(row.get("trade_date"))
        if f.parent == DROP:                       # 새 파일은 아카이브로 이동
            dst = ARCH/f"{f.stem}_{NOW:%Y%m%d}.csv.gz"
            with open(f, "rb") as a_, gzip.open(dst, "wb") as b_: shutil.copyfileobj(a_, b_)
            f.unlink()
            log(f"  CME 드롭 수거: {f.name} → {dst.name}")
    return {k: {"trades": v["trades"], "qty": v["qty"], "n_days": len(v["days"])}
            for k, v in agg.items()}

# ───────────────── 페어링 규칙 ─────────────────
# (패턴, 전통옵션 심볼, CME 이벤트 이름 키워드)
RULES = [
    (r"bitcoin|\bbtc\b",            "IBIT", "BITCOIN"),
    (r"ethereum|\beth\b",           "ETHA", "ETHER"),
    (r"\bgold\b|xau",               "GLD",  None),
    (r"\bsilver\b",                 "SLV",  None),
    (r"\boil\b|crude|wti",          "USO",  "CRUDE"),
    (r"s&p|spx|\bsp500\b",          "SPY",  "S&P"),
    (r"nasdaq",                     "QQQ",  "NASDAQ"),
    (r"\bfed\b|rate (cut|hike)|fomc","TLT", "FED"),
    (r"\bcpi\b|inflation",          None,   "CPI"),
    (r"unemploy|jobs|payroll",      None,   "UNEMPL"),
    (r"\bgdp\b",                    None,   "GDP"),
]
def cond_date(slug, end_iso):
    """만기 판정용 날짜. 슬러그에 조건 날짜가 박혀 있으면 그것이 경제적 만기다.
    다지선다 이벤트의 endDate 는 이벤트 전체 종료일이라 실제 조건보다 몇 달 뒤인 경우가 있다."""
    for pat, order in ((r"(\d{2})-(\d{2})-(\d{4})", "mdy"), (r"(\d{4})-(\d{2})-(\d{2})", "ymd")):
        m = re.search(pat, slug or "")
        if m:
            a, b, c = m.groups()
            y, mo, dd = (c, a, b) if order == "mdy" else (a, b, c)
            try: return dt.date(int(y), int(mo), int(dd)), "slug"
            except ValueError: pass
    try: return dt.datetime.fromisoformat((end_iso or "").replace("Z","+00:00")).date(), "endDate"
    except Exception: return None, None

def horizon_gap(end, expiries):
    """PMUS 만기와 가장 가까운 옵션 만기의 간격(일)."""
    if end is None: return None, None
    best = None
    for e in expiries:
        d = dt.datetime.strptime(e, "%y%m%d").date()
        gap = abs((d-end).days)
        if best is None or gap < best[1]: best = (str(d), gap)
    return best if best else (None, None)

def build_pairs(pmus, cboe, cme):
    pairs = []
    for m in pmus:
        m["_cond"], m["_cond_src"] = cond_date(m.get("slug"), m.get("end"))
        text = (m["q"] or "").lower()
        for pat, sym, cme_kw in RULES:
            if not re.search(pat, text): continue
            leg2 = {}
            if sym and sym in cboe:
                exp, gap = horizon_gap(m.get("_cond"), cboe[sym]["expiries"])
                ivs = [r["atm_iv"] for r in cboe[sym]["expiries"].values() if r["atm_iv"]]
                leg2["option"] = {"sym": sym, "nearest_expiry": exp, "gap_days": gap,
                                  "atm_iv_med": round(statistics.median(ivs), 3) if ivs else None}
            if cme_kw:
                hits = {k: v for k, v in cme.items() if cme_kw in k.upper()}
                if hits:
                    leg2["cme_event"] = {k: v for k, v in sorted(hits.items(),
                                          key=lambda x: -x[1]["qty"])[:2]}
            if not leg2: continue
            gap = leg2.get("option", {}).get("gap_days")
            sp = m.get("spread")
            # 거래가능성 게이트: 양방향 호가가 붙어 있어야 유동성 점수를 준다.
            # 스프레드가 넓거나 한쪽만 있는 책은 명목잔량이 커도 헤지에 못 쓴다.
            if sp is None:      liq_mult = 0.0
            elif sp <= 0.03:    liq_mult = 1.0
            elif sp <= 0.10:    liq_mult = 0.5
            else:               liq_mult = 0.1
            score = (min(m["depth_usd"], 200_000)/200_000) * 50 * liq_mult \
                  + (30 if leg2.get("cme_event") else 0) \
                  + (20 if gap is not None and gap <= 3 else (10 if gap is not None and gap <= 14 else 0))
            pairs.append({"score": round(score, 1), "pm": m, "twin": leg2, "rule": pat,
                          "tradable": liq_mult >= 0.5})
            break
    pairs.sort(key=lambda x: -x["score"])
    return pairs

def board(pairs, cme):
    L = [f"# 페어링 순위표  ({NOW:%Y-%m-%d %H:%M} UTC)", "",
         "점수 = 유동성(50) + CME동종상품 존재(30) + 만기매칭(20)", "",
         f"| # | 점수 | PMUS 마켓 | 깊이$ | 스프레드 | 조건만기 | 전통 다리 | 만기갭 | CME 다리 |",
         f"|---|---|---|---|---|---|---|---|---|"]
    for i, p in enumerate(pairs[:30], 1):
        m = p["pm"]; o = p["twin"].get("option") or {}
        c = ", ".join(list(p["twin"].get("cme_event") or {})[:1]) or "-"
        qd = m['q'][:38] + (f" [{m['strike_hint']}]" if m.get('strike_hint') else "")
        cd = m.get("_cond"); src = "" if m.get("_cond_src") == "slug" else "*"
        L.append(f"| {i} | {p['score']} | {qd[:46]} | {m['depth_usd']:,} | {m.get('spread','-')} "
                 f"| {cd}{src} | {o.get('sym','-')} | {o.get('gap_days','-')} | {c[:30]} |")
    L += ["", "`*` = 조건날짜를 슬러그에서 못 찾아 endDate 사용 (다지선다 이벤트면 과대추정 가능)",
          "", "## 규칙(자산군)별 최고 페어 — 비교축 선택용", "",
          "| 자산군 | 페어수 | 거래가능 | 최고점수 | 최고 깊이$ | 대표 마켓 |",
          "|---|---|---|---|---|---|"]
    byrule = {}
    for p_ in pairs: byrule.setdefault(p_["rule"], []).append(p_)
    for rule, arr in sorted(byrule.items(), key=lambda x: -max(a["score"] for a in x[1])):
        best = max(arr, key=lambda a: a["score"])
        deep = max(arr, key=lambda a: a["pm"]["depth_usd"])
        tr = [a for a in arr if a.get("tradable")]
        L.append(f"| `{rule[:24]}` | {len(arr)} | {len(tr)} | {best['score']} | {deep['pm']['depth_usd']:,} "
                 f"| {best['pm']['q'][:38]} |")
    L += ["", "## CME 이벤트 컨트랙트 체결 집계 (드롭된 CSV 기준)", ""]
    econ_kw = ("CPI","UNEMPL","GDP","BITCOIN","ETHER","FED","CRUDE","S&P","NASDAQ")
    econ = {k: v for k, v in cme.items() if any(w in k.upper() for w in econ_kw)}
    L.append("**비스포츠 (논문 관심 대상):**")
    for k, v in sorted(econ.items(), key=lambda x: -x[1]["qty"]) or []:
        L.append(f"- {k}: {v['trades']}체결 / {v['qty']:,}계약 / {v['n_days']}일치")
    if not econ: L.append("- (없음)")
    L.append("")
    L.append("**스포츠 상위 5:**")
    for k, v in sorted(cme.items(), key=lambda x: -x[1]["qty"])[:5]:
        L.append(f"- {k}: {v['trades']}체결 / {v['qty']:,}계약")
    if not cme:
        L.append("- (아직 CSV 없음 — cme_drop/ 에 Event_Contract_Swaps_TS.csv 를 넣어라)")
    (OUT/"SCOREBOARD.md").write_text("\n".join(L))

def main():
    stamp = f"{NOW:%Y%m%d_%H}"
    cme = ingest_cme()
    if "--board" in sys.argv:
        snaps = sorted(OUT.glob("scan_*.json.gz"))
        d = json.load(gzip.open(snaps[-1], "rt"))
        build_and = build_pairs(d["pmus"], d["cboe"], cme)
        board(build_and, cme); log("순위표 재생성"); return
    pmus = scan_pmus()
    if not pmus:
        # 점검창 등으로 후보가 비었다. 직전 정상 SCOREBOARD 를 덮어쓰지 않는다.
        # 0페어 순위표로 덮으면 정상 관측치가 소실되고, 다음 사이클까지 오독을 부른다.
        log("degraded — 순위표 갱신 건너뜀 (직전 정상본 보존)")
        return
    cboe = scan_cboe()
    with gzip.open(OUT/f"scan_{stamp}.json.gz", "wt") as f:
        json.dump({"ts": NOW.isoformat(), "pmus": pmus, "cboe": cboe, "cme": cme}, f)
    pairs = build_pairs(pmus, cboe, cme)
    board(pairs, cme)
    log(f"완료: 페어 {len(pairs)}개 → out/SCOREBOARD.md")

if __name__ == "__main__": main()
