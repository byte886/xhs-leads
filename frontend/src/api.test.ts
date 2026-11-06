import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiLoginBegin, apiLoginStatus } from './api'

// R-6.36d：api 层 fetch 封装守护（超时保护 / 错误 detail 提取 / POST 契约）。
function mockFetch(impl: typeof fetch) {
  vi.stubGlobal('fetch', impl)
}
afterEach(() => { vi.unstubAllGlobals() })

describe('apiLoginStatus', () => {
  it('ok 时解包 json', async () => {
    mockFetch(async () => new Response(JSON.stringify({ logged_in: true, nickname: 'x' }), { status: 200 }))
    const r = await apiLoginStatus()
    expect(r.logged_in).toBe(true)
  })
  it('非 2xx 且 body.detail 存在 → 抛 detail 文案', async () => {
    mockFetch(async () => new Response(JSON.stringify({ detail: '登录已过期' }), { status: 401 }))
    await expect(apiLoginStatus()).rejects.toThrow('登录已过期')
  })
  it('非 2xx 且无 detail → 抛 status 行', async () => {
    mockFetch(async () => new Response('oops', { status: 500, statusText: 'ISE' }))
    await expect(apiLoginStatus()).rejects.toThrow('500 ISE')
  })
})

describe('apiLoginBegin', () => {
  it('POST /api/auth/login_begin 带 force 体', async () => {
    let captured: any = null
    mockFetch(async (_url, init) => { captured = init; return new Response(JSON.stringify({ ok: true }), { status: 200 }) })
    await apiLoginBegin(true)
    expect(captured.method).toBe('POST')
    expect(JSON.parse(captured.body)).toEqual({ force: true })
  })
})

// R-6.36d 表驱动：全部端点 URL/方法契约守护（mock fetch，断言解包 json）。
import * as api from './api'

function okRes(body: any) {
  return new Response(JSON.stringify(body), { status: 200 })
}

describe('api 端点契约（表驱动）', () => {
  afterEach(() => { vi.unstubAllGlobals() })
  const cases: Array<[string, string, string, () => Promise<unknown> | unknown]> = [
    ['apiLoginCancel', '/api/auth/login_cancel', 'POST', () => api.apiLoginCancel()],
    ['apiCollapseDebugWindows', '/api/auth/collapse_debug_windows', 'POST', () => api.apiCollapseDebugWindows()],
    ['apiLicenseStatus', '/api/license_status', 'GET', () => api.apiLicenseStatus()],
    ['apiNotes', '/api/notes?keyword=', 'GET', () => api.apiNotes('翡翠')],
    ['apiAuthors', '/api/authors', 'GET', () => api.apiAuthors()],
    ['apiClientsList', '/api/clients_list', 'GET', () => api.apiClientsList()],
    ['apiClientsAgg', '/api/clients_agg', 'GET', () => api.apiClientsAgg()],
    ['apiClone', '/api/clone', 'POST', () => api.apiClone('f1')],
    ['apiLogs', '/api/publish_logs', 'GET', () => api.apiLogs()],
    ['apiQrcode', '/api/qrcode', 'GET', () => api.apiQrcode()],
    ['apiMirrorOpen', '/api/mirror/open', 'POST', () => api.apiMirrorOpen()],
    ['apiGetAppConfig', '/api/app_config', 'GET', () => api.apiGetAppConfig()],
    ['apiStatusNow', '/api/status_now', 'GET', () => api.apiStatusNow()],
    ['apiCleanupImages', '/api/cleanup_images', 'POST', () => api.apiCleanupImages()],
    ['apiDrafts', '/api/drafts', 'GET', () => api.apiDrafts()],
    ['apiDraftDelete', '/api/draft_delete', 'POST', () => api.apiDraftDelete(3)],
    ['apiGetLLMConfig', '/api/llm_config', 'GET', () => api.apiGetLLMConfig()],
    ['apiClearLLMConfig', '/api/llm_config/clear', 'POST', () => api.apiClearLLMConfig()],
  ]
  it.each(cases)('%s 命中 %s', async (_name, url, method, fn) => {
    let captured: any = null
    mockFetch(async (u, init) => { captured = { u: String(u), method: init?.method || 'GET' }; return okRes({ ok: true }) })
    await fn()
    expect(captured.method).toBe(method)
    expect(captured.u).toContain(url)
  })

  it('apiMirrorScreenshotUrl 纯函数拼 URL', () => {
    expect(api.apiMirrorScreenshotUrl('default')).toContain('/api/mirror/screenshot')
  })

  it('apiUploadImage 走 multipart FormData', async () => {
    let captured: any = null
    mockFetch(async (_u, init) => { captured = init; return okRes({ ok: true }) })
    const f = new File(['x'], 'a.jpg', { type: 'image/jpeg' })
    await api.apiUploadImage(f)
    expect(captured.method).toBe('POST')
    expect(captured.body).toBeInstanceOf(FormData)
  })
})

