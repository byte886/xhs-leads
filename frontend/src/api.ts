import type { Author, ClientUser, ClientsAgg, ClonePayload, LicenseStatus, LoginStatus, LogItem, Note, SearchParams } from './types'

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

export async function apiLoginStatus(timeoutMs = 8000): Promise<LoginStatus> {
  // 超时保护：后端/引擎启动慢时不让 fetch 无限挂起（挂起会导致 App.vue onMounted
  // 卡在 await、强制登录弹窗永远不弹）。超时抛错，由调用方决定重试/兜底。
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), timeoutMs)
  try {
    return await j(await fetch(`${BASE}/api/login_status`, { signal: ctl.signal }))
  } finally {
    clearTimeout(timer)
  }
}

// R-6.5：请求打开真实登录页（壳窗口导航由 launcher 登录守卫执行，登录后 Cookie 注入引擎自动返回）
// 登录 UI 改版：force=true 强制登录（启动未登录/被踢、采集异常被踢、APP 强制登录对话框「去登录」）
// ——登录页无叉不可关闭；force=false 主动重登（设置页「重新登录」）——登录页带叉可关闭回 APP。
export async function apiLoginBegin(force = false): Promise<{ ok: boolean; already_logged_in?: boolean; force?: boolean }> {
  return j(await fetch(`${BASE}/api/auth/login_begin`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ force }),
  }))
}

export async function apiLoginCancel(): Promise<{ ok: boolean }> {
  return j(await fetch(`${BASE}/api/auth/login_cancel`, { method: 'POST' }))
}

// R-6.22g：登录引导弹出时收起采集调试窗（右窗关闭），壳窗保留——「需要登录时右侧
// 要么关闭要么有提示」用户约定。登录完成后再点采集，后端按登录态恢复调试窗。
export async function apiCollapseDebugWindows(): Promise<{ ok: boolean; closed?: number }> {
  return j(await fetch(`${BASE}/api/auth/collapse_debug_windows`, { method: 'POST' }))
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

export async function apiCollectClear(): Promise<{ ok: boolean; images_removed: number }> {
  // 清空本批采集数据（笔记 + 客户 + 采集图片）。破坏性动作，由调用方显式触发。
  return j(await fetch(`${BASE}/api/collect_clear`, { method: 'POST' }))
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
  const d = await j<{ logs: LogItem[] }>(await fetch(`${BASE}/api/publish_logs`))
  return d.logs || []
}

export async function apiPublishStats(logId: number, noteUrl: string): Promise<{ ok: boolean; stats?: Record<string, string>; note?: string }> {
  return j(await fetch(`${BASE}/api/publish_stats`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ log_id: logId, note_url: noteUrl })
  }))
}

/** 刷新数据：去「我」的主页按标题找到那条笔记，点开抓一次（全程点击，不拼 URL）。 */
export async function apiPublishStatsRefresh(logId: number): Promise<{ ok: boolean; stats?: Record<string, string>; detail?: string }> {
  return j(await fetch(`${BASE}/api/publish_stats_refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ log_id: logId })
  }))
}

/** 不回粘贴：直接读右边引擎窗口当前页（用户刚发布完，页面就停在那条笔记上）。 */
export async function apiPublishLinkFromCurrent(logId: number): Promise<{ ok: boolean; stats?: Record<string, string>; detail?: string }> {
  return j(await fetch(`${BASE}/api/publish_link_from_current`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ log_id: logId })
  }))
}

// ---- R 批 v1.4：镜像层（登录小窗 / 调试视图共用 CDP 桥） ----
/** 登录小窗/调试视图打开：确保引擎打开小红书登录页（截图流/事件转发的目标页就绪） */
export async function apiMirrorOpen(account = 'default'): Promise<{ ok: boolean; account?: string }> {
  return j(await fetch(`${BASE}/api/mirror/open?account_id=${encodeURIComponent(account)}`, { method: 'POST' }))
}

/** 调试实时窗口开关（R-6.15）：on=引擎独立小窗实时可见操作；off=回无头（引擎重启生效） */
export async function apiMirrorWindow(on: boolean, account = 'default'): Promise<{ ok: boolean; window?: boolean; note?: string; error?: string }> {
  return j(await fetch(`${BASE}/api/mirror/window`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ on, account_id: account }),
  }))
}

/** R-6.18 双窗口设置：采集过程是否在右侧采集窗前置显示（collect_tab_visible） */
export async function apiGetAppConfig(): Promise<{ ok: boolean; collect_tab_visible?: boolean; error?: string }> {
  try {
    return await j(await fetch(`${BASE}/api/app_config`))
  } catch (e) {
    return { ok: false, error: (e as Error).message }
  }
}

export async function apiSetAppConfig(patch: { collect_tab_visible: boolean }): Promise<{ ok: boolean; saved?: string[]; error?: string }> {
  return j(await fetch(`${BASE}/api/app_config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  }))
}

export async function apiStatusNow(): Promise<{
  action: string; detail: string; collecting: boolean
  collected?: number; limit?: number; keyword?: string
  /** R-6.53z5：进行中任务 id —— 页面重载后据此接回轮询，按钮恢复「停止」 */
  task_id?: string
}> {
  return j(await fetch(`${BASE}/api/status_now`))
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
