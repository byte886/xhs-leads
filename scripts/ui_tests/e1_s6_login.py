# -*- coding: utf-8 -*-
"""E1-场景6 登录态与 Cookie 闭环（连 9333 headless）。

验证：清本地 cookies.json → 刷新 → 「未登录/Cookie 失效」状态 + LoginModal 引导（Profile 提示/打开小红书链接/
「立即读取 Chrome 登录态」按钮）→ 点立即读取（读日常 Chrome，未登录则失败走扫码引导）→ 恢复 cookies.json →
刷新 → 「已登录（小红书）Cookie 有效」+ 弹窗关闭。
安全设计：脚本自带备份/恢复（/tmp/cookies_backup_e1s6.json），异常退出也会尝试恢复；
登录状态判定有 30s 后端缓存，清 cookie 后需重启后端（或等缓存过期）才能看到未登录态。
踩坑记录：
  - login_status 只读本地 cookies.json（刻意不拉浏览器）；重启后端 = pkill uvicorn + nohup 重启（保留 notes 数据）；
  - 「立即读取 Chrome 登录态」读的是日常 Chrome（钥匙串解密），与 9333 headless profile 无关——日常 Chrome 未登录时失败属预期，走扫码引导是产品设计。

前置：后端 8791（本脚本会重启它）；9333 headless；本脚本会短暂清空 data/cookies.json（自动恢复）。
"""
import json
import pathlib
import subprocess
import sys
import time
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2]
COOKIE = ROOT / 'data' / 'cookies.json'
BACKUP = '/tmp/cookies_backup_e1s6.json'


def restart_backend():
    # B 方案新架构：先 SIGTERM 引擎主 Chrome（优雅关闭落盘登录态），再收 uvicorn。
    # 禁止 pkill -9 直杀 uvicorn（绕过 shutdown 钩子会让引擎变孤儿、登录态不落盘，已实证）。
    profile = str(pathlib.Path.home() / '.xhs-leads-profile')
    subprocess.run(['bash', '-c',
                    f'ENGINE_PID=$(pgrep -f "user-data-dir={profile}" | head -1); '
                    f'[ -n "$ENGINE_PID" ] && kill -TERM $ENGINE_PID'], check=False)
    time.sleep(6)
    subprocess.run(['pkill', '-9', '-f', 'uvicorn'], check=False)
    time.sleep(1)
    proc = subprocess.Popen(
        ['nohup', f'{ROOT}/app/.venv/bin/python', '-m', 'uvicorn', 'src.server:app',
         '--host', '127.0.0.1', '--port', '8791', '--app-dir', f'{ROOT}/app/src'],
        cwd=str(ROOT), stdout=open('/tmp/uvicorn.log', 'w'), stderr=subprocess.STDOUT)
    time.sleep(3)


def main():
    # 0) 备份现有 cookies
    bak = COOKIE.read_text() if COOKIE.exists() else '{"cookies":[]}'
    pathlib.Path(BACKUP).write_text(bak)

    try:
        # 1) 清空 cookies.json + 重启后端（清 login 缓存）
        COOKIE.write_text(json.dumps({'cookies': [], 'imported_at': ''}))
        restart_backend()

        # 2) 刷新 APP → 未登录状态 + 弹窗
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
            ctx = b.contexts[0]
            pg = next((x for x in ctx.pages if '8791' in (x.url or '')), None)
            if pg is None:
                pg = ctx.new_page()
            pg.goto('http://127.0.0.1:8791/', timeout=20000)
            pg.wait_for_timeout(3000)
            st = pg.evaluate("""() => {
                const hits = [...document.querySelectorAll('*')].filter(e => e.children.length===0 && /登录/.test(e.textContent||'')).map(e=>e.textContent.trim());
                const modal = !!document.querySelector('.login-modal, [class*=login]');
                const btn = !!document.querySelector('button');
                const hasBtn = [...document.querySelectorAll('button')].some(b2 => /立即读取/.test(b2.innerText));
                return {login: [...new Set(hits)].slice(0,3), modal, hasBtn};
            }""")
            print("1) 未登录状态:", st)

            # 3) 点「立即读取 Chrome 登录态」→ 读日常 Chrome（未登录则失败，弹窗保持走扫码引导）
            btn = pg.locator('button', has_text='立即读取 Chrome 登录态').first
            print("2) 立即读取按钮存在:", btn.count() > 0)
            if btn.count():
                btn.click()
                pg.wait_for_timeout(8000)
                msg = pg.evaluate("""() => {
                    const m = document.querySelector('.login-modal, [class*=login]');
                    return m ? m.innerText.replace(/\\n+/g,' ').slice(0,140) : '(弹窗已关)';
                }""")
                print("3) 点击后弹窗:", msg)
            b.close()
    finally:
        # 4) 恢复 cookies.json（无论成败）
        pathlib.Path(BACKUP).write_text(bak)
        COOKIE.write_text(bak)
        restart_backend()
        time.sleep(1)
        # 5) 验证已登录恢复
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
            ctx = b.contexts[0]
            pg = next((x for x in ctx.pages if '8791' in (x.url or '')), None)
            if pg is None:
                pg = ctx.new_page()
            pg.goto('http://127.0.0.1:8791/', timeout=20000)
            pg.wait_for_timeout(3000)
            st2 = pg.evaluate("""() => {
                const hits = [...document.querySelectorAll('*')].filter(e => e.children.length===0 && /已登录|未登录/.test(e.textContent||'')).map(e=>e.textContent.trim());
                const modal = !!document.querySelector('.login-modal, [class*=login]');
                return {login: [...new Set(hits)].slice(0,3), modalStillOpen: modal};
            }""")
            print("4) 恢复后:", st2)
            b.close()


if __name__ == '__main__':
    main()
