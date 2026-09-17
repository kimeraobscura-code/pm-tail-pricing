#!/usr/bin/env python3
"""extractors — 주장 하나당 '아티팩트에서 그 숫자를 다시 계산하는' 함수 하나.

reconcile.py 가 이걸 호출해 claims.json 의 value 와 대조한다.

왜 탐색이 아니라 명시적 함수인가:
  results/*.json 전체를 훑어 value 와 tol 안에 드는 숫자를 찾는 방식은 해봤더니
  주장 하나당 수십~수백 건이 걸린다(0.17 은 어디에나 있다). 우연히 맞은 숫자로
  초록불이 켜지면 대조를 안 하느니만 못하다. 그래서 '어느 파일 어느 필드를
  어떻게 집계해서 이 수치가 되는가' 를 사람이 한 번 적어둔다.

세 가지 결과만 낸다:
  값 반환    재계산 성공. reconcile 이 tol 로 판정한다.
  External   근거가 이 레포 밖에 있다(세션 계산·다른 머신 raw). 재현 불가로 보고.
  Unmapped   아티팩트가 그 주장을 뒷받침하지 못한다. 고쳐야 할 구멍.
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"


class External(Exception):
    """근거가 레포 밖. 재현 불가."""


class Unmapped(Exception):
    """아티팩트로 이 주장을 되짚을 수 없다."""


def load(name):
    p = RESULTS / name
    if not p.exists():
        raise Unmapped(f"아티팩트 없음: results/{name}")
    return json.load(open(p))


# ── 주장별 추출기 ────────────────────────────────────────────────────────────
# 각 함수는 (값, 산출근거 문자열) 을 돌려준다.

def _pending(artifact, job, what):
    """맥미니 2026-08-25 배치 산출물이 results/ 로 승격되지 않은 주장.

    레포 코드로 재현은 가능하다(스크립트가 여기 있다). 빠진 건 그 코드가 낸 출력이다.
    lib.py:21 의 OUT = analysis/out/ 이 gitignore 라 job 출력은 손으로 골라 옮긴 것만
    results/ 에 있고, job2·job3 은 옮겨지지 않았다.
    """
    raise Unmapped(
        f"{what} — {artifact} 미커밋. {job} 가 내는 출력인데 analysis/out/ 이 gitignore 라 "
        "승격되지 않았고 이 랩탑에는 out/ 자체가 없다. 맥미니 회수가 경로다."
    )


def c1_ladder_rho2():
    # verify1_fits.json 은 KXBTCD·KXETHD 계약별 가격모형 적합(a·b·r2·sigma)이라
    # 사다리 ρ² 와 무관하다. 사다리는 lib.ladder_implied() 이고 job3 가 부른다.
    _pending("results/job3_atten.json", "analysis/job3_atten.py",
             "금 사다리 내재가격의 12시간 지평 ρ²")


def c2_atm_premium():
    _pending("results/job2_summary.json", "analysis/job2_smile.py",
             "금 일별 등가격 내재σ 0.301 (atm_sigma)")


def c2_otm_ratio():
    _pending("results/job2_summary.json", "analysis/job2_smile.py",
             "금 외가격 내재σ / 실현σ (otm_sigma ÷ realized_sigma)")


def c3_paired_low():
    """정체 5분 표본의 저가구간 호가 편의."""
    d = load("job8b_paired_stale5.json")
    band = d["results"]["저가(롱샷)"]
    return band["bias_q"], (
        f"results/job8b_paired_stale5.json .results['저가(롱샷)'].bias_q "
        f"(계약 {d['contracts']}개, 체결 {band['n_t']} / 호가 {band['n_q']})"
    )


def c4_pin_freq():
    # job2_smile.py 가 ask_hist_le15 로 내는 값이다. 레포 밖이 아니라 미커밋이다.
    _pending("results/job2_summary.json", "analysis/job2_smile.py",
             "BTC 일별 외가격 분봉의 최소호가 고착 비율 (ask_hist_le15)")


def c5_decay_ratio():
    """금 주별 감쇠상수 / 일별 감쇠상수. ρ²(h)=A/(1+c/h) 의 c (시간).

    주의: 이전 판은 prereg2_result.json 을 근거로 걸어두고 6.36 을 재계산해
    원장의 6.83 과 불일치(fail)를 냈다. 둘은 서로 다른 두 실행이다.
    사전등록2 는 이벤트 20/10, job3 는 이벤트 38/10 의 규모 확장판이고
    §5 가 인용하는 0.208h/1.421h 는 job3 쪽이다. 없는 불일치였다.
    """
    d = load("prereg2_result.json")
    dd, wk = d.get("KXGOLDD", {}).get("fit"), d.get("KXGOLDW", {}).get("fit")
    ref = ""
    if dd and wk:
        ref = (f" 참고로 사전등록2 는 {wk['c']:.4f}h / {dd['c']:.4f}h = {wk['c']/dd['c']:.2f} "
               f"[이벤트 {d['KXGOLDW']['events_used']}/{d['KXGOLDD']['events_used']}건] 로 다른 값이다.")
    raise Unmapped(
        "금 주별/일별 감쇠상수 비 — results/job3_atten.json 미커밋. "
        "analysis/job3_atten.py 가 내는 출력인데 analysis/out/ 이 gitignore 라 승격되지 않았다."
        + ref
    )


def c7_gld_bleed():
    raise External("출처가 '세션 계산(CBOE)'. 재계산 스크립트 verify/recompute_benchmark.py 도 부재.")


def c8_brier_spread():
    """정산 완료 이벤트별 브라이어 = mean((final_mid - outcome)^2). 주장은 그 최댓값."""
    E = load("event_studies.json")
    bs = []
    for e in E:
        cal = [x for x in e.get("calibration", [])
               if x.get("final_mid") is not None and x.get("outcome") is not None]
        if cal:
            bs.append((sum((x["final_mid"] - x["outcome"]) ** 2 for x in cal) / len(cal),
                       f"{e['series']} {e['event']}"))
    if not bs:
        raise Unmapped("event_studies.json 에 calibration 이 비어 있다.")
    lo, hi = min(bs), max(bs)
    return hi[0], (
        f"results/event_studies.json 이벤트 {len(bs)}건의 브라이어 범위 "
        f"{lo[0]:.4f}({lo[1]}) ~ {hi[0]:.4f}({hi[1]}); 주장은 상단값"
    )


def c9_floor_size():
    """최소호가 잔량 비중의 최댓값."""
    F = load("floor_size.json")
    tk, row = max(F.items(), key=lambda kv: kv[1]["floor_size_share"])
    rt, rrow = max(F.items(), key=lambda kv: kv[1]["ratio"])
    return row["floor_size_share"], (
        f"results/floor_size.json 최대 floor_size_share={row['floor_size_share']} ({tk}); "
        f"최대 ratio={rrow['ratio']}배 ({rt})"
    )


def c10_venue_gap():
    raise External("칼시·PMUS 교차 비교 재계산 스크립트 verify/recompute_venue_gap.py 가 부재. "
                   "event_studies.json 에는 거래소 간 중간가 차 필드가 없다.")


def c11_halt_null():
    raise External("출처가 '맥미니 raw 09-11' 로 레포 밖. 재계산 스크립트 verify/recompute_halt.py 도 부재.")


EXTRACTORS = {
    "C1-ladder-rho2": c1_ladder_rho2,
    "C2-atm-premium": c2_atm_premium,
    "C2-otm-ratio": c2_otm_ratio,
    "C3-paired-low": c3_paired_low,
    "C4-pin-freq": c4_pin_freq,
    "C5-decay-ratio": c5_decay_ratio,
    "C7-gld-bleed": c7_gld_bleed,
    "C8-brier-spread": c8_brier_spread,
    "C9-floor-size": c9_floor_size,
    "C10-venue-gap": c10_venue_gap,
    "C11-halt-null": c11_halt_null,
}
