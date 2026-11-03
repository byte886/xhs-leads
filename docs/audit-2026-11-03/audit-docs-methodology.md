# 查缺补漏清单 —— 文档与方法论部分（线 B 只读盘点）

> 任务：docs/ 与 constitution/、quality-gates/ 对照 backend/src 现状，找文档结论/命令/时序/端口/窗口模型/登录流程的不一致、过时方案与更新建议。
> 全程只读：仅 Read / grep / git log；未启动服务、未跑测试、未改仓库原文。

## 比对基准

| 项 | 值 |
|---|---|
| 仓库 | `/Users/wenjiechen/Desktop/codex-zhuke` |
| 比对基准 commit（盘点开始时记录） | `f9503204f5b0acdef9461b1383fd36827817df39`（R-6.29 未登录闭环窗口治理：移除判定页开窗副作用） |
| 盘点结束前复查 HEAD | 仍为 `f950320`，**未前进**（并行 R-6.30 系列行为保持型重构在本盘点窗口内未落到 HEAD，结论有效） |
| 基线事实（代码现状） | 前端 8791（`config.front_port`，launcher 写 `XHS_SHELL_URL` 为权威）；引擎 CDP `9223`（`config.engine_port`）；壳=CFT **有头**应用模式窗口（`shell_window.ensure_shell` → `--app` 命令行路由），左半屏；Tab2 采集窗右半屏（`collect_tab_visible=true`，运行时 `data/config.json` 实测）；窗口几何 `win_w=min(1280,主屏宽//2)=960, h=800`（`engine_pool.shell_window_geometry`）；未登录启动后 Tab2 停在本地 `/login-hint`，已登录停在 `xiaohongshu.com/explore`；登录交互=壳窗内导航真实 `xiaohongshu.com/login?redirectPath=…`，`id_token` 捕获后 `import_cookies` 注入引擎；关主窗=退出应用（R-6.22p）。 |

问题类型标记：①=与代码实现直接不一致；②=过时/已被推翻方案；④=建议更新项。

---

## 一、docs/ 根目录设计文档

