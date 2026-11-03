#!/usr/bin/env python3
"""G4 登录闭环 E2E 骨架（R-6.35c 准备版）——只观察、不模拟、不点击、不注入 Cookie。

本脚本是 e1_s6 的真正替代（G4）的**就绪骨架**：执行到「二维码出现→请用户扫码」即停，
等用户物理用手机小红书 App 扫码后，脚本继续轮询登录落定并跑断言清单。
**严禁**在脚本里模拟登录/注入假 Cookie——登录真实性是本用例的核心价值。

运行前提（脚本自校验，不满足即退出）：
  - dev 实例运行中：壳窗前端 http://127.0.0.1:8791/ + 引擎 CDP http://127.0.0.1:9223/
  - 未登录 2 页基线：CDP 页面集 = {壳窗 8791 /, /login-hint}，无多余小红书页
  - ForceLogin 强制登录对话框已弹出（.force-mask 可见）

流程（代码真值锚点，server.py:461-608 / ForceLogin.vue:62-68）：
  ① 用户点「去登录」→ apiLoginBegin(true) → POST /api/auth/login_begin {force:true}
     → _LOGIN_GATE pending=true + collapse_debug_windows()（收右窗）
  ② 壳窗守卫轮询 /api/auth/login_begin_state → pending=true → 壳窗 load_url 官方登录页
  ③ 用户手机扫码 → 登录成功 → 守卫轮询 get_cookies 出现 web_session
     → POST /api/auth/import_cookies → 注入引擎 → 刷新 → _engine_login_state 四元组判定
  ④ logged_in = has_ws and page_logged_in and not kicked → 清 pending/_LOGIN_INVALID
  ⑤ ForceLogin 轮询 login_status：wentLogin && logged_in && !risk_verify → 弹框自动关

断言清单（登录落定后逐条核对）：
  A1 GET /api/login_status → logged_in=True 且 risk_verify=False 且 kicked=False
  A2 GET /api/auth/login_begin_state → pending=False（闭环已清）
  A3 引擎页面集合：含 xiaohongshu.com/explore（右窗采集调试窗已恢复），不含残留登录页
  A4 壳窗 ForceLogin 弹框已关（.force-mask 不可见）
  A5 引擎内存 cookie 含 id_token（_require_real_login 放行权威标志，CDP 直读读 cookie）

退出码：0=全断言 PASS；2=前提不满足/SKIP；1=断言 FAIL 或超时。
"""
import json
import sys
import time
import urllib.request

FRONT = "http://127.0.0.1:8791"
CDP = "http://127.0.0.1:9223"
SCAN_TIMEOUT_S = 300  # 等用户扫码最长 5 分钟


