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
    ['apiMirrorOpen', '/api/mirror/open', 'POST', () => api.apiMirrorOpen()],
    ['apiMirrorClose', '/api/mirror/close', 'POST', () => api.apiMirrorClose('acc_a')],
    ['apiListAccounts', '/api/accounts', 'GET', () => api.apiListAccounts()],
    ['apiAddAccount', '/api/accounts', 'POST', () => api.apiAddAccount()],
    ['apiDeleteAccount', '/api/accounts/acc_a', 'DELETE', () => api.apiDeleteAccount('acc_a')],
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
  it('apiAiGenerate：action+note 进 body', async () => {
    let u = ''; let body: any = null
    mockFetch(async (uu, init) => { u = String(uu); body = JSON.parse(init!.body as string); return okRes({ ok: true }) })
    await api.apiAiGenerate('改写', { a: 'b' })
    expect(u).toBe('/api/ai_generate')
    expect(body).toMatchObject({ action: '改写', note: { a: 'b' } })
  })
})

describe('账号管理 API 契约', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('apiAddAccount：POST body 带 account_id 与 role', async () => {
    let body: any = null
    mockFetch(async (_u, init) => {
      body = JSON.parse(init!.body as string)
      return okRes({ ok: true, account_id: 'acc_a' })
    })
    const r = await api.apiAddAccount('acc_a', 'mixed')
    expect(body).toEqual({ account_id: 'acc_a', role: 'mixed' })
    expect(r.account_id).toBe('acc_a')
  })

  it('apiAddAccount：留空时 account_id 为空字符串（后端自动生成）', async () => {
    let body: any = null
    mockFetch(async (_u, init) => {
      body = JSON.parse(init!.body as string)
      return okRes({ ok: true })
    })
    await api.apiAddAccount()
    expect(body).toEqual({ account_id: '', role: 'mixed' })
  })

  it('apiDeleteAccount：account_id 进路径且编码', async () => {
    let u = ''
    mockFetch(async (uu) => { u = String(uu); return okRes({ ok: true }) })
    await api.apiDeleteAccount('my-acc_1')
    expect(u).toBe('/api/accounts/my-acc_1')
  })

  it('apiMirrorClose：account_id 进 query 且编码', async () => {
    let u = ''
    mockFetch(async (uu) => { u = String(uu); return okRes({ ok: true }) })
    await api.apiMirrorClose('a b')
    expect(u).toContain('/api/mirror/close?account_id=a%20b')
  })

  it('apiListAccounts：解包 accounts 与 port_map', async () => {
    mockFetch(async () => okRes({
      accounts: [{ account_id: 'default', running: true, headless: false }],
      port_map: { default: 9223 },
      active: 'default',
    }))
    const r = await api.apiListAccounts()
    expect(r.accounts).toHaveLength(1)
    expect(r.port_map.default).toBe(9223)
    expect(r.active).toBe('default')
  })
})
