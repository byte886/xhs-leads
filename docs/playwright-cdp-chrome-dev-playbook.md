# Playwright / CDP / Chrome 桌面壳应用开发与崩溃排查手册

> 适用：macOS 桌面壳应用（PyInstaller .app + Playwright `launch_persistent_context` 驱动 Chrome for Testing 引擎）。
> 沉淀来源：XHS Leads 项目 2026-10~11 CFT 引擎反复「意外退出」完整排查（24 份 .ips 同栈取证、157 升级验证失败并回退、R-6.53b~h 修复链）。
> 目标：后续再遇到引擎崩溃 / 打包差异 / 调试问题，按本手册流程走，不重新踩坑。

---

## 1. 崩溃取证方法论（核心资产）

### 1.1 macOS .ips 崩溃报告解析

- **位置**：`~/Library/Logs/DiagnosticReports/Google Chrome for Testing-*.ips`
- **格式**：**首行是元数据头，第二行起才是 JSON body**（`json.loads` 必须跳首行）
- **判读要点**：
  - `exception.type`：EXC_BREAKPOINT（Chromium CHECK 崩溃的典型形式）
  - `signal.signal` / `termination.code`：SIGTRAP / 5
  - `faultingThread` → `threads[ft].name`：线程角色（本项目固定 `Chrome_IOThread`）
  - 栈顶帧 `imageOffset`：相对 Framework 二进制的偏移（同栈判定用，见 1.2）

```bash
LATEST=$(ls -t ~/Library/Logs/DiagnosticReports/ | grep "Chrome for Testing" | head -1)
F=~/Library/Logs/DiagnosticReports/$LATEST
python3 -c "
import json
lines = open('$F').read().split('\n',1)
body = json.loads(lines[1])
print('exception:', body.get('exception',{}).get('type'))
print('termination:', body.get('termination',{}).get('code'))
ft = body.get('faultingThread', 0)
print('faulting线程名:', body['threads'][ft].get('name'))
for fr in body['threads'][ft]['frames'][:8]:
    print('  帧:', fr.get('symbol', fr.get('imageOffset','')), '偏移+'+str(fr.get('imageOffset','')))
"
```

### 1.2 同栈判定（升级/回退验证的核心）

**判定同一确定性 CHECK，用"栈结构对比"而非"偏移一致"**：

| 要素 | 对比方式 |
|---|---|
| 异常类型 | 完全一致（如 EXC_BREAKPOINT） |
| 信号 / 退出码 | 完全一致（SIGTRAP / 5） |
| 线程角色 | 完全一致（Chrome_IOThread） |
| 帧结构 | 前 N 帧符号与顺序一致；栈顶偏移可不同 |

> 版本升级后二进制基址不同 → **偏移差异不代表修复**。本项目 156（+0x9b18408）→ 157（+0x9ca4478）栈顶偏移不同但五要素全同 → 判定同一 CHECK，157 未修复，立即回退，不做无效投入。

### 1.3 Crashpad 对照取证

- `~/Library/Application Support/Google/Chrome for Testing/Crashpad/pending/*.dmp`（150+ 份时说明长期反复崩溃）
- 与 .ips 逐帧吻合可交叉验证；未吻合时以 .ips 为准

### 1.4 复刻实验设计（控制变量）

**核心教训：profile 是头号混淆变量。**「源码不崩 / 打包才崩」极可能是 profile 假象，不是打包问题。

- **三要素隔离**：
  - 二进制：source 引擎 vs dist 引擎（build 拷贝，应同源同版，先 `--version` 复核）
  - profile（user-data-dir）：干净开发 profile vs 真实积累 profile
  - env：DYLD_LIBRARY_PATH / XHS_LEADS_DATA 等注入
- **等价实验**：源码直跑 + 注入与打包版相同的 `XHS_LEADS_DATA`（同一真实 profile）+ 同采集负载 + 同监控时长 → 若同样崩，与打包无关，纯引擎+profile 触发。

