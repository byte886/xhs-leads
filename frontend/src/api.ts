import type { Author, ClientUser, ClientsAgg, ClonePayload, LicenseStatus, LoginStatus, LogItem, Note, SearchParams, SearchResult } from './types'

const BASE = ''

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) msg = String(body.detail)
    } catch { /* keep default */ }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

export async function apiLoginStatus(): Promise<LoginStatus> {
  return j(await fetch(`${BASE}/api/login_status`))
}

export async function apiLicenseStatus(): Promise<{ ok: boolean; status: LicenseStatus }> {
  return j(await fetch(`${BASE}/api/license_status`))
}

export async function apiLicenseActivate(key: string): Promise<{ ok: boolean; note?: string; status?: LicenseStatus }> {
  return j(await fetch(`${BASE}/api/license_activate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key }),
  }))
}

export async function apiCollectStart(p: SearchParams): Promise<{ ok: boolean; task_id: string; keyword: string; limit: number }> {
  const body = {
    keyword: p.keyword, limit: p.limit, sort: p.sort, note_type: p.noteType,
    time_range: p.timeRange, scope: p.scope, distance: p.distance,
    interval: p.interval, fast: !!p.fast,
  }
  return j(await fetch(`${BASE}/api/collect_start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }))
}

export async function apiCollectProgress(taskId: string): Promise<{
  ok: boolean; status: string; keyword: string; limit: number; collected: number; note: string
}> {
  return j(await fetch(`${BASE}/api/collect_progress?task_id=${encodeURIComponent(taskId)}`))
}

export async function apiCollectStop(taskId: string): Promise<{ ok: boolean; note: string }> {
  return j(await fetch(`${BASE}/api/collect_stop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task_id: taskId })
  }))
}

export async function apiSearch(p: SearchParams): Promise<SearchResult> {
  const body = { keyword: p.keyword, limit: p.limit, sort: p.sort, note_type: p.noteType, interval: p.interval, fast: !!p.fast }
  // 90s 超时：平台 AI 搜索页渲染偶发慢/卡，超时后前端自动恢复按钮并提示，避免无限"搜索中"
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), 90000)
  try {
    return await j(await fetch(`${BASE}/api/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: ctl.signal
    }))
  } finally {
    clearTimeout(timer)
  }
}

export async function apiNotes(keyword = '', order = 'score'): Promise<{ total: number; notes: Note[] }> {
  const q = new URLSearchParams()
  if (keyword) q.set('keyword', keyword)
  if (order) q.set('order', order)
  return j(await fetch(`${BASE}/api/notes?${q.toString()}`))
}

export async function apiAuthors(): Promise<{ total: number; total_notes: number; authors: Author[] }> {
  return j(await fetch(`${BASE}/api/authors`))
}

export async function apiClientsList(feedId = '', intent = ''): Promise<{ total: number; clients: ClientUser[] }> {
  const p = new URLSearchParams()
  if (feedId) p.set('feed_id', feedId)
  if (intent) p.set('intent', intent)
  const q = p.toString()
  return j(await fetch(`${BASE}/api/clients_list${q ? `?${q}` : ''}`))
}

export async function apiClientsAgg(): Promise<ClientsAgg> {
  return j(await fetch(`${BASE}/api/clients_agg`))
}

export async function apiClone(feedId: string): Promise<ClonePayload> {
  return j(await fetch(`${BASE}/api/clone`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ feed_id: feedId })
  }))
}

export async function apiPublish(payload: {
  title: string
  desc: string
  tags: string[]
  images: string[]
  visible: string
  account?: string
}): Promise<{ ok: boolean; note?: string; [k: string]: unknown }> {
  return j(await fetch(`${BASE}/api/publish`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }))
}

export async function apiSaveDraft(payload: {
  title: string
  desc: string
  tags: string[]
  images: string[]
  visible: string
  feed_id?: string
}): Promise<{ ok: boolean; note?: string; [k: string]: unknown }> {
  return j(await fetch(`${BASE}/api/save_draft`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }))
}

export async function apiLogs(): Promise<LogItem[]> {
  const d = await j<{ logs: LogItem[] }>(await fetch(`${BASE}/api/logs`))
  return d.logs || []
}

export async function apiPublishStats(logId: number, noteUrl: string): Promise<{ ok: boolean; stats?: Record<string, string>; note?: string }> {
  return j(await fetch(`${BASE}/api/publish_stats`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ log_id: logId, note_url: noteUrl })
  }))
}

export async function apiImportCookie(): Promise<{ ok: boolean; count?: number; account?: string; error?: string }> {
  return j(await fetch(`${BASE}/api/import_cookie`, { method: 'POST' }))
}

