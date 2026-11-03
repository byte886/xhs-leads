// R-6.37e：ForceLogin 强制登录对话框组件测试
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ForceLogin from './ForceLogin.vue'

vi.mock('../api', () => ({
  apiLoginBegin: vi.fn().mockResolvedValue({ ok: true }),
  apiLoginStatus: vi.fn().mockResolvedValue({ logged_in: false, kicked: false, risk_verify: false }),
  apiCollapseDebugWindows: vi.fn().mockResolvedValue({ ok: true }),
}))

describe('ForceLogin', () => {
  it('open=false 时不渲染对话框内容', () => {
    const w = mount(ForceLogin, { props: { open: false } })
    expect(w.text()).toBe('')
  })

  it('open=true 时渲染去登录主操作按钮', () => {
    const w = mount(ForceLogin, { props: { open: true } })
    expect(w.text()).toContain('去登录')
  })
})
