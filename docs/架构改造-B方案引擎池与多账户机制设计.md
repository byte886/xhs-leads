# 架构改造：B 方案引擎池 + 多账户机制（设计文档）

> 状态：设计定稿，实施中
> 日期：2026-10-29
> 关联：`docs/方案A-Cookie续期-钥匙串授权调研与落地.md`（历史决策，B 方案取代其主链路）

---

## 一、背景与目标

### 1.1 现状痛点（用户连续多轮确认）

| 痛点 | 根因 |
|---|---|
| ① 用户每次要带 CDP 参数才能跑 | `browser_client.py` 用 `Popen --remote-debugging-port` + `connect_over_cdp`，Chrome 144+ 每次外部 TCP 连接必弹「要允许远程调试吗？」，依赖 press_allow 代点，且用户机器要配辅助功能权限 |
| ② Mac 需配钥匙串开关才能解 Chrome Cookie | 方案 A 直读 Chrome Cookie 库需钥匙串「始终允许」授权（macOS 禁合成点击，只能人工一次） |
| ③ 单账户单引擎，无法采集号/发布号分离 | `_browser` 全局单例 + 单份 `cookies.json` |
| ④ 无日志体系 | 全部 `print`，多账号后无法排障 |
| ⑤ 端口硬编码 | 8791（前端保留页判断）/ 9223（pkill 清理）写死 |

### 1.2 B 方案（已 PoC 五验证点全通过）

**Playwright `launch_persistent_context(channel='chrome', user_data_dir=...)`**：

- **无授权弹窗**：Playwright 用内部 stdio 管道（`--remote-debugging-pipe`）连接，不暴露 TCP 端口、不算外部连接，从机制上不触发「允许远程调试」授权流程（PoC 验证点 1：ps 无 `--remote-debugging-port`、全程无 press_allow）✅
- **免钥匙串**：解密发生在浏览器进程内部（它是加密存储创建者自动有权），通道路径与方案 A 完全不同（PoC 验证点 3 对照实验：本机 Keychain 零「Chromium Safe Storage」授权态下 CDP 取 Cookie 14 条成功）✅
- **脚本控制 / 网络监听 / CDP 调试**：Playwright 全保留（验证点 2/4/5）✅
- **登录态持久化**：正常 `ctx.close()` 落盘（`pkill -9` 会丢，已实证）✅

### 1.3 目标

1. 引擎从「单例」→「多账户引擎池」（每账号 = 一个独立 user-data-dir = 一个独立登录态）
2. 统一配置中心、统一日志审计、每账号风控状态机、优雅生命周期、任务标准化、数据层带账号维度
3. 生产形态（~~pywebview 壳~~，已被 R-6.16 路线A 改为有头 CFT 应用窗壳）与调试形态共用同一套机制
4. 兼容现有前端与 API（默认账号语义，前端无感）

---

## 二、架构总览

```
┌─────────────────────────────────────────────────────────┐
│ 前端 Vue3（127.0.0.1:<动态端口>，相对路径 /api/...）       │
└──────────────┬──────────────────────────────────────────┘
               │ HTTP
┌──────────────▼──────────────────────────────────────────┐
│ server.py（FastAPI，路由随功能演进，以代码为准；account_id 适配） │
│  登录校验 → 任务路由 → 数据层（SQLite 带 account_id）        │
└──────────────┬──────────────────────────────────────────┘
               │ 调用
┌──────────────▼──────────────────────────────────────────┐
│ engine_pool.py（新：多账户引擎池）                         │
│  ┌───────────────────────────────────────────────────┐   │
│  │ AccountEngine（每账号一个实例）                      │   │
│  │  context = launch_persistent_context(user_data_dir)│   │
│  │  生命周期：懒启动 / 优雅关闭 / 自愈重启               │   │
│  │  风控状态机：正常 → 熔断 → 冷却 → 人工介入            │   │
│  └───────────────────────────────────────────────────┘   │
│  （账号 A：采集号  ← user_data_dir=~/.xhs-leads/accounts/A）│
│  （账号 B：发布号  ← user_data_dir=~/.xhs-leads/accounts/B）│
└───────┬──────────────┬──────────────────┬───────────────┘
        │              │                  │
┌───────▼──────┐ ┌─────▼───────┐  ┌──────▼──────────────┐
│ config.py   │ │ logger.py   │  │ risk.py             │
│ 统一配置中心 │ │ 日志+审计+脱敏 │  │ 风控状态机（每账号）   │
└─────────────┘ └─────────────┘  └─────────────────────┘
```

