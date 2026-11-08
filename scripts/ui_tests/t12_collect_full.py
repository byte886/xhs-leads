"""t12 采集全流程（点界面「开始采集」→ 逐条入库 → 自动收尾）。

采集 UI 自动化：点壳窗界面上的「开始采集」，逐秒读界面状态。

走页面级 CDP 单会话（webSocketDebuggerUrl），不经浏览器级 connect_over_cdp，
避免与后端已持有的 Playwright 连接抢会话。

判定三件事：
1. 点完 ≤1s 按钮是否翻成「停止」（前端拿到 ack 的体感 = 用户不再"一直等待"）
2. 采集中界面是否有条数/进度更新（3s 进度轮询生效）
3. 跑完是否回到「开始采集」
"""
import json
import re
import sys
import time
import urllib.request

import websocket

BASE = "http://127.0.0.1:9223"
SHELL = "127.0.0.1:8791"
IDLE, RUN = "开始采集", "停止"


def list_pages():
    return json.load(urllib.request.urlopen(f"{BASE}/json/list", timeout=6))


def find_shell():
    for p in list_pages():
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
})()""" % IDLE

JS_SPINNER = "document.querySelectorAll('.btn-spinner').length"

JS_BODY = """(() => {
  const t = (document.body.innerText || '').split('\\n');
  const keep = [];
  for (const ln of t) {
    const s = ln.trim();
    if (!s) continue;
    if (/采集|条|搜索中|停止|评分|失败|登录/.test(s) && s.length <= 60) keep.push(s);
  }
  return keep.slice(0, 6).join(' | ');
})()"""


def main():
    pg = find_shell()
    if pg is None:
        print("FAIL 没找到壳窗页面")
        return 1
    print(f"壳窗 page: {pg['url']}")
    cdp = Cdp(pg["webSocketDebuggerUrl"])
    try:
        time.sleep(1)
        bt = cdp.js(JS_BTN)
        print(f"[初始] 按钮={bt}")
        if bt != IDLE:
            print(f"FAIL 初始按钮不是「开始采集」（={bt}）")
            return 1

        t0 = time.time()
        clicked = cdp.js(JS_CLICK)
        print(f"[点击] {clicked} 耗时 {time.time()-t0:.2f}s")
        if clicked != "CLICKED":
            print(f"FAIL 点不动：{clicked}")
            return 1

        switched = None
        for _ in range(25):
            time.sleep(0.1)
            if cdp.js(JS_BTN) == RUN:
                switched = time.time() - t0
                break
        print(f"[ack] 按钮翻「停止」耗时 {switched:.2f}s" if switched else
              "[ack] FAIL 2.5s 内没翻成「停止」")

        print("\n=== 逐秒界面状态 ===")
        samples = []
        done_at = None
        for i in range(60):
            time.sleep(1)
            el = round(time.time() - t0, 1)
            bt = cdp.js(JS_BTN)
            sp = cdp.js(JS_SPINNER)
            body = cdp.js(JS_BODY)
            samples.append((el, bt, sp, body))
            print(f"[{el:>5}s] 按钮={bt} 转圈={sp} 界面={body}")
            if bt == IDLE and i >= 3:
                done_at = el
                print(f"\n[完成] {done_at}s 回到「开始采集」")
                break

        ok_ack = switched is not None and switched <= 1.0
        progressed = any(("条" in (s[3] or "")) or ("评分" in (s[3] or "")) for s in samples)
        finished = done_at is not None
        print("\n=== 判定 ===")
        print(f"1 立刻 ack（≤1s 翻按钮）  : {'PASS' if ok_ack else 'FAIL'}")
        print(f"2 界面条数/进度有更新     : {'PASS' if progressed else 'FAIL'}")
        print(f"3 跑完回到「开始采集」    : {'PASS' if finished else 'FAIL'}")
        return 0 if (ok_ack and progressed and finished) else 2
    finally:
        cdp.close()


if __name__ == "__main__":
    sys.exit(main())
