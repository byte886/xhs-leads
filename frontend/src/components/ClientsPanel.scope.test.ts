// 采集客户弹层：默认按篇，切「全部」看跨次累积；清空需两次确认
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

const list = vi.fn()
const clear = vi.fn()
vi.mock('../api', () => ({
  apiClientsList: (...a: unknown[]) => list(...a),
  apiClientsClear: (...a: unknown[]) => clear(...a),
  apiContactOpen: vi.fn(async () => ({ ok: true })),
}))

import ClientsPanel from './ClientsPanel.vue'

beforeEach(() => {
  list.mockReset()
  clear.mockReset()
  list.mockResolvedValue({
    total: 2,
    clients: [
      { uid: 'u1', nickname: '甲', comments: ['多少钱'], intent: 'high', comment_count: 1, note_count: 1 },
      { uid: 'u2', nickname: '乙', comments: ['求链接'], intent: 'low', comment_count: 1, note_count: 1 },
    ],
  })
})

describe('ClientsPanel 口径切换', () => {
  it('默认按篇查询（带 feedId）', async () => {
    mount(ClientsPanel, { props: { feedId: 'f1' } })
    await new Promise((r) => setTimeout(r, 0))
    expect(list).toHaveBeenCalledWith('f1', '')
    expect(list.mock.calls.every((c) => c[0] === 'f1')).toBe(true)
  })

  it('切到「全部」不带 feedId，并出现清空入口', async () => {
    const w = mount(ClientsPanel, { props: { feedId: 'f1' } })
    await new Promise((r) => setTimeout(r, 0))
    await w.findAll('.cl-scope-btn')[1].trigger('click')
    await new Promise((r) => setTimeout(r, 0))
    expect(list).toHaveBeenLastCalledWith('', '')
    expect(w.find('.cl-clear').exists()).toBe(true)
  })

  it('清空按钮第一次点击只进入确认态', async () => {
    const w = mount(ClientsPanel, { props: { feedId: 'f1' } })
    await new Promise((r) => setTimeout(r, 0))
    await w.findAll('.cl-scope-btn')[1].trigger('click')
    await w.find('.cl-clear').trigger('click')
    expect(w.find('.cl-clear').text()).toContain('再点一次确认清空')
    expect(clear).not.toHaveBeenCalled()
    await w.find('.cl-clear').trigger('click')
    expect(clear).toHaveBeenCalledTimes(1)
  })

  it('只传本篇时不显示口径切换条', async () => {
    const w = mount(ClientsPanel, { props: {} })
    await new Promise((r) => setTimeout(r, 0))
    expect(w.find('.cl-scope').exists()).toBe(false)
    expect(list).toHaveBeenCalledWith('', '')
  })
})
