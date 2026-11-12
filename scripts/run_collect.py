#!/usr/bin/env python3
"""真实采集闭环驱动：点「开始采集」→ 定时采样 → 记录全过程（搜索结果/详情/评分/卡片）。

用法：python run_collect.py [秒数，默认 150]
"""
import json
import sys
import time

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"

SNAP_JS = r"""
() => ({
  btn: ((document.querySelector('.btn-search') || {}).textContent || '').trim(),
  stop: !!document.querySelector('.btn-search.stop'),
  cards: document.querySelectorAll('.card').length,
  scores: [...document.querySelectorAll('.card .score-badge')]
            .map(e => (e.textContent || '').trim()),
  info: ((document.querySelector('.info-bar') || {}).innerText || '')
          .replace(/\s+/g, ' ').trim().slice(0, 120),
  toast: [...document.querySelectorAll('.toast, .alert, .err, .error')]
           .map(e => (e.textContent || '').trim()).filter(Boolean).slice(0, 2),
  login_hint: !!document.querySelector('.login-hint, .login-tip'),
})
"""

CLICK_JS = r"""
() => {
  const b = document.querySelector('.btn-search');
  if (!b) return { ok: false, err: 'no btn' };
  b.click();
  return { ok: true, text: (b.textContent || '').trim() };
}
"""


def main() -> int:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        shell = next((q for q in ctx.pages if "127.0.0.1" in (q.url or "")), None)
        collect = next((q for q in ctx.pages if "xiaohongshu.com" in (q.url or "")), None)
        click = shell.evaluate(CLICK_JS)
        shots = []
        t0 = time.time()
        while time.time() - t0 < seconds:
            time.sleep(4)
            snap = shell.evaluate(SNAP_JS)
            snap["t"] = round(time.time() - t0, 1)
            snap["url"] = (collect.url if collect else "")
            shots.append(snap)
            if snap["cards"] and not snap["stop"] and "失败" not in snap["info"]:
                break
        print(json.dumps({"ok": True, "click": click, "shots": shots},
                        ensure_ascii=True)
              .replace("\\u", "\\u"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
