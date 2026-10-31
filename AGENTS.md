# AGENTS.md — xhs-leads AI 操作手册

> 生成自 clean-code-gauntlet 模板。本项目遵循"确定性质量关卡 + 多 Agent 流水线"方法论。

## 核心规则（强制）

1. **质量关卡不可绕过**：Agent 产出的代码必须通过 `quality-gates/` 定义的全部关卡（CRAP/变异/覆盖率/架构），否则不得交接。关卡是机器强制，不是建议。
2. **流水线纪律**：按 roles/ 定义的职责边界工作；specifier 产出必须经人工审批；QA 程序由人审（按关键性）。
3. **不堆提示词**：初始提示词精简到最小；规则写进 quality-gates/ 与 constitution/，不靠长提示词约束 Agent。
4. **上下文卫生**：单 Agent 单任务；Agent 完成任务即交接，下一个 Agent 面对干净上下文。
5. **业务语义人兜底**：核心业务路径、反例、边界条件、验收标准由人钉死，尽量转成可执行测试。

## 执行前必读

1. `README.md` — 项目结构与快速开始
2. `constitution/project.prompt` — 项目宪法（最高优先级）
3. `constitution/engineering.prompt` — 工程规则
4. `constitution/workflow.prompt` — 工作流规则
5. 本角色 prompt：`roles/<你的角色>.prompt`
