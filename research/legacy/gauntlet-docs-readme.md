# codex-xhs-app — 文档

> 生成自 clean-code-gauntlet。按需补充。

## 建议结构

```
docs/
├── REQUIREMENTS.md      # 需求与验收标准（由 specifier 的 Gherkin 汇总）
├── ARCHITECTURE.md      # 架构决策与模块边界（architect 维护）
├── QA-PROCEDURES.md     # QA 程序（specifier 写、人审）
└── DECISIONS.md         # 项目级决策记录（仿 ADR）
```

## 规则

- 事实性内容（验收标准、关卡阈值、版本）以代码/配置为准，文档不双写；
- 变更进项目 CHANGELOG，重大决策进 DECISIONS.md。
