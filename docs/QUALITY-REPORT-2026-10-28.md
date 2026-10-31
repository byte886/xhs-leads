# xhs-leads 全项目 Clean Code 质检报告（2026-10-28 凌晨）

> 范围：后端（app/src/src/*）+ 前端（app/frontend/src/*）+ 生产脚本（app/scripts/*）+ 浏览器提取（extension/lib/extract.js）+ 测试（tests/*）。
> 方式：本轮为**可执行检查项**（pytest / import-linter / radon / 构建 / 语法 / crapper），结合 Clean Code Gauntlet 六维度门（CRAP / 变异 / 覆盖率 / 架构 / DRY）。**gauntlet 工具链已修通**（crapper 可产出 CRAP 报告；此前"卡死"根因 = crapper 内部跑 pytest 撞上收尾挂起，随挂起根治一并解决；tree-sitter 语法包确认已装齐）。

## 一、六维度结果

| 维度 | 工具 | 结果 | 说明 |
|---|---|---|---|
| 测试（全量） | pytest | ✅ 9 文件全绿 EXIT=0 | score 38 / cookie_reader 15+23 / browser_client 7 / collect_pipeline 6 / server / server_routes 60 / server_api 10 / **server_cdp 29（新增）**，共 173 |
| 架构约束 | import-linter | ✅ 2/2 contracts kept, 0 broken | leaf 纯净（score/cookie_reader）；server 顶层（无环） |
| 圈复杂度 | radon / crapper | ✅ 已清零（阈值 ≤8） | server 5 处（b818b64）+ **browser_client.chrome_executable CC10→5（本轮）**；当前全项目无 >8 函数 |
| 覆盖率 | pytest --cov | ✅ 85%（≥80 门达标） | cookie_reader 93% / score 95% / server 72%→**83%**（新增 CDP 配置层测试）；browser_client / collect_pipeline 按 `.coveragerc` 设计豁免（人工冒烟口径） |
| CRAP / 变异 | crapper / mutator | ✅ 已落地 | CRAP 数值真实（lcov 挂载修复：必须放 `target/coverage/python/lcov.info`）；变异重跑（13:25）：server 171 killed / score 51 / cookie_reader 35 killed，**存活 0**；browser_client / collect_pipeline 按豁免未执行变异（无覆盖=未运行，与 .coveragerc 口径一致） |
| DRY | dryer | ✅ 0 候选 | 修复 1 处完全重复（`_platform_setup_script`/`_pick_chrome_script` → 合并 `_script_path`）后 `No duplicate candidates found` |
| 前端构建 | npm run build | ✅ 通过 | 产出 ../web/assets/index-CnNw2ilS.js / CRKb7nSt.css |
| 脚本语法 | node --check | ✅ extract.js 通过 | 生产链路注入源 |

## 二、圈复杂度（radon，阈值 ≤8）

**已清零**（commit `b818b64`，2026-10-28 重构完成）：原 5 处超标全部拆分至 ≤8，server.py + collect_pipeline.py 现无任何 >8 函数。

| 原函数 | 原复杂度 | 重构后 | 拆分出的 helper |
|---|---|---|---|
| `cdp_setup` | 15 | 3 | `_persist_and_read_cdp` / `_check_cdp_ready` / `_check_chrome_state` / `_launch_cdp_chrome` |
| `_read_cookie_status` | 11 | 4 | `_parse_web_session` / `_web_session_value` / `_web_session_expires` / `_expires_ok` |
| `_enrich_notes` | 11 | 主循环 | `_enrich_fast` / `_fill_fans` |
| `notes` | 11 | 组装 | `_notes_query` / `_decorate_note` / `_attach_score_info` |
| `get_feed_detail` | 15 | 8 | `_merge_detail_core` / `_merge_detail_interactions` |
| `_cleanup_orphan_images`（顺手） | 10 | 拆分 | `_referenced_feed_ids` / `_keep_image` |

验证：全量 144 测试通过（无失败）+ 后端重启冒烟（cdp_status/login_status 正常）。

## 三、本轮已修复

- **test_server_routes.py collect 测试挂起**：collect_start worker 新增 `_enrich_notes`（真实浏览器详情补全）步骤后，测试未同步 mock → TestClient 内真实调用浏览器导致全量 pytest 挂起。已补 `fake_enrich` monkeypatch（commit `07b11c9`）。
- 附带发现：**多个 pytest 进程并发会互相抢 DB/资源**，串行分文件执行稳定。
- **【根治】全量 pytest 收尾挂起（根因闭环）**：`@app.on_event("startup")` 注册的是**原始函数对象**，测试里 `monkeypatch.setattr(server, "_startup_cleanup", ...)` 只改模块属性、对 FastAPI 事件表无效 → TestClient 每次启动仍真实执行 `browser_client.cleanup_tabs()`（连接 9223 + 启动 playwright driver 子进程），全量运行时多个 driver 子进程/线程泄漏 → pytest 退出收尾挂起（采样铁证：主线程等锁 + `os.waitpid` 等子进程 + kqueue loop）。**根治 = `tests/conftest.py` 全局 autouse fixture `_no_real_browser` 把 `browser_client.cleanup_tabs` patch 为 noop**。验证：全量 173 测试 `[100%]` EXIT=0，耗时正常。
- **覆盖率真相**：browser_client / collect_pipeline 不在报告是 `.coveragerc` 的**设计豁免**（注释写明"集成层按人工冒烟口径豁免"），非测量故障。真实口径 TOTAL 85%（server 72%→83% 因新增 tests/test_server_cdp.py CDP 配置层 29 个测试）。

## 四、遗留待办（需用户决策/专项）

1. ~~全量 pytest 收尾挂起~~ **✅ 已根治**（见上节，conftest 全局 autouse patch + pytest-timeout 双保险）。
2. ~~覆盖率挂起/未达标~~ **✅ 已达标**：真实口径 85% ≥ 80% 门（server 83%）；browser_client / collect_pipeline 按 `.coveragerc` 设计豁免（集成层人工冒烟口径）。
3. ~~gauntlet CRAP / 变异 / DRY~~ **✅ 已全部落地**：
   - CRAP：lcov 挂载修复（crapper 只认 `target/coverage/python/lcov.info` 等约定路径，此前 `coverage.lcov` 放项目根读不到）→ 数值真实；全项目唯一 CC 超标点 `chrome_executable`（CC10）已重构拆分为 5。
   - 变异：mutator 对当前代码重跑（13:25）——server 171 / score 51 / cookie_reader 35 变异体**全部被杀，存活 0**；browser_client / collect_pipeline 因无覆盖率豁免未执行变异（与 .coveragerc 豁免口径一致）。
   - DRY：dryer 发现 server.py 2 处完全重复（`_platform_setup_script`/`_pick_chrome_script`）→ 合并为 `_script_path` 公共函数 → `No duplicate candidates found`。
4. **平台限流**：10-28 清晨两次低频探测（05:13 / 上午）均 180s 超时且 Chrome 出现 `website-login/captcha` tab（"请求太频繁"），已按原则清理残留 tab、未连环重试——**仍限流中**；待恢复后跑真实链路（采集→评分→作者榜→克隆验证提取增强→客户库→草稿），发布永远手动。
