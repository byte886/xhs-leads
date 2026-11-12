#!/usr/bin/env python3
"""设置壳窗采集条件（关键词/排序/上限等），供两端做同条件对比。

用法：python set_conditions.py 手串 [排序值] [笔记类型值]
"""
import json
import sys

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"

PROBE_JS = r"""
() => ({
  inputs: [...document.querySelectorAll('input')].map((e, i) => ({
    i, type: e.type, ph: e.placeholder || '', val: e.value,
  })),
  selects: [...document.querySelectorAll('select')].map((e, i) => ({
    i, val: e.value, opts: [...e.options].map(o => o.value),
  })),
})
"""

SET_JS = r"""
([kw, sortVal, typeVal]) => {
  const setNative = (el, v) => {
    const d = Object.getOwnPropertyDescriptor(el.__proto__, 'value');
    d.set.call(el, v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  const kwEl = [...document.querySelectorAll('input')]
    .find(e => e.type === 'text' && (e.value || e.placeholder));
  if (kwEl && kw) setNative(kwEl, kw);
  const sels = [...document.querySelectorAll('select')];
  if (sels[0] && sortVal) setNative(sels[0], sortVal);
  if (sels[2] && typeVal) setNative(sels[2], typeVal);
  return {
    kw: kwEl ? kwEl.value : null,
    sels: sels.map(e => e.value),
  };
}
"""


def main() -> int:
    kw = sys.argv[1] if len(sys.argv) > 1 else "手串"
    sort_val = sys.argv[2] if len(sys.argv) > 2 else "综合"
    type_val = sys.argv[3] if len(sys.argv) > 3 else "图文"
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        shell = next((q for q in ctx.pages if "127.0.0.1" in (q.url or "")), None)
        before = shell.evaluate(PROBE_JS)
        after = shell.evaluate(SET_JS, [kw, sort_val, type_val])
        shell.wait_for_timeout(600)
        print(json.dumps({"before": before, "after": after}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
