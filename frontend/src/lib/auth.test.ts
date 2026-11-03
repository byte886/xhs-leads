import { describe, expect, it } from 'vitest'
import { isLoginAbnormal } from './auth'

// R-6.36d：登录失效判定（R-6.21f 拍板口径）的行为守护。
// 只对「平台明示会话失效」信号弹强制登录；风控/发布拦截/裸词一律不触发。
describe('isLoginAbnormal', () => {
  it('空值不触发', () => {
    expect(isLoginAbnormal(null)).toBe(false)
    expect(isLoginAbnormal(undefined)).toBe(false)
    expect(isLoginAbnormal('')).toBe(false)
  })

  it('明示会话失效信号触发', () => {
    const yes = ['请重新登录', '登录已过期', '登录态失效', '被踢下线', '登录超限', '需要验证', '验证登录', '需人工扫码后重试', '请扫码验证']
    for (const msg of yes) expect(isLoginAbnormal(msg)).toBe(true)
  })

  it('风控/功能限制文案不触发（重登不解决的场景）', () => {
    const no = ['触发平台风险拦截，建议停用 24h', '操作过于频繁', '发布被平台拦截（验证码）', '风控熔断', '请确认 Chrome 已登录小红书且笔记可正常访问']
    for (const msg of no) expect(isLoginAbnormal(msg)).toBe(false)
  })

  it('「需人工扫码/验证后重试」属扫码验证请求 → 触发（R-6.21f 实测口径）', () => {
    expect(isLoginAbnormal('登录后查看（Cookie 有效但被风控，需人工扫码/验证后重试）')).toBe(true)
  })

  it('裸「登录」不触发（防误伤功能限制类提示）', () => {
    expect(isLoginAbnormal('登录')).toBe(false)
  })
})