**核心不变式**：业务代码（collect_pipeline / server）只通过 engine_pool 的**账号路由函数**碰浏览器；登录态、Cookie、风控状态全部按账号隔离。

---

## 三、各机制设计

### 3.1 多账户引擎池（engine_pool.py，重写 browser_client 的引擎层）

**账号注册表** `data/accounts.json`：

```json
{
  "accounts": {
    "default": {
      "role": "collect",
      "user_data_dir": "~/.xhs-leads/accounts/default",
      "status": "ok",
      "cookie_expires_at": null,
      "created_at": "...",
      "updated_at": "..."
    }
  },
  "active": "default"
}
```

字段：
- `account_id`：稳定标识（`default` / `collect_a` / `publish_b`）
- `role`：`collect`（采集号）/ `publish`（发布号）/ `mixed`（通用，默认）
- `user_data_dir`：`~` 相对可移植
- `status`：`ok` / `cooldown`（冷却中，含 until）/ `manual`（需人工介入）
- `cookie_expires_at`：web_session 到期时间（约 7 天，到期提醒刷新）

**引擎管理**：
- `get_engine(account_id="default")`：懒启动；`asyncio.Lock` 保护；连接失败冷却重试
- `close_engine(account_id)` / `close_all()`：`ctx.close()` 优雅落盘（绝不用 pkill -9）
- 自愈：上下文断开（`is_connected()==False`）→ 自动重建
- 退出钩子：FastAPI shutdown 事件 → `close_all()`
- **保底空白页**：保留现有 `_close_page_safe` 逻辑（最后一个页面先补 about:blank）

**操作路由**（保持函数签名兼容，新增可选 `account_id`）：

```python
async def search(keyword, limit, on_progress=None, should_stop=None, filters=None, account_id="default")
async def note(note_id, xsec_token="", wait_ms=5000, account_id="default")
async def user(uid, wait_ms=5000, account_id="default")
async def clients(note_id, xsec_token="", account_id="default")
...
```

默认 `account_id="default"` → **现有 server 代码零改动**；多账号时前端/后端显式传账号。

**登录态**：每账号 Cookie 存 `<user_data_dir>/../cookies/<account_id>.json`（或 data/cookies/<account_id>.json），不再单份 `data/cookies.json`。

### 3.2 统一配置中心（config.py）

- 新增 `data/config.json`（引擎端口 / 账号 / 行为参数）
- 保留旧文件（cdp_config.json / llm_config.json / persona.json）通过 config 统一入口读写，**不迁移数据文件**（避免破坏现有用户数据）
- 读取优先级：环境变量 > config.json > 默认值
- 路径一律 `~` 相对（沿用 `_home_relative` 模式）
- **账号独占端口**（R-6.57 修订，原为"引擎调试端口固定 9223"）：默认账号仍用 **9223**（`config.engine_port`，`XHS_PROFILE_PORT` 可覆盖），兼容 36 个硬编码 9223 的老测试脚本；其余账号由 `config.account_debug_port` 分配 **9224–9249**，登记在 `accounts.json` 的 `debug_port` 并持久化（不按空闲端口漂移，避免测试连错）。
  改因：Chromium 对 `--remote-debugging-port` 绑定失败**不报错、静默跳过**，两账号共用 9223 时第二个及之后的账号 CDP 根本不可达（真机对照实验已证实），多账号在测试层面等于不存在。
  8800-8899 仍是**前端端口**被占时的自动探测区间（非引擎端口）
- 前端保留页判断：`cleanup_tabs()` 从 config 读当前前端端口，不再硬编码 8791

### 3.3 统一日志 + 审计 + 脱敏（logger.py）

- 标准 `logging`：`data/logs/app.log`（RotatingFileHandler，5MB×5）+ 控制台
- 按模块 logger：`codex_xhs.engine` / `codex_xhs.server` / `codex_xhs.risk`
- **审计**：账号级操作留痕（登录/导入Cookie/采集/克隆/发布/草稿），含 account_id + 动作 + 结果 + 耗时
- **脱敏**：Cookie 值、web_session、api_key 一律 `***`；日志绝不输出 Cookie 明文（新增 `redact()` 工具，所有 Cookie 打印点必须过它）

