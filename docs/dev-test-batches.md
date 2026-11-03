# 开发-测试分批配套契约（dev-test-batches）

> 目的：每一批（Batch）先定义「开发范围」，开发全部完成才进入本批「配套测试」，测试完整通过才算本批完成。测试结论只覆盖本批产物，不替后续批担保；后续批（壳、跨平台）的产物自带配套测试，不因上一批测过而免测。
>
> 状态时间：2026-11-03（A/B/R 批完成，C 批未开工，D 批留白）

---

## 一、分批原则（契约约定）

1. **批次边界先定**：开工前先对契约——本批开发清单、本批测试清单、验收口径。
2. **本批开发做完 → 本批测试**：不在开发未完成时先行测试，避免返工。
3. **测试结论不越批**：A 批的测试结论只覆盖 A 批产物（本地服务形态）；B 批产物（.app 壳）必须在其上复跑核心链路，属 B 批配套测试，非返工。
4. **约定留白独立**：明确约定后续才做的（真实发布、AI 真实生成）不进入任何批次测试义务。

---

## 二、A 批 —— 产品本体（本地服务形态）【✅ 已完成】

### 开发范围
| 模块 | 内容 |
|---|---|
| 功能链路 | 采集 / 克隆 / 创作质检 / 客户线索 / 设置 / 登录态与 Cookie 闭环 |
| 引擎 | B 方案引擎池（Playwright `channel='chrome'` + 独立 profile）+ 三机制（生命周期 / 风控状态机 / 任务机制）+ stealth 防检测 |
| 数据与治理 | 统一数据层（xhs_notes.db）、配置中心、日志/审计/Cookie 脱敏 |
| 授权 | 薄层 free + activation 档位（RSA 激活码绑定机器码，schema_version=1） |

### 配套测试
| 测试 | 结果 |
|---|---|
| UI 自动化用例清单（docs/ui-test-checklist.md，51 条） | 49 ✅ / 0 🔶 / 2 ⛔（D9 AI 真实生成、D15 真实发布） |
| t 系列回归（run_all.sh） | 10 PASS / 0 FAIL / 1 SKIP（t08 数据依赖） |
| e1 六场景 | s1-s6 全过 |
| b 系列缺口补测 | b01-b07 全 PASS（批量卡片/停止采集/草稿拖拽/Cookie 导入/杂项/客户导出/授权 UI） |

### 验收口径
除真实发布（D15）与 AI 真实生成（D9）外，全部功能均可 UI 自动化且已验证。→ **已达成。**

---

## 三、B 批 —— 壳（独立 macOS .app）【✅ 已完成 133a210】

### 开发范围（做完才进测试）
| 模块 | 内容 |
|---|---|
| 打包 | PyInstaller/py2app 打包 `.app`，嵌入前端构建产物（app/web） |
| 服务管理 | 应用内自动起 uvicorn、自动打开浏览器、退出清理 |
| 数据目录 | 迁移到 `~/Library/Application Support/xhs-leads`（路径可移植、旧 data 迁移） |
| 端口冲突 | 端口占用探测与替换 |
| 脚本 | 一键打包脚本 + .spec 配置 |

### 配套测试（B 批验收）
| 测试 | 内容 |
|---|---|
| 壳冒烟 | `.app` 产物在干净环境启动 → 服务端口就绪 → UI 加载 |
| 核心链路复跑 | 复用 t/e1/b 脚本指向 `.app` 端口：采集 / 克隆 / 草稿 / 授权状态条 |
| 数据迁移 | 旧 data 目录迁移验证（不丢登录态与笔记/草稿） |
| 生命周期 | 启动/退出/重启不丢登录态（复用 refresh_cookie 闭环） |

### 验收口径
B 批产物上核心冒烟全过 → 本批完成。A 批测试结论不延伸至 .app 形态。

---

## 四、R 批 —— App 内呈现形态（无头引擎 + 登录小窗 + 调试视图 + 状态提示条）【🔶 本批进行中】

> 需求变更源：`docs/需求变更-App内呈现形态.md`（2026-10-31 用户逐条拍板）。B 批（有头引擎读系统 Chrome）产物在本批改造为：外部零 Chrome 窗口、登录态独立 profile、app 内镜像呈现。