---

## 2. 环境差异排查（「打包后才出问题」类）

### 2.1 三通道对比表

| 维度 | 取证命令 | 打包态 vs 开发态注意点 |
|---|---|---|
| 引擎二进制路径 | `ps -p <pid> -o command=` | 打包=`.app/Contents/Resources/engine/`，源码=`~/Library/Application Support/xhs-leads/engine/` |
| user-data-dir | `ps -p <pid> -o command= \| grep -o "\-\-user-data-dir=[^ ]*"` | 打包=launcher 注入 `XHS_LEADS_DATA` → `data/profile`；源码默认 `~/.xhs-leads/accounts/default` |
| 环境变量 | `ps eww <pid>` 解析（见 2.2） | PyInstaller/宿主应用可能注入 DYLD_LIBRARY_PATH |

### 2.2 ps eww 环境取证

`ps eww` 输出 env 以空格分隔，**含空格的值会被截断**——用已知变量名做锚点解析：

```bash
EP=$(pgrep -f "Google Chrome for Testing" | head -1)
python3 -c "
import subprocess
out = subprocess.run(['ps','eww','-p','$EP','-o','command='],capture_output=True,text=True).stdout
i = out.find('DYLD_LIBRARY_PATH=')
j = out.find('PYTHONUNBUFFERED=', i)  # 下一已知变量锚
print(repr(out[i+len('DYLD_LIBRARY_PATH='):j].strip() if j>i else out[i:i+200]))
"
```

### 2.3 PyInstaller DYLD 坑（官方 common-issues-and-pitfalls）

- 冻结 app 启动外部子程序时，runtime hooks 可能修改 `DYLD_LIBRARY_PATH` 并被子进程继承 → 启动外部程序前需 sanitize
- **但先验证**：注入路径是否真实存在（`os.path.isdir`）。指向不存在路径时 dyld 忽略 → 排除该通道，不浪费精力。
- 本项目实测：打包版引擎 env 的 DYLD_LIBRARY_PATH 指向豆包 sandbox_runtime lib（不存在）→ 排除。

---

## 3. 引擎管理（下载 / 升级 / 回退）

### 3.1 下载源（国内环境）

| 源 | 状态 |
|---|---|
| `https://registry.npmmirror.com/-/binary/chrome-for-testing/<版本>/mac-x64/chrome-mac-x64.zip` | ✅ 可直连（首选） |
| `https://storage.googleapis.com/chrome-for-testing-public/...` | ❌ 被墙（直连 exit 28 / 代理 TLS exit 35） |
| `https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json` | ✅ 可直连（查 Stable 最新与兼容清单） |

### 3.2 升级/回退 SOP

1. **备份当前引擎**：`rsync -a ~/Library/Application Support/xhs-leads/engine/ ~/Library/Application Support/xhs-leads/engine-chromeXXX-backup/`
2. **替换两处**：source（`~/Library/Application Support/xhs-leads/engine/`）+ dist（`.app/Contents/Resources/engine/`）——**两处都要换**，否则混淆验证
3. **复核版本**：两处 `<引擎>/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing --version`
4. **启动验证**：登录态保留、采集跑一轮
5. **崩溃判定**：新 .ips 与旧栈按 1.2 五要素对比 → 同 CHECK 则回退（restore 备份 + 复核版本）
6. **Playwright 兼容**：升级前确认本机 playwright 版本支持（`pip show playwright` / 官方 release notes）

---

## 4. 守护与自愈架构最佳实践

### 4.1 事件驱动 + 轮询兜底（业界标准）

- **Playwright 标准模式**：`browser.on("disconnected")` 事件驱动重启 + `asyncio.Lock` 防并发重建 + 有限重试；另有 close 事件 + 轮询兜底组合
- **本项目对照**：`page.on("close")` / `page.on("crash")` 事件 + keeper 5s 轮询 + engine_pool 惰性检查自愈 → **方向与业界一致，无需结构改动**

