// 填入小红书发布页：标题/正文/图片齐了才可用（拉取期间与缺字段都拦在前面）
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

const clone = vi.fn()
vi.mock('../api', () => ({
  apiClone: (...a: unknown[]) => clone(...a),
  apiFillXhs: vi.fn(async () => ({ ok: true, note: 'ok' })),
  apiUploadImage: vi.fn(async () => ({ url: '/images/x.png' })),
  apiAiGenerate: vi.fn(async () => ({ result: '' })),
  apiGetLLMConfig: vi.fn(async () => ({ configured: true })),
}))

import PublishPage from './PublishPage.vue'

const flush = async () => { await new Promise((r) => setTimeout(r, 0)); await new Promise((r) => setTimeout(r, 0)) }

beforeEach(() => { clone.mockReset() })

function btn(w: ReturnType<typeof mount>) {
  return w.find('.btn-pub')
}

describe('PublishPage 填入就绪判定', () => {
  it('克隆载荷未返回时按钮不可用，并提示正在加载', async () => {
    let release: (v: unknown) => void = () => {}
    clone.mockReturnValue(new Promise((r) => { release = r }))
    const w = mount(PublishPage)
    await (w.vm as any).openFor({ feed_id: 'f1', title: '标题', desc: '正文内容' })
    await flush()
    expect(btn(w).attributes('disabled')).toBeDefined()
    expect(btn(w).text()).toBe('加载中…')
    expect(btn(w).attributes('title')).toContain('正在加载')
    release({ title: '标题', desc: '正文内容', images_local: ['/images/a.jpg'] })
    await flush()
    expect(btn(w).attributes('disabled')).toBeUndefined()
    expect(btn(w).text()).toBe('填入小红书发布页')
    expect(w.find('.fill-hint').exists()).toBe(false)
  })

  it('克隆回来但没有图片时仍不可用，提示还缺图片', async () => {
    clone.mockResolvedValue({ title: '标题', desc: '正文内容', images_local: [] })
    const w = mount(PublishPage)
    await (w.vm as any).openFor({ feed_id: 'f2', title: '标题', desc: '正文内容' })
    await flush()
    expect(btn(w).attributes('disabled')).toBeDefined()
    expect(btn(w).text()).toBe('缺图片')
  })

  it('标题或正文为空也不可用', async () => {
    clone.mockResolvedValue({ title: '', desc: '', images_local: ['/images/a.jpg'] })
    const w = mount(PublishPage)
    await (w.vm as any).openFor({ feed_id: 'f3', title: '', desc: '' })
    await flush()
    expect(btn(w).attributes('disabled')).toBeDefined()
    expect(btn(w).text()).toBe('缺标题、正文')
    expect(btn(w).attributes('title')).toContain('标题')
  })

  it('载荷自带完整图片时立即可用（不等待后台克隆）', async () => {
    clone.mockReturnValue(new Promise(() => {}))
    const w = mount(PublishPage)
    await (w.vm as any).openFor({ feed_id: 'f4', title: '标题', desc: '正文', images: ['/images/a.jpg'] })
    await flush()
    expect(btn(w).attributes('disabled')).toBeUndefined()
  })
})
