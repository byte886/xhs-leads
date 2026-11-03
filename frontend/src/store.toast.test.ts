import { describe, it, expect } from 'vitest'
import { appState, toast } from './store'

describe('toast 分级（R-6.43a）', () => {
  it('error 类：设置 toastType=error 且不自动消失', () => {
    toast('采集失败：网络错误', 'error')
    expect(appState.toastMsg).toContain('采集失败')
    expect(appState.toastType).toBe('error')
    expect(appState.toastMsg).toBeTruthy()
    appState.toastMsg = ''
  })
  it('ok 类：设置 toastType=ok', () => {
    toast('发布成功', 'ok')
    expect(appState.toastType).toBe('ok')
    appState.toastMsg = ''
  })
})
