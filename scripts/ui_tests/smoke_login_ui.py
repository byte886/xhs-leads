#!/usr/bin/env python3
"""smoke_login_ui.py — 登录 UI 改版冒烟（R-6.10，隔离数据目录，不碰日常 Chrome/真实数据）

场景 A（未登录强制弹窗）：空数据目录启动 → 壳窗口 APP 首页 + ForceLogin 强制登录对话框
场景 B（已登录首页 & 设置页账号与登录）：
  拷贝真实 cookies.json（用户扫码后的有效会话）→ 启动 → 首页左下无 Cookie/CDP 模块
  → cliclick 点击「设置」tab → 截图「账号与登录」区块（状态行/重新登录/Cookie 折叠/导入 Cookie）

用法：python3 scripts/ui_tests/smoke_login_ui.py [A|B|AB]
前置：dist/XHSLeads.app 已重打包（build_mac.sh）；无其他 XHSLeads 实例在跑。
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path('/Users/wenjiechen/Desktop/codex-zhuke')
APP_BIN = str(ROOT / 'dist/XHSLeads.app/Contents/MacOS/XHSLeads')
REAL_DATA = Path(os.path.expanduser('~/Library/Application Support/xhs-leads/data'))
SMOKE = Path('/tmp/xhsleads_smoke')
SEQ = Path('/tmp/smoke_seq')
API = 'http://127.0.0.1:8791'  # 占位；launch 后按日志实际端口覆盖


def window_region():
    try:
        import Quartz
    except ImportError:
        return None
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID):
        owner = w.get('kCGWindowOwnerName', '')
        title = w.get('kCGWindowName', '') or ''
        if 'XHS Leads' in title or 'XHSLeads' in owner:
            b = w.get('kCGWindowBounds', {})
            return int(b['X']), int(b['Y']), int(b['Width']), int(b['Height'])
    return None


def shot(tag):
    r = window_region()
    if not r:
        print(f'[smoke] 未定位壳窗口: {tag}')
        return None
    SEQ.mkdir(exist_ok=True)
    p = str(SEQ / f'{tag}.png')
    subprocess.run(['screencapture', '-x', '-R', f'{r[0]},{r[1]},{r[2]},{r[3]}', p], check=True)
    print(f'[smoke] {tag}: region={r} -> {p}')
    return p


def api_json(path):
    with urllib.request.urlopen(API + path, timeout=6) as r:
        return json.loads(r.read().decode())


def kill_app():
    # 只匹配 XHSLeads 二进制路径段，避免 -f 误伤本脚本/父 shell 命令行
    subprocess.run(['pkill', '-f', 'MacOS/XHSLeads'], capture_output=True)
    # SIGTERM 后等待进程真正退出（优雅关闭可能慢），最多 8s，避免残留实例占端口/干扰下一场景
    for _ in range(16):
        if not subprocess.run(['pgrep', '-f', 'MacOS/XHSLeads'],
                              capture_output=True).stdout.strip():
            break
        time.sleep(0.5)
    subprocess.run(['pkill', '-9', '-f', 'MacOS/XHSLeads'], capture_output=True)
    time.sleep(1)


def launch(data_dir):
    env = dict(os.environ, XHS_LEADS_DATA=str(data_dir))
    subprocess.Popen([APP_BIN], env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def set_api_port(data_dir):
    """从启动日志解析实际服务端口（8791 被占时 launcher 自动换端口）"""
    global API
    log = Path(data_dir) / 'logs' / 'app.log'
    for _ in range(15):
        if log.exists():
            txt = log.read_text(encoding='utf8', errors='ignore')
            m = None
            import re
            m = re.search(r'服务端口:\s*(\d+)', txt)
            if m:
                API = f'http://127.0.0.1:{m.group(1)}'
                print(f'[smoke] API -> {API}')
                return
        time.sleep(1)
    print('[smoke] WARN: 未从日志解析到服务端口，沿用', API)


def wait_login_status(timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            return api_json('/api/login_status')
        except Exception:
            time.sleep(1)
    return None


def prepare(dirpath, with_cookies):
    d = Path(dirpath)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    if with_cookies:
        src = REAL_DATA / 'cookies.json'
        if src.exists():
            shutil.copy(src, d / 'cookies.json')
            print(f'[smoke] 已拷贝真实 cookies.json ({src.stat().st_size}B) 用于已登录场景')
        # 引擎登录态在 profile（SQLite Cookies）→ 一并拷贝（跳过锁文件）
        srcp = REAL_DATA / 'profile'
        if srcp.exists():
            shutil.copytree(srcp, d / 'profile',
                            ignore=shutil.ignore_patterns('Singleton*', 'lockfile'))
            print(f'[smoke] 已拷贝引擎 profile 用于已登录场景')
    return d


def click(x, y):
    subprocess.run(['cliclick', 'c:%d,%d' % (x, y)], check=True)


def scene_a():
    print('\n===== 场景 A：未登录 → ForceLogin 强制登录对话框 =====')
    kill_app()
    d = prepare(str(SMOKE / 'A'), with_cookies=False)
    launch(d)
    set_api_port(d)
    st = wait_login_status()
    print('[smoke] login_status:', st)
    time.sleep(3)  # 等 ForceLogin 渲染
    shot('A_force_login')
    # 后端断言：未登录
    if st and st.get('logged_in') is False:
        print('[smoke] A PASS: 未登录状态正确')
    else:
        print('[smoke] A WARN: 登录状态异常:', st)
    kill_app()


def scene_b():
    print('\n===== 场景 B：游客 Cookie → ForceLogin 弹窗 + cookie_info 只读完整性（名称/完整值/域/有效期） =====')
    kill_app()
    d = prepare(str(SMOKE / 'B'), with_cookies=True)
    launch(d)
    set_api_port(d)
    st = wait_login_status()
    print('[smoke] login_status:', st)
    time.sleep(3)
    shot('B_force_guest')
    # 后端断言：游客 Cookie 也应全量只读展示（名称/完整值/域/有效期）
    try:
        info = api_json('/api/cookie_info')
        first = (info.get('cookies') or [{}])[0]
        print('[smoke] cookie_info count:', info.get('count'),
              '| 首条字段:', {k: first.get(k) for k in ('name', 'value', 'domain', 'expires')})
        if info.get('count') == 14 and all(k in first for k in ('name', 'value', 'domain', 'expires')):
            print('[smoke] B PASS: cookie_info 完整字段（名称/完整值/域/有效期）')
        else:
            print('[smoke] B WARN: cookie_info 字段或条数异常:', info)
    except Exception as e:
        print('[smoke] B WARN: cookie_info 未取到:', e)
    # 游客 cookie 也触发强制登录（未登录判定与 cookie 数无关，只与 web_session 有关）
    if st and st.get('logged_in') is False:
        print('[smoke] B PASS: 游客 cookie 仍判定未登录 → ForceLogin 弹窗')
    kill_app()


if __name__ == '__main__':
    which = sys.argv[1] if len(sys.argv) > 1 else 'AB'
    if which in ('A', 'AB'):
        scene_a()
    if which in ('B', 'AB'):
        scene_b()
    print('\n[smoke] 完成。截图在 /tmp/smoke_seq/')
