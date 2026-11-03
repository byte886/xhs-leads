# 查缺补漏清单 · 测试与方法论部分（线 B 只读盘点）

> 盘点日期：2026-11-03 ｜ 方法：全程只读静态评估（读脚本/文档/源码、grep、git log/show），未启动任何进程、未执行任何测试脚本、未修改仓库原文。
> 对照基准：backend/src 当前实现 + scripts/packaging/launcher.py + 当前「未登录启动时序」基线（启动后稳定 2 页：壳窗 `http://127.0.0.1:8791/` 左半 + Tab2 `http://127.0.0.1:8791/login-hint` 右半；无 explore 判定页、无窗口震荡；960×800 对齐）。

## 0. 比对基准

| 项 | 值 |
|---|---|
| 盘点开始时 `git rev-parse HEAD` | **f9503204f5b0acdef9461b1383fd36827817df39**（R-6.29 未登录闭环窗口治理：移除判定页开窗副作用） |
| 盘点时工作区 | 干净；仅有未跟踪文件 `backend/src/injections.py`（并行 R-6.30 重构产物，未触碰我负责的只读范围） |
| 结束时 HEAD 复查 | 见文末「六、结束复查」 |

---

## 一、现有测试资产清单（脚本 → 覆盖 → 断言 → 与基线对齐情况）

### 1.1 一键回归骨架

| 资产 | 覆盖什么 | 断言方式 | 与当前基线对齐情况 |
|---|---|---|---|
| `run_all.sh` | 后端 8791 就绪检查 → 引擎 9223 就绪（不通则 POST `/api/mirror/open` 拉起，15 次×2s）→ **先跑 layout_verify** → 串行跑 t01–t11（退出码 2=SKIP 不计败） | 退出码统计 PASS/FAIL/SKIP | 端点 `/api/mirror/open` 仍在（server.py:919，现走 `mirror.open_login`）。**隐患**：就绪触发打开的是小红书登录页，与「未登录=Tab2 login-hint」新基线的页面集相互作用无人断言。E1/b/e2/smoke 均不进一键跑（README 明示，合理） |
| `ui_tests/README.md` | 方法论血泪教训 5 条（禁 evaluate click、用 is_visible、onMounted 竞态等）+ 脚本表 | — | **脚本表停在 e1-s6**：未收录 b01–b08、e2_wall_popup、layout_verify、smoke_login_ui（而后三者已是 run_all.sh 实际执行/人工闸口资产）。t 系列基线写「10 PASS / 1 SKIP(t08)」，与 dev-test-batches.md 的「9/0/2」不一致 |

### 1.2 t 系列（t01–t11，进 run_all）

| 脚本 | 覆盖 | 断言质量 | 对齐情况 |
|---|---|---|---|
| t01/t02/t03 | AI 按钮灰态、4-Tab 导航、冒烟 | 有 assert；t02 docstring 残留「CDP 9333」字样 | 功能面回归，不碰窗口布局；连 9223 正确。均用 `new_page()` 新开测试页，**不在壳窗内操作**，因此对「壳窗/Tab2 页面集」零感知 |
| t04 | 发布前质检红线（取消不真发、LOGS_DELTA=0） | 真断言 + DB 计数 | 与窗口基线无关 |
| t05 | 筛选默认态 | 有 res 收集 | `set_viewport_size(1360×920)` 新页，不验壳窗 |
| t06–t11 | 评分/去重/发布记录/客户聚合/列表/指引 | 开头 sqlite3 查行数，空则 `SystemExit(2)` SKIP（SKIP 语义规范） | 与窗口基线无关 |

### 1.3 E1 真实链路（不进 run_all，人工低频跑）

