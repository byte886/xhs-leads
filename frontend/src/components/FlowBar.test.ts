// FlowBar 组件测试（@vue/test-utils）：4 个平级功能选项卡
import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import FlowBar from './FlowBar.vue'
import { appState, setTab } from '../store'

beforeEach(() => {
  appState.tab = 'collect'
})

describe('FlowBar', () => {
  it('渲染 4 个功能 Tab（采集/客户/档案/设置）', () => {
    const w = mount(FlowBar)
    const labels = w.findAll('.tab').map(b => b.text())
    expect(labels).toHaveLength(4)
    expect(labels.join()).toContain('采集')
    expect(labels.join()).toContain('客户')
    expect(labels.join()).toContain('档案')
    expect(labels.join()).toContain('设置')
  })

  it('选项卡不带序号与箭头（不是流程步骤）', () => {
    const w = mount(FlowBar)
    const labels = w.findAll('.tab').map(b => b.text())
    expect(labels.join()).not.toMatch(/[1-9]/)
    expect(w.findAll('.dot')).toHaveLength(0)
    expect(w.findAll('.arrow')).toHaveLength(0)
  })

  it('点击 Tab 切换 appState.tab', async () => {
    const w = mount(FlowBar)
    const tabs = w.findAll('.tab')
    await tabs[1].trigger('click')
    expect(appState.tab).toBe('clients')
  })

  it('当前 Tab 带 cur class', async () => {
    const w = mount(FlowBar)
    setTab('clients')
    await w.vm.$nextTick()
    const cur = w.find('.tab.cur')
    expect(cur.text()).toContain('客户')
  })

  it('不再渲染登录/AI Key 状态徽标', () => {
    const w = mount(FlowBar)
    expect(w.findAll('.sysbar')).toHaveLength(0)
    expect(w.findAll('.sys-item')).toHaveLength(0)
  })
})
