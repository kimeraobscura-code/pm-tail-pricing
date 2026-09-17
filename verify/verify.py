#!/usr/bin/env python3
"""verify — 논문 주장 원장의 자가 점검 루프.

claims.json 의 각 주장에 대해:
  1) 근거 파일이 존재하고 비어 있지 않은가
  2) 저장된 값이 현재 산출물과 일치하는가 (tol 이내)
  3) README 에 그 수치가 실제로 적혀 있는가 (문서-데이터 동기화)
  4) status 가 confirmed 인데 caveat 이 달려 있지 않은가 (등급 정합성)

/loop 로 주기 실행하면, 분석을 다시 돌리거나 글을 고칠 때 생기는 drift 를 잡는다.
이 프로젝트에서 수동으로 두 번 잡은 오류(계약군 불일치·월물 혼입)가 정확히
이 점검이 자동으로 잡았어야 할 종류다.

사용: python3 verify.py            점검만
      python3 verify.py --json     기계 판독 출력
python 3.9 호환.
"""
import json, pathlib, re, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
# ROOT 가 이미 레포 루트다. 예전엔 REPO = ROOT/"pm-tail-pricing" 이라 늘 없는 경로를
# 가리켰고, readmes 가 빈 문자열이 되어 3)번 문서-데이터 동기화 검사가 조용히
# 통과하고 있었다. 값 대조(2번)는 reconcile.py 가 맡는다.
REPO = ROOT
# 원고가 유니코드 빼기표(−)를 쓰므로 정규화하지 않으면 부호 있는 수치를 놓친다.
DASHES = str.maketrans({"−":"-","–":"-","—":"-","‐":"-"," ":" "})
def num_in(text, v, tol):
    if v is None: return True
    text = text.translate(DASHES)
    for m in re.finditer(r"-?\d+\.?\d*", text.replace(",", "")):
        try: x=float(m.group())
        except ValueError: continue
        if abs(x-v) <= (tol if tol else 1e-9) or (v and abs(x-abs(v)*100)<=(tol or 0)*100): return True
    return False

def main():
    C = json.load(open(pathlib.Path(__file__).parent/"claims.json"))["claims"]
    readmes = ""
    for f in ("README.md","README.ko.md"):
        p = REPO/f
        if p.exists(): readmes += p.read_text()
    issues, ok = [], 0
    for c in C:
        cid, st = c["id"], c["status"]
        if st == "retracted":
            if c["claim"][:12] in readmes and "철회" not in readmes and "retract" not in readmes.lower():
                issues.append((cid, "철회한 주장이 README 에 경위 없이 남아 있다"))
            else: ok += 1
            continue
        src = c.get("source","")
        for cand in (REPO/src, ROOT/src):
            if cand.exists():
                if cand.stat().st_size < 50:
                    issues.append((cid, f"근거 파일이 비어 있다: {src}"))
                break
        else:
            if "/" in src and not src.startswith(("세션","미계산")):
                issues.append((cid, f"근거 파일 없음: {src}"))
        if st == "confirmed" and c.get("caveat"):
            issues.append((cid, "confirmed 인데 caveat 이 달려 있다 → provisional 로 내릴 것"))
        if c.get("value") is not None and readmes and not num_in(readmes, c["value"], c.get("tol")):
            issues.append((cid, f"값 {c['value']} 이 README 에서 발견되지 않음 (글-데이터 불일치 의심)"))
        if not issues or issues[-1][0] != cid: ok += 1
    byst = {}
    for c in C: byst[c["status"]] = byst.get(c["status"],0)+1
    if "--json" in sys.argv:
        print(json.dumps({"ok":ok,"issues":issues,"status":byst}, ensure_ascii=False)); return
    print(f"주장 {len(C)}건 — " + " · ".join(f"{k} {v}" for k,v in sorted(byst.items())))
    print(f"통과 {ok} · 지적 {len(issues)}\n")
    for cid, msg in issues: print(f"  [{cid}] {msg}")
    if not issues: print("  지적 없음")
    op = [c for c in C if c["status"]=="open"]
    if op:
        print(f"\n미결 {len(op)}건 (논문에 '후속 과제'로 명시되어야 함):")
        for c in op: print(f"  [{c['id']}] {c['claim']}")

if __name__ == "__main__": main()
