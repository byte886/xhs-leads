// R-6.37e：组件挂载测试（@vue/test-utils）——FlowBar 4-Tab 导航与状态提示
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import FlowBar from './FlowBar.vue'
import { appState, setTab } from '../store'

vi.mock('../api', () => ({
  apiLoginStatus: vi.fn().mockResolvedValue({ logged_in: true, nickname: '阿汤', kicked: false }),
  apiGetLLMConfig: vi.fn().mockResolvedValue({ configured: true }),
}))

beforeEach(() => {
  appState.tab = 'collect'
  appState.login = { logged_in: false, nickname: '', kicked: false }
})

describe('FlowBar', () => {
  it('渲染 4 个工作流 Tab（采集/创作/客户/设置）', () => {
    const w = mount(FlowBar)
    const labels = w.findAll('.tab').map(b => b.text())
    expect(labels.join()).toContain('采集')
    expect(labels.join()).toContain('创作')
    expect(labels.join()).toContain('客户')
    expect(labels.join()).toContain('设置')
  })

  it('点击 Tab 切换 appState.tab', async () => {
    const w = mount(FlowBar)
    const tabs = w.findAll('.tab')
    await tabs[2].trigger('click')
    expect(appState.tab).toBe('clients')
  })

  it('当前 Tab 带 cur class 且 hint 随 Tab 切换', async () => {
    const w = mount(FlowBar)
    setTab('create')
    await w.vm.$nextTick()
    const cur = w.find('.tab.cur')
    expect(cur.text()).toContain('创作')
    expect(w.find('.flow-hint').text()).toContain('克隆')
  })

  it('挂载后拉取登录态写入 appState.login', async () => {
    mount(FlowBar)
    await new Promise(r => setTimeout(r, 10))
    expect(appState.login.logged_in).toBe(true)
    expect(appState.login.nickname).toBe('阿汤')
  })
})
