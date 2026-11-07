import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  fetchSavePlaces, listDirs, saveTextToPath, saveZipToPath, shortPath,
} from './saveApi'

function mockFetch(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => handler(url, init))
  vi.stubGlobal('fetch', fn)
  return fn
}

function json(body: unknown, ok = true, status = 200): Response {
  return { ok, status, text: async () => JSON.stringify(body), json: async () => body } as unknown as Response
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('fetchSavePlaces', () => {
  it('返回常用位置', async () => {
    mockFetch(() => json({ downloads: '/Users/me/Downloads', documents: '/Users/me/Documents' }))
    await expect(fetchSavePlaces()).resolves.toEqual({
      downloads: '/Users/me/Downloads', documents: '/Users/me/Documents',
    })
  })
})

describe('listDirs', () => {
  it('路径进 query 并做编码', async () => {
    const f = mockFetch(() => json({ path: '/a', parent: '/', dirs: [] }))
    await listDirs('/Users/me/My Doc')
    expect(f.mock.calls[0][0]).toBe('/api/list_dirs?path=%2FUsers%2Fme%2FMy%20Doc')
  })

  it('后端报错时抛出原文', async () => {
    mockFetch(() => ({ ok: false, status: 400, text: async () => '路径不存在或不是文件夹' }) as unknown as Response)
    await expect(listDirs('/nope')).rejects.toThrow('路径不存在')
  })
})

describe('saveTextToPath', () => {
  it('回传路径与是否覆盖', async () => {
    mockFetch(() => json({ ok: true, path: '/tmp/a.csv', bytes: 3, overwrote: true }))
    await expect(saveTextToPath('/tmp/a.csv', 'x,y')).resolves.toEqual({
      ok: true, path: '/tmp/a.csv', bytes: 3, overwrote: true,
    })
  })

  it('提交路径与内容', async () => {
    const f = mockFetch(() => json({ ok: true, path: '/tmp/a.csv', bytes: 1, overwrote: false }))
    await saveTextToPath('/tmp/a.csv', 'x')
    expect(JSON.parse(String(f.mock.calls[0][1]?.body))).toEqual({ path: '/tmp/a.csv', content: 'x' })
  })

  it('写入失败时抛出原文（便于 toast 展示）', async () => {
    mockFetch(() => ({ ok: false, status: 400, text: async () => '写入失败：只读' }) as unknown as Response)
    await expect(saveTextToPath('/tmp/a.csv', 'x')).rejects.toThrow('写入失败：只读')
  })
})

describe('saveZipToPath', () => {
  it('提交 feed_ids 与目标路径，回传缺失清单', async () => {
    const f = mockFetch(() => json({
      ok: true, path: '/tmp/i.zip', bytes: 10, count: 2, missing: ['f3'], overwrote: false,
    }))
    await expect(saveZipToPath(['f1', 'f2', 'f3'], '/tmp/i.zip')).resolves.toEqual({
      ok: true, path: '/tmp/i.zip', bytes: 10, count: 2, missing: ['f3'], overwrote: false,
    })
    expect(JSON.parse(String(f.mock.calls[0][1]?.body))).toEqual({
      feed_ids: ['f1', 'f2', 'f3'], save_path: '/tmp/i.zip',
    })
  })
})

describe('shortPath', () => {
  it('只保留末两段，避免提示过长', () => {
    expect(shortPath('/Users/a/Downloads/x.zip')).toBe('…/Downloads/x.zip')
  })

  it('路径段不足时原样返回', () => {
    expect(shortPath('/x.zip')).toBe('/x.zip')
  })
})
