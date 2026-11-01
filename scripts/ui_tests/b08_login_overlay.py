# -*- coding: utf-8 -*-
"""b08 登录成功过渡遮罩补测（R-6.9）。

覆盖：扫码登录 → 壳窗口注入全屏「登录成功，正在进入工作台…」遮罩（遮挡小红书登录后页面、
拦截误操作）→ 引擎注入完成 → 切回 APP 首页。

前置：APP（8791 / 引擎 9223）已运行；本脚本会**清掉当前登录态**触发完整登录流程。
架构注意：壳窗口 = launcher WebView（独立浏览器实例），CDP 9223 只连引擎 headless Chrome，
故遮罩验证采用**连续截图留证**（脚本每 2s 截壳窗口区域），事后人工核对遮罩帧；
登录完成由 login_status API 权威断言。

步骤：
 0) 备份 cookies.json 与引擎 profile Cookies（会话类）；清引擎内存 cookie
 1) 杀 APP → 重启 → 断言 login_status=not_logged_in 四态 + 壳窗口登录页截图①
 2) 交互闸口：等待用户扫码（最长 8 分钟）；期间每 2s 截图留证 /tmp/b08_seq/NNN.png
 3) login_status 变 logged_in → 末帧截图（APP 页）→ 断言引擎注入完成
 4) 输出截图清单供人工核对遮罩帧（退出码：0 通过 / 1 失败 / 2 SKIP）
"""
import os, json, shutil, sys, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(os.path.expanduser('~/Desktop/codex-zhuke'))
DATA = Path(os.path.expanduser('~/Library/Application Support/xhs-leads/data'))
COOKIES = DATA / 'cookies.json'
PROFILE_COOKIES = DATA / 'profile' / 'Default' / 'Cookies'
PROFILE_COOKIES_J = DATA / 'profile' / 'Default' / 'Cookies-journal'
SHELL_REGION = '2240,200,1280,800'  # 兜底（历史副屏位置）；优先动态定位
SEQ = Path('/tmp/b08_seq')
API = 'http://127.0.0.1:8791/api/login_status'
SESSION_NAMES = {'id_token', 'web_session', 'last_web_session', 'web_session_ts'}
# 最长等待用户扫码（秒）
SCAN_TIMEOUT = 480
# 扫码前连续截图窗口（秒）——遮罩出现在 id_token 检测到后、引擎注入完成前
SHOT_INTERVAL = 2.0


def _window_region() -> str:
    """动态定位壳窗口（重启后可能回到主屏默认位置，勿硬编码坐标）。"""
    try:
        import Quartz  # noqa: PLC0415
        wins = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
        )
        for w in wins:
            owner = w.get('kCGWindowOwnerName', '') or ''
            if 'XHSLeads' in owner or 'XHS Leads' in owner:
                b = w.get('kCGWindowBounds', {})
                x, y = int(b.get('X', 0)), int(b.get('Y', 0))
                ww, hh = int(b.get('Width', 1280)), int(b.get('Height', 800))
                return f'{x},{y},{ww},{hh}'
    except Exception:  # noqa: BLE001
        pass
    return SHELL_REGION


def api_login_status():
    try:
        with urllib.request.urlopen(API, timeout=3) as r:
            return json.load(r)
    except Exception:
        return None


def snapshot(name: str) -> str:
    path = SEQ / name
    os.system(f'screencapture -x -R{_window_region()} "{path}"')
    return str(path)


def kill_app():
    os.system('pkill -f "XHSLeads.app/Contents/MacOS/XHSLeads" 2>/dev/null; '
              'pkill -f "remote-debugging-port=9223" 2>/dev/null')
    time.sleep(3)


def launch_app():
    os.system(f'open "{ROOT}/dist/XHSLeads.app"')
    time.sleep(9)


def clear_engine_cookies():
    """CDP 清引擎内存 cookie（磁盘 Cookies 由备份/移走兜底）。"""
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')
        ctx = b.contexts[0]
        ctx.clear_cookies()
        b.close()