### 4.2 崩溃恢复链设计要求（本项目已实现并验证）

```
引擎崩溃 → 自动重建 → 壳窗重开 → App 不误退 → 登录态保持 → 无报错
```

两个真实 bug 教训（都属"自愈链自身缺陷"而非引擎问题）：
1. **误传模块而非单例**：`attach_engine(engine_pool)`（模块）应为 `attach_engine(engine_pool.pool)`（单例）→ 校验逻辑 hasattr(模块,"get")=False → AttributeError → 守卫从引入起从未生效（R-6.53h）
2. **崩溃恢复被误判为「用户关主窗」**：keeper 需区分「引擎崩溃后的重建」与「用户主动关窗」，否则 App 误退出（R-6.53b~g）

修复模式：自愈逻辑加 try/except 防御（校验异常 → 「保守跳过退出」→ return），单测覆盖（`backend/tests/test_shell_window_unit.py`，16 passed 基线）。

### 4.3 已知坑清单（实战经验，跨项目复用）

| 坑 | 现象 | 对策 |
|---|---|---|
| CDP 假死 | Playwright 内部 timeout 依赖 CDP 响应 → 任务永久挂起 | `asyncio.wait_for` 外层超时兜底 |
| compositor 白屏 | 窗口级死锁，CDP 侧 DOM/截图正常 | 走窗口级恢复，不做页面内修复 |
| init script 注入失效 | 箭头函数只定义不执行；`//` 注释吞尾段；observer 锚点早 null | 用 IIFE；注释用 `/* */`；锚点用 `document` |
| 全新 profile headless 被风控 | 小红书安全限制 300012 | 非 headless 有窗口启动 |
| 壳窗形态不一致 | 带/不带地址栏并存 | 枚举全部创建入口，统一 `--app` 窗口路由 |
| 主进程杀不掉 | pkill/kill -TERM 对主进程不可靠 | kill -9 + `pgrep` 核对无残留、端口唯一占用 |

---

## 5. 调试工具链速查

| 用途 | 命令 |
|---|---|
| 后端探活 | `curl -s http://127.0.0.1:8791/api/status_now`（action/collected/limit） |
| 登录态 | `curl -s http://127.0.0.1:8791/api/login_status` |
| CDP 目标列表 | `curl -s http://127.0.0.1:9223/json/list` |
| 引擎进程 | `pgrep -f "Google Chrome for Testing"` |
| 崩溃计数 | `ls ~/Library/Logs/DiagnosticReports/ \| grep -c "Chrome for Testing"` |
| 应用日志 | `data/logs/app.log`（追加不清空，按最新时间戳判读；R-6.53h 后 AttributeError 计数定格不增） |
| 引擎日志 | `data/logs/chrome-engine.log` |
| 触发采集 | `POST /api/collect_start {"keyword":"...","limit":15}`（低频铁律：风控敏感，不连环重试） |

---

## 6. 本项目事实基线（截至 2026-11-05）

| 事实 | 结论 |
|---|---|
| 崩溃根因 | CFT 156/157 引擎内部**确定性 CHECK**（EXC_BREAKPOINT / SIGTRAP / termination 5 / Chrome_IOThread），**代码层无法消除** |
| 是否与打包相关 | **否**——源码直跑 + 真实 profile 同样崩；「打包后才崩」是 profile 相关性假象 |
| 触发相关变量 | 真实积累 profile（`data/profile`）比干净开发 profile 更易触发；采集负载后数分钟出现 |
| 自愈链 | R-6.53b~h 全修复并真机验证（崩→自动重建→App 不误退→登录态保持）；commit `7f84543` 已 push |
| 引擎版本 | 当前 156.0.8078.80（157.0.8103.38 已验证同 CHECK 未修复，已回退）；156 备份在 `engine-chrome156-backup/` |
| 后续方向（待决策） | 维持自愈兜底 / 打包 24 份同栈 .ips 上报 crbug / 等 CFT 158 再试 |
