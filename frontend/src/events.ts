/**
 * R-6.53z8c：SSE 事件总线客户端。
 *
 * 前端的运行状态一律由后端「变化时推送」，不再定时轮询接口。
 * 断线由浏览器按服务端 retry:3000 自动重连；重连成功派发一次 __open__，
 * 订阅者据此补拉一次全量（一次性同步，不是轮询）。
 */
export type AppEvent = { kind: string; data: Record<string, any> }
type Handler = (ev: AppEvent) => void

const BASE = ''
const handlers = new Set<Handler>()
let source: EventSource | null = null

function dispatch(ev: AppEvent) {
  handlers.forEach((h) => {
    try { h(ev) } catch { /* 单个订阅者异常不影响其他订阅者 */ }
  })
}

function ensureSource() {
  if (source || typeof window === 'undefined' || typeof EventSource === 'undefined') return
  source = new EventSource(`${BASE}/api/events`)
  source.onopen = () => dispatch({ kind: '__open__', data: {} })
  source.onmessage = (m) => {
    let ev: AppEvent
    try { ev = JSON.parse(m.data) } catch { return }
    dispatch(ev)
  }
  // onerror 不处理：EventSource 断线自动重连（服务端已下发 retry）
}

/** 订阅应用事件，返回取消订阅函数。 */
export function onAppEvent(h: Handler): () => void {
  ensureSource()
  handlers.add(h)
  return () => { handlers.delete(h) }
}
