---
description: 원고의 모든 수치를 아티팩트까지 되짚고, 되짚기 실패한 것과 재현 경로가 끊긴 것을 보고한다
argument-hint: "[--fix] 원장·추출기를 실제로 갱신까지 할지"
allowed-tools: Read, Grep, Glob, Bash, Write, Edit
---

# 재현성 감사

## 1. 기계 점검 먼저

```bash
python3 verify/reconcile.py
```

`fail` / `gap` / `drift` 의 뜻은 `verify/reconcile.py` 문서화 문자열에 있다. 이 출력이 감사의 출발점이다. 여기서 초록불인 항목은 다시 검사하지 않는다.

## 2. 원고에만 있는 수치 찾기

원장(`verify/claims.json`)은 원고의 **모든** 수치를 담아야 한다. 실제로는 글을 고치면서 원장에 안 올라간 숫자가 생긴다.

대상 원고에서 수치를 전부 뽑고, `claims.json` 의 어느 항목에도 대응하지 않는 것을 나열한다. 표 안의 숫자, 괄호 안 신뢰구간, n 값도 수치다. 각각에 대해 판정한다.

- 결론을 떠받치는 수치다 → 원장에 새 항목으로 올릴 것 (id·status·source·recompute·where·tol 채워서)
- 맥락 설명일 뿐이다 → 그냥 둔다. 다만 출처는 본문에 밝혀야 한다

## 3. 되짚기

원장의 각 항목에 대해 `results/` 의 파일을 **직접 열어** 그 수치가 실제로 나오는지 확인한다. `reconcile.py` 가 `gap` 으로 표시한 항목이 표적이다. 판정은 셋 중 하나다.

- **되짚힌다** → `verify/extractors.py` 에 추출기 함수를 추가한다. 함수는 `(값, 산출근거 문자열)` 을 돌려주고, 어느 파일 어느 필드를 어떻게 집계했는지 근거 문자열에 적는다.
- **아티팩트가 없다** → `Unmapped` 를 던지는 추출기를 쓰고, 어떤 아티팩트를 커밋해야 하는지 메시지에 적는다.
- **레포 밖 계산이다** → `External` 을 던진다. 그리고 그 계산을 스크립트로 옮길 것인지, 원고에서 그 수치를 뺄 것인지 결정해야 한다고 보고한다.

## 4. 재현 경로

레포만 받은 제3자 기준으로 확인한다.

```bash
ls analysis/ verify/
python3 -c "import json;[print(c['id'], c.get('recompute')) for c in json.load(open('verify/claims.json'))['claims']]"
```

- `recompute` 에 적힌 스크립트가 실재하는가
- `README` 의 재현 절차가 실제 파일명과 맞는가
- 필요한 키·계정이 명시돼 있는가
- `.gitignore` 된 경로(`out/`, `raw/`, `cache/`)를 원장이나 원고가 근거로 가리키고 있지 않은가 — 가리키고 있으면 제3자는 그 파일을 못 본다

## 5. 보고

`review/` 에 `repro-<YYYY-MM-DD>.md` 로 쓴다.

```markdown
# 재현성 감사 — <날짜>
되짚힘 N / 전체 N · 아티팩트 부재 N · 레포 밖 N · 스크립트 부재 N

## 제출 전 반드시
- [ ] ...

## 원장에 없는 원고 수치
| 수치 | 위치 | 판정 |
```

`--fix` 가 주어졌으면 `verify/extractors.py` 와 `verify/claims.json` 을 실제로 고친다. 이때도 **원고 본문은 건드리지 않는다.** 수치가 틀렸다면 무엇을 무엇으로 바꿔야 하는지 보고만 하고, 고치는 건 사람이 판단한다.

고친 뒤에는 반드시 다시 돌려 초록불을 확인한다.

```bash
python3 verify/reconcile.py
```
