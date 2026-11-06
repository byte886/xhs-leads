import { describe, expect, it } from 'vitest'
import { noteUrl } from './xhsUrl'

describe('noteUrl 参数检查', () => {
  it('空 feedId 返回空串（不打开链接）', () => {
    expect(noteUrl('')).toBe('')
    expect(noteUrl(undefined)).toBe('')
  })

  it('无 token 时只给干净路径', () => {
    expect(noteUrl('abc123')).toBe('https://www.xiaohongshu.com/explore/abc123')
  })

  it('脏 token 不拼进 URL', () => {
    for (const dirty of ['null', 'NULL', 'none', 'undefined', '', '  ']) {
      expect(noteUrl('abc123', dirty)).toBe('https://www.xiaohongshu.com/explore/abc123')
    }
  })

  it('token 做 encodeURIComponent', () => {
    expect(noteUrl('abc', 'a+b/c=')).toBe(
      'https://www.xiaohongshu.com/explore/abc?xsec_token=a%2Bb%2Fc%3D')
  })
})
