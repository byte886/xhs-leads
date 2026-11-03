# xhs-leads

> 版本：v1.0（2026-11-03，R-6.38a 文档线对齐）

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
| PRD（现行需求） | [`docs/Codex自动获客-复刻方案PRD.md`](docs/Codex自动获客-复刻方案PRD.md) |
| 界面设计（现行 4-Tab 现状） | [`docs/界面重构设计-对齐版.md`](docs/界面重构设计-对齐版.md) |
| 待办事项（现行） | [`docs/project/待办事项.md`](docs/project/待办事项.md) |
| 需求实现闭环清单（历史快照） | [`docs/project/需求实现闭环清单.md`](docs/project/需求实现闭环清单.md) |
| 多专家角色迭代开发工作流（现行 SOP） | [`docs/project/多专家角色迭代开发工作流.md`](docs/project/多专家角色迭代开发工作流.md) |
| 治理梳理报告（2026-11-03） | [`docs/project/多专家治理梳理报告-2026-11-03.md`](docs/project/多专家治理梳理报告-2026-11-03.md) |
| 质量报告（Gauntlet 最终态） | [`docs/QUALITY-REPORT-2026-11-03.md`](docs/QUALITY-REPORT-2026-11-03.md) |
| **资产索引（唯一，全量清单）** | [`docs/project/项目资产与工具清单.md`](docs/project/项目资产与工具清单.md) |
| 授权机制 ADR | [`docs/授权机制选型与薄层设计-ADR.md`](docs/授权机制选型与薄层设计-ADR.md) |
| 枚举式全量审计报告（2026-11-03） | [`docs/project/枚举式全量审计报告-2026-11-03.md`](docs/project/枚举式全量审计报告-2026-11-03.md) |
| 后端模块与测试 | [`backend/README.md`](backend/README.md) |
| 前端工程与组件 | [`frontend/README.md`](frontend/README.md) |
| macOS 壳与打包 | [`scripts/packaging/README.md`](scripts/packaging/README.md) |
| UI 自动化测试（t / e1 / b 系列） | [`scripts/ui_tests/README.md`](scripts/ui_tests/README.md) |

## 提交前检查（每轮 R 批强制）

**每轮 R 批：先读 [`docs/project/多专家角色迭代开发工作流.md`](docs/project/多专家角色迭代开发工作流.md)**（任务单按其 §七.4 模板带 mini-ADR 字段）→ 收尾验收出口（§六：pytest≥321、commit/merge 门全绿、0 断链、bash -n/py_compile/vue-tsc/vitest、决策记录入库）。

```bash
# Gauntlet commit 门（vendor 在 clean-code-gauntlet 技能目录；新机器需先安装该技能）
bash /Users/wenjiechen/Doubao/skills/clean-code-gauntlet/tooling/bin/quality-check.sh --stage commit
# merge 阶段加 --stage merge
```

（gates 配置：[`quality-gates/gates.yaml`](quality-gates/gates.yaml)、[`tools/python.yaml`](quality-gates/tools/python.yaml)、[`tools/typescript.yaml`](quality-gates/tools/typescript.yaml)；等价变异体登记 [`EQUIVALENT-MUTANTS.md`](EQUIVALENT-MUTANTS.md)。）

## UI 回归（一键入口）

```bash
# 一键 UI 回归（登录 e2e + smoke + 启动未登录校验）
bash scripts/ui_tests/run_all.sh
```

入口脚本：[`scripts/ui_tests/run_all.sh`](scripts/ui_tests/run_all.sh)、[`g4_login_e2e_skeleton.py`](scripts/ui_tests/g4_login_e2e_skeleton.py)、[`smoke_login_ui.py`](scripts/ui_tests/smoke_login_ui.py)、[`startup_unlogged_verify.py`](scripts/ui_tests/startup_unlogged_verify.py)；说明见 [`scripts/ui_tests/README.md`](scripts/ui_tests/README.md)。

## 常用命令

```bash
cd frontend && npm test      # vitest（55 过）
cd frontend && npm run cruise  # dependency-cruiser 架构检查
```

## 状态（2026-11-03）

- ✅ 命名与目录结构重构批完成；仓库 `byte886/xhs-leads`（PUBLIC）
- ✅ 壳形态定稿：有头 CFT 应用窗（R-6.16 路线A）+ 双窗布局（壳窗左半 / 采集调试窗右半，960×800）+ 登录闭环（ForceLogin→壳窗真实登录页→id_token 注入）
- ✅ 后端 A 批非登录态回归全绿；UI 回归 t 系列 PASS=9 / FAIL=0 / SKIP=2
- ℹ️ e1 真实链路连本机引擎实例 9223（同一登录态 profile），无需另开外部 Chrome
- ℹ️ `roles/` 与 `constitution/` 的 swarm-forge 六角色（specifier→coder→…→QA）为 clean-code-gauntlet 框架模板，**本仓实际工作流 = SOP 文档 + Playwright 脚本 + pytest**，未启用 Gherkin 流水线，勿按六角色流程开工。
