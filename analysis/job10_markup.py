#!/usr/bin/env python3
"""job10 — 마크업: 예측시장 이진계약 대 상장옵션 복제 디지털 (BTC, 소급).

논문의 핵심 변수를 처음으로 계산한다. 지금까지 §7 벤치마크(115일 ETF 옵션)와
예측시장(1일 이하)은 만기가 달라 비율을 낼 수 없었다.

해법: Deribit 일별 BTC 옵션. 만기된 인스트루먼트도 이름을 구성하면
get_tradingview_chart_data 로 시간봉이 나온다(무키, 한국 접근 가능, 약 8주 깊이).
일별 만기 옵션의 콜스프레드로 디지털을 복제하면 칼시 이진계약과 같은 $1 지급구조가 된다.

비교 설계
  - 관측 시각을 맞춘다(같은 UTC 시간).
  - 잔존만기는 다르다(칼시 시간별 1~5h, Deribit 일별 ~24h 이내). T 를 각자 것으로 쓰고
    **머니니스를 맞춰** 비교한다. 만기 불일치는 한계로 보고한다.
  - 디지털 = (C(K) - C(K+ΔK)) / ΔK  (Deribit 가격은 BTC 단위 → 현물 곱해 달러 환산)
  - 마크업 = 칼시 이진가 ÷ Deribit 복제 디지털가
  - 양쪽의 최소호가 바닥을 각각 표시한다(칼시 $0.01, Deribit 0.0001 BTC).

출력: out/job10_markup.json + 콘솔 표
python 3.9 호환, 표준 라이브러리만.
"""
import json, math, os, statistics, sys, time, urllib.request, urllib.error
import datetime as dt
from lib import k_candles_1m, OUT, cached

UTC = dt.timezone.utc
UA = {"User-Agent": "Mozilla/5.0"}
DBT = "https://www.deribit.com/api/v2/public"
K = "https://api.elections.kalshi.com/trade-api/v2"
MON = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]

def log(*a): print(*a, file=sys.stderr, flush=True)

def jget(u, tries=3):
    for i in range(tries):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30).read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503): time.sleep(2*(i+1)); continue
            raise
        except Exception:
            if i == tries-1: raise
            time.sleep(1.5*(i+1))
    raise RuntimeError("retries")

def dbt_name(d, strike, cp="C"):
    return f"BTC-{d.day}{MON[d.month-1]}{d.strftime('%y')}-{int(strike)}-{cp}"

