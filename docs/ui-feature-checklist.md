# UI 功能清单与测试覆盖映射（xhs-leads）

> 用途：UI 测试完整性检查的基线。反向遍历 UI 组件穷举细节点 → 映射到功能清单 → 标注测试覆盖状态，暴露未覆盖项。
> 更新：2026-10-30（授权薄层 + activation 档位后）

## 一、功能清单（FRD 基线）

### M1 全局框架（App / FlowBar / CardMenu / LoginModal / CardGrid 批量）
| 编号 | 功能 | 细节点 |
|---|---|---|
| FR-01 | 4-Tab 导航 | 采集 / 创作 / 客户 / 设置，Tab 切换 + 提示文案 |
| FR-02 | 全局状态条 | 登录态 / CDP / AI Key 共享观察，点击跳对应设置 |
| FR-03 | 登录态检测 | 启动自动检测，状态条实时反映 |
| FR-04 | 登录二维码弹窗 | 自动读取登录态 / 打开登录页（新窗口小红书）/ 失败重试 |
| FR-05 | 全局 toast | 操作结果提示 |
| FR-06 | 卡片右键菜单 | 一键克隆（抄内容）/ 一键采集客户（采人）/ 打开原作品 |
| FR-07 | 卡片批量操作 | 全选图文 / 清空选择 / 下载图片包 / 导出 JSON / 导出 CSV |
| FR-08 | 卡片选中态 | 单选 / 多选 / 已选计数 |

### M2 采集页（CollectPanel / FilterRank）
| 编号 | 功能 | 细节点 |
|---|---|---|
| FR-10 | 搜索参数 | 关键词 / 结果上限 / 搜索排序（最新·最热）/ 笔记类型 / 时间范围 / 范围 / 距离 / 采集间隔 / 快速档 |
| FR-11 | 任务式采集 | 开始采集（采集中可再点=停止）/ 进度显示（n/limit）/ 收尾统一评分排序展示 |
| FR-12 | 筛选行 | 排序 / 时间快速筛选，已选筛选行回显 |
| FR-13 | CDP 连接配置 | Chrome 路径 / Profile / 端口展示 / 重启调试连接 / 保存并验证 / 引导教程 |
| FR-14 | Cookie 导入 | 粘贴文本导入 → 后端解析注入 → 校验有效性展示 |
| FR-15 | Cookie 状态展示 | 已登录（昵称）/ 未登录提示 |

### M3 卡片列表（CardGrid）
| 编号 | 功能 | 细节点 |
|---|---|---|
| FR-20 | 评分体系 | 评分徽标 / 悬浮 tip / 说明弹窗（维度明细） |
| FR-21 | 卡片标签 | 结论标签（tagConclusion） |
| FR-22 | 打开原作品 | 跳转 explore 原链接 |
| FR-23 | dedup 防重 | 重复笔记提示（60s 任务防重 + 库内去重展示） |
| FR-24 | 数据导出 | JSON / CSV 导出 |

### M4 创作页（PublishPage）
| 编号 | 功能 | 细节点 |
|---|---|---|
| FR-30 | 一键克隆 | 拉详情 → 标题/正文/图片填入创作页 |
| FR-31 | 内容编辑 | 标题（≤50 字计数）/ 正文（≤1000）/ 话题标签 |
| FR-32 | 图片管理 | 追加 / 单张删除 / 拖拽排序 / 点开看全图 lightbox |
| FR-33 | AI 工具条 | 标题 / 正文 / 标签生成；Key 门控（未配置灰态 + 跳设置） |
| FR-34 | 发布前质检 | 质检面板（违规词/规则命中）/ 开关 |
| FR-35 | 草稿 | 保存草稿 / 草稿面板（列表）/ 恢复草稿 / 删除草稿 |
| FR-36 | 发布 | 可见性（默认仅自己可见）/ 强确认 / 发布护栏 / 发布记录 |
| FR-37 | 复制原作品链接 | 一键复制 |
| FR-38 | 发布统计 | 记录区汇总（parseStats） |

### M5 客户页（ClientsPanel）
| 编号 | 功能 | 细节点 |
|---|---|---|
| FR-40 | 一键采集客户 | 卡片/笔记 → 采人（自动访问详情，切客户 Tab） |
| FR-41 | 线索统计条 | 线索总数 / 高意向 / 留联系方式命中 / 采集说明 |
| FR-42 | 意向分级 Tab | 按意向分组浏览 |
| FR-43 | 客户列表 | 昵称 / 评论时间 / 属地 / 命中词 / 资料链接 |
| FR-44 | 属地聚合 | ip_top 聚合视图 |
| FR-45 | 去联系指引 | guide 面板（获客指引） |
| FR-46 | 导出线索 CSV | 一键导出 |

