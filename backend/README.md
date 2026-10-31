# backend — Python 后端（FastAPI 业务壳）

> 小红书获客工具的**业务后端**：采集、评分、克隆、客户库、草稿、发布护栏、激活与登录态管理。独立于壳（`scripts/packaging/`）与前端（`frontend/`）。

## 结构

```
backend/
├── src/                 # Python 包，包名即 src（含 __init__.py；无 xhs_leads 双层）
├── tests/               # pytest 套件（17 文件）
├── pyproject.toml       # 工程元数据 + import-linter 契约
├── pytest.ini           # pythonpath=src / testpaths=tests / timeout=60
└── .venv/               # Python 3.12 虚拟环境（不入库）
```

## src 模块职责

| 模块 | 职责 |
|---|---|
| `server.py` | FastAPI 应用 :8791（路由 /api/*，托管 `frontend/dist` 静态资源） |
| `config.py` | 统一配置中心：ROOT=parents[2]、DATA_DIR（XHS_LEADS_DATA 覆盖）、profile 路径归一 |
| `browser_client.py` | Chrome CDP 客户端（读 Cookie 库、页面采集执行） |
| `engine_pool.py` | 多账户引擎池 + 生命周期管理 |
| `collect_pipeline.py` | 采集流水线（详情/互动数据补全） |
| `score.py` | 低粉爆款 4 维评分（like_fan_ratio 40% / low_fan 25% / interaction_health 20% / freshness 15%） |
| `cookie_reader.py` | Chrome Cookie 库读取 |
| `intent.py` / `llm_service.py` / `risk.py` | 客户意向分级 / LLM 门控 / 风控状态机 |
| `license.py` / `jsonio.py` / `logger.py` | 激活码校验 / JSON 存取 / 日志与 Cookie 脱敏审计 |

## 常用命令

```bash
# 启动（注意 PYTHONPATH=backend：import src 需要包父目录）
PYTHONPATH=backend .venv/bin/python -m uvicorn src.server:app --host 127.0.0.1 --port 8791

# 测试（非登录态基线：100 passed / 8 deselected，~10s）
.venv/bin/python -m pytest

# 只跑非登录态（排除真实 Chrome 登录链路）
.venv/bin/python -m pytest tests/test_server_routes.py tests/test_server_api.py tests/test_server_cdp.py -q --timeout=30 -o addopts= -k "not RefreshCookie and not LoginStatus"
```

## 测试口径

- `pytest.ini`：`pythonpath=src` 相对 rootdir=backend（等效把 backend 入 path）、`testpaths=tests`、默认 `-q`、`timeout=60`（日常用 `-o addopts=` 抵消 -q 看细节）。
- **登录态链路**（TestRefreshCookie / TestLoginStatus / test_real_login）需要真实 Chrome + 小红书有效登录，属外部因素；登录失效时按「外部阻塞」处理，不属代码回归。
- 数据隔离：conftest 将 DATA_DIR/CONFIG 重定向到 tmp_path，测试不触碰真实数据目录。

## 与壳/前端的边界

- 数据目录：开发态 `ROOT/data/`；打包态 `~/Library/Application Support/xhs-leads/data`（launcher 注入 XHS_LEADS_DATA）。
- 静态托管：由 `server.py` 服务 `frontend/dist/`（构建产物随 .app 打包）。