| 文档位置 | 类型 | 文档说法 | 代码现状（文件:符号/行） | 建议 |
|---|---|---|---|---|
| `docs/需求变更-App内呈现形态.md` R1（L16）、§三.1（L38）、§五验收（L81） | ②（被 R-6.16 路线A推翻） | "引擎无头化 headless=True / channel=chrome / 外部零 Chrome 窗口" | `backend/src/config.py:75` `headless:False` 默认；`engine_pool.py:198-199` `headless=cfg headless`；`shell_window.py:2-9` 新壳=CFT 有头窗口承载前端，CDP 9223 直接驱动；启动即出可见窗口 | 顶部加"已被 R-6.16 路线A（有头 CFT 壳）推翻"横幅；无头降级仅留作 `config.headless` 开关说明 |
| `docs/需求变更-App内呈现形态.md` §三.7（L44）、R-6.5/R-6.6 附注（L65-76） | ②（被 R-6.16 推翻） | "壳内 UI 载体 = pywebview 内嵌 WKWebView；launcher.py `_login_guard` 守卫线程；pywebview `get_cookies()`；`webview.create_window(1280x800)`" | `scripts/packaging/launcher.py` 全文无 `webview` import；壳窗口由 `shell_window.ensure_shell` 用 CFT `--app` 路由弹出；登录守卫=`shell_window.login_guard` asyncio 任务（`launcher.py:246` `asyncio.create_task(shell_window.login_guard(...))`）；Cookie 经 Playwright `context.cookies()`（`shell_window.py:225`） | 附注标注"守卫已迁至 shell_window.py、载体由 pywebview 改为 CFT 应用窗"；删 pywebview/线程描述 |
| `docs/需求变更-App内呈现形态.md` R-6.6 附注（L60、L73） | ①/② | "启动即 login_begin 主动置 pending；启动即强制导航 login 整页登录页" | `shell_window.py:425-431`「启动不自动导航：未登录由 APP 前端强制登录框承接，点『去登录』才导航」；R-6.21e（L554-556）移除运行时 kicked 轮询；R-6.29 移除 explore 判定页开窗 | 改写为"启动停 APP 前端 + 未登录 Tab2=login-hint；ForceLogin 点去登录才导航" |
| `docs/需求变更-App内呈现形态.md` R3（L18）、§9.1 登录小窗镜像 | ②（R-6.5 用户已否决镜像） | "登录小窗=完整登录页镜像（CDP 截图流+Input 事件转发）" | 同文件 R-6.5 附注 L65 自述"用户明确否决截图镜像"；现行=壳窗内真实 xhs 登录页。`mirror.py`/`/api/mirror/*` 仅留作设置页调试视图（§9.2），不再用于登录 | 在 R3/§9.1 标"登录镜像已否决，仅调试视图保留" |
| `docs/界面重构设计-对齐版.md` §一/§二（L15、L56） | ①（与前端实际不符） | "单页一体式；**无独立设置页**——搜索参数/Cookie/账号都收进左侧面板" | `frontend/src/App.vue:176-197` 实为 **4-Tab**（collect/create/clients/settings，v-show）；`SettingsPanel.vue` 独立存在，含「重新登录」force=false、采集 Tab 开关、调试视图 | §二补注"实际落地为 4-Tab 骨架（见 ui-feature-checklist FR-01），单页流程条保留为采集 Tab 内" |
| `docs/界面重构设计-对齐版.md` §六（L154） | ②（容器已退役） | "采集 = xiaohongshu-mcp 容器（不动）" | 架构 B：`engine_pool.py` Playwright `launch_persistent_context` 直驱 CFT；`docker/` 仅历史资产（project-structure L40「当前交付无 Docker」） | 标"mcp 容器已被 engine_pool 取代" |
| `docs/界面重构设计-对齐版.md` §九（L182） | ②（同 R1） | "引擎为无头 Chrome，外部零浏览器窗口" | 同上，有头 CFT 壳 | 同步加 R-6.16 推翻注 |
| `docs/架构改造-B方案引擎池与多账户机制设计.md` §二图（L59-60）、§3.1（L84） | ①（路径名过期） | 账号 `user_data_dir=~/.codex-xhs/accounts/A` | `backend/src/config.py:39` `ACCOUNTS_ROOT="~/.xhs-leads/accounts"`；default 账号目录=`DATA_DIR/profile`（config.py:255） | 全局 `~/.codex-xhs/accounts` → `~/.xhs-leads/accounts`；补 default=数据目录内 profile |
| `docs/架构改造-B方案…md` §1.3（L34）、§五 P3（L201） | ② | "生产形态=pywebview 壳 + 后台引擎" | 壳=CFT 应用窗（见上），无 pywebview | 标 P3 壳方案已被 R-6.16 路线A取代 |
| `docs/架构改造-B方案…md` §3.2（L129） | ①（端口口径） | "引擎端口探测 8800-8899" | `config.py:71` `engine_port` 默认 **9223**；`engine_pool.py:204` 实参 `--remote-debugging-port=9223`；8800-8899 是 **front_port** 冲突探测区间（config.py:115） | 区分引擎端口(9223 固定)与前端端口(8791→8800-8899 探测) |
| `docs/架构改造-B方案…md` §八现象2（L248）、验收（L264） | ②（已 R-6.15 移除） | "每账号节流 90s / 同关键词 60s dedup→409 / 连续 mark_soft 指数退避" | `config.py:81` `collect_min_interval:90.0` 标注**废弃（R-6.15 移除任务间节流）**；SOP L46/L136 载 R-6.22h 移除 dedup（同词重采=200） | 现象2 表格加"节流/dedup 已按 R-6.15/R-6.22h 拍板移除" |
| `docs/架构改造-B方案…md` §二图（L47） | ④（数字过期） | "server.py 39 路由" | 实测 server.py 路由 ~70+（本次 grep 到 60+ 个 `@app.*`） | 改"路由数量随功能演进，以代码为准" |
| `docs/方案A-Cookie续期-钥匙串授权调研与落地.md` 全文 | ②（已被 B 方案取代） | 直读系统 Chrome Cookie SQLite + 钥匙串「始终允许」解密续期 | 架构 B 已注明（本文件 L5）"B 方案取代其主链路"；现行登录态=壳窗真实登录页 → Cookie 注入引擎独立 profile（`shell_window.import_cookies`），不再碰系统 Chrome Cookie 库 | 顶部已有历史定位，建议加一行"主链路已废，仅留调研/死路清单参考"；`cookie_reader.py` 文件仍在但链路停用 |
| `docs/Codex自动获客-复刻方案PRD.md` v1.4 D8（版本记录 L12、决策表 D8） | ②（同 R1/R3） | "引擎无头化 + 登录小窗镜像 + 外部零 Chrome 窗口" | 已被 R-6.16/R-6.5 实际形态取代（有头 CFT 壳窗真实登录页） | 加 v1.5 记录：壳形态定为有头 CFT 应用窗，登录=壳窗真实页，镜像仅调试视图 |
| `docs/ui-design-standards.md` §6（L57） | ①（标题文案与机制不符） | "调试窗标题固定：引擎注入 extract.js，统一标题为「小红书」" | `shell_window.py:128-133` `_COLLECT_DEBUG_TITLE_JS` 把标题设为 **"XHS Leads · 采集调试窗口"**，机制为 page 级 `add_init_script`（`shell_window.py:120`），非 extract.js | 文案改为"标题统一为「XHS Leads · 采集调试窗口」，由 shell_window 页级 init script 守护" |

