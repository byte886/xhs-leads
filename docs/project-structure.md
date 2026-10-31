# 项目结构与命名规范（xhs-leads）

> 本文件是仓库的**唯一路径总览**：目录地图、命名规范、路径语义与常用命令。新机器部署、代码定位、命名决策均以此为准。
> 状态：与「命名与目录结构重构批」同步定稿（2026-10-31，提交 `8d4f432`）。

## 1. 目录地图（v3 定稿）

```
xhs-leads/                     # 仓库 / 文件夹 / APP 名
├── backend/                   # Python 后端（FastAPI 业务壳）
│   ├── src/                   # Python 包，包名即 src（含 __init__.py）
│   │   ├── server.py          #   FastAPI 应用 :8791（采集/评分/克隆/客户库/草稿/发布护栏）
│   │   ├── config.py          #   统一配置中心（ROOT/DATA_DIR/profile 归一）
│   │   ├── browser_client.py  #   Chrome CDP 客户端（读取 Cookie 库）
│   │   ├── engine_pool.py     #   多账户引擎池 + 生命周期管理
│   │   ├── collect_pipeline.py#   采集流水线
│   │   ├── score.py           #   低粉爆款 4 维评分
│   │   ├── cookie_reader.py   #   Cookie 读取（Chrome 库）
│   │   ├── intent.py / llm_service.py / risk.py   # 意向分级 / LLM 门控 / 风控状态机
│   │   ├── license.py / jsonio.py / logger.py     # 激活码 / JSON 存取 / 日志审计
│   ├── tests/                 # pytest 套件（17 文件；登录态链路单列 test_real_login.py）
│   ├── pyproject.toml         # 后端工程与 import-linter 契约
│   ├── pytest.ini             # pythonpath=src / testpaths=tests / timeout=60
│   └── .venv/                 # Python 3.12 虚拟环境（不入库）
├── frontend/                  # Vue3 前端源码
│   ├── src/                   # App.vue / api.ts / store.ts / 10 个页面组件
│   ├── vite.config.ts         # outDir: 'dist'（构建产物并入 frontend/dist/）
│   ├── package.json           # 包名 xhs-leads-frontend
│   └── dist/                  # 构建产物（打包进 .app，不入库）
├── scripts/                   # 脚本与壳（非业务代码）
│   ├── packaging/             # macOS 壳三件套（launcher.py / xhs_leads.spec / build_mac.sh）
│   ├── ui_tests/              # UI 自动化（t 系列 headless + e1 系列真实态，见其 README）
│   ├── browser/               # 浏览器辅助资源（extension/lib 等，随 .app 打包）
│   ├── legacy/                # 旧引导脚本归档（setup_chrome.py / pick_chrome.py）
│   └── gen_license.py         # 激活码签发工具
├── docs/                      # PRD / 调研 / 质检 / 决策文档（历史，含 research/ 调研资产）
├── data/                      # 开发态本地数据目录（账号/采集/cookie/激活码，全部不入库）
├── .github/                   # CI 工作流（quality-gates）
├── constitution/ roles/       # 多 Agent 协作提示词（engineering/project/workflow + 六角色）
├── docker/                    # 历史容器资产（当前交付无 Docker）
├── research/                  # 遗留调研归档（legacy/gauntlet 文档等）
├── AGENTS.md                  # 项目协作约定
└── README.md                  # 指针式首页（本文件为地图详情）
```

## 2. 命名规范（全局映射表）

