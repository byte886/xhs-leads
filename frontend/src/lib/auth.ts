// 登录失效判定（R-6.21e）：登录引导由**交互异常驱动**（用户拍板：不在后台定时
// 轮询刷新登录态——只有触碰平台时由交互失败携带的信号决定是否弹强制登录框）。
// 后端交互错误 detail 携带的信号词（_require_login 的 kicked/not_logged_in note、
// 采集/发布失败文案）：命中即视为需要重新登录。
export function isLoginAbnormal(msg: string | null | undefined): boolean {
  if (!msg) return false
  return msg.includes('登录') || msg.includes('被踢') || msg.includes('验证码')
}