| 脚本 | 覆盖 | 断言质量 | 对齐情况 |
|---|---|---|---|
| e1_s1_filters | 真实筛选→collect_start 请求体→低频采集 | route 拦 body 真断言 | docstring 前置写「后端 **8800**」，与 README/其余脚本 8791 矛盾（笔误） |
| e1_s2_clone | badge-note 选卡→克隆→创作 tab | 真断言 | 对齐 |
| e1_s3_qc_images | 质检四项/lightbox/增删图/草稿 | 真断言 | 对齐 |
| e1_s4_clients | 真实采集客户落库 | 真断言 | 对齐（风控敏感，人工低频） |
| e1_s5_settings | AI Key 门控/掩码/清除复原 | 真断言 | docstring 残留「连 9333 headless」 |
| **e1_s6_login** | 登录态闭环（旧设计） | 点「立即读取 Chrome 登录态」按钮 | **已过时**：该按钮在全仓库源码中已不存在（`grep -rn 立即读取 backend/` 0 命中），cookie_reader 读系统 Chrome 链路已下线；`restart_backend()` 仍用 `app/.venv` + `--app-dir app/src`（仓库现结构为 `backend/src`）、pgrep 旧 profile 路径 `~/.xhs-leads-profile`（已归一到数据目录内 `profile`）。dev-test-batches.md 已自认「s6 打包态不适用、不重复执行」 |

### 1.4 B/E2 补测（不进 run_all）

| 脚本 | 覆盖 | 断言质量 | 对齐情况 |
|---|---|---|---|
| b01_batch_cards | 全选/清空/单选/图片包/JSON/CSV | 真断言 | docstring 残留「连 9333 headless」；**无 SKIP 语义**——无 notes 数据时 `wait_for_selector('.card')` 15s 超时直接 FAIL，与 README 宣称的「数据依赖自动 SKIP」不一致 |
| b02_stop_collect | 采集中再点停止→回落空闲 | 真断言 | docstring 残留 9333 |
| b03_draft_img | 正文计数/标签/拖拽/删草稿 | 真断言 | docstring 残留 9333 |
| b04_cookie_import | 三分支（空/无效/有效）+ UI toast | 后端 400/200 真断言 | docstring 残留 9333 |
| b05_misc_cards | 结论标签/打开原作品/复制链接 | 真断言 | docstring 残留 9333 |
| b06_clients_export | 导出线索 CSV | 真断言 | docstring 残留 9333 |
| b07_license_ui | free 状态条/激活流程 | API 断言 | 硬编码 `~/Desktop/codex-zhuke`；需 8791+8792 双实例 + `/tmp/b07_code.txt` 激活码，前置沉重 |
| **b08_login_overlay**（R-6.9） | **全仓最完整的登录链路自动化**：备份/清会话→重启→断言 `login_status=not_logged_in`→人工扫码闸口（8min）→轮询断言 `logged_in`+注入完成→末帧截图 | login_status API 权威断言 + 2s/帧截图留证 | ①DATA 写死 `~/Library/Application Support/xhs-leads/data`（打包态），与 smoke_login_ui 注释「硬编码该路径已随仓库迁移失效、统一走仓库 data」**口径互相矛盾**；②docstring 架构假设「壳窗=launcher WebView 独立实例、CDP 9223 连不到所以只能截图」——**已被 R-6.22d「壳/采集统一 --app 路由」推翻**（壳窗现在就在 9223 CDP 里，layout_verify 已实证可 `getWindowBounds`）；③**不断言 2 页稳定/无 explore 判定页/无窗口震荡**，仅拍壳窗区域 |
| e2_wall_popup（R-6.21e/f/g） | 真实采集触发登录墙→后端 note 命中→前端 `.force-mask` 弹框 | 双真断言 + 截图 | 选择器有效（frontend/src/components/ForceLogin.vue 确有 force-mask/force-box；今日 dist 已重构建）。无窗口断言 |

### 1.5 布局/启动时序专项（本任务核心对照物）