### 3.4 引擎生命周期管理

| 场景 | 行为 |
|---|---|
| 后端启动 | 不预启引擎（懒加载），清理残留 tab |
| 后端关闭 | `close_all()`：先 `ctx.close()` 落盘登录态，再停 Playwright |
| 引擎崩溃/断开 | `is_connected()==False` → 自动重建（带冷却防抖） |
| 任务结束 | 保留引擎常驻（避免反复开关被风控）；页面清理走 `cleanup_tabs()` |
| 一次性动作（Cookie 校验） | 用后 `close_engine()` 优雅关闭（替代原 `stop_profile` 的 pkill -9） |

### 3.5 风控状态机（risk.py，每账号）

状态迁移：

```
ok ──(登录墙/验证码/限流检测)──► cooldown（熔断，until=now+N）
ok ──(采集频率超闸)──────────► throttle（仅该账号拒绝新任务，N 秒）
cooldown ──(人工确认/到期)────► ok
任何状态 ──(Cookie 失效)──────► manual（需扫码/导 Cookie，界面引导）
```

- 信号源：`_detect_login_wall`（登录墙/验证码文本）、采集 429/异常、发布护栏（已有 24h 熔断并入）
- **每账号独立**：A 号风控不影响 B 号
- 采集节流 `_check_collect_throttle` 从全局改为每账号
- **操作节奏**：现有 `human_delay` / `REQUEST_INTERVAL` 保留并下沉到引擎层（每账号同配置，避免出现「秒级反复开关 Chrome」）

### 3.6 任务机制标准化

- `_COLLECT_TASKS` 内存态保留（任务生命周期短），但状态加 `account_id` + `started_at` + 结果摘要
- 任务 → 引擎路由：采集任务默认走 `role=collect` 的账号（未配置则 default）
- 进度/取消 API 不变（兼容前端），响应增加 account_id
- 断点续采（本轮不做：采集是即时任务，采完即弃，符合产品语义；记录到待办）

### 3.7 数据层统一（SQLite 带账号维度）

- `notes` / `publish_logs` / `clients` / `drafts` 增加 `account_id TEXT DEFAULT 'default'` 列（幂等 ALTER）
- 写入时记当前账号；查询默认全账号（兼容），可选按账号过滤
- 搜索数据「用完即弃」原则不变（notes 仍按批次 DELETE 重写；草稿/发布记录长期保留）

### 3.8 跨平台 Chrome 发现

- **已验证**：`chrome_executable()` 已有 macOS（/Applications + ~/Applications）与 Windows（注册表 App Paths + 常见路径）实现，`cookie_reader` 有 Windows DPAPI 分支（结构就位待实测）
- 本轮：补单测 + 补 Linux 兜底（罕见，仅文档标注）；生产壳首次启动引导（找不到 Chrome → 前端提示 + 文件选择器）后续随 UI 做

---

## 四、兼容策略

1. **默认账号**：所有 API 不传 `account_id` 时走 `default`，现有前端/测试零改动
2. **数据不迁移**：`default` 账号继续用现有 `data/cookies.json` 与现有 SQLite 数据（兼容老库）
3. **环境变量向后兼容**：`XHS_BROWSER_MODE` / `XHS_PROFILE_PORT` / `XHS_PROFILE_DIR` 仍生效（映射到 default 账号）
4. **模块兼容**：`browser_client.py` 对外函数签名不变（加可选参数），`collect_pipeline` 零改动
5. **测试约定**：UI 自动化测试用独立空白 profile（不碰日常账号）；低频搜索词

---

## 五、实施阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1（本轮） | config.py + logger.py + engine_pool 重构 + 生命周期 + 风控状态机 | 现有 E1 测试全绿；多账号引擎可分别启动/关闭；日志有审计无明文 Cookie |
| P2（本轮） | server.py 适配（account_id 透传 + 每账号节流 + shutdown 钩子）+ 数据层 ALTER | /api/collect_start 带账号；notes 表带 account_id |
| P3（进行中） | 多账号可调试地基（R-6.57，已落地机制层）：账号独占端口 + 账号窗口错开 + `/api/accounts` + `scripts/accounts.py` | 两账号各自 CDP 可达（9223/9224 真机验证）；第二账号窗口出现在屏内而非屏幕外；`GET /api/accounts` 返回 port_map |
| P3（后续） | pywebview 壳改造 + 账号管理 UI（1 采集 + 1 发布） | 独立 App 窗口；账号页增删/扫码/状态 |
| P4（后续） | 跨平台装机引导（Chrome 发现 + 一键配置） | Windows 实测 |

