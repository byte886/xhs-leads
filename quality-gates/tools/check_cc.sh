#!/usr/bin/env bash
# CC gate: executes gates.yaml "cyclomatic complexity <= 8 per function".
set -uo pipefail
cd "$(dirname "$0")/../.."
CRAPPER="${CRAPPER_BIN:-$HOME/Doubao/skills/clean-code-gauntlet/tooling/vendor/crapper/crapper}"
if [ ! -x "$CRAPPER" ]; then echo "CC gate SKIP: crapper not found ($CRAPPER)"; exit 2; fi
BASE="quality-gates/cc-baseline.txt"
ACT=$("$CRAPPER" -s backend/src --no-coverage | awk 'NF>=3 && $3+0>8 {print $2"."$1" "$3}' | sort)
if [ -z "$ACT" ]; then echo "CC gate PASS: all functions <= 8"; exit 0; fi
COUNT=$(printf '%s\n' "$ACT" | grep -c .)
echo "CC gate: $COUNT function(s) over 8 (baseline=$(grep -vc '^#' "$BASE" 2>/dev/null || echo 0))"
FAIL=0
while read -r key cc; do
  [ -z "$key" ] && continue
  bcc=$(awk -v k="$key" '$1==k{print $2}' "$BASE" 2>/dev/null)
  if [ -z "$bcc" ]; then echo "  FAIL(new): $key CC=$cc"; FAIL=1
  elif [ "$cc" -gt "$bcc" ]; then echo "  FAIL(regressed): $key CC=$cc > baseline $bcc"; FAIL=1
  else echo "  KNOWN(debt): $key CC=$cc (baseline $bcc)"; fi
done <<< "$ACT"
if [ "$FAIL" -eq 0 ]; then echo "CC gate PASS (known debt only)"; fi
exit "$FAIL"