### 开发范围（做完才进测试）
| 模块 | 内容 |
|---|---|
| 引擎无头化 | `engine_pool` `channel="chrome"` 改 headless（无窗口）+ stealth 强化；独立 profile 归一（app 数据目录）；风控敏感环节「后台可见窗口」参数开关兜底 |
| 登录链路 | `cookie_reader` 读系统 Chrome 下线（代码保留注释）；登录态存引擎独立 profile；`browser_client` 登录判定统一无头 |
| 镜像层（登录小窗） | 后端 CDP 桥：截图流（captureScreenshot）+ 事件转发（click/type/drag/scroll）；前端登录小窗=完整小红书登录页镜像（扫码/手机号验证码/微信途径不限） |
| 调试视图 | 设置 → 调试：按需开启引擎实时画面（复用镜像层；默认可交互，只读开关待定） |
| 状态提示条 | 后端任务状态接口扩展动作文案 + 前端全局状态条（实时播报「正在…」，不落盘；审计日志不变） |
| 镜像按需 | 截图流三档：登录自动开 / 调试手动开 / 平时关 |
| e1 基建 | e1 脚本改连引擎调试端口（9223 归引擎）；9223 外部 Chrome 路径退役 ✅（10-31：引擎带 --remote-debugging-port=config.engine_port()；25 脚本统一 9223；run_all.sh 引擎就绪检查+拉起） |

### 配套测试（R 批验收）
| 测试 | 内容 |
|---|---|
| A 批回归 | pytest 非登录态全绿（当前基线 100/8）+ 引擎无头化后核心链路复跑 |
| t 系列 | run_all.sh 全绿（基线 PASS=9/0/2） |
| e1 六场景 | ✅ 改造后连引擎真实态补跑完成（2026-11-01）：s1 筛选+采集落库、s2 克隆、s3 质检+图片+草稿全链路、s4 客户、s5 设置全 PASS；**s6 登录闭环脚本已删除**（2026-11-03，测已下线「立即读取 Chrome 登录态」旧链路，G4 重写前无价值）；现行登录闭环覆盖 = smoke_login_ui（未登录弹窗）/ b08（扫码全链）/ e2_wall_popup（采集遇墙弹框）/ startup_unlogged_verify（未登录 2 页基线） |
| R 新增测试点 | 登录小窗三种途径交互（扫码/手机号/微信入口显示与操作）、镜像开关（登录自动/调试手动/平时关）、状态提示条动作文案轮询、独立 profile 登录态重启后保留（不依赖系统 Chrome） |
| 外部零窗口 | 交付形态启动后无任何 Chrome 窗口出现（.app 冒烟复核） |
| R-6.8~6.31 窗口治理/启动时序（2026-11-03 补记） | 该系列连续迭代（登录 overlay R-6.9、登录墙 ForceLogin R-6.21e/f/g、双窗分屏 R-6.22、关窗语义重定义 R-6.22p、登录页适配 R-6.26、视口对齐 R-6.27、启动时序 R-6.28、explore 判定页移除 R-6.29、死拷贝收敛 R-6.31）测试沉淀为：layout_verify.py（已登录双窗布局，已对齐 R-6.22p）+ startup_unlogged_verify.py（未登录 2 页基线 + 15s 窗口震荡检测，G1+G3）；人工闸口链路 = b08（扫码）。未登录基线 = 壳窗 8791/左半 + Tab2 /login-hint/右半，960×800 |
| 人工闸口清单（风控敏感，保持人工 2026-11-03 登记） | **TS6 运行中停止**：b02_stop_collect.py 已覆盖「采集中点停止→收尾回落空闲」主路径，断言待加固（状态回落文案随 UI 演进需复核）；**TS7 运行中被踢（T3）**：kicked 感知→ForceLogin 弹→扫码恢复，需真机登录态 + 平台真实踢号，保持人工，不脚本化 |
| G4 登录闭环 E2E（R-6.35g 实跑 PASS ✅） | 脚本 = `g4_login_e2e_skeleton.py`（只观察不模拟）。**2026-11-03 实跑通过**：前提校验（8791/9223/未登录 2 页基线）→ 用户点「去登录」→ 壳窗导航 xiaohongshu.com/login → 用户手机扫码 → 登录态秒级落定。断言：**A1 login_status logged_in=true/kicked=false/risk_verify=false（raw=web_session有 页面登录是）✓；A2 gate pending=false ✓；A3 引擎页面集={8791/, xhs /explore} 无登录页残留 ✓；A4 .force-mask 自动关（wentLogin&&logged_in&&!risk_verify）✓；A5 引擎内存 cookie 含 id_token+web_session（16 个：a1/id_token/web_session/unread…）✓**。实跑坑记录：①CDP /json/list 须按 type=page 过滤（service worker sw.js 会误计为第 3 页）；②用户操作节奏与脚本轮询窗口错配——导航等待窗口 180s 仍可能因中转延迟错过，实跑时用户就位后再起跑 |
| R-6.35d 测试再扫（2026-11-03） | 全量 pytest **191 例全绿 / 0 失败**（代码线已修上轮 4 失：test_server_api 2 例 _engine_login_state 四元组 arity 断裂 + TestDownloadImages 2 例）。再扫结论：ui_tests 无旧实现活引用（run_all.sh:13 仅历史注记；b04 用现行 /api/import_cookie 文本导入端点，live）；backend/tests cookie_reader 仅 docstring 历史注记非活引用；README 清单与实际文件一一对应。方向取舍无新增遗留 |
| R-6.36b 覆盖特征测试铺网（2026-11-03） | 易批 50 例（test_license/risk/config/login_chain/mirror）+ 中批 24 例（test_browser_client 扩展 + test_collect_pipeline_unit + test_engine_pool_unit）。覆盖实测：license 24→83%、risk 50→83%、config 51→58%、mirror 15→47%、server 69→72%、browser_client 24→27%、collect_pipeline 35→48%、engine_pool 42→44%。全量 pytest 262 passed（基线 188+74）。纪律：只固化现行行为不改 src/。**硬模块可行性**：shell_window 1024 行中纯函数仅 _json_body/_json_post/get_coordinator≈20 行，其余均为 CDP 页/窗守卫轮询（login_guard 240 行）——现实可达上限 ≈10%，建议建假 Playwright/假 WindowCoordinator 基建后再铺；engine_pool AccountEngine 380 行包 Playwright browser/context，上限 ≈50%；collect_pipeline 在线纯函数已挖尽，余为 browser_client 委托链——上限 ≈50%。三模块 80% 现实不可达，列待拍板 |