---

## 二、docs/project/ 与 docs/gates/

| 文档位置 | 类型 | 文档说法 | 代码现状 | 建议 |
|---|---|---|---|---|
| `docs/project/新机器部署手册.md` §0 全景图（L17-37）、§5.1（L107-115）、§5.2（L119-129） | ①/②（整体停留在 B 方案之前） | "外部 Google Chrome CDP 9223 + 独立 profile `~/.codex-xhs-profile` + 手动点『允许远程调试』+ `setup_chrome.py/pick_chrome.py` 引导" | 现行引擎=`engine_pool` `launch_persistent_context` 自动拉起 CFT/系统 Chrome，无「允许远程调试」弹窗、无钥匙串；profile=`data/profile`（开发态）；`scripts/legacy/` 才归档 setup/pick 脚本（project-structure L34） | **P0 重写**：以 project-structure.md §4 命令为准（`PYTHONPATH=backend uvicorn src.server:app` / `scripts/packaging/build_mac.sh`），删除外部 Chrome CDP 手动段 |
| `docs/project/新机器部署手册.md` §3（L79-83）、§4（L95-98）、§2.5（L66-68） | ①（路径/包名过期） | `cd app/...`；`uvicorn codex_xhs.server:app --app-dir app/src`；前端 build→`app/web`；profile `~/.codex-xhs-profile` | 包名已归一为 `src`、目录为 `backend/`（project-structure L11-29、L51）；前端产物 `frontend/dist`；default profile=`backend/../data/profile` | 全量 `app/`→`backend/`、`codex_xhs`→`src`、`app/web`→`frontend/dist` |
| `docs/project/新机器部署手册.md` §6（L143、L169）、§10（L220） | ④（数字内部矛盾且过期） | "295 个 EXIT=0" vs §10"173 个" | 以 `backend/.venv pytest` 实跑为准（本次未跑测试）；六维口径见 gates.yaml | 统一为一条"测试基线以 run_all/ pytest 实跑为准"，删除自相矛盾数字 |
| `docs/project/新机器部署手册.md` §8-5（L198） | ① | "采集随机间隔 2-5s" | `config.py:82` `request_interval:[1.0,1.8]`（拟人区间） | 改为 1.0-1.8s |
| `docs/project-structure.md` §4（L93-94） | ①（e1 连接对象过期） | "e1 真实链路（需 9223 **外部可见 Chrome** + 小红书已登录）" | 需求变更 R-5（L57）：e1/t 系列改连**引擎实例**的 9223 调试端口，旧"外部可见 Chrome"路径退役；`engine_pool.py:204` 引擎自身带 9223 | 改为"e1 连本机引擎实例 9223（同登录态 profile），无需另开外部 Chrome" |
| `docs/project-structure.md` §2 命名表（L57） | ①（profile 目录过期） | "Chrome 调试 Profile `.codex-xhs-profile` → `.xhs-leads-profile`" | `config.py:37,255` 默认 profile=数据目录内 `profile`（开发态 `data/profile`）；`~/.xhs-leads-profile` 仅历史兼容残留 | 命名表补"现行 default profile=`<数据目录>/profile`" |
| `docs/project-structure.md` §1 目录图（L14、L26） | ④ | "browser_client.py # 读取 Cookie 库"；"10 个页面组件" | browser_client 现为 CFT/CDP 驱动层；`frontend/src/components/` 实测 11 个 .vue（含 ForceLogin/StatusBar/WorkArea） | 改 browser_client 注释为"CFT/引擎驱动封装"；组件数改 11 |
| `docs/project/采集与登录闭环操作流程SOP.md` 版本头（L3） | ④（滞后 R-6.26~6.29） | "版本 R-6.22 系列（2026-11-02）" | HEAD=R-6.29；后续 R-6.26c 登录页居中+滚动、R-6.27 viewport 跟随窗口宽、R-6.28 启动时序（Tab2=login-hint/explore 联动）、R-6.29 移除判定页开窗均未反映 | 版本号升到 R-6.29；§三补"未登录启动 Tab2=login-hint 提示页，登录成功自动回 explore" |
| `docs/gates/G1-W1-阶段启动.md`、`docs/project/gates/G1-W1…md` | ②（历史阶段记录） | "Docker 部署 xiaohongshu-mcp :18060、扫码登录" | Docker 已移除、引擎为 Playwright persistent context | 文档本身标注"已完成(2026-10-26)"，属历史里程碑，建议顶部加"当时技术形态，已被后续 B/R 批取代"即可，不必改内容 |
| `docs/project/待办事项.md`、`需求实现闭环清单.md`、`调研选项取舍决策记录.md` 等 | ④ | 未逐行比对 | 多为阶段待办/决策留痕 | 建议下一轮按 R-6.30 后状态统一回勾，本次不展开 |

