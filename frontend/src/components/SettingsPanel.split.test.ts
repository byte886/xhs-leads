import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// 这些组件只依赖少量 store 能力与设置类接口，全部替换成桩，避免测试依赖真实后端
vi.mock('../store', () => ({
  toast: vi.fn(),
  refreshSysStatus: vi.fn(),
  appState: { params: { fast: false, interval: 1000 } },
}))
vi.mock('../api', () => ({
  apiGetLLMConfig: vi.fn(async () => ({ configured: false })),
  apiSetLLMConfig: vi.fn(async () => ({ configured: true })),
  apiClearLLMConfig: vi.fn(async () => ({ configured: false })),
  apiTestLLM: vi.fn(async () => ({ ok: true })),
  apiGetVisionConfig: vi.fn(async () => ({ configured: false })),
  apiSetVisionConfig: vi.fn(async () => ({ configured: true })),
  apiClearVisionConfig: vi.fn(async () => ({ configured: false })),
  apiTestVision: vi.fn(async () => ({ ok: true })),
  apiGetImageConfig: vi.fn(async () => ({ configured: false })),
  apiSetImageConfig: vi.fn(async () => ({ configured: true })),
  apiClearImageConfig: vi.fn(async () => ({ configured: false })),
  apiTestImageConfig: vi.fn(async () => ({ ok: true })),
  apiLicenseStatus: vi.fn(async () => ({
    status: { mode: 'free', features: ['采集'], machine_fingerprint: 'FINGERPRINT123456', has_activation: false },
  })),
  apiLicenseActivate: vi.fn(async () => ({ ok: true, status: {} })),
  apiMirrorOpen: vi.fn(async () => ({ ok: true })),
  apiMirrorClose: vi.fn(async () => ({ ok: true })),
  apiListAccounts: vi.fn(async () => ({ accounts: [], port_map: {} })),
  apiAddAccount: vi.fn(async () => ({ ok: true })),
  apiDeleteAccount: vi.fn(async () => ({ ok: true })),
  apiRenameAccount: vi.fn(async () => ({ ok: true })),
  apiRefreshIdentity: vi.fn(async () => ({ ok: true })),
  apiScanLogin: vi.fn(async () => ({ ok: true })),
}))

import SettingsPanel from './SettingsPanel.vue'
import AccountSettings from './settings/AccountSettings.vue'
import CollectSettings from './settings/CollectSettings.vue'
import AboutSettings from './settings/AboutSettings.vue'
import AiSettings from './settings/AiSettings.vue'

describe('设置页分区拆分', () => {
  it('默认只挂 AI 分区，其余三个分类置灰不可点', async () => {
    const w = mount(SettingsPanel)
    await flushPromises()
    const navs = w.findAll('.nav-item')
    expect(navs).toHaveLength(4)
    expect(navs[0].attributes('disabled')).toBeUndefined()
    expect(navs.slice(1).every((n) => n.attributes('disabled') !== undefined)).toBe(true)

    expect(w.text()).toContain('生成文章')
    expect(w.text()).toContain('分析图片')
    expect(w.text()).toContain('生成图片')
    expect(w.text()).not.toContain('账号管理')

    // 点了置灰项也不会切到账号分区
    await navs[1].trigger('click')
    await flushPromises()
    expect(w.text()).not.toContain('账号管理')
  })

  it('AI 分区三张能力卡各自带供应商/Key/模型与操作按钮', async () => {
    const w = mount(AiSettings)
    await flushPromises()
    expect(w.findAll('.ai-block')).toHaveLength(3)
    expect(w.findAll('select')).toHaveLength(3)
    expect(w.text()).toContain('未配置')
    expect(w.text()).toContain('测试连接')
    const providers = w.findAll('select')[0].findAll('option').map((o) => o.text())
    // 文本能力只列文本模型供应商（DeepSeek / 阿里云百炼 / 自定义）
    expect(providers).toContain('DeepSeek')
    expect(providers).not.toContain('Google · Nano Banana')
  })

  it('账号分区列出账号管理并给出新增入口', async () => {
    const w = mount(AccountSettings)
    await flushPromises()
    expect(w.text()).toContain('账号管理（0）')
    expect(w.text()).toContain('新增账号')
  })

  it('采集分区显示请求间隔与随机区间', async () => {
    const w = mount(CollectSettings)
    expect(w.text()).toContain('全局请求间隔')
    expect(w.text()).toContain('实际每次 1.0–1.8 秒（随机）')
  })

  it('授权分区显示免费版与设备指纹', async () => {
    const w = mount(AboutSettings)
    await flushPromises()
    expect(w.text()).toContain('免费版')
    expect(w.text()).toContain('FINGERPRINT1')
  })
})
