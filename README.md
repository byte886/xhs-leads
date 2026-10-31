# xhs-leads

小红书「低粉爆文」采集 / 克隆 / 发布本地工具（FastAPI 后端 + Vue3 前端 + 外部 Chrome CDP，macOS 壳）。

**核心产品目标**：帮助个人创业者 / 小商家寻找高价值可参考的低粉爆文笔记进行模仿。

- **采集**：按关键词采集小红书笔记 → 详情/互动数据补全 → 低粉爆款 4 维评分（like_fan_ratio 40% / low_fan 25% / interaction_health 20% / freshness 15%）
- **一键克隆**：拉取爆款笔记 + 封面优先/去重提取图片 → 生成可编辑新稿（标题/正文/标签/图片可调）
- **客户库**：笔记互动用户价值视图（评论区线索 → 意向分级 → 聚合 → 去联系）
- **草稿 / 发布**：保存草稿后可再编辑再发布；**发布永远手动（红线）**
- **登录**：外部 Chrome CDP（独立 profile `~/.xhs-leads-profile`），扫码登录 + 读 Chrome Cookie 库

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
| PRD / 调研 / 质检历史 | `docs/`（含 `research/`、`project/`） |

## 状态（2026-10-31）

- ✅ 命名与目录结构重构批完成（提交 `8d4f432` + `08a963f`）；仓库改名 `byte886/xhs-leads`（PUBLIC）
- ✅ 后端 A 批非登录态回归全绿：**100 passed / 8 deselected**
- ✅ 前端重建成功（`frontend/dist/`）；`.app` 重打包冒烟通过（HTTP 200，标题「XHS Leads · 小红书获客助手」）
- ✅ UI 回归 t 系列：**PASS=9 / FAIL=0 / SKIP=2**（数据依赖自动跳过）
- ⏳ e1 真实链路六场景待补跑（需 9223 可见 Chrome + 小红书已登录；登录失效为外部阻塞）
- ⏳ push 待 e1 全绿后执行（成功前不 push）；C 批 Windows 打包未开工
