# 원고 점검 루프

쓰고 → 검토하고 → 구멍 찾고 → 다시 고치는 루프. 기계가 잡을 수 있는 것은 전부
기계가 먼저 잡고, 모델은 판단이 필요한 것만 본다.

## 한 줄 실행

```bash
bash scripts/check.sh            # 문체 + 수치 대조. LLM 호출 0
bash scripts/check.sh --strict   # 재현 경로 부재(gap)도 실패로 취급
```

종료코드 1 이면 고칠 것이 있다는 뜻이다. 커밋 훅이 같은 스크립트를 돌린다.

## 세 층

### 1층 — 문체 (Vale, 결정적)

`vale` 가 `README.md` · `README.ko.md` · `prereg/*.md` 를 본다. 규칙은
`styles/PaperVoice/` 에 있고 전부 직접 쓴 것이다. 네트워크도 `vale sync` 도 필요 없다.

| 규칙 | 등급 | 잡는 것 |
|---|---|---|
| `AiTells` | error | "It is worth noting", "plays a crucial role", "sheds light on" 류 20종 |
| `NotOnly` | error | "not only X but also Y" 구문 |
| `Units` | error | `pp` 와 `percentage points` 를 섞어 쓰는 것 |
| `Overclaim` | warning | novel / unprecedented / proves that / clearly shows 등 13종 |
| `Significance` | warning | `significant(ly)` — 통계적 유의성과 혼동되는 자리 |
| `Hedge` | suggestion | very · rather · in order to 같은 군더더기 |
| `Enumeration` | suggestion | First, / Second, / Finally, 로 시작하는 문단 |
| `EmDash` | 기본 off | 대시. 이 원고는 의도적으로 쓴다 |

도메인 용어 사전은 `styles/config/vocabularies/Paper/accept.txt`. 새 고유명사를
쓰기 시작하면 여기 한 줄 추가한다. 문장 첫머리에 올 수 있는 일반명사는
`[Ww]eeklies` 처럼 대소문자 양쪽을 쓴다.

`EmDash` 를 켜려면 `.vale.ini` 의 `PaperVoice.EmDash = NO` 를 `YES` 로.

주의: `.vale.ini` 의 값은 쉼표로 분리된다. 정규식에 `{1,60}` 같은 수량자를 쓰면
거기서 잘린다. 규칙 `.yml` 파일 안에서는 상관없다.

### 2층 — 수치 대조 (reconcile, 결정적)

```bash
python3 verify/reconcile.py
```

`verify/claims.json` 의 각 주장을 `results/` 의 아티팩트까지 되짚는다. 주장 하나당
추출기 함수 하나가 `verify/extractors.py` 에 있고, 그 함수가 아티팩트에서 숫자를
다시 계산한다.

되짚는 방식이 왜 '탐색'이 아니라 '명시적 함수'인가: `results/*.json` 전체에서
`value ± tol` 안에 드는 숫자를 찾는 방식을 해봤더니 주장 하나당 수십~수백 건이
걸린다. `0.17` 은 어디에나 있다. 우연히 맞은 숫자로 초록불이 켜지면 대조를 안
하느니만 못하다.

검사 일곱 가지와 심각도 세 등급은 `verify/reconcile.py` 의 문서화 문자열에 적혀
있다. 요약하면

- `fail` — 값 불일치 · 근거 파일 부재 · 철회 주장 잔존 · 등급 부정합. 커밋을 막는다
- `gap` — 재현 경로가 끊김(레포 밖 근거, 스크립트 부재). 보고만 한다
- `drift` — 원고와 원장이 어긋남. 보고만 한다

`gap` 을 막지 않는 이유는 아직 여러 건이 열려 있어 전부 막으면 아무 커밋도 안 되기
때문이다. 현재 수치는 `bash scripts/check.sh` 첫 줄에 찍힌다. 제출 직전에는
`--strict` 로 돌려서 전부 닫는 것이 목표다.

`verify/verify.py` 는 원장 위생(파일 존재·등급 정합)만 보는 더 가벼운 검사다.
수치 대조는 `reconcile.py` 가 맡는다.

새 주장을 원장에 올렸으면 `extractors.py` 에 추출기도 같이 쓴다. 추출기는
`(값, 산출근거 문자열)` 을 돌려주고, 근거 문자열에 어느 파일 어느 필드를 어떻게
집계했는지 적는다. 되짚을 수 없으면 `Unmapped`, 레포 밖 계산이면 `External` 을
던진다. 조용히 통과시키지 않는다.

### 3층 — 심사 (모델)

```
/seven-pass-review              # 7차원 병렬 적대적 심사 + 반증 검증
/audit-reproducibility          # 원고 수치 → 아티팩트 되짚기 감사
/audit-reproducibility --fix    # 원장·추출기까지 실제로 갱신
```

정의는 `.claude/commands/` 에 있다. 두 커맨드 모두 **원고 본문은 고치지 않는다.**
심사와 수정을 분리해야 헛지적이 원고를 망가뜨리지 않는다. 결과는 `review/` 에
날짜별 파일로 쌓인다.

`/seven-pass-review` 는 지적을 낸 뒤 각각을 기각하려 드는 검증 패스를 한 번 더
돌린다. 이 단계를 건너뛰면 그럴듯한 헛지적이 남는다.

## 훅

- `.claude/settings.json` — 원고 마크다운을 편집할 때마다 그 파일에만 Vale 을
  돌린다(`scripts/lint-hook.sh`). `warning` 이상만 보여주고, `error` 면 종료코드 2 로
  그 자리에서 고치게 한다. 턴이 끝날 때 `reconcile.py` 요약 3줄을 찍는다.
- `.githooks/pre-commit` — `scripts/check.sh`. `git config core.hooksPath .githooks`
  가 이미 걸려 있다. 급할 때는 `git commit --no-verify`.

## 제출 전 체크리스트

```bash
bash scripts/check.sh --strict
```

- [ ] `fail` 0
- [ ] `gap` 0 — 레포 밖 근거를 스크립트로 옮기거나, 그 수치를 원고에서 뺀다
- [ ] `drift` 0 — 원고 본문의 숫자와 원장이 일치
- [ ] `/seven-pass-review` 의 fatal·major 소진
- [ ] `open` 상태 주장이 원고에 '후속 과제'로 명시돼 있다
