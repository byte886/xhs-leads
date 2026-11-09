import { reactive, readonly, watch } from 'vue'
import type { Note, SearchParams, Stage } from './types'

const CONDITIONS_KEY = 'xhs_collect_conditions'
export const DEFAULT_PARAMS: SearchParams = {
  keyword: '',
  limit: 20,
  sort: '综合',
  noteType: '图文',
  timeRange: '不限',
  scope: '不限',
  distance: '不限',
  interval: 1000,
  fast: false
}

function loadStoredParams(): SearchParams {
  try {
    const raw = localStorage.getItem(CONDITIONS_KEY)
    if (!raw) return { ...DEFAULT_PARAMS }
    const parsed = JSON.parse(raw)
    return { ...DEFAULT_PARAMS, ...parsed }
  } catch { return { ...DEFAULT_PARAMS } }
}

function saveParams(p: SearchParams) {
  try { localStorage.setItem(CONDITIONS_KEY, JSON.stringify(p)) } catch { /* 隐私模式等写失败静默 */ }
}

/** 工作流 4-Tab（v1.5 对齐同类产品模式）：采集 → 创作 → 客户 → 设置 */
export type Tab = 'collect' | 'create' | 'clients' | 'settings'

/** 全局共享观察状态（顶部状态条：登录 / AI Key） */
export interface SysStatus {
  aiKey: boolean
}

/** 全局状态：单页一体式工作流 */
export const appState = reactive({
  tab: 'collect' as Tab,
  stage: 'input' as Stage,
  // 全局共享状态（跨页观察：登录态 / AI Key）
  sys: {
    aiKey: false,
  } as SysStatus,
  // 采集配置（启动时从 localStorage 恢复用户上次条件）
  params: loadStoredParams() as SearchParams,
  collecting: false,
  endedNeedLogin: false,
  // 搜索结果
  notes: [] as Note[],
  total: 0,
  count: 0,
  taskLabel: '',
  status: '搜索中',
  elapsed: 0,
  // 勾选
  selected: {} as Record<string, boolean>,
  login: { logged_in: false, nickname: '', kicked: false },
  // 登录态刷新信号（登录成功后 +1，采集面板据此重新拉登录状态）
  authRefreshTick: 0,
  // toast
  toastMsg: '',
  toastType: 'ok' as 'ok' | 'error',
})

let toastTimer: ReturnType<typeof setTimeout> | null = null

watch(() => appState.params, (p) => saveParams(p), { deep: true })
export function toast(msg: string, type: 'ok' | 'error' = 'ok') {
  appState.toastMsg = msg
  appState.toastType = type
  if (toastTimer) clearTimeout(toastTimer)
  if (type === 'ok') {
    toastTimer = setTimeout(() => { appState.toastMsg = '' }, 2400)
  }
}

export function setStage(s: Stage) {
  appState.stage = s
  if (s === 'search') appState.status = '搜索中'
}

/** 切换工作流 Tab；切到 create 时默认回到创作空态由调用方 openFor 载入 */
export function setTab(t: Tab) {
  appState.tab = t
}

/** 刷新全局共享状态（登录 / AI Key），由 FlowBar 状态条挂载时调用 */
export function refreshSysStatus(part: Partial<SysStatus>) {
  Object.assign(appState.sys, part)
}

export function toggleSelect(feedId: string) {
  if (appState.selected[feedId]) {
    delete appState.selected[feedId]
  } else {
    appState.selected[feedId] = true
  }
}

export function clearSelected() {
  appState.selected = {}
}

export function selectedCount(): number {
  return Object.keys(appState.selected).length
}

export function selectedNotes(): Note[] {
  const sel = appState.selected
  return appState.notes.filter(n => sel[n.feed_id])
}

export const stageOrder: Stage[] = ['input', 'search', 'filter', 'pick']
export const stageName: Record<Stage, string> = {
  input: '待输入 · 理解任务，等待输入',
  search: '搜索作品 · 搜索中…',
  filter: '筛选结果 · 评分排序',
  pick: '选择内容 · 勾选目标'
}

/** 图片地址统一走本地代理：/app/images/x → /api/img?path=/app/images/x（xhscdn 防盗链） */
export function coverProxy(u: string | null | undefined): string {
  if (!u) return ''
  if (u.startsWith('/app/images/')) return '/api/img?path=' + encodeURIComponent(u)
  return u
}

export { readonly }
