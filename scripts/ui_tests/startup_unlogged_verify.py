# -*- coding: utf-8 -*-
"""未登录启动时序 + 窗口震荡回归（审计缺口 G1 + G3，P0）。

G1：未登录态启动稳定后，type=page **恰好 2 页**：
    - 壳窗 = http://127.0.0.1:8791/（左半，主 APP）
    - 采集窗 = https://www.xiaohongshu.com/login（右半，直接是扫码页）
    无 xiaohongshu.com/explore 判定页、无 about:blank、无第三个窗口。

G3：窗口震荡/闪现零断言补齐——每 ~1s 同时轮询 /api/login_status 与 9223 页面集，
    持续 15s，断言页面集 URL 集合**逐次恒等于上述 2 页**（不允许新页弹出/旧页
    消失再建 = 无窗口震荡）。

依赖：
- dev 实例在跑（API 127.0.0.1:8791 + CDP 127.0.0.1:9223）；
- **只读**：本脚本不杀进程、不重启、不改登录态、不开关任何窗口。
- 实例未运行时明确报错并给出拉起命令（脚本自身不拉起）。

用法：backend/.venv/bin/python scripts/ui_tests/startup_unlogged_verify.py
退出码：全部断言 PASS → 0；任一 FAIL → 1。
"""
import json
import sys
import time
import urllib.request

API = "http://127.0.0.1:8791"
CDP = "http://127.0.0.1:9223"
SHELL_PATH = "/"  # 未登录基线：壳窗 8791 根路径 + 采集窗小红书登录页
STABILIZE_ROUNDS = 15  # G3 轮数（每轮 ~1s，共 ~15s）
ROUND_GAP = 1.0


def _get(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read()


def _page_urls():
    """9223/json/list → type=page 的 URL 列表。"""
    _, body = _get(f"{CDP}/json/list")
    tabs = json.loads(body)
    return [t.get("url", "") for t in tabs if t.get("type") == "page"]


def _norm_path(url: str) -> str:
    """URL → 归一化 path：去 origin、去 hash、去尾部 /（根路径归一为 '/'）。"""
    marker = "127.0.0.1:8791"
    if marker not in url:
        return url.split("#", 1)[0]
    rest = url.split(marker, 1)[1].split("#", 1)[0]
    if rest in ("", "/"):
        return "/"
    return rest.rstrip("/") or "/"


def _check_instance() -> str:
    """实例就绪检查；未运行返回中文报错（含拉起命令提示）。就绪返回 ''。"""
    try:
        code, _ = _get(f"{API}/api/login_status", timeout=3)
        assert code == 200
    except Exception:  # noqa: BLE001
        return (
            "FAIL: dev 实例未运行（8791 /api/login_status 不通）。\n"
            "  本脚本只读，不自动拉起；请先执行：\n"
            "  lsof -t -iTCP:8791 -sTCP:LISTEN | xargs kill -9; "
            "lsof -t -iTCP:9223 -sTCP:LISTEN | xargs kill -9; sleep 2\n"
            "  cd /Users/wenjiechen/Desktop/codex-zhuke && nohup \\\n"
            "    \"/Users/wenjiechen/Library/Application Support/Doubao/sandbox_runtime/"
            "bases/e74152cd379ba45d7ca7f16dfa51b727/bin/python3.14\" \\\n"
            "    scripts/packaging/launcher.py > /private/tmp/l_run_startup.log 2>&1 &\n"
            "  待 /api/login_status 返回 200 后重跑本脚本。"
        )
    try:
        _get(f"{CDP}/json/version", timeout=3)
    except Exception:  # noqa: BLE001
        return "FAIL: 引擎 CDP 9223 不通（后端在但引擎未起）。请确认 launcher 完整启动后重跑。"
    return ""


def _g1_assert(urls: list) -> list:
    """G1 断言：未登录基线恰 2 页 = 壳窗(8791 /) + 采集窗(xiaohongshu /login)。

    R-6.53z8k：右窗不再用本地 /login-hint 中间页，直接落小红书登录页。"""
    errs = []
    if len(urls) != 2:
        errs.append(f"页面数={len(urls)}（期望 2）: {urls}")
        return errs
    if not any("127.0.0.1:8791" in u and _norm_path(u) == SHELL_PATH for u in urls):
        errs.append(f"缺壳窗页(8791 {SHELL_PATH}): {urls}")
    if not any("xiaohongshu.com" in u and "/login" in u for u in urls):
        errs.append(f"采集窗应落小红书登录页: {urls}")
    for u in urls:
        if u.strip().rstrip("/") in ("", "about:blank"):
            errs.append(f"存在空白残留页: {u}")
    return errs


def main() -> int:
    bad = _check_instance()
    if bad:
        print(bad)
        return 1

    # ---- G1：当前页面集一次性断言 ----
    urls0 = _page_urls()
    print("== G1 未登录页面集基线 ==")
    for u in urls0:
        print(f"  page: {u}")
    g1_errs = _g1_assert(urls0)
    if g1_errs:
        for e in g1_errs:
            print(f"  [FAIL] {e}")
        print("SUMMARY: G1 FAIL（页面集不符未登录基线）")
        return 1
    print("  [PASS] 恰好 2 页 = {8791 /, xiaohongshu /login}，无空白/第三窗")

    # ---- G3：15s 轮询，页面集逐次恒定（窗口震荡检测） ----
    print(f"== G3 页面集稳定性（{STABILIZE_ROUNDS} 轮 × {ROUND_GAP}s） ==")
    base_set = set(urls0)
    fails = 0
    for i in range(1, STABILIZE_ROUNDS + 1):
        time.sleep(ROUND_GAP)
        try:
            code, _ = _get(f"{API}/api/login_status", timeout=3)
        except Exception as e:  # noqa: BLE001
            print(f"  round{i}: [FAIL] login_status 请求异常: {e!r}")
            fails += 1
            continue
        if code != 200:
            print(f"  round{i}: [FAIL] login_status={code}（应用退出/不健康）")
            fails += 1
            continue
        urls = _page_urls()
        cur_set = set(urls)
        if cur_set != base_set:
            print(f"  round{i}: [FAIL] 页面集变化（震荡）: {sorted(cur_set)} != {sorted(base_set)}")
            fails += 1
            continue
        perr = _g1_assert(urls)
        if perr:
            print(f"  round{i}: [FAIL] {'; '.join(perr)}")
            fails += 1
            continue
        print(f"  round{i}: [PASS] 页面集恒定 {len(urls)} 页，login_status=200")

    if fails:
        print(f"SUMMARY: G3 FAIL（{fails} 轮页面集漂移/应用不健康，存在窗口震荡）")
        return 1
    print("SUMMARY: G1+G3 ALL PASS（未登录 2 页基线稳定 15s，无窗口震荡）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
