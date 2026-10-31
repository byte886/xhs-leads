# frontend — Vue3 前端

> 小红书获客工具的可视化界面：采集筛选、卡片评分、克隆创作、客户库、草稿发布、登录态与设置。包名 `xhs-leads-frontend`。

## 工程要点

- 技术栈：Vue 3 + TypeScript + Vite。
- **构建产物并入 `frontend/dist/`**（vite `outDir: 'dist'`，随 .app 打包、由后端静态托管）；不入库。
- 后端 API 基址：`src/api.ts` 指向 `127.0.0.1:8791`（同源托管，开发态走 Vite 代理）。

## 命令

```bash
npm install          # 首次/依赖变更后
npm run build        # → frontend/dist/（改动后必须重建，否则 .app 内是旧产物）
npm run dev          # 开发热更新（Vite dev server）
```

## 源码结构（frontend/src/）

| 文件/组件 | 职责 |
|---|---|
| `main.ts` / `App.vue` | 入口与整体布局（四步 FlowBar + 工作区 + 登录弹窗） |
| `api.ts` / `types.ts` / `store.ts` | 后端 API 封装 / 类型 / 全局状态 |
| `components/CollectPanel.vue` | 采集页：关键词、筛选（排序/时间/采集方式）、参数透传 |
| `components/CardGrid.vue` / `CardMenu.vue` | 卡片网格 / 卡片操作菜单（评分徽标、说明弹窗、tooltip、克隆、导出） |
| `components/FilterRank.vue` | 筛选行与排序控件 |
| `components/WorkArea.vue` | 创作区：标题/正文/标签/图片（追加、删除、拖拽排序） |
| `components/PublishPage.vue` | 草稿/发布（发布永远手动——红线） |
| `components/ClientsPanel.vue` | 客户库：评论区线索 → 意向分级 → 聚合 → 去联系指引 |
| `components/SettingsPanel.vue` | 设置：AI Key（门控/掩码）、CDP、账号人设 |
| `components/LoginModal.vue` | 登录态与 Cookie 闭环界面 |

## 边界

- 构建产物由后端静态托管（生产）；开发期用 `npm run dev` 对接 8791。
- `frontend/dist/` 与 `node_modules/` 均在 .gitignore；克隆后需自行 `npm install && npm run build`。
