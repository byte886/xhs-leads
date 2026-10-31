# 需求变更 — App 内呈现形态（无头引擎 + 登录小窗 + 调试视图 + 状态提示条）

> 版本：v1.4（需求变更）｜日期：2026-10-31｜状态：用户逐条对齐 ✅，已编排进 R 批
> 关联：PRD `docs/Codex自动获客-复刻方案PRD.md`（v1.3 前置）、待办 `docs/project/待办事项.md`、测试批次 `docs/dev-test-batches.md`（新增 R 批）、界面设计 `docs/界面重构设计-对齐版.md`

---

## 〇、变更背景（本轮讨论完整决策链）

用户提出：**「完全不需要 Chrome 的独立启动，无论是小红书还是 APP，都是属于在 APP 里面去运行和呈现」**。即交付形态 = 打开 XHSLeads.app 后，工具 UI 与小红书页面都在 app 内呈现，**外部不出现任何 Google Chrome 窗口，也不读系统 Chrome 登录态**。经多轮逐条对齐，结论如下。

## 一、需求变更清单（逐条已拍板）

| # | 变更点 | 内容 | 拍板 |
|---|---|---|---|
| R1 | **引擎无头化** | 采集/登录引擎改为系统 Chrome **无头模式**（`channel="chrome"` + headless，不下载任何额外引擎）；外部零 Chrome 窗口 | ✅ 用户拍板（10-31） |
| R2 | **登录态独立 profile** | 登录在引擎**独立 profile**（app 数据目录内）完成，**不再读系统 Chrome Cookie 库**；`cookie_reader` 读系统 Chrome 链路下线（代码保留注释） | ✅ 用户拍板 |
| R3 | **登录小窗（镜像）** | app 界面内弹「登录小窗」= **完整小红书网页登录页镜像**（CDP 截图流 + 事件转发）：扫码 / 手机号+验证码 / 微信（如网页版有）**全部可用，不限制任何登录途径**；交互为真实转发（点击/输入/拖拽均支持） | ✅ 用户拍板 |
| R4 | **调试视图** | 设置页新增「调试」入口：**按需开启**引擎实时画面（观察采集过程），可交互（与登录小窗同款镜像层；只读模式待实现时给默认值） | ✅ 用户拍板 |
| R5 | **全局状态提示条** | 界面**实时播报**「正在打开笔记 #3… / 正在提取封面图… / 正在评分…」；不落盘（区别于审计日志），后端动作 → 前端轮询/推送 | ✅ 用户拍板 |
| R6 | **镜像按需** | 截图流三档：登录自动开 / 调试手动开 / 平时关（不占资源） | ✅ 用户拍板 |
| R7 | **测试基建改造** | e1 系列从「连 9223 外部 Chrome」改为连引擎（引擎调试端口）；t 系列 headless 机制复用 | 编排决策 |
| R8 | **顺序与 push** | **R 批先行 → e1 在最终形态补跑 → 全绿后 push**（铁律：成功前不 push；重构批三项本地提交顺延至 R 批全绿一并推送） | ✅ 用户拍板（按推荐 A 方案） |

## 二、影响面分析（四类文档 + 代码模块）

| 层 | 受影响对象 | 变更 |
|---|---|---|
| 需求 | PRD v1.3 → v1.4；需求约束清单 | 登录机制从「读系统 Chrome」改为「app 内独立 profile 登录」；新增「登录小窗/调试视图/状态提示条」功能点 |
| 原型 | 界面重构设计-对齐版 v1.0 | 新增：登录小窗组件、设置→调试视图、全局状态提示条（线框见该文档 R 批章节） |
| 开发 | `engine_pool.py`（headless 化 + 独立 profile 归一）、`browser_client.py`（登录判定统一）、`cookie_reader.py`（下线）、`server.py`（新增登录小窗/调试/状态接口 + CDP 桥）、`config.py`（profile 路径） | 引擎/登录/镜像/状态 4 组改造 |
| 开发·前端 | `LoginModal.vue`（重构为镜像小窗）、`SettingsPanel.vue`（+调试视图入口）、`App.vue`（+全局状态提示条）、`api.ts`（+新接口） | 3 组件改造 |
| 测试 | `dev-test-batches.md` 新增 **R 批**；`ui_test`（e1 基建改造）；A 批/t 系列回归重跑 | R 批配套测试见该文档 |
| 排期 | `docs/project/待办事项.md`（新增 R 阶段）；任务清单 | 见 §四 |

## 三、方案设计要点

