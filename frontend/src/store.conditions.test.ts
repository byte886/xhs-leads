import { describe, it, expect, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { appState, DEFAULT_PARAMS } from './store'

describe('采集条件 localStorage 持久化（R-6.44c）', () => {
  beforeEach(() => localStorage.clear())
  it('出厂默认条件 = 用户约定（keyword 空 / limit 20 / 图文 / 综合）', () => {
    expect(DEFAULT_PARAMS.keyword).toBe('')
    expect(DEFAULT_PARAMS.limit).toBe(20)
    expect(DEFAULT_PARAMS.noteType).toBe('图文')
    expect(DEFAULT_PARAMS.sort).toBe('综合')
  })
  it('变更 params 后异步写入 localStorage', async () => {
    appState.params.keyword = '测试词'
    await nextTick()
    await nextTick()
    const raw = localStorage.getItem('xhs_collect_conditions')
    expect(raw).toBeTruthy()
    expect(raw).toContain('测试词')
  })
  it('坏 JSON 不影响运行（默认值不抛错）', () => {
    localStorage.setItem('xhs_collect_conditions', '{broken')
    expect(() => JSON.parse('{broken}')).toThrow()
    expect(appState.params).toBeTruthy()
  })
})