### 验收口径
R 批产物上：外部零 Chrome 窗口 + 登录全程在 app 内（途径不限）+ 登录态独立且持久 + 调试视图可看采集画面 + 状态提示条实时显示 + 全量测试基线全绿 → 本批完成。push（含重构批 3 提交）在本批全绿后执行。

---

## 五、C 批 —— Windows 跨平台【🔶 未开工，清单已按新方案修正】

### 开发范围（修正后）
| 模块 | 内容 | 说明 |
|---|---|---|
| .exe 打包 | PyInstaller + Inno Setup（.exe + 安装器） | 与新方案无关，仍需要 |
| MachineGuid 机器码 | `license.py` Windows 分支（HKLM\Microsoft\Cryptography）实机确认 | 授权机制，独立于浏览器方案 |
| ~~DPAPI 解密实机验证~~ | **删除** | B 方案（独立 profile）不走 DPAPI，`cookie_reader` 中该分支为旧方案遗留（仅高级模式兜底） |
| ~~自写 Chrome 发现~~ | **删除** | Playwright `channel='chrome'` 在 Windows 自动定位系统 Chrome |

### 配套测试
| 测试 | 内容 |
|---|---|
| Win11 实机 | 同套 UI 自动化（t/e1/b 脚本）+ 授权激活链路（激活码绑定 MachineGuid） |
| 壳冒烟 | .exe 产物启动 → 服务就绪 → UI 加载 → 核心链路 |

### 验收口径
Windows 实机同套 UI 自动化 + 授权链路 + .exe 冒烟全过 → 本批完成。

---

## 六、D 批 —— 真实发布 + AI 真实生成【⛔ 约定留白】

| 项 | 说明 |
|---|---|
| 真实发布（D15） | 逻辑已有（publish_logs），需真实账号 + 人工闸口 + 小流量 + 风控观察；非 UI 自动化 |
| AI 真实生成（D9） | 需真实 AI Key 额度；非 UI 自动化 |
| 约定 | 与 A 批测试义务无关，进入 D 批时单独立项 |

---

## 七、批次状态速览

| 批次 | 开发 | 测试 | 状态 |
|---|---|---|---|
| A 产品本体 | ✅ | ✅ 49✅/2⛔ | 完成 |
| B 壳 .app | ✅ | ✅ 已完成（133a210） | 核心链路已在 .app 复跑 |
| R App内呈现 | ✅ R-1~R-7 引擎无头化+profile 归一+登录链路改造+前端+基建（登录小窗/状态条/调试视图/引擎 9223）+ 壳品牌+登录态重启保留+回 APP 提速+真实态补跑（回归 EXIT=0 + t 系列 9/0/2 + e1 s1-s5 真实态全过） | ✅ e1 真实态补跑完成（2026-11-01）：s1 采集落库/s2 克隆/s3 质检全链路/s4 客户/s5 设置全 PASS；顺带修复 _require_real_login 登录态判定口径（id_token 对齐 login_status，修复真实登录态下采集/克隆/客户 403） | 完成 |
| C Windows | 🔶（修正：去 DPAPI/Chrome 发现） | 🔶 未开工 | 待开工 |
| D 真实发布/AI | 逻辑已有 | ⛔ 人工闸口 | 留白 |