### M6 设置页（SettingsPanel）
| 编号 | 功能 | 细节点 |
|---|---|---|
| FR-50 | AI 模型配置 | API Key（掩码展示）/ Base URL / 模型名 / 测试连接 / 保存 / 清除 |
| FR-51 | CDP 配置 | Chrome 路径浏览 / Profile / 重启 / 验证 / 引导教程 |
| FR-52 | 账号人设 | 行业 / 口吻 / 常发主题 / 保存 |
| FR-53 | 授权状态条 | 模式标签（免费版/未激活/激活版）/ 已开通功能 / 设备指纹 |
| FR-54 | 激活 | 激活码输入 / 激活 / 已激活提示（机器码绑定） |

### M7 授权与后端闸门
| 编号 | 功能 | 细节点 |
|---|---|---|
| FR-60 | 授权接口 | GET /api/license_status；POST /api/license_activate（验签/机器码/有效期） |
| FR-61 | 业务闸门 | collect / clone / publish / llm 四入口过 check_license（free 恒放行） |

## 二、UI 映射表（细节点 → UI 位置 → 测试覆盖）

覆盖状态：✅ = 有自动化脚本断言；◐ = 手工实测过（Playwright 手动验证）但无脚本资产；❌ = 无任何覆盖。

| 细节点 | UI 位置 | 自动化脚本 | 状态 |
|---|---|---|---|
| 4-Tab 导航 | FlowBar tabs | t02 / t03 | ✅ |
| 全局状态条 | FlowBar sys | t03 | ✅ |
| 登录态检测 | FlowBar/App onMounted | e1_s6 | ✅ |
| 登录二维码弹窗 | LoginModal | e1_s6（弹窗/恢复/关闭） | ✅ |
| toast | 全局 | e1_s5（保存 toast）等 | ✅ |
| 卡片右键菜单 | CardMenu | e1_s2（克隆入口） | ✅（克隆/客户两入口 e1 覆盖，打开原作品 ◐） |
| 全选图文 | CardGrid 批量 | 无 | ❌ |
| 清空选择 | CardGrid 批量 | 无 | ❌ |
| 下载图片包 | CardGrid 批量 | 无 | ❌ |
| 导出 JSON | CardGrid 批量 | 无 | ❌ |
| 导出 CSV | CardGrid 批量 | 无 | ❌ |
| 卡片选中态 | CardGrid isSel | 无 | ❌ |
| 搜索参数 7 项传递 | CollectPanel 参数区 | e1_s1（sort/time_range/note_type/scope/distance/limit 全断言） | ✅ |
| 开始采集 | doCollect | e1_s1 | ✅ |
| 停止采集 | doCollect 再点 | 无 | ❌ |
| 采集进度显示 | appState.collecting | e1_s1（部分） | ◐ |
| 收尾评分排序 | 后端 collect 完成 | e1_s1（task done + 排序） | ✅ |
| 筛选行 | FilterRank | e1_s1（已选筛选行断言） | ✅ |
| CDP 配置区 | CollectPanel/CDP | e1_s5（CDP 就绪） | ✅ |
| 保存并验证 CDP | doSaveCdpConfig | 无（对话框型） | ❌ |
| Cookie 导入 | showImport/doImportCookie | 无 | ❌ |
| Cookie 状态展示 | CollectPanel 登录条 | e1_s6 | ✅ |
| 评分徽标 | score-badge | t06 | ✅ |
| 悬浮 tip | score-tip | t06 | ✅ |
| 评分弹窗 | scoreModal | t06 | ✅ |
| 卡片结论标签 | tagConclusion | 无 | ❌ |
| 打开原作品 | CardGrid/CardMenu | ◐（跳转路径被 s4/s2 间接覆盖） | ◐ |
| dedup 防重 | 采集任务层 | t07 | ✅ |
| 一键克隆 | CardMenu → PublishPage | e1_s2（标题填充/图片补全） | ✅ |
| 标题/正文/标签编辑 | pub-field | e1_s2（标题）；正文/标签手编 | ◐ |
| 图片追加 | triggerAdd | e1_s3 | ✅ |
| 图片删除 | removeImg | e1_s3 | ✅ |
| 图片拖拽排序 | onDragStart/Over/Drop | 无 | ❌ |
| 图片预览 lightbox | openPreview | e1_s3（看全图） | ✅ |
| AI 工具条门控 | ai-bar/ai-btn | t01（按钮态） | ✅（生成调用需 Key，◐） |
| 发布前质检 | runQC/qcOpen | e1_s3 + t04 | ✅ |
| 保存草稿 | saveDraft | e1_s3（草稿库入库） | ✅ |
| 草稿面板/恢复 | openDraftPanel/restoreDraft | e1_s3（恢复最新草稿） | ✅ |
| 删除草稿 | removeDraft | 无 | ❌ |
| 发布护栏 | publish/guard | t04（违规词拦截/强确认） | ✅ |
| 真实发布（平台动作） | publish 确认后 | 无（按约定留用户） | ❌（有意留白） |
| 发布记录区 | loadLogs/parseStats | t03（summary）+ t08（SKIP 数据依赖） | ✅ |
| 复制原作品链接 | PublishPage | 无 | ❌ |
| 一键采集客户 | App collectClients | e1_s4（点击→详情→切 Tab） | ✅ |
| 线索统计条 | info-bar | t09 | ✅ |
| 意向分级 Tab | setClTab | t09/t10 | ✅ |
| 客户列表 | loadClients | t10 | ✅ |
| 属地聚合 | loadAgg/ip_top | t09 | ✅ |
| 去联系指引 | guideOpen/goTalk | t11 | ✅ |
| 导出线索 CSV | exportClientsCsv | 无 | ❌ |
| AI Key 配置/测试/清除 | SettingsPanel | e1_s5 | ✅ |
| CDP 设置区 | SettingsPanel | e1_s5 | ✅ |
| 人设保存 | savePersona | e1_s5 | ✅ |
| 授权状态条 | lic-bar | ◐（手动 UI 实测） | ◐ |
| 激活流程 | lic-act 输入/激活 | ◐（8792 activation 实例实测） | ◐ |
| license_status 接口 | GET /api/license_status | curl 实测 | ◐ |
| license_activate 接口 | POST /api/license_activate | curl 实测（真码 200/坏码 400） | ◐ |
| 四业务闸门 | collect/clone/publish/ai_generate | 自检 + 回归（free 放行） | ◐ |

