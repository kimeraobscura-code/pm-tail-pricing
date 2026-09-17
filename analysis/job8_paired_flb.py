#!/usr/bin/env python3
"""job8 — 호가 편의와 체결가 편의의 짝지은 차이 검정.

job1(호가 중간값, 계약 3,064)과 job5(체결가, 계약 20,235)는 계약군이 달라
두 추정치를 나란히 놓고 "체결 시점에 편의가 줄었다"고 말할 수 없다.
두 표본의 신뢰구간도 겹친다(저가 [-1.39,-0.74], 고가 [+1.98,+2.55]).

이 잡은 교집합 계약만 남기고 계약별 차이 d_i = (호가편의_i) - (체결편의_i) 를
구한 뒤, 그 차이에 계약 단위 부트스트랩을 적용해 신뢰구간을 하나만 낸다.
0을 포함하면 "차이 없음"이며, 그 경우 §3 의 방향성 주장도 철회해야 한다.

맥미니에서 실행:
  cd ~/Downloads/hs-macmini-20260822/hs-research   # job1/job5 산출물이 있는 곳
  python3 <이 파일> --out1 out/job1_obs.jsonl --out5 out/job5_obs.jsonl
python 3.9 호환.
"""
import argparse, json, math, pathlib, random, sys, collections

LOW, HIGH = 0.15, 0.85

def load(path, price_key, out_key):
    """관측 JSONL -> {ticker: [(implied, realized), ...]}"""
    per = collections.defaultdict(list)
    with open(path, errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln: continue
            try: r = json.loads(ln)
            except Exception: continue
            tk = r.get("ticker") or r.get("market") or r.get("id")
            p  = r.get(price_key); y = r.get(out_key)
            if tk is None or p is None or y is None: continue
            try: p = float(p); y = float(y)
            except (TypeError, ValueError): continue
            if 0.0 < p < 1.0: per[tk].append((p, y))
    return per

def band_bias(obs, lo, hi):
    """구간 내 (실현 - 함축) 평균. 관측 없으면 None."""
    v = [(y - p) for p, y in obs if lo <= p < hi]
    return (sum(v) / len(v)) if v else None

def boot(diffs, B=2000, seed=17):
    """계약 단위 부트스트랩. diffs 는 계약별 차이 리스트."""
    if len(diffs) < 8: return None
    rnd = random.Random(seed); n = len(diffs); means = []
    for _ in range(B):
        s = [diffs[rnd.randrange(n)] for _ in range(n)]
        means.append(sum(s) / n)
    means.sort()
    return (sum(diffs)/n, means[int(0.025*B)], means[int(0.975*B)])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out1", required=True, help="job1 관측 JSONL (호가)")
    ap.add_argument("--out5", required=True, help="job5 관측 JSONL (체결)")
    ap.add_argument("--price1", default="implied"); ap.add_argument("--y1", default="realized")
    ap.add_argument("--price5", default="implied"); ap.add_argument("--y5", default="realized")
    ap.add_argument("--B", type=int, default=2000)
    a = ap.parse_args()

    q = load(a.out1, a.price1, a.y1)
    t = load(a.out5, a.price5, a.y5)
    common = sorted(set(q) & set(t))
    print(f"호가 계약 {len(q):,} / 체결 계약 {len(t):,} / 교집합 {len(common):,}")
    if len(common) < 30:
        print("교집합이 너무 작다. 필드명(--price1/--y1 등)을 산출물 스키마에 맞춰라.")
        print("샘플 키:", list(next(iter(q.values()))[:1]) if q else None)
        return

    for name, lo, hi in (("저가(롱샷)", 0.0, LOW), ("고가(즐겨찾기)", HIGH, 1.0)):
        diffs = []
        for tk in common:
            bq = band_bias(q[tk], lo, hi); bt = band_bias(t[tk], lo, hi)
            if bq is None or bt is None: continue
            diffs.append(bq - bt)
        r = boot(diffs, a.B)
        if r is None:
            print(f"{name}: 짝지은 계약 {len(diffs)}개 — 표본 부족"); continue
        m, lo_ci, hi_ci = r
        sig = "유의" if (lo_ci > 0 or hi_ci < 0) else "비유의(0 포함)"
        print(f"{name}: 짝지은 계약 {len(diffs):,}개  차이 {m*100:+.2f}%p "
              f"[{lo_ci*100:+.2f}, {hi_ci*100:+.2f}]  → {sig}")
    print("\n판정: 두 구간 모두 비유의면 §3 의 '호가가 체결보다 크다'는 방향성 주장도 철회한다.")

if __name__ == "__main__": main()
