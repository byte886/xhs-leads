# xhs-leads · Claude Code 项目规则

> 只放常驻必需项。细节一律读指针，不要在本文件里扩写。

## 真相源（唯一）

- 需求：`docs/v2/01-范围基线.md`（`AC-<域>-<序号>`）
- 测试：`docs/v2/02-测试架构.md`
- 决策：`docs/DECISIONS.md`
- 其余文档（PRD v1.5 / 待办 / 闭环清单 / ui-test-checklist 等）为**参考**，冲突时以上三份为准。

## 五条铁律（违反即回滚）

1. **发布永远手动**：任何自动化不得调用 `/api/publish`。
2. **小红书导航白名单**：只允许直接开 `/login`；`/explore` 仅已确认登录时允许；其余页面只准靠鼠标/键盘或 JS 点击进入。
3. **禁 `reload`**，**禁关闭壳窗/用户登录页**（只允许清空白页）。
4. **单账号单实例**：profile 目录独占，不得复用产品 profile 起测试实例，不得以端口作为单实例判据。
5. **改 AC 必须同提交带测试**（`scripts/trace.py --check` 会拦）。新增功能先加 AC 编号，禁止先写代码后补需求。

## 常用命令

```bash
# 后端
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q          # 新套件（T0-T3）
PYTHONPATH=backend backend/.venv/bin/python scripts/trace.py --check
# 前端
cd frontend && npm test && npm run build
# UI 烟测（仅 T4，需引擎与后端已起）
bash scripts/ui/smoke_unlogged/run.sh
```

## 禁止事项

- 不运行 `npx playwright init-agents`（会生成 planner/generator/healer，与本仓断言纪律冲突）。
- 不修改 `legacy/`（归档区，只读参照）。
- 不为了让测试通过而改测试（失败先分类：flaky / 真 bug / 环境）。
- 不新增并行编号体系（R-6.x / P0-x / FR-xx 已冻结）。

## 角色分工

见 `.claude/agents/`：`qa-oracle`（AC 与测试映射）、`redline-guard`（审 diff）、`ui-runner`（只跑 T4）、`test-triager`（失败分类）。
