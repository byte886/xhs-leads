# 壳 + 打包（macOS / Windows）说明

> 本目录集中「壳 + 打包」全部代码：入口、PyInstaller 配置、构建脚本。业务代码在 `backend/`，前端在 `frontend/`。

## 构成（三件套 + 两端共用层）

| 文件 | 职责 |
|---|---|
| `launcher.py` | **应用壳入口**（PyInstaller 入口脚本，也是开发态 CLI） |
| `spec_common.py` | **两端共用的单一来源**：`datas` / `hiddenimports` / `Analysis` / `EXE` / `COLLECT` 参数（改这里两端同时生效，避免漏拷文件） |
| `xhs_leads.spec` | macOS 专属差异：`BUNDLE` + icns + Info.plist（bundle `com.xhsleads.app`） |
| `xhs_leads_win.spec` | Windows 专属差异：ico + macOS 桥接模块 excludes |
| `check_pack.py` | **两端共用产物自检**：扩展三件套 / 前端产物 / 内置引擎 / 主程序，缺一项就让构建失败 |
| `build_mac.sh` | macOS 一键构建：前端重建（可跳过）→ PyInstaller → 自检 → zip |
| `build_win.ps1` | Windows 一键构建（在 Windows 上跑，Mac 不能交叉编译）：venv/依赖 → PyInstaller → 自检 → zip |

## launcher.py 职责

1. **数据目录路由**：`XHS_LEADS_DATA` 覆盖 > 打包态 `~/Library/Application Support/xhs-leads/data` > 开发态项目根 `data/`。
2. **旧目录迁移**：首次启动自动把旧 `~/Library/Application Support/codex-zhuke/data` 复制到新目录（排除 logs/私钥/Chrome 锁文件 `Singleton*`/`RunningChromeVersion`/`LOCK*` 等瞬时文件，整体 try/except 容错——失败仅告警不阻塞启动），保住登录态/数据库/授权。
3. **环境注入**：`XHS_LEADS_DATA` 写入进程环境，`server/config` 统一读取；开发态把 `backend/` 注入 `sys.path`（`import src.*` 需要父目录，与 spec pathex 语义一致）。
4. **服务与生命周期**：同进程起 uvicorn（后台线程，端口冲突自动找空闲替代），服务就绪后由**内置 CFT Chrome（--app 壳窗）承载前端 UI**（打开 `.app` 后工具 UI 与小红书登录页都在 CFT 壳窗口内、CDP 9223 可控；R-6.16 路线 A，无 pywebview/WKWebView 依赖）；`CZ_NO_BROWSER=1` 走无窗口事件循环（自动化冒烟路径）。退出：关窗 / cmd+Q / `CZ_SELF_EXIT_AFTER` 测试钩子 / `CZ_EXIT_ON_FILE` 外部退出文件 → 优雅停服（见「退出方式」）。
5. **登录守卫（R-6.5/R-6.6 强制登录）**：`_login_guard` 后台线程——**启动即强制**：服务就绪主动 `POST /api/auth/login_begin`（不依赖前端按钮）→ 守卫轮询到 pending 后**壳窗口内真实导航小红书整页登录页** `xiaohongshu.com/login?redirectPath=…`（`load_url` + `evaluate_js` 双保险，打包态 cocoa `load_url` 曾静默失效）→ 用户原生扫码/手机号+验证码/微信完成（验证码真实走「获取验证码」）→ 守卫 `get_cookies()` 捕获 cookie，**只认 `id_token`**（登录成功专属令牌；未登录态 `web_session` 是占位值，以其触发会注入无效会话并被 URL 锁死覆盖真实会话）→ `POST /api/auth/import_cookies` 注入引擎独立 profile → 引擎判定（`id_token` 主判定 + 页面 `xhsIsLoggedIn` 辅助）通过后回前端；**强制语义**：未登录无 `id_token` 时窗口永远停留在登录页（URL 锁死 `/login`，域内 explore 自由浏览也会被拉回；无超时），仅登录成功才回 APP；`CZ_SKIP_LOGIN_GUARD=1` 跳过守卫。
6. **运行模式（dev/prod）**：见下节——日志去向、前端入口随模式切换。

## 运行模式（dev / prod）——打包进壳后不便调试，故显式区分

