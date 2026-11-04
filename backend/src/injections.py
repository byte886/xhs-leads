"""JS 注入集中注册表（R-6.30a）。

背景：历史上注入脚本散落在三处——
- browser_client.py 模块级 `_EXTRACT_SRC`（extension/lib/extract.js 文本，evaluate 与
  add/remove_init_script 共用）；
- engine_pool.py 模块级 `_STEALTH_SRC`（内联，context add_init_script）；
- shell_window.py 模块级 5 个 JS 常量 + login_guard 函数体内局部定义 2 个 JS。
分散导致「悬空引用」类历史 bug（R-6.10 曾有 _OPEN_LOGIN_PANEL_JS 长期未定义，
NameError 被 try 吞掉静默降级）。本模块统一登记所有注入来源，命名收敛、单一事实源。

注册表（name → 元数据）：
- js: 注入文本（调用方 evaluate / add_init_script 的原串，内容与历史逐字节一致）
- kind: "evaluate"（页面级 evaluate）/ "context_init"（context add_init_script）/
  "page_init"（page add_init_script）——标注注入方式
- note: 使用说明（为何走/不走 inject_once）

幂等注入助手 inject_once(page, name)：evaluate 类注入注入前查 window.__xhs_injected
集合防重复；add_init_script 类（stealth / collect_debug_title）因注入机制为
「每次新文档自动执行」，不走页面标记助手，但其 JS 来源同样在本注册表登记。
"""
import pathlib
import sys

# 项目根定位（与 browser_client 原逻辑一致：打包态 _MEIPASS 旁 Resources）
if getattr(sys, "_MEIPASS", None):
    # 打包态（PyInstaller macOS）：资源随包进 Contents/Resources
    _ROOT = pathlib.Path(sys._MEIPASS).parent / "Resources"
else:
    _ROOT = pathlib.Path(__file__).resolve().parents[2]  # backend/src -> 项目根

# ---------------------------------------------------------------------------
# 注册表：name → {"js": str, "kind": str, "note": str}
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, dict] = {}


def _register(name: str, js: str, kind: str, note: str = "") -> None:
    _REGISTRY[name] = {"js": js, "kind": kind, "note": note}


def get(name: str) -> str:
    """取注册表中注入 JS 原文（evaluate / add_init_script 原样使用）。"""
    try:
        return _REGISTRY[name]["js"]
    except KeyError as e:
        raise KeyError(f"未登记的注入脚本: {name!r}（注册表: {sorted(_REGISTRY)}）") from e


# ---- 1. 页面提取脚本（文件单一事实源：extension/lib/extract.js） ----
_EXTRACT_SRC = (_ROOT / "extension" / "lib" / "extract.js").read_text(encoding="utf8")
_register(
    "extract",
    _EXTRACT_SRC,
    kind="evaluate+context_init",
    note="搜索/笔记/用户页 DOM 提取函数集。_load/_drain_page/登录判定走 evaluate；"
         "headless 判定走 ctx.add/remove_init_script（对新文档自动执行）。"
         "函数声明幂等，页面级重复 evaluate 无副作用——evaluate 类调用可走 inject_once。",
)

# ---- 2. 防自动化检测 stealth（引擎 context 启动时注入一次） ----
_STEALTH_SRC = r"""
// 直接语句形式（add_init_script 的字符串会被执行；箭头函数表达式只是创建不会执行）
window.__stealth_injected = true;
try { delete navigator.webdriver; } catch (e) {}
try {
  Object.defineProperty(Navigator.prototype, 'webdriver', {
    get: () => undefined, configurable: true,
  });
} catch (e) {}
try {
  Object.defineProperty(Navigator.prototype, 'languages', {
    get: () => ['zh-CN', 'zh', 'en-US', 'en'], configurable: true,
  });
} catch (e) {}
try {
  Object.defineProperty(Navigator.prototype, 'plugins', {
    get: () => [1, 2, 3, 4, 5], configurable: true,
  });
} catch (e) {}
try {
  const originalQuery = Navigator.prototype.permissions
      && Navigator.prototype.permissions.query;
  if (originalQuery) {
    Navigator.prototype.permissions.query = (parameters) =>
      parameters && parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery.call(Navigator.prototype.permissions, parameters);
  }
} catch (e) {}
try {
  window.chrome = window.chrome || {};
  window.chrome.runtime = window.chrome.runtime || {
    connect: () => {}, sendMessage: () => {}, id: undefined,
  };
} catch (e) {}
"""
_register(
    "stealth",
    _STEALTH_SRC,
    kind="context_init",
    note="引擎 context add_init_script（启动一次，对每个新文档自动执行）。"
         "非页面级，不走 inject_once；无 remove 对应（随 context 生命周期）。",
)