| 脚本 | 覆盖 | 断言 | 与「未登录 2 页基线」对齐情况 |
|---|---|---|---|
| **layout_verify.py**（R-6.22c/d，挂 run_all.sh 第一步） | **已登录**双窗分屏：左壳窗(8791)+右采集窗(xiaohongshu 非 /login)、应用模式(window.open 落新窗)、left=0/等宽/等高/不重叠、**Tab1 四连关后 keeper 重建保持** | 9 条语义断言 + bounds 断言 width==960 写死 | **三处与现状矛盾**：①section2「四连关重建」期望关壳窗后 keeper 重建——R-6.22p(3af6a15，晚于本脚本最后修改 a13ae36) 已拍板「关主窗=整体退出、不再重建」，现在关壳窗→`on_shell_closed`→应用退出，脚本等不到重建必 FAIL，且可能把 dev 实例带走；②右窗识别 `_is_collect` 要求 xiaohongshu.com——**未登录基线 Tab2=/login-hint，会被判「右窗缺失」FAIL**，本脚本实际只在已登录态可跑；③width==960 硬编码，而 `engine_pool.shell_window_geometry()` 是 `min(1280, 主屏宽//2)`——换分辨率误报。高度 800 从不断言 |
| smoke_login_ui.py（R-6.10） | 场景 A 空数据目录启动→ForceLogin 弹窗截图；场景 B 游客 cookie→cookie_info 只读字段 | `logged_in is False` + `count==14` 硬编码 + 截图 | **零窗口/零页面集断言**：不看开了几页、Tab2 是什么、有无震荡；`count==14` 脆（cookie 条数一变就 WARN）；`cliclick` 函数定义后无人调用（死代码）；只截图不断言弹窗可见性/文案 |

### 1.6 对照文档

| 文档 | 与实现一致性 |
|---|---|
| `docs/dev-test-batches.md` | 头部「状态时间 2026-10-31：R 批进行中」与第七节「R 批 ✅ 完成」自相矛盾；t 系列基线「PASS=9/0/2」与 README「10/1/1」对不上（实际 t06–t11 共 6 个 SKIP 门控脚本）；**R-6.8~R-6.29 窗口治理全系列（login overlay/墙弹/布局/关窗语义/启动时序/explore 判定页移除）在本批契约里完全没有测试条目**；提「pytest 基线 100/8」——backend/tests/ 确有 16 个测试文件，但不在本次盘点执行范围内 |
| `docs/login-fingerprint-diagnosis.md`（2026-10-26） | **整篇属容器 xhs-mcp 时代**（Docker 无头 + windows 伪指纹 + `--enable-automation` 被限流）。R 批已改为 Playwright 引擎独立 profile + stealth，文档的「已排除路径 4 条 / 根治路径 A·B·C」全部针对旧架构；仅「登录链路抓包成果」（qrcode create/userinfo/X-s 签名）一节知识仍可回溯。`/api/qrcode` 端点虽仍在（server.py:881），但它服务的「容器扫码弹窗」形态已不是当前登录主路径（当前主路径=壳窗官方登录页+守卫捕 id_token） |
| `docs/project/采集与登录闭环操作流程SOP.md`（R-6.22） | 质量最高的对齐产物：T1–T4 登录触发链、四元组判定、RE_AUTH 词表均与代码一致；其第六节测试计划 TS1–TS7 中 **TS6（运行中停止）标注「待回归」、TS7（运行中被踢）标注「待验证」**——正是测试资产缺口的官方自承 |

---

## 二、缺口清单（③ SOP/测试脚本缺口核心 + ④ 建议新增/更新）

