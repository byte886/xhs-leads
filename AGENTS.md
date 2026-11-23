# AGENTS.md — xhs-leads AI 操作手册

> 本仓已迁到 Claude Code 原生工作流：规则以 `CLAUDE.md` 为准，子代理在 `.claude/agents/`。
> 原 clean-code-gauntlet 的 `constitution/`、`roles/`（六角色补偿设计）已按 `docs/DECISIONS.md` 退役删除。

## 核心规则（强制）

1. **质量关卡不可绕过**：Agent 产出的代码必须通过 `quality-gates/` 定义的关卡（CRAP/变异/覆盖率/架构），否则不得交接。关卡是机器强制，不是建议。
2. **铁律优先**：`CLAUDE.md` 的五条铁律（发布永远手动、导航白名单、禁 reload/禁关壳窗、单账号单实例、改 AC 同提交带测试）违反即回滚。
3. **不堆提示词**：规则写进 `CLAUDE.md` 与 `quality-gates/`，不靠长提示词约束 Agent。
4. **上下文卫生**：单 Agent 单任务；Agent 完成任务即交接，下一个 Agent 面对干净上下文。
5. **业务语义人兜底**：核心业务路径、反例、边界条件、验收标准由人钉死，尽量转成可执行测试。

## 执行前必读

1. `CLAUDE.md` — 项目规则与五条铁律（最高优先级）
2. `docs/v2/01-范围基线.md` — 需求基线（`AC-<域>-<序号>`）
3. `docs/v2/02-测试架构.md` — 测试口径（T0-T4 分层）
4. `README.md` — 项目结构与快速开始
5. `docs/project/小红书页面导航与登录硬性规则.md` — **小红书页面导航与登录硬性规则（强制）**：URL 只允许开 `/login`（浏览页 `/explore` 仅已确认登录时可用），其余页面只准靠模拟鼠标/键盘或 JS 点击进入，全场景禁止 `reload`；改任何小红书相关代码前必须先读