---

## 三、docs/ UI 测试与清单类

| 文档位置 | 类型 | 文档说法 | 代码现状 | 建议 |
|---|---|---|---|---|
| `docs/ui-test-checklist.md` B9（L37） | ① | "60s 内重复发起采集 → dedup_key 拦截提示（t07 ✅）" | SOP L46/L136-139：R-6.22h 已移除 dedup，同词重采=200 | 标 B9 行为已反转，用例预期改为"同词连点均 200"（对齐 SOP TS5） |
| `docs/ui-test-checklist.md` A4/A5/A6（L19-21） | ②（旧登录交互） | "未登录点『立即读取』→ 登录弹窗；点『打开小红书登录页』→ **新窗口**打开" | 现行=`ForceLogin.vue` APP 内强制登录框（App.vue:205），点去登录后**壳窗本身**导航登录页，无"立即读取"按钮、无新开窗口 | 改写为 ForceLogin 文案/按钮口径；删"立即读取/新窗口" |
| `docs/ui-feature-checklist.md` FR-23（L36） | ① | "dedup 防重（60s 任务防重 + 库内去重）" | 同上，60s 任务防重已移除（仅库内笔记去重保留） | FR-23 改为"库内去重；任务层 60s 防重已按 R-6.22h 移除" |
| `docs/ui-feature-checklist.md` FR-04（L14）、FR-13/FR-51（L26、L67） | ④ | "登录弹窗：新窗口小红书"；"CDP 连接配置：重启调试连接" | 登录为壳窗内导航；CDP 配置路由（`/api/cdp_*`）仍在但形态已变引擎池 | FR-04 对齐 ForceLogin；CDP 区标注"legacy 配置，现行自动引擎" |
| `docs/UI测试报告-选择内容页面-2026-10-26.md` L23、L26、L45-46 | ②（时点快照） | "采集客户=P4 占位 toast；下载图片包=P3 占位" | 采集客户已实装（App.vue:67-93 真实抓评论区互动用户入库）；下载图片包路由 `/api/download_images` 已存在（server.py:1616） | 属 10-26 历史测试报告，建议顶部加"本报告为时点快照，采集客户/下载后续已落地" |

