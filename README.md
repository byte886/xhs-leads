# xhs-leads

小红书「低粉爆文」采集/克隆/发布本地工具（FastAPI + Vue3 + 外部 Chrome CDP）。

## 这是什么

**核心产品目标：帮助用户寻找高价值可参考的低粉爆文笔记进行模仿，一切功能服务于此。**

- **采集**：按关键词采集小红书笔记 → 详情/互动数据补全 → 评分（低粉爆款 4 维权重：like_fan_ratio 40% / low_fan 25% / interaction_health 20% / freshness 15%）
- **作者榜**：作者价值聚合视图（P2）
- **一键克隆**：拉取爆款笔记 + 封面优先/去重提取图片 → 生成可编辑新稿（标题/正文/标签/图片可调整、追加、拖拽排序）
- **客户库**：笔记互动用户价值视图（P4）
- **草稿 / 发布**：保存草稿后可再编辑再发布；**发布永远手动（红线）**
- **登录**：外部 Chrome CDP（9223 + 独立 profile `~/.xhs-leads-profile`），扫码登录 + 读 Chrome Cookie 库；生产环境由内置脚本引导用户自助开启远程调试

## 架构（v2.0 定稿，无 Docker）

```
外部 Google Chrome（CDP 9223 + ~/.xhs-leads-profile，小红书登录态）
        ↓ ① CDP + 读 Cookie 库
FastAPI 业务壳 :8791（采集/评分/克隆/客户库/草稿/发布护栏）
        ↓ ② 静态托管
Vue3 前端（npm run build → app/web）
```

- 已移除 Docker（采集/发布不再走 xhs-mcp 容器）
- 生产交付无豆包/无 AI 环境：CDP 仅调试；生产靠 `app/scripts/setup_chrome.py` / `pick_chrome.py` 引导用户自助

## 目录结构

- `docs/` — PRD、调研方案、需求约束、质检报告
- `docs/research/` — 生财 MCP + Google/GitHub 调研（step1-6b，带来源）
- `docs/project/` — 开发对齐机制、待办事项、**需求约束清单**、资产清单、**新机器部署手册**
- `app/src/src/` — 业务代码（server.py / browser_client.py / collect_pipeline.py / score.py / cookie_reader.py）
- `app/frontend/` — Vue3 前端源码（npm run build → `app/web/`）
- `app/scripts/` — 生产引导脚本（setup_chrome.py / pick_chrome.py）
- `tests/` — 9 个测试文件 173 个测试

## 状态（2026-10-28）

- ✅ P1 采集/评分 / P2 作者榜 / P4 客户库 / 一键克隆（图片提取增强）/ 草稿 / 筛选透传
- ✅ 六维质检全绿：测试 173 EXIT=0、覆盖率 85%、圈复杂度全项目 ≤8、CRAP 数值真实、变异存活 0、DRY 0 候选
- ✅ 提交链：`0a74046`（挂起根治+85%）→ `8bfb681`（质检六维全绿收尾）
- ⏳ 平台限流中（10-28 仍 captcha），解除后跑真实链路验证；发布永远手动
- 📋 待办与开放项见 `docs/project/待办事项.md`、`docs/project/需求约束清单.md`

## 快速开始

**→ 先读 `docs/project/新机器部署手册.md`（v2.0：Python 业务壳 + Vue3 构建 + CDP 登录 + 测试/质检 + 纪律红线全覆盖）。**

```bash
# 后端
cd app && python3 -m venv .venv && source .venv/bin/activate
pip install fastapi "uvicorn[standard]" pydantic httpx pytest pytest-cov pytest-timeout coverage import-linter radon
python3 -m uvicorn src.server:app --host 127.0.0.1 --port 8791 --app-dir app/src &

# 前端（改动后必须重建）
cd app/frontend && npm install && npm run build

# 测试
PYTHONPATH=app/src app/.venv/bin/python -m pytest -q --no-header -p no:cacheprovider -W ignore
```
