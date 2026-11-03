# xhs-leads

小红书「低粉爆文」采集 / 克隆 / 发布本地工具（FastAPI 后端 + Vue3 前端 + 有头 CFT 引擎池，macOS 壳）。

**核心产品目标**：帮助个人创业者 / 小商家寻找高价值可参考的低粉爆文笔记进行模仿。

- **采集**：按关键词采集小红书笔记 → 详情/互动数据补全 → 低粉爆款 4 维评分（like_fan_ratio 40% / low_fan 25% / interaction_health 20% / freshness 15%）
- **一键克隆**：拉取爆款笔记 + 封面优先/去重提取图片 → 生成可编辑新稿（标题/正文/标签/图片可调）
- **客户库**：笔记互动用户价值视图（评论区线索 → 意向分级 → 聚合 → 去联系）
- **草稿 / 发布**：保存草稿后可再编辑再发布；**发布永远手动（红线）**
- **登录**：启动后壳窗（左半）承载前端、未登录弹 ForceLogin，点「去登录」→ 壳窗导航真实小红书登录页 → 捕 `id_token` 注入引擎独立 profile（`<数据目录>/profile`）；采集调试窗在右半（未登录=/login-hint，已登录=explore）。由 `engine_pool.launch_persistent_context` 自动拉起 CFT，无「允许远程调试」弹窗、无钥匙串。

## 快速开始

> **完整目录地图、命名规范与路径语义见 [`docs/project-structure.md`](docs/project-structure.md)**（唯一路径总览）。

```bash
# 后端（PYTHONPATH=backend，勿写成 backend/src）
PYTHONPATH=backend backend/.venv/bin/python -m uvicorn src.server:app --host 127.0.0.1 --port 8791

# 前端（改动后必须重建 → frontend/dist/）
cd frontend && npm install && npm run build

# 测试（非登录态基线 100 passed / 8 deselected）
cd backend && .venv/bin/python -m pytest

# macOS 壳打包 → dist/XHSLeads.app
bash scripts/packaging/build_mac.sh
```

## 文档索引

| 主题 | 位置 |
|---|---|
| 目录地图 / 命名规范 / 路径总览 | [`docs/project-structure.md`](docs/project-structure.md) |
| 后端模块与测试 | [`backend/README.md`](backend/README.md) |
| 前端工程与组件 | [`frontend/README.md`](frontend/README.md) |
| macOS 壳与打包 | [`scripts/packaging/README.md`](scripts/packaging/README.md) |
| UI 自动化测试（t / e1 / b 系列） | [`scripts/ui_tests/README.md`](scripts/ui_tests/README.md) |
| PRD / 需求 / 质检历史 | `docs/`（`project/`、`gates/`、`requirements/`） |

## 提交前检查

提交前跑 Gauntlet commit 门：

```bash
bash /private/tmp/gauntlet/tooling/bin/quality-check.sh --stage commit
```

（vendor 在 `/private/tmp/gauntlet`，gates 配置在仓库 `quality-gates/`；merge 阶段加 `--stage merge`。最新结果见 `docs/QUALITY-REPORT-2026-11-03.md`。）

## 状态（2026-11-03）

- ✅ 命名与目录结构重构批完成；仓库 `byte886/xhs-leads`（PUBLIC）
- ✅ 壳形态定稿：有头 CFT 应用窗（R-6.16 路线A）+ 双窗布局（壳窗左半 / 采集调试窗右半，960×800）+ 登录闭环（ForceLogin→壳窗真实登录页→id_token 注入）
- ✅ 后端 A 批非登录态回归全绿；UI 回归 t 系列 PASS=9 / FAIL=0 / SKIP=2
- ℹ️ e1 真实链路连本机引擎实例 9223（同一登录态 profile），无需另开外部 Chrome
- ℹ️ `roles/` 与 `constitution/` 的 swarm-forge 六角色（specifier→coder→…→QA）为 clean-code-gauntlet 框架模板，**本仓实际工作流 = SOP 文档 + Playwright 脚本 + pytest**，未启用 Gherkin 流水线，勿按六角色流程开工。