# ---- 3. 采集调试窗口标题守护（page 级 init script + 当前文档补注） ----
# R-6.48h：改为自执行 IIFE——add_init_script 对字符串按源码 eval（`() => {...}`
# 只定义不调用，跨文档导航后守护失效，主窗口登录页 title 裸奔即此根因）。
_COLLECT_DEBUG_TITLE_JS = """(() => {
    const T = 'XHS Leads · 采集调试窗口';
    const setT = () => { if (document.title !== T) document.title = T; };
    // R-6.48i：事件级守护替代 setInterval 轮询（用户拍板：守护循环不科学、有可见
    // 中间态）——页面脚本/SPA 一改 title 立即改回，微任务级无闪烁。
    // 锚点必须是 document：documentElement/head 在 document 创建极早期可能为 null，
    // observe(null) 抛 TypeError 会中断 IIFE 导致 observer 从未挂载（实测坑）。
    // document.subtree 覆盖 title 元素增删/文本变化（SPA 重建 head/title 均捕捉）。
    const obs = new MutationObserver(() => setT());
    obs.observe(document, { childList: true, characterData: true, subtree: true });
    setT();
    document.addEventListener('DOMContentLoaded', () => setT());
})()"""
_register(
    "collect_debug_title",
    _COLLECT_DEBUG_TITLE_JS,
    kind="page_init",
    note="每次 evaluate 都会新建一个 setInterval（JS 内部无防重），故由"
         "_ensure_debug_title 以 Python 侧 page._debug_title_installed 标志防重——"
         "不走 inject_once 的 window 标记。page.add_init_script + 当前文档立即 evaluate。",
)

# ---- 3b. 壳窗口标题守护（page 级 init script）：主窗口无论 APP 页还是小红书登录页，
# 窗口标题恒定品牌名（用户 R-6.48h 拍板：主窗口标题统一一致，不随页面状态变化）。
# 自执行 IIFE（原因同上：add_init_script 对字符串按源码 eval）。----
_SHELL_TITLE_JS = """(() => {
    const T = 'XHS Leads · 小红书获客助手';
    const setT = () => { if (document.title !== T) document.title = T; };
    // R-6.48i：事件级守护替代 setInterval 轮询（同 collect_debug_title）。
    // 锚点必须是 document（documentElement/head 早期可能为 null，observe(null)
    // 抛 TypeError 中断 IIFE → observer 从未挂载，实测坑）。
    const obs = new MutationObserver(() => setT());
    obs.observe(document, { childList: true, characterData: true, subtree: true });
    setT();
    document.addEventListener('DOMContentLoaded', () => setT());
})()"""
_register(
    "shell_title",
    _SHELL_TITLE_JS,
    kind="page_init",
    note="与 collect_debug_title 同法：page.add_init_script + 当前文档立即 evaluate，"
         "Python 侧 page._shell_title_installed 标志防重。init script 对后续导航"
         "（含守卫导航到小红书登录页）自动生效。",
)