| 旧名 | 新名 | 说明 |
|---|---|---|
| 仓库/文件夹 `codex-zhuke` | `xhs-leads` | 根仓库名；本地文件夹未强制改名（GitHub 已改 `byte886/xhs-leads`） |
| Python 包 `codex_xhs` | `src`（位于 `backend/src/`） | 包名即 src，**无** `src/xhs_leads` 双层 |
| 前端包 `codex-xhs-frontend` | `xhs-leads-frontend` | package.json name |
| 旧仓库 `codex-xhs-autogain` | `xhs-leads` | GitHub 仓库改名（PUBLIC，remote 已同步） |
| 类/前缀 `CodeXHS` | `XHSLeads` | 代码符号 |
| App 显示名 | `XHS Leads · 小红书获客助手` | 页面标题 / .app 名 `XHSLeads.app` |
| Bundle ID `com.codexzhuke.app` | `com.xhsleads.app` | Info.plist / PyInstaller bundle |
| Chrome 调试 Profile `.codex-xhs-profile` | `.xhs-leads-profile` | 真实态 e1 链路 / 生产引导 |
| 数据目录 `~/.codex-xhs/accounts` | `~/.xhs-leads/accounts` | 旧账户配置路径 |
| 环境变量 `CODX_XHS_DATA` | `XHS_LEADS_DATA` | 覆盖数据目录 |
| 配置节 `[codex-xhs]` | `[xhs-leads]` | 配置分组名 |

**命名铁律**：新代码一律使用新名；历史文档（docs/ 调研、PRD 中「Codex 自动获客」）与 launcher 的旧目录迁移字符串**有意保留**，不改。

## 3. 路径语义（重构后，所有路径深度已按此校准）

| 用途 | 规则 |
|---|---|
| 仓库根定位 | `ROOT = Path(__file__).resolve().parents[2]`（backend/src/*.py）；`scripts/packaging/*` 内 `SPEC_DIR=#scripts/packaging/`、`ROOT=SPEC_DIR.parent.parent` |
| 开发态数据目录 | `ROOT/data/`（config.DATA_DIR；`XHS_LEADS_DATA` 环境变量可覆盖） |
| 打包态数据目录 | `~/Library/Application Support/xhs-leads/data`（launcher 负责迁移旧 `codex-zhuke/data` → 新目录，排除 Chrome 锁文件并容错） |
| 前端构建产物 | `frontend/dist/`（vite outDir='dist'；随 .app datas 打包） |
| Python 导入 | `import src.*` 需 `backend/` 入 `sys.path`（pytest.ini `pythonpath=src` 相对 rootdir=backend；启动命令 `PYTHONPATH=backend`） |
| 打包态 sys.path | launcher 开发态注入 `parents[2]/backend`；打包态 `_MEIPASS` 内自动可见 |

## 4. 常用命令

```bash
# 后端启动（注意 PYTHONPATH 是 backend，不是 backend/src）
cd xhs-leads && PYTHONPATH=backend backend/.venv/bin/python -m uvicorn src.server:app --host 127.0.0.1 --port 8791

# A 批后端测试（非登录态全绿基线：100 passed / 8 deselected）
cd backend && .venv/bin/python -m pytest

# 前端构建
cd frontend && npm install && npm run build        # → frontend/dist/

# macOS 壳打包（含前端重建；可加 --no-frontend-build 跳过）
bash scripts/packaging/build_mac.sh                # → dist/XHSLeads.app

# UI 回归（t 系列 headless，不依赖登录）
bash scripts/ui_tests/run_all.sh                   # 当前基线 PASS=9 FAIL=0 SKIP=2

# e1 真实链路（需 9223 可见 Chrome + 小红书已登录；低频单次）
backend/.venv/bin/python scripts/ui_tests/e1_s1_filters.py   # s1–s6 逐个

# .app 冒烟（免浏览器）
CZ_NO_BROWSER=1 dist/XHSLeads.app/Contents/MacOS/XHSLeads
```

## 5. 文档索引

| 你要找 | 位置 |
|---|---|
| 壳构成 / 打包 / 冒烟细节 | `scripts/packaging/README.md` |
| 后端模块职责 / 测试写法 | `backend/README.md` |
| 前端工程 / 组件 / 构建 | `frontend/README.md` |
| UI 测试体系（t/e1/b 系列） | `scripts/ui_tests/README.md` |
| PRD / 调研 / 质检历史 | `docs/`（research/、project/ 子目录） |
