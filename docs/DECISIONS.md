# 决策日志（DECISIONS）

> 只追加不改写。编号 `DR-###`。旧体系 ADR（R-6.41-01~09）不再新增，需要时在此引用旧编号并重述结论。
> 格式：日期 / 决策 / 理由 / 影响范围 / 替代方案。

## DR-001 · 2026-11-07 · 测试与需求体系推倒重建
- **决策**：现有测试基线（pytest 321 / vitest 55 / UI 脚本 20+）、六维质量关卡、需求与测试文档全部作废，按 `docs/v2/` 重建。
- **理由**：六套并行编号、42 份文档无单一真相源、三向矩阵靠人肉维护、红线只写在文档里，导致"问题反复修反复出"。
- **影响**：旧物 `git tag pre-rebuild-2026-11-07` + 移入 `legacy/`，只读留档；`constitution/`、`roles/` 随 Claude Code 落地退出使用。
- **替代方案（已否决）**：在旧体系上继续打补丁——已试过 3 个月，边际收益递减。

## DR-002 · 2026-11-07 · 执行器定为 Claude Code
- **决策**：测试与需求的落地全部由 Claude Code 执行，用原生 `CLAUDE.md` + subagents + hooks 替代补偿式 `constitution/roles`。
- **理由**：豆包侧无原生多 Agent 能力时，`constitution/roles` 是补偿设计；迁到 Claude Code 后继续沿用属双重抽象。
- **影响**：`.claude/agents/` 四角色（qa-oracle / redline-guard / ui-runner / test-triager）；不做"把 roles 文案搬进 agent 提示词"的迁移式复制。

## DR-003 · 2026-11-07 · 不引入 Playwright Test Agents 与 AI 覆盖率补测工具
- **决策**：不运行 `npx playwright init-agents`；不引入 Qodo Cover 类补测工具。
- **理由**：healer 的"功能坏了就 skip"语义与本仓"断言必须真实"冲突；覆盖率补测只补数字不补语义，本仓改由 AC 追溯 + 变异测试守。
- **影响**：T4 只保留 3 个自建烟测入口，其余旧 UI 脚本归档。

## DR-004 · 2026-11-07 · AC 三态制（A/M/X）
- **决策**：每条 AC 必须落 `automated` / `manual-gate` / `out-of-scope` 之一，无第四态；由 `scripts/trace.py` 机器校验。
- **理由**：人工维护的三向矩阵已证明会失真（ADR-01~09 均为补丁）。
- **影响**：测试需带 `@pytest.mark.ac(...)`；merge 门禁止"裸 AC"。

## 待拍板（由用户决定，未决前相关 AC 标 `X`）
- G2 多账号前端 UI 做不做
- G3 数据回流反哺评分做到哪一步
- G4 养号是否彻底砍
- G5 授权激活自用阶段是否保留
- G7 视频采集是否随"只做图文"收敛
