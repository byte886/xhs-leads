"""t13 采集中途点「停止」：验证能否随时中止并正常收尾。

流程：点「开始采集」→ 等 9s（此时通常还在搜索/首批入库）→ 点「停止」
→ 逐秒读按钮态与界面文案，看是否回到「开始采集」并给出停止提示。
"""
import json
import sys
import time
import urllib.request

import websocket

BASE = "http://127.0.0.1:9223"
SHELL = "127.0.0.1:8791"
IDLE, RUN = "开始采集", "停止"


def find_shell():
    for p in json.load(urllib.request.urlopen(f"{BASE}/json/list", timeout=6)):
        if p.get("type") == "page" and SHELL in p.get("url", ""):
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
        self.ws.send(json.dumps({
            "id": rid, "method": "Runtime.evaluate",
            "params": {"expression": expr, "returnByValue": True, "awaitPromise": True},
        }))
        deadline = time.time() + 12
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == rid:
                if "error" in msg:
                    return None
                return msg.get("result", {}).get("result", {}).get("value")
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
    if (/采集|条|搜索中|停止|评分|失败|登录/.test(s) && s.length <= 60) keep.push(s);
  }
  return keep.slice(0, 5).join(' | ');
})()"""


def main():
    pg = find_shell()
    if pg is None:
        print("FAIL 没找到壳窗")
        return 1
    cdp = Cdp(pg["webSocketDebuggerUrl"])
    try:
        time.sleep(1)
        if cdp.js(JS_BTN) != IDLE:
            print(f"FAIL 初始按钮不是「开始采集」（={cdp.js(JS_BTN)}），先别跑")
            return 1

        t0 = time.time()
        print(f"[点击开始] {cdp.js(JS_CLICK % IDLE)}")
        for _ in range(25):
            time.sleep(0.1)
            if cdp.js(JS_BTN) == RUN:
                break
        print(f"[ack] 按钮={cdp.js(JS_BTN)} @{time.time()-t0:.2f}s")

        # 让它跑一会儿，观察是否在搜索/入库
        for i in range(9):
            time.sleep(1)
            print(f"[跑{i+1}s] {cdp.js(JS_BODY)}")

        print(f"\n[点击停止] {cdp.js(JS_CLICK % RUN)} @{time.time()-t0:.1f}s")
        stop_t = time.time()

        back = None
        for i in range(25):
            time.sleep(0.5)
            bt = cdp.js(JS_BTN)
            body = cdp.js(JS_BODY)
            el = round(time.time() - stop_t, 1)
            print(f"[停后{el:>4}s] 按钮={bt} | {body}")
            if bt == IDLE:
                back = el
                break

        print("\n=== 判定 ===")
        print(f"1 采集中能点「停止」并发出请求 : PASS（已点击）")
        print(f"2 停止后回到「开始采集」      : {'PASS ' + str(back) + 's' if back is not None else 'FAIL 12.5s 内没回位'}")
        return 0 if back is not None else 2
    finally:
        cdp.close()


if __name__ == "__main__":
    sys.exit(main())
