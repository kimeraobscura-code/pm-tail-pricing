#!/usr/bin/env python3
"""reconcile — 원고의 수치 ↔ 커밋된 아티팩트 대조.

claims.json 의 각 주장에 대해 7가지를 본다.

  VALUE    extractors.py 가 아티팩트에서 재계산한 값이 claims.json 의 value 와 tol 안인가
  SOURCE   근거 파일이 실재하고 비어 있지 않은가
  SCRIPT   recompute 에 적힌 재계산 스크립트가 실재하는가
  SECTION  where 가 가리키는 원고 절이 실재하는가
  PROSE    그 절의 본문에 그 수치가 실제로 적혀 있는가 (글-데이터 동기화)
  GRADE    confirmed 인데 caveat 이 달려 있지 않은가
  RETRACT  철회한 주장이 경위 없이 원고에 남아 있지 않은가

심각도
  fail  즉시 고칠 것. 기본으로 종료코드 1 (pre-commit 이 막는다)
  gap   재현 경로가 없다. 보고만 하고 통과. --strict 를 주면 fail 로 승격
  drift 원고와 원장이 어긋난다. 보고만 하고 통과

사용
  python3 verify/reconcile.py              사람이 읽는 보고
  python3 verify/reconcile.py --strict     gap 도 실패로 취급
  python3 verify/reconcile.py --json       기계 판독
python 3.9 호환.
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from extractors import EXTRACTORS, External, Unmapped  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANUSCRIPTS = ("README.md", "README.ko.md")

FAIL, GAP, DRIFT = "fail", "gap", "drift"


def sections(text):
    """'### 3. 제목' 형태의 절을 번호 -> 본문 으로."""
    out, cur, buf = {}, None, []
    for line in text.splitlines():
        m = re.match(r"^#{2,4}\s*(\d+)\.\s", line)
        if m:
            if cur is not None:
                out[cur] = "\n".join(buf)
            cur, buf = int(m.group(1)), [line]
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf)
    return out


# 원고는 유니코드 빼기표(−, U+2212)와 여러 대시를 쓴다. 정규화하지 않으면
# 부호 있는 수치를 전부 놓쳐서 PROSE 검사가 조용히 오탐을 낸다.
DASHES = str.maketrans({"−": "-", "–": "-", "—": "-", "‐": "-", " ": " "})


def has_number(text, v, tol):
    """본문에 그 수치가 있는가. 백분율·퍼센트포인트 표기까지 같이 본다."""
    tol = tol or 1e-9
    text = text.translate(DASHES)
    for m in re.finditer(r"-?\d+(?:,\d{3})*(?:\.\d+)?", text):
        try:
            x = float(m.group().replace(",", ""))
        except ValueError:
            continue
        if abs(x - v) <= tol or abs(x - v * 100) <= tol * 100 or abs(x / 100 - v) <= tol:
            return True
    return False


def check(claim, docs, secs):
    """주장 하나에 대한 지적 목록 [(심각도, 항목, 메시지)]."""
    out = []
    cid, status = claim["id"], claim["status"]
    v, tol = claim.get("value"), claim.get("tol")

    # RETRACT — 철회 주장이 경위 없이 남아 있는가
    if status == "retracted":
        head = claim["claim"][:14]
        for name, text in docs.items():
            if head in text and not re.search(r"철회|retract", text, re.I):
                out.append((FAIL, "RETRACT", f"철회한 주장이 {name} 에 경위 없이 남아 있다"))
        return out

    # GRADE — 등급 정합성
    if status == "confirmed" and claim.get("caveat"):
        out.append((FAIL, "GRADE", "confirmed 인데 caveat 이 달려 있다 → provisional 로 내릴 것"))

    # SOURCE — 근거 파일 실재
    src = claim.get("source", "")
    if "/" in src:
        p = ROOT / src
        if not p.exists():
            out.append((FAIL, "SOURCE", f"근거 파일 없음: {src}"))
        elif p.stat().st_size < 50:
            out.append((FAIL, "SOURCE", f"근거 파일이 비어 있다: {src}"))

    # SCRIPT — 재계산 스크립트 실재
    rec = claim.get("recompute", "")
    if rec and not (ROOT / rec).exists():
        out.append((GAP, "SCRIPT", f"재계산 스크립트 없음: {rec} (재현 불가)"))

    # VALUE — 아티팩트에서 재계산해 대조
    fn = EXTRACTORS.get(cid)
    if fn is None:
        if v is not None:
            out.append((GAP, "VALUE", "추출기 미작성 → 대조 안 됨. extractors.py 에 함수를 추가할 것"))
    else:
        try:
            got, prov = fn()
        except External as e:
            out.append((GAP, "VALUE", f"레포 밖 근거라 재현 불가 — {e}"))
        except Unmapped as e:
            out.append((GAP, "VALUE", f"아티팩트가 주장을 뒷받침 못 함 — {e}"))
        except Exception as e:  # 추출기 자체가 깨진 경우
            out.append((FAIL, "VALUE", f"추출기 예외: {type(e).__name__}: {e}"))
        else:
            claim["_recomputed"], claim["_provenance"] = got, prov
            if v is None:
                out.append((DRIFT, "VALUE", f"원장에 value 가 없다. 재계산값 {got:.6g} 를 적을 것 — {prov}"))
            elif abs(got - v) > (tol or 1e-9):
                out.append((FAIL, "VALUE",
                            f"불일치: 원장 {v} (tol {tol}) vs 재계산 {got:.6g} — {prov}"))

    # SECTION / PROSE — 원고 동기화
    where = claim.get("where", "")
    m = re.search(r"§\s*(\d+)", where)
    if m:
        n = int(m.group(1))
        for name, sec in secs.items():
            if n not in sec:
                out.append((DRIFT, "SECTION", f"{name} 에 §{n} 절이 없다"))
            elif v is not None and not (v == 0 and not tol) and not has_number(sec[n], v, tol):
                out.append((DRIFT, "PROSE", f"{name} §{n} 본문에 {v} 가 안 보인다 (글-데이터 불일치 의심)"))
    return out


def main():
    strict = "--strict" in sys.argv
    claims = json.load(open(ROOT / "verify" / "claims.json"))["claims"]
    docs = {n: (ROOT / n).read_text() for n in MANUSCRIPTS if (ROOT / n).exists()}
    secs = {n: sections(t) for n, t in docs.items()}

    rows = []
    for c in claims:
        rows.append((c, check(c, docs, secs)))

    counts = {FAIL: 0, GAP: 0, DRIFT: 0}
    for _, iss in rows:
        for sev, _, _ in iss:
            counts[sev] += 1

    if "--json" in sys.argv:
        print(json.dumps({
            "counts": counts,
            "claims": [{"id": c["id"], "status": c["status"],
                        "recomputed": c.get("_recomputed"),
                        "provenance": c.get("_provenance"),
                        "issues": [{"severity": s, "kind": k, "message": m} for s, k, m in iss]}
                       for c, iss in rows],
        }, ensure_ascii=False, indent=1))
        return 1 if counts[FAIL] or (strict and counts[GAP]) else 0

    by_status = {}
    for c in claims:
        by_status[c["status"]] = by_status.get(c["status"], 0) + 1
    print("주장 {} 건 — {}".format(
        len(claims), " · ".join("{} {}".format(k, v) for k, v in sorted(by_status.items()))))
    print("fail {} · gap {} · drift {}\n".format(counts[FAIL], counts[GAP], counts[DRIFT]))

    mark = {FAIL: "✗", GAP: "△", DRIFT: "·"}
    for c, iss in rows:
        if not iss:
            got = c.get("_recomputed")
            tail = "  재계산 {:.6g} 일치".format(got) if got is not None else ""
            print("  ✓ [{}]{}".format(c["id"], tail))
            if c.get("_provenance"):
                print("      {}".format(c["_provenance"]))
            continue
        print("  [{}] {}".format(c["id"], c["claim"][:64]))
        for sev, kind, msg in iss:
            print("      {} {:8s} {}".format(mark[sev], kind, msg))

    op = [c for c in claims if c["status"] == "open"]
    if op:
        print("\n미결 {} 건 (원고에 '후속 과제'로 명시되어야 함):".format(len(op)))
        for c in op:
            print("  [{}] {}".format(c["id"], c["claim"]))

    bad = counts[FAIL] or (strict and counts[GAP])
    print("\n{}".format("실패 — 위 ✗ 를 고칠 것" if bad else "통과 (✗ 없음)"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
