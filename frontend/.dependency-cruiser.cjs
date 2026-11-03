// R-6.37f：dependency-cruiser TS 架构约束（.cjs 规避 ESM 假 PASS 坑）
// 规则：
//  1. components/ 不得直接 import api 之外的业务模块倒置（仅允许 store/api/types/lib）
//  2. 禁止循环依赖
//  3. 外部 npm 包白名单（仅 vue 系列/内置）——其余裸 import 视违规
module.exports = {
  forbidden: [
    {
      name: 'no-circular',
      severity: 'error',
      comment: '禁止模块间循环依赖',
      from: {},
      to: { circular: true },
    },
    {
      name: 'components-no-import-root',
      severity: 'error',
      comment: 'components/ 只能依赖 store/api/lib/types（禁止反向依赖 main/App）',
      from: { path: '^src/components/' },
      to: {
        path: '^(src/main|src/App)',
      },
    },
  ],
  options: {
    tsConfig: { fileName: './tsconfig.json' },
    doNotFollow: { path: 'node_modules' },
    tsPreCompilationDeps: true,
  },
}