---

## 六、风险与护栏

| 风险 | 对策 |
|---|---|
| 多账号同机指纹关联 | 支持机制但使用上错峰（采集/发布不同时段）；文档提示 |
| 优雅关闭遗漏丢登录态 | shutdown 钩子 + 任务 finally 兜底 `close_engine`；禁止 pkill -9 路径 |
| 改造破坏现有功能 | 默认账号语义 + 现有 E1 六场景回归；成功前不推送 |
| 日志泄密 | 所有 Cookie/Key 打印点强制 `redact()`；code review 关卡 |

---

## 七、验收标准（可执行）

- [ ] `uvicorn` 启动无报错，日志落 `data/logs/app.log`
- [ ] `browser_client.search()` 默认账号行为与改造前一致（E1 s1/s3 回归）
- [ ] 两个账号（default + test_a）可分别启动引擎、互不干扰
- [ ] 关闭后端进程 → 引擎优雅关闭 → 重启后登录态保留（免重扫）
- [ ] 日志中无 Cookie 明文（grep web_session 值不到）
- [ ] `cleanup_tabs` 不再硬编码 8791；pkill 路径移除
- [ ] 风控熔断只影响触发账号

---

## 八、三现象机制（操作观感 + 风控恢复）——2026-10-29 补充

用户实测观察到的三个负面现象，机制层一并解决：

### 现象 1：反复开关小红书页面（观感非人 → 易触发风控）

| 根因 | 机制对策（已落地） |
|---|---|
| 任务级反复启停 Chrome 进程（窗口开-关-开） | 引擎常驻：进程级启停仅发生在后端 shutdown / 用户退出 APP；任务结束不再关闭引擎 |
| 每个任务新建 tab 用完即关（反复开关 tab） | `AccountEngine._task_page` 任务 tab 复用：同账号后续任务同 tab 导航覆盖，不新建不关闭；非任务页（校验页等）才关闭 |
| Chrome 关最后一个页整窗退出 | `close_page_safe` 保底空白页兜底（既有） |
| 校验路径反复清空/注入 Cookie | `verify_cookies` 默认 headless 判定（不碰引擎）；引擎回退仅兜底 |

### 现象 2：搜索等动作反复执行（重复操作被平台判异常）

| 根因 | 机制对策（已落地） |
|---|---|
| 同关键词任务重复提交 | ~~collect_start 任务唯一键 `keyword|account_id` + 60s 窗口去重 → 409 提示~~【已被 R-6.22h 移除：同词/换词均允许立即重启，dedup 仅留审计展示】 |
| 失败任务空转重试 | 任务状态机 error 即停；软风控走恢复闭环（见现象 3），不自动重复执行 |
| 平台任务频率失控 | ~~每账号节流 90s（`risk.check_throttle`）~~【已被 R-6.15 移除：`collect_min_interval` 废弃，仅留拟人请求间隔 1-1.8s + 滚动采集 stale 终止】 |

### 现象 3：遇风控就停死等用户（实际多为临时频率异常，扫码/验证即可恢复）

| 旧行为（问题） | 新机制（已落地） |
|---|---|
| 登录墙/验证码一律 `mark_risk` 24h 熔断死等 | 软/硬分级：验证码/扫码/登录墙 → `mark_manual`（不死等）；明确封禁 → `mark_hard` 24h |
| 风控后无自动恢复 | 恢复闭环由 `login_status` 承担：判定有效 → 落盘 Cookie → **自动 `risk.resolve` 解锁**（z8o 起删除冗余的 `refresh_cookie_from_chrome`） |
| 连续临时风控无节奏控制 | `mark_soft` 指数退避：1→2→4→8→16 分钟封顶 30 分钟，冷却到期自动回 ok，不要求人工 |
| 前端无引导 | 拦截文案明确「页面已打开登录入口，请扫码或完成验证，恢复后自动解除」；登录页保留供扫码 |

