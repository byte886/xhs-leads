import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { appState, clearSelected, coverProxy, refreshSysStatus, selectedCount, selectedNotes, setStage, setTab, toast, toggleSelect } from './store'

// R-6.36d：store 纯逻辑守护（选择集 / 图片代理 / stage / toast 计时）。
describe('coverProxy 图片防盗链代理', () => {
  it('空值返回空串', () => {
    expect(coverProxy(null)).toBe('')
    expect(coverProxy(undefined)).toBe('')
  })
  it('/app/images/ 本地图走 /api/img?path= 代理（encodeURIComponent）', () => {
    expect(coverProxy('/app/images/foo bar.jpg')).toBe('/api/img?path=' + encodeURIComponent('/app/images/foo bar.jpg'))
  })
  it('外链原样返回', () => {
    expect(coverProxy('https://sns-webpic.xhscdn.com/x.jpg')).toBe('https://sns-webpic.xhscdn.com/x.jpg')
  })
})

describe('勾选集 selected', () => {
  beforeEach(() => { appState.notes = [{ feed_id: 'a1' } as any, { feed_id: 'a2' } as any]; appState.selected = {} })
  it('toggleSelect 切换 + selectedCount', () => {
    toggleSelect('a1')
    expect(selectedCount()).toBe(1)
    toggleSelect('a1')
    expect(selectedCount()).toBe(0)
  })
  it('selectedNotes 只返回勾选的笔记对象', () => {
    toggleSelect('a1')
    expect(selectedNotes().map(n => n.feed_id)).toEqual(['a1'])
  })
  it('clearSelected 清空', () => {
    toggleSelect('a1'); toggleSelect('a2')
    clearSelected()
    expect(selectedCount()).toBe(0)
  })
})

describe('setStage / setTab', () => {
  it('进 search 时状态文案=搜索中', () => {
    setStage('search')
    expect(appState.stage).toBe('search')
    expect(appState.status).toBe('搜索中')
  })
  it('其它 stage 不覆盖 status', () => {
    appState.status = 'xx'
    setStage('filter')
    expect(appState.status).toBe('xx')
  })
  it('setTab 切页', () => {
    setTab('settings')
    expect(appState.tab).toBe('settings')
  })
})

describe('toast 2.4s 自动清除', () => {
  beforeEach(() => { vi.useFakeTimers(); appState.toastMsg = '' })
  afterEach(() => { vi.useRealTimers() })
  it('toast 设文案，2.4s 后清空，连续调用重置计时器', () => {
    toast('第一')
    expect(appState.toastMsg).toBe('第一')
    vi.advanceTimersByTime(2000)
    toast('第二')
    expect(appState.toastMsg).toBe('第二')
    vi.advanceTimersByTime(2400)
    expect(appState.toastMsg).toBe('')
  })
})

describe('refreshSysStatus', () => {
  it('局部合并 sys 状态', () => {
    refreshSysStatus({ aiKey: true })
    expect(appState.sys.aiKey).toBe(true)
  })
})