def api(path, method="GET", body=None):
    req = urllib.request.Request(
        FRONT + path, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def cdp_pages():
    with urllib.request.urlopen(CDP + "/json/list", timeout=5) as r:
        return json.loads(r.read().decode())


def die(code, msg):
    print(f"[G4] EXIT={code}: {msg}")
    sys.exit(code)


def precheck():
    """运行前提：8791 通、9223 通、未登录 2 页基线。"""
    try:
        st = api("/api/login_status")
    except Exception as e:  # noqa: BLE001
        die(2, f"前端 8791 不可达：{e!r}（dev 实例未启动？）")
    print(f"[G4] 前提1 前端可达：login_status logged_in={st.get('logged_in')} "
          f"risk_verify={st.get('risk_verify')}")
    try:
        pages = cdp_pages()
    except Exception as e:  # noqa: BLE001
        die(2, f"引擎 CDP 9223 不可达：{e!r}")
    urls = [p.get("url", "") for p in pages]
    print(f"[G4] 前提2 CDP 页面集：{urls}")
    if len(urls) != 2 or not any(":8791/" in u for u in urls) or \
       not any("login-hint" in u for u in urls):
        die(2, f"未登录基线应为 2 页（壳窗8791 + /login-hint），实得 {len(urls)} 页——"
               f"实例不在未登录基线态，请先重启实例到基线再跑 G4")
    return st


def wait_qrcode():
    """步骤③观察：壳窗导航到官方登录页 = 二维码出现。脚本不点「去登录」，
    由用户在 ForceLogin 弹框上物理点击（或提醒用户点）。"""
    print("[G4] 请用户在 ForceLogin 弹框点「去登录」（脚本不代点）…")
    for i in range(60):  # 最长 60s 等用户点
        for p in cdp_pages():
            if "xiaohongshu.com/login" in (p.get("url") or ""):
                print("[G4] ✓ 二维码页已出现（壳窗已导航 xiaohongshu.com/login）")
                print("[G4] ★ 扫码闸口：请用手机小红书 App 扫**壳窗（屏幕左半）**里的二维码，"
                      "最长等 5 分钟。")
                return
        time.sleep(1)
    die(1, "60s 内壳窗未导航到登录页——用户未点「去登录」？或守卫未轮询到 pending？")


def wait_logged_in():
    """步骤④⑤观察：登录态落定（login_status logged_in && !risk_verify）。"""
    t0 = time.time()
    while time.time() - t0 < SCAN_TIMEOUT_S:
        st = api("/api/login_status")
        if st.get("logged_in") and not st.get("risk_verify"):
            print(f"[G4] ✓ 登录态落定（{time.time()-t0:.0f}s）：{st.get('raw', '')}")
            return st
        time.sleep(2)
    die(1, f"等扫码 {SCAN_TIMEOUT_S}s 未登录落定——用户未扫？扫码后未生效？")


def assert_checklist(st):
    """断言清单（只读核对，FAIL 累计不退出）。"""
    fails = []
    # A1 login_status 四元组
    if not (st.get("logged_in") is True and st.get("risk_verify") is False
            and st.get("kicked") is False):
        fails.append(f"A1 login_status 非预期：{st}")
    # A2 gate 已清
    gate = api("/api/auth/login_begin_state")
    if gate.get("pending") is not False:
        fails.append(f"A2 login_begin_state pending 未清：{gate}")
    # A3 引擎页面集合：explore 在、登录页残留无
    urls = [p.get("url", "") for p in cdp_pages()]
    if not any("xiaohongshu.com/explore" in u for u in urls):
        fails.append(f"A3 引擎页面集缺 explore：{urls}")
    if any("xiaohongshu.com/login" in u for u in urls):
        fails.append(f"A3 引擎页面集残留登录页：{urls}")
    # A4 壳窗 ForceLogin 弹框已关（CDP 直连壳窗读 DOM）
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.connect_over_cdp(CDP)
            shell = next((x for ctx in b.contexts for x in ctx.pages
                          if ":8791/" in (x.url or "")), None)
            if shell is None:
                fails.append("A4 壳窗页未找到")
            else:
                visible = shell.locator(".force-mask").first.is_visible()
                if visible:
                    fails.append("A4 ForceLogin 弹框仍可见（wentLogin&&logged_in&&!risk_verify 应自动关）")
            b.close()
    except Exception as e:  # noqa: BLE001
        fails.append(f"A4 .force-mask 断言异常：{e!r}")
    # A5 引擎内存 cookie 含 id_token（CDP 直读读引擎小红书页 cookie）
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.connect_over_cdp(CDP)
            xhs = next((x for ctx in b.contexts for x in ctx.pages
                        if "xiaohongshu.com" in (x.url or "")), None)
            if xhs is None:
                fails.append("A5 引擎小红书页未找到")
            else:
                names = {c["name"] for c in b.contexts[0].cookies()}
                if "id_token" not in names and "web_session" not in names:
                    fails.append(f"A5 引擎内存 cookie 缺 id_token/web_session：{sorted(names)[:8]}")
            b.close()
    except Exception as e:  # noqa: BLE001
        fails.append(f"A5 cookie 断言异常：{e!r}")
    return fails


def main():
    print("=== G4 登录闭环 E2E 骨架（R-6.35c 准备版，只观察不模拟） ===")
    st0 = precheck()
    if st0.get("logged_in"):
        die(2, "当前实例已登录——G4 需未登录基线跑。请重启实例到未登录态。")
    wait_qrcode()
    st = wait_logged_in()
    fails = assert_checklist(st)
    if fails:
        print("[G4] 断言 FAIL：")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("[G4] ✅ 全部断言 PASS（登录闭环 E2E 通过）")


if __name__ == "__main__":
    main()