1. **无头引擎**：`launch_persistent_context(channel="chrome", headless=True, user_data_dir=<app 数据目录>/profile)`；stealth 注入保留并强化（无头风控面高于有头）；风控敏感环节可临时切「后台可见窗口」兜底（同一内核，代码共用，参数开关）。
2. **登录小窗/调试视图共用镜像层**：后端 CDP 桥（`Page.captureScreenshot` 截图流 + `Input.dispatch` 事件转发：click/type/drag/scroll）+ 前端组件（登录小窗强制开、调试视图手动开）。
3. **独立 profile**：登录态落 `~/Library/Application Support/xhs-leads/data/profile`（打包态）/ `ROOT/data/profile`（开发态）；重启后登录态保留（无头持久化上下文）；不读系统 Chrome 库。
4. **状态提示条**：后端任务状态接口扩展动作文案（当前动作 + 进度），前端全局状态条轮询显示；审计日志（logger.py）保持不变，两者并存不混淆。
5. **e1 基建**：引擎带 `--remote-debugging-port=<固定端口>` 启动供 e1 连接；9223 外部 Chrome 测试路径退役。
6. **运行模式（dev/prod）**：打包进壳后改码必须重打包、日志落盘、前端静态——不便调试；故显式区分（`XHS_LEADS_MODE=dev|prod`，缺省 auto：PyInstaller 打包态→prod、源码→dev）。dev=源码直跑（vite HMR 5173 + 控制台日志，t/e1 回归所在）；prod=壳内交付形态（静态托管 + app.log，只做壳冒烟验收）。详见 `scripts/packaging/README.md` 运行模式节。
7. **壳内 UI 载体 = pywebview 内嵌窗口（R 批最终定稿，替代 webbrowser.open）**：launcher 起 uvicorn 就绪后 `webview.create_window("XHS Leads", url, 1280x800)` 内嵌 WKWebView 呈现前端——打开 `.app` 后工具 UI 与小红书登录镜像小窗**都在壳窗口内**、外部零 Chrome 窗口（根因：旧 launcher 用 `webbrowser.open` 弹系统浏览器，已全文件重写移除）。`window.events.closed` 关窗优雅停服；`CZ_NO_BROWSER=1` 保留无窗口事件循环（自动化冒烟）；spec `hiddenimports += collect_submodules("webview")`；退出通道 = 关窗 / cmd+Q / `CZ_SELF_EXIT_AFTER` / `CZ_EXIT_ON_FILE`（GUI 主循环下 Python 信号 handler 延迟为已知边界，见 packaging README「退出方式」）。

## 四、批次编排（R 批 — App 内呈现形态）

> 插入点：dev-test-batches B 批之后、C 批（Windows）之前。重构批已落盘未 push，push 并入本批收尾。