def backup_and_clear_session():
    """备份 cookies.json + 引擎 profile Cookies；清会话类，触发登录流程。

    顺序关键：**先杀 APP（引擎停）→ 再移 profile Cookies 文件**——引擎运行中 rename
    Cookies 存在文件锁竞态（Chrome 持句柄，移走可能静默失败），残留会话会在下次启动时
    被守卫读到 → 假阳性「引擎已登录」跳过登录页（正是 R-6.8 想修的盲区变体）。
    """
    ts = int(time.time())
    kill_app()
    shutil.copy2(COOKIES, DATA / f'cookies.json.bak_b08_{ts}')
    for f in (PROFILE_COOKIES, PROFILE_COOKIES_J):
        if f.exists():
            shutil.copy2(f, DATA / 'profile' / 'Default' / f'{f.name}.bak_b08_{ts}')
    # cookies.json：移除会话类，保留设备指纹
    data = json.loads(COOKIES.read_text(encoding='utf-8'))
    data['cookies'] = [c for c in data.get('cookies', []) if c.get('name') not in SESSION_NAMES]
    COOKIES.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    # 引擎磁盘 Cookies 移走（引擎已停，无文件锁；移走失败则显式报错）
    for f in (PROFILE_COOKIES, PROFILE_COOKIES_J):
        if f.exists():
            f.rename(f'{f}.bak_b08_{ts}')
            assert not f.exists(), f'Cookies 移走失败（文件锁？）: {f}'
    print(f'[b08] 备份完成：cookies.json.bak_b08_{ts} / Cookies.bak_b08_{ts}')


def main():
    SEQ.mkdir(exist_ok=True)
    # 0) 清会话（备份优先，防失败破坏；先杀引擎再移 Cookies，防文件锁竞态）
    #    B08_NO_CLEAR=1：跳过清会话/重启，接续「已在登录页」的外部状态（诊断后复用）
    if not os.environ.get('B08_NO_CLEAR'):
        backup_and_clear_session()
        # 1) 重启 → 登录页（引擎磁盘 Cookies 已移走，启动后无残留会话可读）
        kill_app()
        launch_app()
    ls = api_login_status()
    print('[b08] 当前 login_status:', ls)
    assert ls and ls.get('status') == 'not_logged_in' and ls.get('logged_in') is False, \
        f'期望 not_logged_in（或 B08_NO_CLEAR 前置已就绪），实际 {ls}'
    p1 = snapshot('001_login_page.png')
    print(f'[b08] ① 壳窗口登录页取证: {p1}')

    # 2) 人工闸口：等待扫码；每 2s 截图留证
    print('[b08] 请在小红书登录页扫码（最多 8 分钟）；脚本每 2s 截图留证…')
    t0 = time.monotonic()
    n = 2
    done = False
    while time.monotonic() - t0 < SCAN_TIMEOUT:
        time.sleep(SHOT_INTERVAL)
        ls = api_login_status()
        n += 1
        snapshot(f'{n:03d}.png')
        if ls and ls.get('logged_in'):
            done = True
            time.sleep(1)  # 给切回 APP 留一点时间再拍末帧
            snapshot(f'{n + 1:03d}_final_app.png')
            print(f'[b08] 登录完成 detected: status={ls.get("status")}')
            break
    if not done:
        print(f'[b08] FAIL: {SCAN_TIMEOUT}s 内未检测到登录完成')
        return 1

    # 3) 引擎注入完成断言
    ls = api_login_status()
    print('[b08] 最终 login_status:', ls)
    assert ls and ls.get('logged_in') is True and ls.get('status') == 'logged_in', f'引擎注入未完成: {ls}'

    # 4) 输出遮罩帧线索（供人工核对：白底大文字帧出现在登录完成前的最后几张）
    frames = sorted(SEQ.glob('*.png'))
    print(f'[b08] 截图共 {len(frames)} 张；登录完成前最后 3 帧最可能含「登录成功，正在进入工作台…」遮罩：')
    for f in frames[-4:]:
        print('   ', f.name)
    print('[b08] PASS: 登录完成 + 引擎注入 + 切回 APP 全链路验证通过（遮罩帧请核对截图）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