# ---- 4. 登录成功过渡遮罩（id_token 出现时注入） ----
_WAITING_OVERLAY_JS = r"""(() => {
  if (document.getElementById('xhs-leads-waiting')) return;
  const d = document.createElement('div');
  d.id = 'xhs-leads-waiting';
  d.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;'
    + 'background:rgba(255,255,255,.55);backdrop-filter:blur(2px);'
    + 'z-index:2147483647;display:flex;flex-direction:column;align-items:center;justify-content:center;'
    + 'font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;';
  const st = document.createElement('style');
  st.textContent = '@keyframes xhs-leads-spin{to{transform:rotate(360deg)}}';
  const t = document.createElement('div');
  t.textContent = '登录成功，正在进入工作台…';
  t.style.cssText = 'font-size:18px;color:#333;background:rgba(255,255,255,.92);'
    + 'padding:10px 20px;border-radius:10px;margin-bottom:16px;letter-spacing:1px;'
    + 'box-shadow:0 2px 12px rgba(0,0,0,.12);';
  const s = document.createElement('div');
  s.style.cssText = 'width:26px;height:26px;border:3px solid #ff2442;border-top-color:transparent;'
    + 'border-radius:50%;animation:xhs-leads-spin 1s linear infinite;background:rgba(255,255,255,.92);'
    + 'padding:4px;background-clip:content-box;box-shadow:0 2px 12px rgba(0,0,0,.12);';
  d.appendChild(st); d.appendChild(t); d.appendChild(s);
  document.body.appendChild(d);
})()"""
_register(
    "waiting_overlay",
    _WAITING_OVERLAY_JS,
    kind="evaluate",
    note="登录成功遮罩。JS 内部按 DOM id 防重；login_guard 监视循环每 0.3s 重复 evaluate，"
         "现走 inject_once（window.__xhs_injected 标记）跳过重复注入——新文档（导航后）"
         "标记自然重置，重新注入时机不变。",
)

# ---- 5. 移除登录成功过渡遮罩 ----
_REMOVE_WAITING_OVERLAY_JS = (
    "(() => { const e = document.getElementById('xhs-leads-waiting'); if (e) e.remove(); })()"
)
_register(
    "remove_waiting_overlay",
    _REMOVE_WAITING_OVERLAY_JS,
    kind="evaluate",
    note="一次性动作（按 id 移除），每次调用语义明确，不走 inject_once。",
)

# ---- 6. 主动重登「✕」关闭按钮（模板：__BASE__ 由调用方替换回 APP 地址） ----
_ACTIVE_CLOSE_JS = (
    "(function(){if(window.__xhs_leads_active_close)return;window.__xhs_leads_active_close=true;"
    "var d=document.createElement('div');d.id='xhs-leads-active-close';d.textContent='✕';"
    "d.style.cssText='position:fixed;top:16px;right:16px;width:34px;height:34px;"
    "border-radius:50%;background:rgba(255,255,255,.94);box-shadow:0 1px 8px rgba(0,0,0,.2);"
    "display:flex;align-items:center;justify-content:center;font-size:15px;color:#555;"
    "cursor:pointer;z-index:2147483646;font-family:-apple-system,\"PingFang SC\",sans-serif;';"
    "d.onclick=function(){window.location.href='__BASE__';};"
    "document.body.appendChild(d);})()"
)
_register(
    "active_close",
    _ACTIVE_CLOSE_JS,
    kind="evaluate",
    note="模板含 __BASE__ 占位符，调用方 .replace('__BASE__', base+'/') 后 evaluate；"
         "JS 内部 window.__xhs_leads_active_close 防重。替换串含动态 base，不走 inject_once。",
)

# ---- 7. 首页模式：点开右上角「登录」面板（R-6.10） ----
_OPEN_LOGIN_PANEL_JS = (
    "(function(){"
    "var btns=[].slice.call(document.querySelectorAll('a,div,span,button'));"
    "var el=btns.find(function(e){var t=(e.textContent||'').trim();"
    "return t==='登录'&&e.offsetWidth>0&&e.offsetHeight>0;});"
    "if(el){el.click();return 'clicked';}return 'no-login-btn';})()"
)
_register(
    "open_login_panel",
    _OPEN_LOGIN_PANEL_JS,
    kind="evaluate",
    note="一次性动作（点击按钮，返回 clicked/no-login-btn）；注入失败由整页登录兜底。"
         "不走 inject_once（动作语义非幂等装饰）。",
)

