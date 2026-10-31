# macOS 壳（scripts/packaging）说明

> 本目录集中「壳 + 打包」全部代码：入口、PyInstaller 配置、构建脚本。业务代码在 `backend/`，前端在 `frontend/`。

## 构成（三件套）

| 文件 | 职责 |
|---|---|
| `launcher.py` | **应用壳入口**（PyInstaller 入口脚本，也是开发态 CLI） |
| `xhs_leads.spec` | PyInstaller 打包配置（产物 `dist/XHSLeads.app`，bundle `com.xhsleads.app`） |
| `build_mac.sh` | 一键构建：前端重建（可跳过）→ venv PyInstaller 打包 |

## launcher.py 职责

1. **数据目录路由**：`XHS_LEADS_DATA` 覆盖 > 打包态 `~/Library/Application Support/xhs-leads/data` > 开发态项目根 `data/`。
2. **旧目录迁移**：首次启动自动把旧 `~/Library/Application Support/codex-zhuke/data` 复制到新目录（排除 logs/私钥/Chrome 锁文件 `Singleton*`/`RunningChromeVersion`/`LOCK*` 等瞬时文件，整体 try/except 容错——失败仅告警不阻塞启动），保住登录态/数据库/授权。
3. **环境注入**：`XHS_LEADS_DATA` 写入进程环境，`server/config` 统一读取；开发态把 `backend/` 注入 `sys.path`（`import src.*` 需要父目录，与 spec pathex 语义一致）。
4. **服务与生命周期**：同进程起 uvicorn（后台线程，端口冲突自动找空闲替代），服务就绪后**pywebview 内嵌 WKWebView 窗口呈现 UI**（打开 `.app` 后工具 UI 与小红书登录页都在壳窗口内、外部零 Chrome 窗口；R 批需求变更，替代旧 `webbrowser.open` 系统浏览器）；`CZ_NO_BROWSER=1` 走无窗口事件循环（自动化冒烟路径）。退出：关窗 / cmd+Q / `CZ_SELF_EXIT_AFTER` 测试钩子 / `CZ_EXIT_ON_FILE` 外部退出文件 → 优雅停服（见「退出方式」）。
5. **运行模式（dev/prod）**：见下节——日志去向、前端入口随模式切换。

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

- **pathex=[ROOT/backend]**：`src` 包父目录入 path（**不要**写成 `backend/src`——`import src` 需要父目录；写错会导致 `collect_submodules("src")` 静默返回 0、打包后启动 `ModuleNotFoundError: No module named 'src'`）。
- **hiddenimports**：全量收集 `src` + `uvicorn` + `webview`（内嵌 UI 载体 pywebview，macOS cocoa 后端）子模块（防动态导入漏包）。
- **datas**：`frontend/dist`（前端静态托管）、`extension/lib`（DOM 提取源）、`scripts/browser`（授权代点 AppleScript）+ playwright driver。
- **console=False**：GUI 应用；日志由 launcher 落盘。

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

## 已知边界

- **前端产物不入库**：`frontend/dist/` 在 .gitignore，交付物为本地构建；克隆仓库后需 `cd frontend && npm install && npm run build`。
- **迁移不覆盖已存在数据**：新数据目录已存在则跳过迁移（幂等）。
- Windows（C 批）未开工：当前壳仅 macOS（`uname -s` 为 Darwin 时可用）。
