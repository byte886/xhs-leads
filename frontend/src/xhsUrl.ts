/**
 * 小红书笔记链接构造（R-6.53z6）：生成前统一做参数检查。
 * - feedId 为空 → 返回空串，调用方不打开链接；
 * - xsecToken 为空 / null / none / undefined 等脏值 → 不拼该参数；
 * - 值一律 encodeURIComponent，避免 token 里的特殊字符破坏 URL。
 */
const XHS = 'https://www.xiaohongshu.com'
const DIRTY = new Set(['null', 'none', 'undefined', 'nan'])

export function noteUrl(feedId: string | null | undefined, xsecToken?: string | null): string {
  const id = (feedId || '').trim()
  if (!id) return ''
  const base = `${XHS}/explore/${encodeURIComponent(id)}`
  const token = (xsecToken || '').trim()
  if (!token || DIRTY.has(token.toLowerCase())) return base
  return `${base}?xsec_token=${encodeURIComponent(token)}`
}

/**
 * 博主主页链接（已实测登录态下不带 token 可正常打开）：
 * userId 为空 → 返回空串，调用方不打开。
 */
export function userUrl(userId: string | null | undefined): string {
  const id = (userId || '').trim()
  if (!id) return ''
  return `${XHS}/user/profile/${encodeURIComponent(id)}`
}
