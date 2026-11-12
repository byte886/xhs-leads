#!/usr/bin/env python3
"""UI 自动化驱动：在壳窗前端点「开始采集」，跟采直到完成/失败/需登录。

走 CDP 操作**前端界面元素**（document.querySelector('.btn-search').click()），
等价于真人点按钮，验证「前端 → 后端 → 采集窗 → 结果回填」整条闭环；
不绕过前端直接调后端接口。

用法（guest 内，应用已启动）：
  python drive_collect_ui.py [--seconds 120] [--out out.json]
"""
import argparse
import json
import sys
import time

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"

SNAPSHOT_JS = """() => {
  const q = (s) => document.querySelector(s);
  const kw = q('input.kw-input') || q('input[placeholder*="关键词"]') || q('input[type="text"]');
  const btn = q('.btn-search');
  const cards = document.querySelectorAll('.note-card, .card, .note-item, article');
  const status = q('.status-bar') || q('.status') || q('.top-status');
  let txt = document.body ? document.body.innerText : '';
  const loginish = /扫码登录|请先登录|登录探索更多内容|验证码/.test(txt);
  return {
    url: location.href,
    keyword: kw ? (kw.value || '') : null,
    btn_text: btn ? btn.innerText.trim() : null,
    btn_stop: btn ? btn.classList.contains('stop') : null,
    disabled: btn ? !!btn.disabled : null,
    card_count: cards.length,
    login_hint: loginish,
    status_text: status ? status.innerText.replace(/\\n+/g, ' | ').slice(0, 200) : null,
    tail: txt.slice(-700).replace(/\\n+/g, ' | '),
  };
}"""

CLICK_JS = """() => {
  const btn = document.querySelector('.btn-search');
  if (!btn) return {ok: false, why: 'no .btn-search'};
  if (btn.disabled) return {ok: false, why: 'disabled'};
  btn.click();
  return {ok: true, text: btn.innerText.trim()};
}"""


def find_shell_page(browser):
    for ctx in browser.contexts:
        for pg in ctx.pages:
            u = pg.url or ""
            if "127.0.0.1" in u and ("879" in u or "880" in u):
                return pg
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--out", default="drive_out.json")
    args = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        page = find_shell_page(browser)
        if page is None:
            print(json.dumps({"error": "shell page not found",
                              "pages": [pg.url for c in browser.contexts for pg in c.pages]},
                             ensure_ascii=False))
            return 1
        rep = {"shell_url": page.url, "steps": []}
        rep["before"] = page.evaluate(SNAPSHOT_JS)
        rep["click"] = page.evaluate(CLICK_JS)
        t0 = time.time()
        while time.time() - t0 < args.seconds:
            time.sleep(5)
            try:
                snap = page.evaluate(SNAPSHOT_JS)
            except Exception as exc:  # noqa: BLE001（页面可能被重启/关掉）
                rep["steps"].append({"t": round(time.time() - t0, 1), "error": str(exc)[:120]})
                break
            snap["t"] = round(time.time() - t0, 1)
            rep["steps"].append(snap)
            # 结束条件：采到卡片且按钮已回到非「停止」态；或明确要求扫码登录
            if snap.get("login_hint"):
                break
            if snap.get("card_count", 0) > 0 and not snap.get("btn_stop"):
                break
        rep["pages_after"] = [pg.url for c in browser.contexts for pg in c.pages]
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print(json.dumps({"before": rep["before"], "click": rep["click"],
                          "steps": len(rep["steps"]),
                          "last": rep["steps"][-1] if rep["steps"] else None},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