| | dev（源码直跑，测试/开发） | prod（打包进壳，交付形态） |
|---|---|---|
| 判定 | 源码运行（无 `_MEIPASS`）；`XHS_LEADS_MODE=dev` 可显式覆盖 | PyInstaller 打包态（`_MEIPASS` 存在）；`XHS_LEADS_MODE=prod` 可显式覆盖 |
| 数据目录 | 项目根 `data/`（`XHS_LEADS_DATA` 可覆盖） | `~/Library/Application Support/xhs-leads/data` |
| 前端入口 | **vite dev server** `http://127.0.0.1:5173`（HMR 热改，`/api` 已由 vite proxy 反代到后端） | 后端同源托管 `frontend/dist`（`http://127.0.0.1:8791/`） |
| 日志 | **默认控制台**（改码即生效、日志即时可见） | 默认落盘 `data/logs/app.log`（GUI 无控制台） |
| 调试 | 改后端直接重启/uvicorn；改前端 HMR 即生效 | 改代码必须重新 `build_mac.sh` 重打包；日志查 app.log |

**环境变量开关**（launcher 语义，均向后兼容）：

| 变量 | 含义 |
|---|---|
| `XHS_LEADS_MODE` | `dev`/`prod` 强制指定运行模式（缺省 auto 判定） |
| `XHS_LEADS_DATA` | 数据目录覆盖（打包/开发两态通用） |
| `CZ_KEEP_CONSOLE` | `1` 保留控制台（不落盘）/ `0` 强制落盘；缺省跟随模式（dev 保留、prod 落盘） |
| `CZ_NO_BROWSER` | `1` 跳过内嵌窗口（无窗口事件循环，自动化冒烟路径） |
| `CZ_SELF_EXIT_AFTER` | 秒数：N 秒后自动优雅退出（无信号环境/壳冒烟测试钩子） |
| `CZ_EXIT_ON_FILE` | 路径：该文件出现即优雅退出（GUI 路径信号延迟的确定性外部退出钩子） |

**退出方式**（按优先级）：

| 触发 | 机制 | 说明 |
|---|---|---|
| 用户关窗（红叉）/ cmd+Q | `window.events.closed` → 优雅停服 | 正常使用路径，实测通过 |
| `CZ_SELF_EXIT_AFTER=N` | 监控线程 deadline → `window.destroy()` | 自动化/冒烟钩子，实测通过 |
| `CZ_EXIT_ON_FILE=<path>` | 监控线程检测文件出现 → 优雅停服 | 外部确定性退出，实测通过 |
| SIGINT/SIGTERM（无窗口路径） | 主线程空闲，信号 handler 即时生效 | 自动化冒烟用，实测通过 |
| SIGINT/SIGTERM（GUI 路径） | **已知限制**：主线程在 AppKit 主循环时 Python 信号 handler 延迟/不执行（本环境 sigwait/sigpending 均不反映 pending，阻塞信号反而让 kill 完全无响应，故不阻塞）；GUI 退出请用关窗 / cmd+Q / 上面两个钩子 | 文档化边界 |

**分工约定**：功能测试/回归（t 系列、e1 补跑、A 批）在 **dev 态**跑（快改快验）；壳态只做「交付形态冒烟」（打开壳 → UI 呈现 → 登录小窗 → 状态条 → 无 Chrome 窗口）。

## xhs_leads.spec 要点

- **两端差异只在 spec 末尾**：公共参数全部来自 `spec_common.py`；改 `datas`/`hiddenimports` 请改该文件，不要在两端 spec 各写一份（那正是「打包版丢扩展」的历史成因）。
- **pathex=[ROOT/backend]**：`src` 包父目录入 path（**不要**写成 `backend/src`——`import src` 需要父目录；写错会导致 `collect_submodules("src")` 静默返回 0、打包后启动 `ModuleNotFoundError: No module named 'src'`）。
- **hiddenimports**：全量收集 `src` + `uvicorn` 子模块；Windows 另追加 `psutil`/`multipart`/`python_multipart`（Mac venv 实测没有 psutil，故只在 Windows 侧追加）。
- **datas**：`frontend/dist`（前端静态托管）+ **整个 `extension/` 目录**（`manifest.json` / `background.js` / `lib/extract.js` 三件套缺一不可——只拷 `lib` 会让打包版「扩展开关已开但未找到含 manifest 的扩展目录，跳过加载」，用户看到的现象就是采集窗少一个蓝色「采集窗口」标签组）。
- **console=False**：GUI 应用；日志由 launcher 落盘。
- **产物自检**：`check_pack.py` 接在两端构建脚本里，覆盖 PyInstaller 6 的 `_internal/` 布局与旧平铺布局，`--platform win32` 时额外查 `XHSLeads.exe`。

## 构建与冒烟

