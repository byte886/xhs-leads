# 容器登录风控诊断记录（2026-10-26）

> ⛔ **退役注（2026-11-03）**：本文属**容器 xhs-mcp 时代**诊断（Docker 无头 + windows 伪指纹 + `--enable-automation` 被平台限流）。R 批已改为 Playwright 引擎独立 profile + stealth、登录态存引擎 profile、登录走壳窗官方登录页 + 守卫捕 id_token，本文的「已排除路径 4 条 / 根治路径 A·B·C」均针对旧容器架构，不再适用。**仅「登录链路抓包成果」一节**（qrcode create/userinfo 接口、X-s 签名要求、二维码渲染方式）作为登录协议知识可回溯。文件保留存档。

## 现象
- 容器 xhs-mcp 打开小红书 explore → 被重定向到 `website-login/captcha`「安全验证：请勿频繁操作，稍后重试」
- `get_login_qrcode` 调用 timed out（多次触发后被限流）
- 应用扫码弹窗因拿不到码显示「重试二维码」

## 对照实验（关键证据）
| 环境 | 打开 explore 结果 | 指纹特征 |
|---|---|---|
| 用户日常 Chrome **隐身窗口**（真实设备） | ✅ 登录弹窗秒出，128×128 二维码正常（dataURI） | UA=Mac Chrome/155、webdriver=false、platform=MacIntel、plugins=5、memory=32GB |
| 容器（无头 + windows 伪指纹） | ❌ 重定向 captcha 限流页 | UA=Windows Chrome/148（seed 生成）、无头特征 |

**关键推论**：两环境出口 IP 相同（Docker NAT），Chrome 正常而容器被限
→ **限流维度 = 设备特征检测**（平台识别容器为无头/自动化设备），非 IP。

## 已排除的路径（实证无效）
1. Chrome cookie 复制进容器（18 条一致但 web_session 指纹绑定，login_status=not_logged_in）
2. 换设备指纹 seed ×4（每次换后短暂窗口，随即再次被限）
3. 清空无效 web_session（页面干净但仍被限流重定向）
4. 模拟点击「登录」按钮（explore 顶部"登录探索更多内容"可点击，但**点击登录动作本身立即触发限流**——rate_limited 状态）

## 实锤证据：容器为何被识别为机器人
容器 Chrome 进程启动参数（docker ps 可见）：
- `--enable-automation` → **navigator.webdriver=true**（最大的自动化标志）
- `--headless` + `--fingerprint-platform=windows`（伪 windows 指纹）
- 对比用户隐身 Chrome：webdriver=false、platform=MacIntel、plugins=5、memory=32GB

**平台策略**：识别容器为自动化设备 → 任何登录/取码动作（点击登录、get_login_qrcode）→ 立即"请勿频繁操作"限流。

## 登录链路抓包成果（可复现，独立函数知识已备齐）
隐身 Chrome 完整生命周期（Network 抓包）：
1. **拿码**：`POST https://edith.xiaohongshu.com/api/sns/web/v1/login/qrcode/create`，body `{"qr_type":1}`
   → 响应：`{"data":{"qr_id":"...","code":"...","url":"https://www.xiaohongshu.com/mobile/login?qrId=...&xhs_code=...&timestamp=...&channel_type=web&component_id=...","multi_flag":0},"code":0}`
2. **二维码图片 = 前端用 url 字符串 + qrcode.js 渲染**（DOM 里 dataURI PNG，4618/5842 字节）——无需截图，字符串即可生成
3. **轮询扫码状态**：`POST https://edith.xiaohongshu.com/api/qrcode/userinfo`，body `{"qrId":"...","code":"..."}` → `{"data":{"result":{"success":...},"codeStatus":0}}`
4. **签名要求**：create 请求带 `X-s`（AB 加密签名）+ `X-t`（时间戳）+ `X-S-Common`，由前端请求拦截器注入——**裸 fetch 被 406 拒绝**（实验证实 code:-1）；页面内 fetch 也需调用前端签名器

## 有效路径（实证可行）
- 设备指纹轮换后存在短暂出码窗口（2026-10-26 19:21「设备登录超限」弹窗带码，可提取 128×128 二维码）
- 平台冷却后（数十分钟~数小时）登录弹窗恢复，即可扫码

## 工程现状（就绪项）
- `/api/qrcode`：check 优先复用现有弹窗二维码（0.6s），无码才触发一次（25s 不重试），限流快速失败
- 二维码提取：方形匹配（宽高差 <30px、100-500px），过滤笔记图/头像
- 前端：Cookie 只读显示（▸/▾）、扫码登录（容器）入口、Cookie 失效 15s 自动弹窗、登录成功三方联动
- 降级采集不受影响（list_feeds 推荐流）

## 后续待验证 / 根治路径
- **现状结论**：当前容器配置（headless + enable-automation + windows 伪指纹）下，平台对任何登录动作即限流，无法稳定出码。19:21 唯一出码窗口 = 指纹轮换后的偶然漏检。
- **根治路径（待选）**：
  - A. 容器浏览器伪装改造：去 --enable-automation（webdriver=false）、非 headless 或 stealth 方案——需改 xhs-mcp 镜像（fork/重 build）
  - B. 独立取码函数：实现/引入 xhs AB 签名（X-s），宿主机直接调 create + userinfo——签名需 JS 环境；且对容器设备 create 本身可能被限，需配真实指纹出口
  - C. 接受现状：低频等待冷却出码窗口 + 降级采集（list_feeds）可用；发布永远用户手动（已确认）
- 冷却恢复后验证链：应用弹窗出码 → 扫码 → login_status=logged_in → 三方 Cookie 对比 → search_feeds 关键词搜索恢复