| # | 缺口 | 现状 | 建议用例/断言 | 优先级 |
|---|---|---|---|---|
| G1 | **未登录启动时序验收完全缺位** | 现有脚本无一条断言「启动后页面集={壳窗8791, Tab2 /login-hint}、无 explore 判定页、无 about:blank、无第三个窗口」；layout_verify 只认 xiaohongshu 右窗，smoke_login_ui 只看 login_status+截图 | 新增 `startup_unlogged_verify.py`：干净数据目录启动后，每 0.5s 拉 `127.0.0.1:9223/json/list` 共 15s，断言 page 数恒=2、URL 集合恰为 `{*/8791/, */login-hint}`、无任何 xiaohongshu.com 页；bounds 断言 960×800、left=0/960、不重叠 | **P0** |
| G2 | **layout_verify 与 R-6.22p 关窗语义矛盾（会误杀实例）** | section2 「Tab1 四连关重建」期望 keeper 复活壳窗；现状（shell_window.py:955-960）关壳窗=触发退出不再重建。按现状跑：round0 起即 FAIL，且 launcher 模式下被测的 dev/app 进程会被一起关掉，run_all.sh 第一步就把后端带走 | 删改 section2：改为(a) 关 Tab2→等 3 轮轮询，断言 Tab2 **不复活、应用不退**（login_status 仍通）；(b) 另立独立 smoke（不进 run_all）断言关壳窗→进程退出 | **P0** |
| G3 | **窗口震荡/闪现无任何回归断言** | R-6.26~6.29 连续修「启动即弹框不闪现/空白残留清理/cleanup 不误关小红书 tab/explore 复用不堆叠」，全靠人眼实测；现有脚本没有一条做「页面集随时间稳定」的监测 | 在 G1 基础上加时变断言：启动后 15s 内页面集 URL 集合 **逐帧相等**（不允许新页弹出/旧页消失再建）；另测「登录成功瞬间」Tab2 从 login-hint→explore 的单次导航（shell_window.py:965-975 联动逻辑零测试） | **P0** |
| G4 | **登录闭环断链：未登录→登录→采集→退出 无一条端到端自动化** | b08 链路最全但假设壳窗不可 CDP（过时）、DATA 路径与 smoke 矛盾；e1_s6 已过时；缺「登录成功→Tab2 联动 explore→ForceLogin 自动关→重采」断言 | 重写登录闭环脚本（合并 b08 的备份/清会话 + CDP 直连壳窗断言）：未登录→壳窗 ForceLogin 可见性/文案断言→人工扫码闸口→断言 Tab2 导航 explore、ForceLogin 关闭、页面集仍=2、`login_status` 四元组 logged_in | **P1** |
| G5 | **run_all.sh 就绪触发与新基线的相互作用未验证** | 引擎未就绪时 POST `/api/mirror/open`（现=打开小红书登录页）；该页面与「未登录=login-hint Tab2」基线并存时页面集是否仍为 2、有无第三个残留登录页，无人断言 | run_all.sh 就绪后加一条轻断言：页面集不含多余小红书登录页（若有，说明 mirror/open 路径与 R-6.28 清理逻辑有缝隙） | P1 |
| G6 | **smoke_login_ui 脆弱断言** | `cookie_info count==14` 硬编码；弹窗只截图不断言 | 改为断言字段完整性（name/value/domain/expires 齐）+ `.force-mask` 可见性真断言（CDP 直连，壳窗已可 9223 见）；删死代码 cliclick | P1 |
| G7 | 高度 800 从未断言 | 代码 `shell_window_geometry()` 返回 800，但 layout_verify 只断 width/等高 | bounds 断言补 `height==800`；width 改动态读 `min(1280, 主屏//2)` 不写死 960 | P1 |
| G8 | TS6/TS7（运行中停止、运行中被踢）SOP 自承未回归 | b02 测了停止但依赖人工节奏；T3 被踢链路无自动化 | b02 补自动 SKIP/真断言加固；TS7 列入下次真机人工闸口清单（风控敏感，保持人工） | P1 |
| G9 | e1_s6_login.py 整体过时 | 断言的按钮/路径/profile 全部退役 | 删除或按新链路重写（G4 覆盖其职责）；dev-test-batches.md 补注退役原因 | P1 |
| G10 | b01 等数据依赖脚本无 SKIP 语义 | 无 notes 数据时 FAIL 而非 SKIP，与 README「数据依赖自动 SKIP」承诺不符 | 仿 t06 开头加 sqlite 行数检查→`SystemExit(2)` | P2 |
| G11 | 脚本 docstring 大面积残留旧端口/旧架构 | b01–b07、e1_s5、t02 写「连 9333 headless」；e1_s1 前置写 8800；b08 写「壳窗=launcher WebView」 | 批量订正为 9223/8791/当前壳窗架构（随 G4/G9 一起改） | P2 |
| G12 | ui_tests/README.md 脚本清单不全 | 缺 b 系列/e2/layout_verify/smoke_login_ui 收录与依赖说明 | 补表；注明各脚本是否进 run_all、数据依赖、人工闸口 | P2 |
| G13 | roles/ 六角色方法论与实际工作方式脱节 | roles/ 要求 Gherkin `.feature`→可执行验收测试、变异测试杀光、CRAP<30/圈复杂度≤8 门槛；仓库实际：**0 个 .feature 文件**、无变异测试接线、quality-gates/gates.yaml 有声明但未见强制执行；research/legacy/ 是 clean-code-gauntlet 初始脚手架残留 | 不阻塞迭代；建议在 roles/ 或 README 注明「本仓实际工作流=SOP 文档+Playwright 脚本+pytest，swarm-forge 六角色为框架模板未启用」，避免后续会话误按 Gherkin 流程开工 | P2 |
| G14 | research/评分模型-低粉爆款.md | 产品调研（评分公式权重），非测试方法论；与 t06_score.py 覆盖范围无冲突，无过时点 | 无需动作，仅登记 | P2 |

