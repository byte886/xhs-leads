#!/usr/bin/env bash
# Regenerate cc-baseline.txt from the same crapper scan that check_cc.sh uses.
# 基线与门禁必须同源，否则会出现「基线记 14、实测 29」这种永远 FAIL 的假红灯（2026-11-23 修复）。
set -uo pipefail
cd "$(dirname "$0")/../.."
CRAPPER="${CRAPPER_BIN:-$HOME/Doubao/skills/clean-code-gauntlet/tooling/vendor/crapper/crapper}"
if [ ! -x "$CRAPPER" ]; then echo "crapper not found: $CRAPPER" >&2; exit 1; fi
BASE="quality-gates/cc-baseline.txt"
{
  echo "# CC baseline for quality-gates/tools/check_cc.sh"
  echo "# 由 quality-gates/tools/gen_cc_baseline.sh 从 crapper 实测生成（勿手抄，手抄就会和门禁口径漂移）。"
  echo "# rows below are accepted debt; delete a row as soon as that function is split."
  echo "# rule: new over-8 function, or a listed one getting worse, fails the gate."
  echo "# 生成时间：$(date '+%Y-%m-%d')；只许减不许增，拆空一行就删掉该行（此后受 ≤8 硬拦）。"
  "$CRAPPER" -s backend/src --no-coverage \
    | awk 'NF>=3 && $3+0>8 {print $2"."$1" "$3}' | sort
} > "$BASE"
echo "wrote $BASE: $(grep -vc '^#' "$BASE") row(s)"
