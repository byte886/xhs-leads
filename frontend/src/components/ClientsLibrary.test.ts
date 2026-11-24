// 客户库 Tab：累积口径文案 + 清空需两次点击确认（防误删历史线索）
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

const apiClientsClear = vi.fn()
vi.mock('../api', () => ({
  apiClientsList: vi.fn(async () => ({
    total: 3,
    clients: [{ uid: 'u1', nickname: '甲', comments: ['多少钱'], intent: 'high', comment_count: 1, note_count: 1 }],
  })),
  apiClientsClear: (...a: unknown[]) => apiClientsClear(...a),
  apiContactOpen: vi.fn(async () => ({ ok: true })),
}))

import ClientsLibrary from './ClientsLibrary.vue'

beforeEach(() => { apiClientsClear.mockReset() })

describe('ClientsLibrary', () => {
  it('展示客户库标题与累积口径', async () => {
    const w = mount(ClientsLibrary)
    await w.vm.$nextTick()
    expect(w.text()).toContain('客户库')
    expect(w.text()).toContain('跨次采集累积')
  })

  it('第一次点击只进入确认态，不真清空', async () => {
    const w = mount(ClientsLibrary)
    await w.find('.lib-clear').trigger('click')
    expect(w.find('.lib-clear').text()).toContain('再点一次确认清空')
    expect(apiClientsClear).not.toHaveBeenCalled()
  })

  it('第二次点击才调用清空接口', async () => {
    apiClientsClear.mockResolvedValue({ ok: true, removed: 3 })
    const w = mount(ClientsLibrary)
    await w.find('.lib-clear').trigger('click')
    await w.find('.lib-clear').trigger('click')
    expect(apiClientsClear).toHaveBeenCalledTimes(1)
  })
})