**缺口合计：14 条（P0×3，P1×6，P2×5）；过时描述集中登记见下节。**

---

## 三、被 R-6.x 取代的过时描述（②）

| 位置 | 过时内容 | 取代者 |
|---|---|---|
| e1_s6_login.py docstring+逻辑 | 「连 9333 headless」「立即读取 Chrome 登录态按钮」「读日常 Chrome 钥匙串」、`app/.venv`+`app/src` 启动路径、`~/.xhs-leads-profile` profile | R 批登录态存引擎独立 profile；R-6.22~6.29 壳窗官方登录页+守卫捕 id_token 闭环 |
| b01/b02/b03/b04/b05/b06/b07、e1_s5、t02 docstring | 「连 9333 headless」前置 | 9333 已退役，统一 9223（代码早已改对，注释未改） |
| b08_login_overlay.py docstring | 「壳窗口=launcher WebView 独立浏览器实例，CDP 9223 连不到，故截图留证」 | R-6.22d 壳/采集统一 --app CFT 路由，壳窗已在 9223 CDP 内（layout_verify 已可 bounds 断言） |
| docs/dev-test-batches.md | 头部「R 批进行中」；t 基线 9/0/2 与 README 10/1/1 两说；R 批测试点止于 e1 s1–s6 | 第七节 R 批已完成；实际 SKIP 门控脚本 6 个；R-6.8~6.29 窗口治理无测试条目 |
| docs/login-fingerprint-diagnosis.md | 容器 xhs-mcp「无头+windows 伪指纹+enable-automation 被限流」结论与根治路径 A/B/C | R 批引擎独立 profile+stealth 新架构；仅抓包知识可回溯 |
| scripts/ui_tests/README.md | 脚本表缺 b 系列/e2/layout_verify/smoke_login_ui | run_all.sh 实际执行面已变 |
| layout_verify.py docstring | 「窗口被关后 keeper 重建，布局必须保持（四连关 Tab1 回归）」 | R-6.22p 关窗语义重定义（关主窗=退出，关采集窗=仅关闭） |

---

## 四、结论

测试/方法论资产**需要整理，且优先级偏高**。功能面回归（t/e1/b 系列）覆盖扎实、断言真实、SKIP 语义规范，是可用资产；但 R-6.22~6.29 连续七轮窗口治理（未登录 login-hint 基线、关窗语义重定义、启动时序、explore 判定页移除）产生的新行为几乎没有对应回归网：唯一的布局脚本 layout_verify 反而与最新语义（R-6.22p）矛盾、按现状跑会误杀 dev 实例（P0）；未登录启动时序、窗口震荡、登录成功后 Tab2 联动三项零断言（P0）；e1_s6、b08 架构假设、登录诊断文档均已被 R-6.x 取代但未标注退役。建议先补 G1–G3 三条 P0（约一个新脚本 + layout_verify 修一节），再排 G4–G9 P1，即可为后续小迭代兜底；roles/research 的方法论脱节属 P2 登记项，不阻塞。

---

## 五、本次盘点未触碰的边界说明

- 未执行任何脚本/服务；未读 backend/tests/ 的 pytest 内容（超出本次范围）；未验证 dist 打包产物与 dev 实例的差异。
- `run_all.sh` 依赖 `/api/mirror/open` 拉起引擎——该端点现存且功能正常，但其就绪后页面集状态未实测（禁止启动），结论为静态推断。

## 六、结束复查

| 项 | 值 |
|---|---|
| 盘点结束时 `git rev-parse HEAD` | **f9503204f5b0acdef9461b1383fd36827817df39**——与开始时一致，HEAD 未前进 |
| 期间观察 | 盘点过程中工作区出现并行 R-6.30 重构改动（`M backend/src/{browser_client,engine_pool,server,shell_window}.py` + 未跟踪 `backend/src/injections.py`），与任务预告一致；我未触碰这些文件，结论基于 f950320 快照 |