**闭环时序（软风控）**：
采集遇验证码 → `mark_manual` + 任务 error（页面保留）→ 前端提示扫码 → 用户扫码/验证 → `login_status` 检测有效 → `risk.resolve` 自动解锁 → 用户重试采集即继续。

### 验收补充（现象三机制）
- [ ] ~~同关键词 60s 内重复 `collect_start` → 409~~【R-6.22h 已改为：同词连点均 200】
- [ ] 连续 3 次 `mark_soft` → 冷却 4 分钟（指数退避），非 24h
- [x] `mark_manual` 后扫码成功 → `login_status` 判有效且 `risk.state` 回 ok（原 refresh_cookie 端点已删）
- [ ] 任务结束引擎窗口保留（不反复开关）；`cleanup_tabs` 仅清理非 APP/空白页

## 九、账号目录设计（R-6.59，2026-11-08 补充）

### 9.1 改之前的状态（问题）

同一个东西散在两个互不相干的根：

| 东西 | 位置 |
|---|---|
| 注册表、库、图片、日志 | `~/Library/Application Support/xhs-leads/data/` |
| default 的 profile | `data/profile/`（81M） |
| 其他账号的 profile | `~/.xhs-leads/accounts/<账号名>/` 另一个根，藏在 home 下 |
| default 的 cookie | `data/cookies.json` 特例 |
| 其他账号的 cookie | `data/cookies/<账号名>.json` |

后果：代码到处特判 default（cookie 路径、profile 路径、端口）；搬数据目录要动两处；换平台要改注册表里的绝对路径；`~/.xhs-leads/accounts/` 下的 `acc_test1` / `verify_b` / dev `default` 看着像真账号。

### 9.2 目标结构

```
<数据目录>/
├── accounts.json               注册表
├── accounts/<账号名>/          所有账号 profile 统一在这里
└── cookies/<账号名>.json       导出 cookie 按账号（default 不再特例）
```

### 9.3 命名规则