---

## 四、constitution/ 与 quality-gates/

| 文档位置 | 类型 | 说法 | 代码现状 | 建议 |
|---|---|---|---|---|
| `constitution/engineering.prompt`、`project.prompt`、`workflow.prompt` | — | 方法论（CRAP≤8、变异=0、覆盖核心≥80%/其余≥60%、六角色流水线、人工闸口） | 与 `quality-gates/gates.yaml` 阈值一致；不描述运行时行为，无代码冲突 | 无需改；保持 |
| `quality-gates/gates.yaml`、`tools/python.yaml` | — | 维度/阈值/触发时机 | 与 engineering.prompt 自洽；`tools/python.yaml` 为工具命令映射 | 无需改 |

---

## 五、优先级汇总

| 优先级 | 数量 | 条目 |
|---|---|---|
| **P0**（与现状直接冲突、会误导操作/部署） | 5 | ①新机器部署手册整体停留在外部 Chrome CDP 旧架构（§0/§3/§5）；②需求变更"无头化+外部零窗口"已被 R-6.16 有头 CFT 推翻；③需求变更 pywebview 壳/launcher 守卫线程描述与现行 CFT 壳+shell_window.login_guard 不符；④需求变更"启动即强制导航登录页"与现行"启动停 APP、点去登录才导航"不符；⑤调试窗标题文档写「小红书」/extract.js，代码实为「XHS Leads · 采集调试窗口」页级注入 |
| **P1**（过时应清理/标注） | ~11 | 架构B方案的 `~/.codex-xhs` 路径、pywebview 壳、引擎端口口径、90s节流/60s dedup；project-structure 的 e1 外部 Chrome、profile 目录、组件数；ui-test/feature checklist 的 dedup 拦截、旧 LoginModal 交互；界面重构"单页无设置页/mcp 容器"；PRD v1.4 D8；方案A、G1 Docker 历史标注 |
| **P2**（可优化） | ~4 | SOP 版本升到 R-6.29 并补 login-hint/启动时序；新机器手册测试数字统一；project-structure browser_client 注释；待办/闭环清单下轮回勾 |

## 六、结论

文档体系**需要一次集中查缺补漏，优先级整体偏高**。根因是项目在 10-31~11-02 经历了两次未回写文档的形态跃迁：①B 方案（engine_pool 取代外部 Chrome CDP/钥匙串）落地后，`新机器部署手册`、`project-structure`、`ui-*checklist` 里仍残留"外部 Chrome 9223 + `~/.codex-xhs-profile` + 手动允许调试"的旧口径；②R-6.16 把"无头引擎 + pywebview 壳 + 登录镜像小窗"改为"有头 CFT 应用窗壳 + 壳窗内真实登录页"后，`需求变更-App内呈现形态.md`、`界面重构设计-对齐版.md` §九、PRD v1.4 D8 仍在传播"无头/外部零窗口/镜像登录"。其中**新机器部署手册是最危险的一份**——它是操作性文档，照它部署会复现已被淘汰的手动 CDP 流程，建议列为 P0 优先重写。相对而言，`采集与登录闭环 SOP`（R-6.22）、`授权 ADR`、`constitution/`、`quality-gates/` 与代码吻合度高，只需小步跟进 R-6.26~6.29 的窗口/时序变更即可。
