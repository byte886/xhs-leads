/**
 * 笔记搜索框匹配规则（只按「标题」「作者昵称」两路，模糊=子串包含）。
 *
 * 设计取舍（2026-11-08）：
 * - 不再匹配笔记ID / 作者ID：这两串 24 位十六进制在小红书 App 里看不到，
 *   用户复制不到，卡面也不展示，属于对用户无效的承诺。
 * - 空格分隔的多个词按「全部命中」处理（AND），单次输入即可筛「作者 + 关键词」，
 *   避免用户输入两个词时因要求连续出现而误判为"搜不到"。
 * - 忽略大小写：中文无大小写差异，英文昵称/标题不受影响。
 */
import type { Note } from '../types'

const FIELDS = (n: Note): string[] => [n.title || '', n.nickname || '']

/** 把查询串拆成小写词数组；空串/纯空格返回空数组（等于不筛选）。 */
export function splitTerms(query: string | null | undefined): string[] {
  return (query || '')
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
}

/** 单个笔记是否命中：每个词都能在标题或作者名里找到子串。 */
export function matchNote(note: Note, terms: string[]): boolean {
  if (!terms.length) return true
  const haystack = FIELDS(note).map(f => f.toLowerCase())
  return terms.every(t => haystack.some(h => h.includes(t)))
}

/** 批量筛选；查询为空时原样返回（保持引用，避免多余渲染）。 */
export function filterNotes(notes: Note[], query: string | null | undefined): Note[] {
  const terms = splitTerms(query)
  if (!terms.length) return notes
  return notes.filter(n => matchNote(n, terms))
}