- **目录名 = 账号 id**：一份数据一个名字，不再维护「id → 目录名」映射表。
- 只允许 `小写字母 / 数字 / 下划线 / 短横线`，首字符必须是字母或数字，≤32 字符。
- 禁止中文（路径编码）、空格、点号、`/` 与 `\`（Windows 非法字符）、大写（Windows 大小写不敏感会撞名）。
- 新增自动 `acc_<6位hex>`（`new_account_id()`），允许手动起名如 `zhubao_main`。
- 实现：`config._ACCOUNT_ID_RE` + `valid_account_id()`，`add_account()` 前置校验。

### 9.4 跨平台关键：注册表存相对路径

注册表 `user_data_dir` 存**相对数据目录的路径**（`accounts/default`），不存 `~/...` 也不存 `/Users/...`。

数据根由 launcher 注入 `XHS_LEADS_DATA` 决定：
- macOS 打包态：`~/Library/Application Support/xhs-leads/data`
- Windows 打包态：`%APPDATA%\xhs-leads\data`（规划中）

搬数据目录、换平台、换机器，注册表一个字都不用改。

`config.resolve_account_dir()` 兼容三种写法：

| 写法 | 例子 | 解析 |
|---|---|---|
| 相对数据目录 | `accounts/default` | `DATA_DIR / raw`（新账号一律这种） |
| `~` 开头 | `~/.xhs-leads/accounts/verify_c` | `expanduser`（早期账号） |
| 绝对路径 | `/Applications/X`、`C:\Users\X` | 原样（自定义位置） |

### 9.5 迁移与兼容

- `ACCOUNTS_ROOT = "~/.xhs-leads/accounts"` 降级为**历史根**：只用于读取与进程扫描，不再写入新账号。
- `watchdog._marks()` 同时认新根 `config.accounts_dir()` 与历史根，避免老账号进程漏管。
- 迁移用 `ditto`（macOS 保留 xattr/ACL）。Windows 等价手段为 `robocopy /COPYALL` 或 `shutil.copytree`，后续实现时补。
- **迁移后必须验登录态，不能只看文件数对得上**（profile 拷贝成功 ≠ 登录态还在）。

### 9.6 本次迁移实测

| 项 | 结果 |
|---|---|
| default：`data/profile` → `accounts/default` | 424 文件一致，81M→79M |
| default 引擎实际参数 | `--user-data-dir=.../data/accounts/default` |
| default 登录态 | `{"status":"logged_in","logged_in":true}` 保住 |
| verify_c：`~/.xhs-leads/accounts/verify_c` → `accounts/verify_c` | 494 文件一致 |
| verify_c 引擎实际参数 | `--user-data-dir=.../data/accounts/verify_c`，9224 正常 |
| 旧目录 | 移到 `/tmp/xhs_migrated_backup_1108`（2.5G，不删，可回滚） |
| 历史根 `~/.xhs-leads/accounts/` | 已清空 |
| 单测 | `test_account_paths.py` 20 例 + 四件合计 52 例、含 engine_window 共 56 例全过 |
| 后端全量 | 全绿（排除 llm_service / real_login） |

## 十、统一登录模式改造（R-6.65 ~ R-6.67，2026-11-08）

### 10.1 背景与问题

真机扫码登录第三类账号时暴露两个问题：
1. **点空白登录组件消失（原有 BUG）**：登录是全屏 modal，卡片外所有区域都是遮罩 `.reds-mask`，点遮罩即关闭（modal 从 DOM 移除），重新登录入口不直观。
2. **登录方式不统一**：第三类账号走独立 `/login`（full-page），而采集窗走 explore 浏览页模式（背后有浏览流、侧边栏登录按钮常驻、关了也能找回），用户要求统一并做抽象。

### 10.2 三步改造

| 步骤 | 版本 | 内容 |
|---|---|---|
| 第一步 mask 拦截 | R-6.65 | 复用现有 `force_login` 注入，在 `engine_pool._launch_after_start` 给第三类账号 context 补挂（此前只挂在 default 壳窗），拦截遮罩点击与 ESC |
| 第二步 explore 模式 | R-6.66 | `login_page` 导航从 `/login` 改 `/explore`；新增 `LOGIN_CLOSE_SELECTORS` 常量补齐 explore modal 的 `.close` 关闭按钮（旧选择器漏覆盖，点 × 仍可关） |
| 第三步 事件驱动 | R-6.67 | 新建 `login_session.py`，`LoginSession` 替代 R-6.64 轮询守护，见 10.3 |

### 10.3 事件驱动登录会话（login_session.py）

真机调研确认的登录信号（探针 `/tmp/probe_*`）：

| 状态 | `GET /api/sns/web/v2/user/me` |
|---|---|
| 未登录 | `data.guest=true`，无 nickname |
| 扫码登录成功 | 前端重新拉取，`data.guest=false` + 真实 nickname |

`LoginSession` 流程（**无轮询**）：
1. 监听挂 **context 级** `response` 事件（页面导航/重建不影响），匹配 user/me 路径；
2. 纯函数 `is_real_login_me` 严格判定 `data.guest === false`（游客/异常响应不触发）；
3. 事件触发后一次性 `read_cookies` 确认 `id_token`（项目权威登录口径，非轮询）；
4. 等 `LOGIN_STABLE=3s` 稳定 → 调 `set_window(on=False)` 切无头；
5. 支持取消、`LOGIN_TIMEOUT=600s` 超时（10 分钟，给用户宽松扫码时间）；finally 自动移除监听。

### 10.4 真机验证

| 项 | 结果 |
|---|---|
| 会话启动 | 后端日志确认 context 级监听挂上 |
| 事件捕获 | 「捕获 user/me guest=false 登录事件」→「自动切无头 成功」 |
| 无头进程参数 | `--headless --remote-debugging-port=9225 --user-data-dir=.../accounts/acc_a` |
| 无头 + 已登录（填补历史空白） | explore 推荐流完整渲染、侧边栏有「我」、无登录 modal、UA 无 Headless 标识 |
| 单测 | 新增 `test_login_session.py` 13 例 + 重写 engine_window 相关；后端全量全绿 |

**踩坑**：首版超时 300s，用户扫码耗时超过 5 分钟导致会话先退出（登录本身成功、仅未自动切无头），已延长到 600s；独立进程脚本读不到后端进程内存里的 `_SESSIONS`（跨进程，同 `_active_page` 恒 None），以后端日志为准。
