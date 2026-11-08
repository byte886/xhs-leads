"""t14 关采集窗行为验证（方案 A）。

步骤：
1. 点界面「开始采集」，确认进入采集
2. 故意关掉右侧采集窗（/json/close）
3. 观察 20 秒：右侧是否被自动重建（方案 A 要求：不重建）、左侧是否给出中断提示
4. 重新点「开始采集」：应自动新建窗口并采到数据
"""
import json
import sys
import time
import urllib.request

import websocket

BASE = "http://127.0.0.1:9223"
SHELL = "127.0.0.1:8791"
IDLE, RUN = "开始采集", "停止"


def pages():
    try:
        return json.load(urllib.request.urlopen(f"{BASE}/json/list", timeout=6))
    except Exception:
        return []


def xhs_pages():
    return [p for p in pages() if p.get("type") == "page"
            and "xiaohongshu" in (p.get("url") or "")]


def shell_page():
    for p in pages():
        if p.get("type") == "page" and SHELL in (p.get("url") or ""):
            return p
    return None


class Cdp:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=15,
                                              max_size=8 * 1024 * 1024)
        self.rid = 0

    def js(self, expr):
        self.rid += 1
        rid = self.rid
        self.ws.send(json.dumps({"id": rid, "method": "Runtime.evaluate",
                                 "params": {"expression": expr,
                                            "returnByValue": True,
                                            "awaitPromise": True}}))
        deadline = time.time() + 12
        while time.time() < deadline:
            m = json.loads(self.ws.recv())
            if m.get("id") == rid:
                if "error" in m:
                    return None
                return m.get("result", {}).get("result", {}).get("value")
        return None

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


JS_BTN = """(() => {
  const bs = [...document.querySelectorAll('button')];
  const b = bs.find(e => {
    const t = (e.textContent || '').trim();
    return t === '%s' || t === '%s';
  });
  return b ? b.textContent.trim() : null;
})()""" % (IDLE, RUN)

JS_CLICK = """(() => {
  const bs = [...document.querySelectorAll('button')];
  const b = bs.find(e => (e.textContent || '').trim() === '%s');
  if (!b) return 'NOT_FOUND';
  if (b.disabled) return 'DISABLED';
  b.click();
  return 'CLICKED';
})()"""

JS_BODY = """(() => {
  const t = (document.body.innerText || '').split('\\n');
  const keep = [];
  for (const ln of t) {
    const s = ln.trim();
    if (!s) continue;
    if (/采集|条|搜索中|停止|评分|失败|登录|窗口/.test(s) && s.length <= 70) keep.push(s);
  }
  return keep.slice(0, 6).join(' | ');
})()"""


def main():
    sp = shell_page()
    if sp is None:
        print("FAIL 没有壳窗")
        return 1
    cdp = Cdp(sp["webSocketDebuggerUrl"])
    try:
        time.sleep(1)
        if cdp.js(JS_BTN) != IDLE:
            print("FAIL 初始不在「开始采集」态")
            return 1

        print(f"[A] 点开始采集 → {cdp.js(JS_CLICK % IDLE)}")
        for _ in range(25):
            time.sleep(0.1)
            if cdp.js(JS_BTN) == RUN:
                break
        print(f"[A] 按钮={cdp.js(JS_BTN)}，采集已启动")

        for i in range(7):
            time.sleep(1)
            print(f"   跑{i+1}s 页面数={len(xhs_pages())} | {cdp.js(JS_BODY)}")

        # 故意关掉采集窗（正是被测场景）
        targets = xhs_pages()
        print(f"\n[B] 关掉采集窗（当前 {len(targets)} 个小红书页）")
        for t in targets:
            try:
                urllib.request.urlopen(
                    f"{BASE}/json/close/{t['id']}", timeout=6).read()
            except Exception as e:
                print(f"   关闭异常: {e}")

        print("\n=== 关窗后 20 秒观察：右侧是否被自动重建 ===")
        rebuilt = False
        saw_note = False
        for i in range(10):
            time.sleep(2)
            el = (i + 1) * 2
            n = len(xhs_pages())
            body = cdp.js(JS_BODY)
            btn = cdp.js(JS_BTN)
            print(f"[+{el:>2}s] 小红书页={n} 按钮={btn} | {body}")
            if n > 0:
                rebuilt = True
            if body and "窗口已关闭" in body:
                saw_note = True

        print("\n=== 再点「开始采集」：应自动新建窗口 ===")
        print(f"[C] 点击 → {cdp.js(JS_CLICK % IDLE)}")
        got = None
        for i in range(40):
            time.sleep(1)
            n = len(xhs_pages())
            body = cdp.js(JS_BODY)
            btn = cdp.js(JS_BTN)
            if i % 3 == 0 or btn == IDLE:
                print(f"[C+{i+1:>2}s] 页={n} 按钮={btn} | {body}")
            if body and ("已按评分排序" in body or "已入库" in body):
                got = body
            if btn == IDLE and i > 8:
                break

        print("\n=== 判定 ===")
        print(f"B1 关窗后不自动重建      : {'PASS' if not rebuilt else 'FAIL 右侧被自动开回来了'}")
        print(f"B2 左侧给出中断提示      : {'PASS' if saw_note else 'FAIL 没看到「窗口已关闭」文案'}")
        print(f"C1 重新点开始能再采      : {'PASS' if got else 'FAIL 重新采集没出结果'} | {got}")
        return 0
    finally:
        cdp.close()


if __name__ == "__main__":
    sys.exit(main())