def dbt_bars(name, t1, t2, res=60):
    """만기 옵션 포함 시간봉. 없으면 None."""
    def fetch():
        try:
            r = jget(f"{DBT}/get_tradingview_chart_data?instrument_name={name}"
                     f"&start_timestamp={int(t1*1000)}&end_timestamp={int(t2*1000)}&resolution={res}")
            return r.get("result") or {}
        except Exception:
            return {}
    r = cached(f"dbt_{name}_{int(t1)}_{int(t2)}_{res}", fetch)
    if not r.get("ticks"): return None
    return {int(ts/1000)//3600*3600: c for ts, c in zip(r["ticks"], r["close"])}

def btc_spot_hourly(t1, t2):
    """바이낸스 1시간봉 종가. data-api 는 한국에서 열린다."""
    def fetch():
        out, a = [], int(t1*1000)
        while a < t2*1000:
            r = jget("https://data-api.binance.vision/api/v3/klines"
                     f"?symbol=BTCUSDT&interval=1h&startTime={a}&limit=1000")
            if not r: break
            out += r; a = r[-1][0] + 3600_000
        return out
    return {int(x[0]/1000): float(x[4]) for x in cached(f"spot_{int(t1)}_{int(t2)}", fetch)}

def kalshi_settled(series, cap=400):
    return jget(f"{K}/markets?series_ticker={series}&status=settled&limit={cap}").get("markets") or []

def main():
    days_back = int(os.environ.get("MARKUP_DAYS", "45"))
    LEAD = float(os.environ.get("MARKUP_LEAD_H", "1"))*3600
    now = time.time()
    log(f"대상: 최근 {days_back}일 · 칼시 KXBTCD × Deribit 일별 BTC 옵션")

    ms = kalshi_settled("KXBTCD")
    by_close = {}
    for m in ms:
        ct = m.get("close_time")
        if not ct: continue
        t = dt.datetime.fromisoformat(ct.replace("Z", "+00:00")).timestamp()
        if now - t > days_back*86400: continue
        try: strike = float(m["ticker"].split("-T")[1])
        except Exception: continue
        by_close.setdefault(int(t), []).append((strike, m))
    log(f"칼시 정산 계약 {sum(len(v) for v in by_close.values()):,}개 / 만기시각 {len(by_close)}개")

    spot = btc_spot_hourly(now - (days_back+2)*86400, now)
    rows = []
    for ct in sorted(by_close):
        cd = dt.datetime.fromtimestamp(ct, UTC)
        # 잔존만기를 맞춘다. 칼시 관측은 만기 1시간 전, Deribit 관측도 그 옵션 만기 1시간 전.
        # 달력 시각은 다르지만 T 가 같다 — 만기 불일치가 결과를 지배하던 1차 설계의 수정.
        obs = ct - LEAD                       # 칼시 관측 시각
        if obs not in spot: continue
        dday = cd.date()
        dexp = dt.datetime(dday.year, dday.month, dday.day, 8, tzinfo=UTC).timestamp()
        dobs = dexp - LEAD                    # Deribit 관측 시각 (같은 T)
        if dobs not in spot: continue
        Sd = spot[dobs]
        S = spot[obs]
        # Deribit 체인은 자기 관측시각(dobs)·자기 현물(Sd) 기준
        lo = int((Sd*0.80)//2000*2000); hi = int((Sd*1.20)//2000*2000)
        chain = {}
        for k in range(lo, hi+2000, 2000):
            b = dbt_bars(dbt_name(dday, k), dobs-7200, dobs+3600)
            if b and dobs in b: chain[k] = b[dobs]*Sd   # BTC → USD
        if len(chain) < 4: continue
        ks = sorted(chain)
        T_k = max((ct-obs)/31536000, 1/31536000)
        T_d = max((dexp-obs)/31536000, 1/31536000)
        for strike, m in sorted(by_close[ct]):
            # Deribit 콜스프레드로 같은 행사가 디지털 복제 (선형보간)
            # 머니니스를 맞춘다: 칼시 행사가/S 와 같은 비율의 Deribit 행사가
            mny = strike/S
            dstrike = mny*Sd
            below = [k for k in ks if k <= dstrike]; above = [k for k in ks if k > dstrike]
            if not below or not above: continue
            k1, k2 = below[-1], above[0]
            dig = (chain[k1]-chain[k2])/(k2-k1)         # 0~1  (같은 머니니스 지점)
            if not (0 < dig < 1): continue
            pm = m.get("last_price_dollars")
            try: pm = float(pm) if pm is not None else None
            except (TypeError, ValueError): pm = None
            if pm is None or not (0 < pm < 1): continue
            floor_k = (pm <= 0.02)
            floor_d = (chain[k1] <= 0.0002*S or chain[k2] <= 0.0002*S)
            rows.append({"close": ct, "obs": obs, "spot": round(S,1), "strike": strike,
                         "moneyness": round(strike/S, 4), "pm": pm, "digital": round(dig,4),
                         "markup": round(pm/dig, 3), "T_kalshi_h": round((ct-obs)/3600,2),
                         "T_deribit_h": round((dexp-obs)/3600,2),
                         "floor_kalshi": floor_k, "floor_deribit": floor_d,
                         "result": m.get("result")})
        log(f"  {cd:%m-%d %H:%M}Z  체인 {len(chain)}  누적 {len(rows)}")

    if not rows:
        print("표본 없음 — Deribit 체인 또는 칼시 정산가 부족"); return
    json.dump(rows, open(OUT/"job10_markup.json","w"))

    def band(r):
        m = r["moneyness"]
        return "깊은외가격 >1.05" if m>1.05 else ("외가격 1.01~1.05" if m>1.01 else
               ("등가격 0.99~1.01" if m>=0.99 else "내가격 <0.99"))
    print(f"\n{'구간':<18}{'n':>5}{'마크업 중앙':>12}{'25%':>9}{'75%':>9}{'PM 바닥%':>10}{'DBT 바닥%':>10}")
    for b in ("내가격 <0.99","등가격 0.99~1.01","외가격 1.01~1.05","깊은외가격 >1.05"):
        g=[r for r in rows if band(r)==b]
        if len(g)<5: continue
        mk=sorted(r["markup"] for r in g)
        print(f"{b:<18}{len(g):>5}{statistics.median(mk):>12.2f}"
              f"{mk[len(mk)//4]:>9.2f}{mk[3*len(mk)//4]:>9.2f}"
              f"{sum(r['floor_kalshi'] for r in g)/len(g)*100:>9.0f}%"
              f"{sum(r['floor_deribit'] for r in g)/len(g)*100:>9.0f}%")
    clean=[r for r in rows if not r["floor_kalshi"] and not r["floor_deribit"]]
    print(f"\n양쪽 모두 바닥 아닌 표본 {len(clean):,}/{len(rows):,}")
    if clean:
        mk=sorted(r["markup"] for r in clean)
        print(f"  마크업 중앙 {statistics.median(mk):.2f}  [{mk[len(mk)//20]:.2f}, {mk[19*len(mk)//20]:.2f}] (5~95%)")
    tk=statistics.median(r['T_kalshi_h'] for r in rows); td=statistics.median(r['T_deribit_h'] for r in rows)
    print(f"\n잔존만기: 칼시 {tk:.1f}h vs Deribit {td:.1f}h (설계상 동일)  "
          f"| 관측 달력시각은 다름 — 한계로 보고")
    print(f"저장: out/job10_markup.json")

if __name__ == "__main__": main()