| 子任务 | 内容 | 依赖 | 状态 |
|---|---|---|---|
| R-0 | 文档同步与排期（本文档 + 四类文档登记 + 任务清单） | — | ✅（10-31，commit d44f0cf） |
| R-1 | 引擎无头化 + 独立 profile 归一（engine_pool/browser_client/config） | R-0 | ✅（10-31 实施完成：config/engine_pool/server.py 已改，250 passed 回归 + 无头冒烟通过；代码随 R 批统一提交 push） |
| R-2 | 登录链路改造：cookie_reader 下线、登录小窗后端（CDP 桥：截图流+事件转发） | R-1 | ✅（10-31 实施完成：cookie_reader 读系统 Chrome 链路停用+注释留档；mirror.py CDP 桥 + /api/mirror/* 3 接口；回归 250 passed；随 R 批统一提交 push） |
| R-3 | 前端：登录小窗组件（完整登录页镜像）+ 全局状态提示条 | R-2 | ✅（10-31 实施完成：LoginModal 镜像登录小窗=截图流+点击转发+输入填入+登录成功自动关；StatusBar 状态条轮询 /api/status_now 实时播报「正在干什么」） |
| R-4 | 前端：设置→调试视图（按需镜像入口） | R-2/R-3 | ✅（10-31 实施完成：设置页调试视图卡片=引擎镜像按需开、可交互点击转发，登录自动开/调试手动开/平时关） |
| R-5 | e1 基建改造（连引擎调试端口）+ t 系列/A 批回归重跑 | R-1~R-4 | ✅（10-31 实施完成：引擎带 --remote-debugging-port=9223（config.engine_port）；25 脚本 9333→9223 统一连引擎实例；run_all.sh 改引擎就绪检查+拉起、旧外部 headless 9333/9223 路径退役；t 系列 PASS=9/FAIL=0/SKIP=2；后端回归 EXIT=0） |
| R-6 | e1 六场景真实态补跑（最终形态）→ 全绿 | R-5 | ⏳ 待引擎 profile 有效登录态（前置修复已完成：login_status 改引擎真实判定——CDP web_session + 页面 xhsIsLoggedIn 双重确认，旧 cookies.json 纸面判定停用留档；实测 stale cookie 假阳性已消除） |
| R-6.5 | **登录形态升级（用户拍板）：截图镜像 → 壳内真实登录页 + Cookie 注入引擎** | R-2/R-5 | ✅ 工程完成（10-31 验收：打包态壳窗口内真实打开小红书登录首页，r65b_login.png 铁证；详见「R-6.5 附注」）。⏳ 真实登录闭环待人工闸口（用户扫码/手机号+验证码一次） |
| R-7 | 文档归档 + **push**（重构批 3 提交 + R 批提交） | R-6/R-6.5 | ⏳ |

**顺序原则**：先引擎（R-1）→ 后登录/镜像（R-2）→ 再 UI（R-3/R-4）→ 基建与回归（R-5）→ 真实态（R-6）→ 归档推送（R-7）。人工闸口：R-6 需用户提供有效登录态；R-6.5 真实登录需用户实际操作一次。

**R-6.5 附注（登录形态升级）**：用户明确否决截图镜像登录（交互不便、手机号验证码无法真实获取）——拍板「APP 壳内直接打开小红书真实登录页（扫码/手机号+验证码/微信原生完成，验证码走『获取验证码』），登录后 Cookie 由 pywebview 拿到注入无头采集引擎独立 profile」。技术 PoC 实测通过（pywebview 6.2.1 cocoa `get_cookies()` 含 HttpOnly、按窗口当前 URL 域过滤；`create_window` 必须本地起步、start 后 `load_url` 切换，直接外链会 `Main window failed to start`）。工程落地：
- **server.py**：`_LOGIN_GATE` + `/api/auth/login_begin`（已登录直接返回）/`login_begin_state`（pending）/`import_cookies`（过滤 xiaohongshu/xhscdn 域 → SimpleCookie/Morsel → `inject_cookies()` 幂等注入 → reload → `_engine_login_state` 真实判定 → 清 30s 缓存）。
- **launcher.py**：`_login_guard` 守卫线程——轮询 pending → 壳窗口 `load_url` + `evaluate_js` 双保险导航登录页 → 循环 `get_cookies()` 找 `web_session` → 注入引擎 → 仅真登录成功/30 分钟超时回前端；**注入未通过留在登录页继续轮询**（15s 节流，修复占位 web_session 误捕拉回抖动）；`CZ_SKIP_LOGIN_GUARD=1` 跳过开关。
- **LoginModal.vue**：重写为登录引导版（「前往登录」→ 触发守卫导航；成功自动关）。
- **判定修复**：`extension/lib/extract.js` 的 `xhsIsLoggedIn()` 收紧——未登录态小红书 explore 同样渲染推荐流，旧「推荐流兜底 → true」会假阳性；现改为显式未登录 UI 特征（顶栏登录按钮/「登录探索更多内容」）→ false，仅头像/用户菜单 → true。
- **打包态验收**：壳窗口内真实登录页（r65b_login.png）、登录页稳定停留 18s 不拉回、引擎独立 profile 正常启动无锁冲突、`login_status` 未登录态判定正确（raw: `web_session=有 页面登录=否`）；后端回归 250 passed EXIT=0。


## 五、验收口径

- R 批完成后：**外部不出现 Chrome 窗口**；登录（扫码/手机号/微信任选）全程在 app 内完成；登录态重启后保留且不依赖系统 Chrome；设置→调试可看采集实时画面；状态提示条实时显示当前动作。
- 测试基线：A 批 pytest 全绿（非登录态 100/8）+ t 系列全绿 + e1 六场景改造后全过 + 新增 R 测试点（登录小窗三种途径交互、镜像开关、状态条文案、独立 profile 登录态持久）。
- 全部达成后执行 push（重构批 + R 批）。

## 六、文档同步登记（本批）

| 文档 | 动作 | 状态 |
|---|---|---|
| `docs/需求变更-App内呈现形态.md`（本文档） | 新建 | ✅ |
| `docs/Codex自动获客-复刻方案PRD.md` | 版本记录 + 决策表追加 v1.4/R 项 | ✅ |
| `docs/project/待办事项.md` | 新增 R 阶段待办（v3.2→v3.3） | ✅ |
| `docs/dev-test-batches.md` | 新增 R 批（开发范围/配套测试/验收口径）+ 状态速览 | ✅ |
| `docs/界面重构设计-对齐版.md` | 追加 R 批 UI 补充章节 | ✅ |