describe('api 请求体映射契约', () => {
  afterEach(() => { vi.unstubAllGlobals() })
  it('apiCollectStart：TS camelCase → snake_case 全字段映射 + fast 布尔化', async () => {
    let body: any = null
    mockFetch(async (_u, init) => { body = JSON.parse(init!.body as string); return okRes({ ok: true }) })
    await api.apiCollectStart({ keyword: '翡翠', limit: 20, sort: '最新', noteType: '视频', timeRange: '一天', scope: '本地', distance: '市内', interval: 500, fast: 1 } as any)
    expect(body).toMatchObject({ keyword: '翡翠', limit: 20, note_type: '视频', time_range: '一天', scope: '本地', distance: '市内', interval: 500, fast: true })
  })
  it('apiCollectProgress：task_id 进 query', async () => {
    let u = ''
    mockFetch(async (uu) => { u = String(uu); return okRes({ ok: true }) })
    await api.apiCollectProgress('t-1')
    expect(u).toContain('/api/collect_progress?task_id=t-1')
  })
  it('apiCollectStop：body 带 task_id', async () => {
    let body: any = null
    mockFetch(async (_u, init) => { body = JSON.parse(init!.body as string); return okRes({ ok: true }) })
    await api.apiCollectStop('t-1')
    expect(body).toEqual({ task_id: 't-1' })
  })
  it('apiSearch：POST /api/search，body 含关键字段', async () => {
    let u = ''; let body: any = null
    mockFetch(async (uu, init) => { u = String(uu); body = JSON.parse(init!.body as string); return okRes({ ok: true }) })
    await api.apiSearch({ keyword: '翡翠', limit: 10, sort: '综合', noteType: '全部', interval: 1000, fast: false } as any)
    expect(u).toBe('/api/search')
    expect(body).toMatchObject({ keyword: '翡翠', note_type: '全部', fast: false })
  })
  it('apiPublish：payload 原样透传', async () => {
    let body: any = null
    mockFetch(async (_u, init) => { body = JSON.parse(init!.body as string); return okRes({ ok: true }) })
    await api.apiPublish({ title: 't', desc: 'd', tags: ['a'], images: ['i'], visible: 'self' })
    expect(body).toMatchObject({ title: 't', visible: 'self' })
  })
  it('apiMirrorEvent：account 默认 default 并展开进 body', async () => {
    let body: any = null
    mockFetch(async (_u, init) => { body = JSON.parse(init!.body as string); return okRes({ ok: true }) })
    await api.apiMirrorEvent({ type: 'move', x: 1, y: 2 } as any)
    expect(body).toMatchObject({ account: 'default', type: 'move', x: 1 })
  })
  it('apiAiGenerate：action+note 进 body', async () => {
    let u = ''; let body: any = null
    mockFetch(async (uu, init) => { u = String(uu); body = JSON.parse(init!.body as string); return okRes({ ok: true }) })
    await api.apiAiGenerate('改写', { a: 'b' })
    expect(u).toBe('/api/ai_generate')
    expect(body).toMatchObject({ action: '改写', note: { a: 'b' } })
  })
})
