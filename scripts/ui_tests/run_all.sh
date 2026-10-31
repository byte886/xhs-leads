#!/usr/bin/env bash
# run_all.sh — 一键跑全套 UI 自动化回归（headless Chrome + Playwright CDP）
#
# 用法：
#   bash scripts/ui_tests/run_all.sh            # 跑全部（数据依赖脚本自动 SKIP）
#   bash scripts/ui_tests/run_all.sh t03 t06    # 只跑指定编号（前缀匹配）
#
# 依赖：
#   - 后端已启动在 127.0.0.1:8791（详见 README「后端启停」）
#   - Python venv 在 backend/.venv（无则自动回退系统 python3 并提示装 playwright）
#   - Google Chrome（默认标准安装路径；可用 CHROME 环境变量指定）
#
# 退出码：全部 PASS（无 FAIL）→ 0；有 FAIL → 1（SKIP 不计失败）
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# ---- Chrome 探测（macOS 标准路径 → 环境变量 → PATH） ----
CHROME="${CHROME:-}"
if [ -z "$CHROME" ]; then
  for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
           "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
    [ -x "$c" ] && { CHROME="$c"; break; }
  done
fi
if [ -z "$CHROME" ]; then
  CHROME="$(command -v google-chrome 2>/dev/null || command -v chromium 2>/dev/null || true)"
fi
if [ -z "$CHROME" ]; then
  echo "✗ 找不到 Chrome。请安装后重试，或用环境变量指定：CHROME=/path/to/chrome bash scripts/ui_tests/run_all.sh"
  exit 1
fi

# ---- Python 解释器 ----
PY=backend/.venv/bin/python
if [ ! -x "$PY" ]; then
  PY=python3
  python3 -c "import playwright" 2>/dev/null || { echo "✗ 缺 venv 且系统 python3 无 playwright"; exit 1; }
fi

# ---- 启动 headless 调试 Chrome（9333） ----
"$CHROME" --headless=new --disable-gpu --no-sandbox --remote-debugging-port=9333 about:blank >/dev/null 2>&1 &
sleep 3

# ---- 后端就绪检查 ----
if ! curl -s -o /dev/null http://127.0.0.1:8791/ ; then
  echo "✗ 后端 127.0.0.1:8791 未启动。先启动后端再跑 UI 测试（见 README「后端启停」）。"
  pkill -f "remote-debugging-port=9333" 2>/dev/null
  exit 1
fi

# ---- 顺序执行 ----
FILTER="${1:-}"
PASS=0; FAIL=0; SKIP=0; FAILED_NAMES=()
for f in scripts/ui_tests/t[0-9]*.py; do
  name="$(basename "$f")"
  if [ -n "$FILTER" ]; then
    hit=0
    for want in "$@"; do
      case "$name" in ${want}*) hit=1; break;; esac
    done
    [ "$hit" = "1" ] || continue
  fi
  if PYTHONPATH=backend/src "$PY" "$f" >/tmp/ui_out.$$ 2>&1; then
    PASS=$((PASS+1)); echo "PASS  $name"
  else
    code=$?
    if [ "$code" = "2" ]; then
      SKIP=$((SKIP+1)); echo "SKIP  $name"
      tail -1 /tmp/ui_out.$$
    else
      FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
      echo "FAIL  $name"
      tail -4 /tmp/ui_out.$$
    fi
  fi
done
rm -f /tmp/ui_out.$$

# ---- 清理 ----
pkill -f "remote-debugging-port=9333" 2>/dev/null

echo "=================================================="
echo "UI 回归结果：PASS=$PASS  FAIL=$FAIL  SKIP=$SKIP"
if [ "$FAIL" -gt 0 ]; then
  echo "失败项：${FAILED_NAMES[*]}"
  exit 1
fi
echo "全部通过 ✓"
exit 0