## 三、完整性检查结论

### 3.1 反向遍历结果
遍历全部前端组件（App.vue + 9 个组件）的按钮、输入框、弹窗、状态条、菜单项，**全部可映射到 §一 功能清单**，无清单外功能。清单内 61 项细节点均有明确 UI 落点。

### 3.2 未覆盖细节点（❌，共 12 项）→ 建议补测
| # | 细节点 | 建议 |
|---|---|---|
| b1 | 批量：全选图文 / 清空选择 | 补脚本（选中计数 + 取消） |
| b2 | 批量：下载图片包 / 导出 JSON / 导出 CSV | 补脚本（产物存在 + 行数/条数校验） |
| b3 | 卡片选中态计数 | 随 b1 |
| b4 | 停止采集 | 补脚本（采集中再点 → task stop → 收尾） |
| b5 | 图片拖拽排序 | 补脚本（拖后顺序断言） |
| b6 | 删除草稿 | 补脚本（删除后列表消失） |
| b7 | Cookie 导入（粘贴表单） | 补脚本（导入 → 校验结果展示） |
| b8 | 卡片结论标签 tagConclusion | 随 t06 补断言 |
| b9 | 正文/话题标签手动编辑 | 随 e1_s2 补断言 |
| b10 | 复制原作品链接 | 小功能，可并入 e1_s2 |
| b11 | 导出线索 CSV | 补脚本（同 b2） |
| b12 | 真实发布 | 有意留白（平台真实动作，发布护栏已自动化覆盖） |

### 3.3 有意留白（不补）
- 真实发布到小红书平台（FR-36 末环）：发布护栏（违规词/确认/可见性）已自动化；真发属平台真实动作，按约定留最终用户手动确认。
- AI 文稿真实生成：依赖用户自备 LLM Key；按钮门控/未配置灰态已自动化，生成质量不属 UI 回归范畴。

## 四、建议补测后回归基线
t01-t11 + e1_s1-s6 + b1-b11（补测脚本建议放 scripts/ui_tests/b1_batch.py / b2_draft_img.py / b3_cookie_stop.py）
