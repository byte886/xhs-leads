// FlowBar 组件测试（@vue/test-utils）：4-Tab 工作流导航
import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import FlowBar from './FlowBar.vue'
import { appState, setTab } from '../store'

beforeEach(() => {
  appState.tab = 'collect'
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

  it('当前 Tab 带 cur class', async () => {
    const w = mount(FlowBar)
    setTab('create')
    await w.vm.$nextTick()
    const cur = w.find('.tab.cur')
    expect(cur.text()).toContain('创作')
  })

  it('不再渲染登录/AI Key 状态徽标', () => {
    const w = mount(FlowBar)
    expect(w.findAll('.sysbar')).toHaveLength(0)
    expect(w.findAll('.sys-item')).toHaveLength(0)
  })
})