```bash
# 构建（默认先重建前端；跳过用 --no-frontend-build）
bash scripts/packaging/build_mac.sh
# 产物：dist/XHSLeads.app（bundle com.xhsleads.app，显示名 XHS Leads）

# 冒烟（无窗口路径；CZ_SELF_EXIT_AFTER=N 可自动优雅退出）
CZ_NO_BROWSER=1 CZ_SELF_EXIT_AFTER=8 dist/XHSLeads.app/Contents/MacOS/XHSLeads
# 验证：curl http://127.0.0.1:8791/ → 200，页面标题「XHS Leads · 小红书获客助手」
# 日志：~/Library/Application Support/xhs-leads/data/logs/app.log
# 停止：lsof -tiTCP:8791 | xargs kill

# GUI 形态冒烟（内嵌窗口呈现；起壳后以 CGWindow 按窗口 ID 截图核验形态）
dist/XHSLeads.app/Contents/MacOS/XHSLeads
# 退出：关窗 / cmd+Q / CZ_EXIT_ON_FILE=/tmp/cz_exit.flag 时 touch /tmp/cz_exit.flag

# 开发态直跑壳（等效 .app 逻辑，数据目录=项目根 data/）
backend/.venv/bin/python scripts/packaging/launcher.py
```

## Windows 打包（在 Windows 机器上跑）

Mac 不能交叉编译 Windows 程序（PyInstaller 只能打本平台包）；本项目在 Parallels Win11 虚拟机里用共享目录跑同一份仓库：

```powershell
# 在 Windows 里（共享路径 \\Mac\Home\Desktop\codex-zhuke）
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\packaging\build_win.ps1            # 全量
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\packaging\build_win.ps1 -SkipDeps  # 引擎/前端已就位时约 40 秒
```

产物：`%LOCALAPPDATA%\xhs-leads-build\dist\XHSLeads\XHSLeads.exe`（onedir，`_internal/` 布局）+ 桌面 `XHSLeads-win64.zip`。
启动自检：构建输出末尾必须出现 `PASS 6 项随包资产齐全`，否则 zip 不应生成。

### 两个只在 Windows 才会犯的坑（都踩过，别再踩）

1. **`build_win.ps1` 必须存为 UTF-8 *with BOM***。Windows PowerShell 5.1 把无 BOM 的 UTF-8 当 GBK 解析，中文注释行尾的 CJK 字符会吞掉换行，**下一行命令被并进注释**→ 静默不执行、不报错、产物照样 `BUILD_OK`。本文件已加 BOM，并在台头写明原因。同理：给 Windows 写 JSON（如 `config.json`）不要用 `Set-Content -Encoding utf8`（5.1 会带 BOM，Python `json.loads` 直接抛错），用 `[IO.File]::WriteAllText(path, text, (New-Object Text.UTF8Encoding $false))`。
2. **扩展 ID 不能靠路径推导**。目录型扩展的 ID 来自安装路径 SHA256，但 Chromium 在 Windows 上的路径规范化口径与 `pathlib.resolve()` 不同（实测推导 `befemk...` vs 真实 `angbdp...`，大小写/分隔符/短路径/前缀十种变体均复现不了），导致 `chrome.runtime.sendMessage` 报 `Could not establish connection. Receiving end does not exist.`，原子建窗被静默降级成命令行建窗（蓝组靠 background.js 兜底才出现）。`shell_window._runtime_ext_ids/_pick_ext_id` 现在**优先取运行时真值**（`ctx.service_workers` 的 `chrome-extension://` URL），路径推导仅作兜底。

判断是否真跑通：Windows 日志里应出现 `扩展 ID 以运行时为准：路径推导=... 实际=...` 与 `采集窗由扩展原子创建 windowId=... groupId=...`，两条缺一就说明走了降级路径。

## 已知边界

- **前端产物不入库**：`frontend/dist/` 在 .gitignore，交付物为本地构建；克隆仓库后需 `cd frontend && npm install && npm run build`。
- **迁移不覆盖已存在数据**：新数据目录已存在则跳过迁移（幂等）。
- **采集窗默认不建**：`config.json` 的 `collect_tab_visible` 默认 `false`（「采集后台完成、不抢前台」口径）。想让启动即出现右采集窗（含蓝色「采集窗口」组），需在设置里打开，或在该数据目录的 `config.json` 里写 `{"collect_tab_visible": true}`——两端行为一致，差异只来自数据目录里的这份配置。
- **`Start-Transcript` 不记录子进程 stdout**：`build-win.log` 里看不到 PyInstaller 的 INFO 与 `check_pack` 输出，判断某步是否执行要看 `prlctl exec` 的 stdout 或产物本身。
