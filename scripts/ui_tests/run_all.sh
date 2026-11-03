#!/usr/bin/env bash
# run_all.sh — 一键跑全套 UI 自动化回归（连引擎调试端口，Playwright CDP）
#
# 用法：
#   bash scripts/ui_tests/run_all.sh            # 跑全部（数据依赖脚本自动 SKIP）
#   bash scripts/ui_tests/run_all.sh t03 t06    # 只跑指定编号（前缀匹配）
#
# 依赖：
#   - 后端已启动在 127.0.0.1:8791（详见 README「后端启停」）
#   - 引擎已启动（后端引擎池，带 --remote-debugging-port=9223；本脚本负责就绪检查与拉起）
#   - Python venv 在 backend/.venv（无则自动回退系统 python3 并提示装 playwright）
#
# R 批 v1.5：不再自起独立 headless Chrome（旧 9333 路径退役）；
# 所有脚本 connect_over_cdp 连引擎调试端口 9223 —— 同一实例 = 同一登录态 profile。
#
# 退出码：全部 PASS（无 FAIL）→ 0；有 FAIL → 1（SKIP 不计失败）
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# ---- Python 解释器 ----
PY=backend/.venv/bin/python
if [ ! -x "$PY" ]; then
  PY=python3
  python3 -c "import playwright" 2>/dev/null || { echo "✗ 缺 venv 且系统 python3 无 playwright"; exit 1; }
fi

# ---- 后端就绪检查 ----
if ! curl -s -o /dev/null http://127.0.0.1:8791/ ; then
  echo "✗ 后端 127.0.0.1:8791 未启动。先启动后端再跑 UI 测试（见 README「后端启停」）。"
  exit 1
fi

# ---- 引擎就绪检查（9223 调试端口）：未就绪则经 /api/mirror/open 触发引擎启动 ----
ENGINE_READY=0
for _ in $(seq 1 15); do
  if curl -s -o /dev/null "http://127.0.0.1:9223/json/version"; then
    ENGINE_READY=1; break
  fi
  # 触发引擎启动（幂等；打开小红书登录页，也是 e1 前置页面）
  curl -s -X POST "http://127.0.0.1:8791/api/mirror/open" -o /dev/null 2>/dev/null || true
  sleep 2
done
if [ "$ENGINE_READY" != "1" ]; then
  echo "✗ 引擎调试端口 9223 未就绪（15 次尝试后仍不通）。检查后端引擎启动（日志 /tmp/uvicorn*.log）。"
  exit 1
fi
echo "✓ 引擎调试端口 9223 就绪（连引擎实例，登录态=引擎独立 profile）"

# ---- 顺序执行 ----
FILTER="${1:-}"
PASS=0; FAIL=0; SKIP=0; FAILED_NAMES=()

# ---- R-6.31a 未登录启动时序+窗口震荡（只读，必须最先跑：此时实例应在未登录 2 页基线）----
# 只读断言 page 恰好 2 页（/ + /login-hint）且 15s 内页面集逐次恒定；任何后续脚本都会开窗，
# 故此 gate 必须位于一切可变布局脚本之前。
if [ -z "$FILTER" ]; then
  if PYTHONPATH=backend/src "$PY" scripts/ui_tests/startup_unlogged_verify.py >/tmp/ui_startup.$$ 2>&1; then
    PASS=$((PASS+1)); echo "PASS  startup_unlogged_verify（未登录2页基线+15s无震荡）"
  else
    FAIL=$((FAIL+1)); FAILED_NAMES+=("startup_unlogged_verify")
    echo "FAIL  startup_unlogged_verify（未登录2页基线+15s无震荡）"
    tail -6 /tmp/ui_startup.$$
  fi
  rm -f /tmp/ui_startup.$$
fi

# ---- R-6.22 双窗口分屏布局约定回归（固定化：左主APP/右Tab2/等宽并排/关Tab2不复活）----
# R-6.31b：section2 已改为 R-6.22p 语义（关 Tab2 不复活、应用不退），不再四连关壳窗
# （关主窗=应用退出，会误杀本后端进程），故 run_all 第一步不会带走后端。
# 必须与 t 系列串行（并发会 TargetClosedError），故独立前置执行。
if [ -z "$FILTER" ]; then
  if PYTHONPATH=backend/src "$PY" scripts/ui_tests/layout_verify.py >/tmp/ui_layout.$$ 2>&1; then
    PASS=$((PASS+1)); echo "PASS  layout_verify（双窗口分屏约定）"
  else
    FAIL=$((FAIL+1)); FAILED_NAMES+=("layout_verify")
    echo "FAIL  layout_verify（双窗口分屏约定）"
    tail -6 /tmp/ui_layout.$$
  fi
  rm -f /tmp/ui_layout.$$
fi

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

echo "=================================================="
echo "UI 回归结果：PASS=$PASS  FAIL=$FAIL  SKIP=$SKIP"
if [ "$FAIL" -gt 0 ]; then
  echo "失败项：${FAILED_NAMES[*]}"
  exit 1
fi
echo "全部通过 ✓"
exit 0
