# -*- coding: utf-8 -*-
"""采集链路口到口走查（UI 自动化）：启动态 → 搜索 → 补全 → 完成/停止。

用途：不写新框架，直接连引擎 CDP 驱动真实 App 页，全程截图 + 状态快照落 jsonl，
用来回答「流程对不对、有没有反人类设计」。发现的回归点由 assertions 兜住。

用法：python scripts/ui_tests/e2e_collect_walkthrough.py [--stop-after 20]
"""
import argparse, json, os, sys, time
from playwright.sync_api import sync_playwright

APP = "http://127.0.0.1:8791"
CDP = "http://127.0.0.1:9223"
OUT = "/tmp/e2e"


def snap(pg, tag, records):
    bar = pg.evaluate("() => { const e=document.querySelector('.status-bar'); return e ? (e.innerText||'').trim().replace(/\\n+/g,' ') : ''; }")
    cards = pg.evaluate("() => document.querySelectorAll('.note-card,.card,.note-item').length")
    records.append({"t": time.time(), "tag": tag, "bar": bar, "cards": cards})
    print(f"  {tag:<10} 卡片={cards} | {bar[:78]}")
    return bar


def main(stop_after=None):
    os.makedirs(OUT, exist_ok=True)
    recs = []
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP)
        pg = next(x for x in b.contexts[0].pages if (x.url or "").rstrip("/") == APP)
        pos = pg.evaluate("""() => { for (const e of [...document.querySelectorAll('button,div')]) {
            const t=(e.innerText||'').trim(); if (t==='开始采集') { const r=e.getBoundingClientRect();
                if (r.width>40&&r.height>20) return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}; } } return null; }""")
        if not pos:
            print("找不到「开始采集」按钮"); return 1
        pg.mouse.click(pos["x"], pos["y"])
        t0 = time.time()
        stop_clicked = False
        for i in range(40):
            time.sleep(3)
            tag = f"T+{int(time.time()-t0):>3}s"
            bar = snap(pg, tag, recs)
            pg.screenshot(path=f"{OUT}/walk_{i:02d}.png")
            if stop_after and not stop_clicked and time.time() - t0 >= stop_after:
                btn = pg.evaluate("""() => { for (const e of [...document.querySelectorAll('button,div')]) {
                    const t=(e.innerText||'').trim(); if (t==='停止采集'||t==='停止') { const r=e.getBoundingClientRect();
                        if (r.width>40&&r.height>20) return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}; } } return null; }""")
                if btn:
                    pg.mouse.click(btn["x"], btn["y"]); stop_clicked = True; print("  >>> 已点停止")
            if ("已完成" in bar or "已停止" in bar) and not bar.startswith("正在"):
                break
    with open(f"{OUT}/walkthrough.jsonl", "w") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # 断言：整条链路必须经过 启动态 → 搜索态 → (补全态) → 结束态
    bars = [r["bar"] for r in recs]
    checks = {
        "有点击后的启动反馈（不是干挂着空闲）": any("正在启动采集" in x or "正在搜索" in x for x in bars[:3]),
        "中途不再出现「第 N/M 条」矛盾文案": not any("第 " in x and " 条" in x and "已扫描" not in x for x in bars),
        "结束有留痕（已完成/已停止）": any(("已完成" in x or "已停止" in x) for x in bars),
    }
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    return 0 if all(checks.values()) else 2


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stop-after", type=int, default=None, help="秒数；给就中途点停止")
    sys.exit(main(ap.parse_args().stop_after))