export interface ImportCookieResult { ok: boolean; valid?: boolean; count?: number; account?: string; error?: string; note?: string; need_auth?: boolean }

export async function apiImportCookieText(text: string): Promise<ImportCookieResult> {
  return j(await fetch(`${BASE}/api/import_cookie`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cookie_text: text })
  }))
}

/** 方案 A：直接读日常 Chrome 的 Cookie 库（钥匙串解密），无需手动复制粘贴 */
export async function apiRefreshCookieFromChrome(): Promise<ImportCookieResult> {
  return j(await fetch(`${BASE}/api/refresh_cookie_from_chrome`, { method: 'POST' }))
}

export interface CookieItem { name: string; value: string; domain: string }

export async function apiCookieInfo(): Promise<{ ok: boolean; count: number; saved_at: string; cookies: CookieItem[] }> {
  return j(await fetch(`${BASE}/api/cookie_info`))
}

export async function apiQrcode(): Promise<{ ok: boolean; guide?: string; url?: string; expires_in?: number; error?: string }> {
  return j(await fetch(`${BASE}/api/qrcode`))
}

/** 启动时清理无引用本地图片（与后端 startup 双保险，防旧图堆积） */
export async function apiCleanupImages(): Promise<{ ok: boolean; removed: number }> {
  return j(await fetch(`${BASE}/api/cleanup_images`, { method: 'POST' }))
}

/** 发布页追加图片：上传本地文件到后端 images 目录，返回代理路径 */
export async function apiUploadImage(file: File): Promise<{ ok: boolean; path?: string; note?: string }> {
  const fd = new FormData()
  fd.append('file', file)
  return j(await fetch(`${BASE}/api/upload_image`, { method: 'POST', body: fd }))
}

export interface DraftItem {
  id: number
  feed_id: string
  title: string
  desc: string
  tags: string[]
  visibility: string
  images: string[]
  created_at: string
}

export async function apiDrafts(): Promise<{ ok: boolean; total: number; drafts: DraftItem[] }> {
  return j(await fetch(`${BASE}/api/drafts`))
}

export async function apiDraftDelete(id: number): Promise<{ ok: boolean; note?: string }> {
  return j(await fetch(`${BASE}/api/draft_delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id }),
  }))
}

export interface CdpStatus {
  ok: boolean
  port: number
  port_ready: boolean
  chrome_running: boolean
  chrome_found: boolean
  state: string
  chrome_path: string
  profile_dir: string
}

export async function apiCdpStatus(): Promise<CdpStatus> {
  return j(await fetch(`${BASE}/api/cdp_status`))
}

export async function apiCdpSetup(restart = false): Promise<{ ok: boolean; status?: string; note?: string; need_restart?: boolean }> {
  return j(await fetch(`${BASE}/api/cdp_setup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ restart }),
  }))
}

export async function apiCdpOpenInspect(): Promise<{ ok: boolean; note?: string }> {
  return j(await fetch(`${BASE}/api/cdp_open_inspect`, { method: 'POST' }))
}

export async function apiCdpPickChrome(): Promise<{ ok: boolean; chrome_path?: string; note?: string }> {
  return j(await fetch(`${BASE}/api/cdp_pick_chrome`, { method: 'POST' }))
}

// ---- O1 AI 模块（Key 门控） ----
export interface LlmConfig {
  configured: boolean
  openai_base_url: string
  default_model: string
  llm_api_key_masked: string
  updated_at: string
}

export interface LlmConfigInput {
  api_key: string
  openai_base_url: string
  default_model: string
}

export async function apiGetLLMConfig(): Promise<LlmConfig> {
  return j(await fetch(`${BASE}/api/llm_config`))
}

export async function apiSetLLMConfig(p: LlmConfigInput): Promise<LlmConfig> {
  return j(await fetch(`${BASE}/api/llm_config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(p),
  }))
}

export async function apiClearLLMConfig(): Promise<LlmConfig> {
  return j(await fetch(`${BASE}/api/llm_config/clear`, { method: 'POST' }))
}

export async function apiTestLLM(p: LlmConfigInput): Promise<{ ok: boolean; error?: string; count?: number; models?: string[]; model_available?: boolean | null }> {
  return j(await fetch(`${BASE}/api/llm_test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(p),
  }))
}

export async function apiAiGenerate(action: string, note: Record<string, string>): Promise<{ ok: boolean; action: string; result: string }> {
  return j(await fetch(`${BASE}/api/ai_generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, note }),
  }))
}
