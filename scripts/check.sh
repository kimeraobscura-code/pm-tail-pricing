#!/usr/bin/env bash
# 원고 점검 단일 진입점. LLM 호출 0.
#   scripts/check.sh            문체 + 수치 대조
#   scripts/check.sh --strict   재현 경로 부재(gap)도 실패로
set -uo pipefail
cd "$(dirname "$0")/.."
rc=0

echo "── 문체 (Vale)"
if command -v vale >/dev/null 2>&1; then
  vale README.md README.ko.md prereg/*.md || rc=1
else
  echo "  vale 미설치 — brew install vale" ; rc=1
fi

echo
echo "── 수치 대조 (reconcile)"
python3 verify/reconcile.py "$@" || rc=1

echo
echo "── 원장 위생 (verify)"
python3 verify/verify.py || rc=1

exit $rc
