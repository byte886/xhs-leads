# -*- coding: utf-8 -*-
"""R-6.53z5 验收：采集中刷新页面（等价「登录往返回到 APP」）后，按钮必须恢复「停止」。

缺口现场：collecting 只存内存，页面重载后 store 为空，onMounted 只恢复列表不恢复采集态，
于是后端仍在采、按钮却显示「开始采集」——用户一点就会把在跑的任务顶掉。

判据（全部满足才 PASS）：
  A. 刷新后按钮文案 == 停止
  B. 刷新后 status_now 的 task_id 与刷新前一致（接回的是同一个任务）
  C. 点按钮后任务转非 running、按钮回到「开始采集」
  D. 刷新后服务端日志里没有新增 POST /api/collect_start（点的是停止，不是重开）
用法：backend/.venv/bin/python scripts/ui_tests/c02d_reload_recover.py [keyword] [日志路径]
返回码：0 PASS / 1 FAIL / 2 环境不就绪
"""
import json, sys, urllib.request
from playwright.sync_api import sync_playwright

APP = "http://127.0.0.1:8791"
KW = sys.argv[1] if len(sys.argv) > 1 else "翡翠手串"
LOG = sys.argv[2] if len(sys.argv) > 2 else "/tmp/ui_test_z7.log"
LIMIT = 3


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{APP}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def log_start_lines():
    try:
        with open(LOG, encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except FileNotFoundError:
        return 0


def collect_start_calls(since_line):
    """统计日志新增行里 POST /api/collect_start 的次数（判 D 用）"""
    try:
        with open(LOG, encoding="utf-8", errors="ignore") as f:
            lines = list(f)[since_line:]
    except FileNotFoundError:
        return -1
    return sum(1 for x in lines if "POST /api/collect_start" in x)


def main():
    before = api("/api/status_now")
    if before.get("collecting"):
        print(f"环境不就绪：已有采集在跑 {before}")
        return 2

    with sync_playwright() as pw:
        br = pw.chromium.connect_over_cdp("http://127.0.0.1:9223")
        ctx = br.contexts[0] if br.contexts else br.new_context()
        page = next((p for p in ctx.pages if (p.url or "").rstrip("/") == APP), None) or ctx.new_page()
        page.goto(APP, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        page.locator('input[placeholder*="关键词"]').fill(KW)
        page.locator('input[type="number"]').first.fill(str(LIMIT))
        btn = page.locator("button.btn-search").first
        btn.click()

        # 等后端真的进入 running（比等卡片更早、更稳）
        running, task_id = None, ""
        for _ in range(20):
            st = api("/api/status_now")
            if st.get("collecting") and st.get("task_id"):
                running, task_id = st, st["task_id"]
                break
            page.wait_for_timeout(1000)
        if not running:
            print("FAIL: 点击后后端未进入采集（按钮={!r}）".format(btn.inner_text().strip()))
            return 1
        print(f"采集中：task_id={task_id} 按钮={btn.inner_text().strip()!r}", flush=True)

        mark = log_start_lines()   # 从这一行开始统计 collect_start 次数
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(3500)

        btn2 = page.locator("button.btn-search").first
        txt = btn2.inner_text().strip()
        st2 = api("/api/status_now")
        print(f"刷新后：按钮={txt!r} 后端={st2}", flush=True)

        ok_a = txt == "停止"
        ok_b = st2.get("task_id") == task_id

        btn2.click()   # 现在它应该是「停止」
        page.wait_for_timeout(1500)
        for _ in range(20):
            st3 = api("/api/status_now")
            if not st3.get("collecting"):
                break
            page.wait_for_timeout(1000)
        page.wait_for_timeout(1000)
        btn3 = page.locator("button.btn-search").first.inner_text().strip()
        st4 = api("/api/status_now")
        print(f"点按钮后：按钮={btn3!r} 后端={st4}", flush=True)

        ok_c = (not st4.get("collecting")) and btn3 == "开始采集"
        calls = collect_start_calls(mark)
        ok_d = calls == 0
        print(f"刷新后新增 collect_start 次数={calls}（期望 0）", flush=True)

        br.close()

    print(f"判据 A(刷新后按钮=停止)={ok_a} B(task_id 一致)={ok_b} C(点后回空闲+开始采集)={ok_c} D(未重新发起)={ok_d}")
    if all([ok_a, ok_b, ok_c, ok_d]):
        print("PASS: 刷新后接回在跑任务，按钮显示「停止」，点击即停止（未顶掉任务）")
        return 0
    print("FAIL: 采集态未正确接回")
    return 1


if __name__ == "__main__":
    sys.exit(main())