# ---- 8. 强制登录锁（原 login_guard 局部 force_login_js，R-6.6→R-6.10） ----
_FORCE_LOGIN_JS = (
    "(function(){"
    "if(window.__xhs_leads_lock_ok)return;window.__xhs_leads_lock_ok=true;"
    "var st=document.createElement('style');st.id='xhs-leads-lock';"
    "st.textContent='[aria-label=\"关闭\"]{display:none!important}[aria-label=\"close\"]{display:none!important}.close-btn{display:none!important}';"
    "document.head.appendChild(st);"
    "new MutationObserver(function(){"
    "var a=document.querySelectorAll('[aria-label=\"关闭\"],[aria-label=\"close\"],.close-btn');"
    "for(var i=0;i<a.length;i++){a[i].style.display='none';a[i].removeAttribute('onclick');}"
    "}).observe(document.body,{childList:true,subtree:true});})();"
)
_register(
    "force_login",
    _FORCE_LOGIN_JS,
    kind="evaluate",
    note="强制登录页关闭按钮锁定。JS 内部 window.__xhs_leads_lock_ok 防重；"
         "login_guard 每 10s 循环补注，现走 inject_once——新文档标记重置即重注，时机不变。",
)

# ---- 9. 登录页窗口适配（原 login_guard 局部 login_fit_js，R-6.26c） ----
_LOGIN_FIT_JS = (
    "(function(){"
    "if(window.__xhs_leads_login_fit)return;window.__xhs_leads_login_fit=true;"
    "var st=document.createElement('style');st.id='xhs-leads-login-fit';"
    "st.textContent='html,body{overflow:auto!important}.reds-modal{overflow-y:auto!important;max-height:100vh!important;height:auto!important;min-height:100vh!important}.login-container{flex:none!important}';"
    "document.head.appendChild(st);"
    "function fit(){"
    "var m=document.querySelector('.reds-modal');var c=document.querySelector('.login-container');"
    "if(!m||!c)return;"
    "var need=c.getBoundingClientRect().height+24>window.innerHeight;"
    "m.style.alignItems=need?'flex-start':'center';"
    "}"
    "fit();"
    "window.addEventListener('resize',fit);"
    "new MutationObserver(fit).observe(document.body,{childList:true,subtree:true});"
    "setInterval(fit,2000);"
    "})();"
)
_register(
    "login_fit",
    _LOGIN_FIT_JS,
    kind="evaluate",
    note="登录页居中+滚动条适配。JS 内部 window.__xhs_leads_login_fit 防重（含自更新 "
         "setInterval）；login_guard 每 5/10s 循环补注，走 inject_once——新文档标记重置即重注。",
)

# ---------------------------------------------------------------------------
# 幂等注入助手
# ---------------------------------------------------------------------------
_MARKER = "__xhs_injected"


async def inject_once(page, name: str) -> bool:
    """页面级幂等 evaluate 注入：查 window.__xhs_injected 集合防重复。

    - 已注入（本 document 已登记）→ 直接返回 True，零注入开销。
    - 未注入 → evaluate 注入并登记标记，返回 True。
    - 页面未就绪/已关闭/导航中（evaluate 抛错）→ 返回 False，调用方下轮重试。

    注意：标记挂在 window 上，SPA 导航/整页跳转产生新 document 后标记自然丢失，
    届时自动重新注入——与历史「循环补注」时机一致，无行为差异。
    """
    js = get(name)
    try:
        done = await page.evaluate(
            "(n) => Array.isArray(window." + _MARKER + ") && window." + _MARKER + ".includes(n)",
            name,
        )
    except Exception:  # noqa: BLE001（页面未就绪：调用方下轮补注）
        return False
    if done:
        return True
    try:
        await page.evaluate(js)
    except Exception:  # noqa: BLE001
        return False
    try:
        await page.evaluate(
            "(n) => { if (!Array.isArray(window." + _MARKER + ")) window." + _MARKER + " = [];"
            " if (!window." + _MARKER + ".includes(n)) window." + _MARKER + ".push(n); }",
            name,
        )
    except Exception:  # noqa: BLE001（标记登记失败不阻断：JS 已注入，多数注入自带内部防重）
        pass
    return True
