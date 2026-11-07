// 导出文件的保存位置：应用内自绘对话框选定路径，后端直接落盘。
//
// 为什么不用浏览器 showSaveFilePicker：APP 外壳是 CFT 窗口，该方法在里面会
// 「立即成功」却不弹面板（2026-11-08 实测），文件被静默写进默认下载目录；
// 后端 osascript 原生面板同样在本进程上下文里不显示。故改自绘对话框，
// 让用户始终看得见、选得到保存位置。

async function getJson<T>(url: string): Promise<T> {
  const resp = await fetch(url)
  if (!resp.ok) throw new Error(((await resp.text()) || String(resp.status)).slice(0, 120))
  return (await resp.json()) as T
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new Error(((await resp.text()) || String(resp.status)).slice(0, 120))
  return (await resp.json()) as T
}

export interface SavePlaces {
  downloads?: string
  desktop?: string
  documents?: string
  home?: string
  last?: string
}

export interface DirEntry {
  name: string
  path: string
}

export interface DirListing {
  path: string
  parent: string | null
  dirs: DirEntry[]
}

export interface SaveTextResult {
  path: string
  bytes: number
  overwrote: boolean
}

export interface ZipSaveResult {
  path: string
  bytes: number
  count: number
  missing: string[]
  overwrote: boolean
}

/** 常用保存位置（下载/桌面/文档/上次用过）。 */
export function fetchSavePlaces(): Promise<SavePlaces> {
  return getJson<SavePlaces>('/api/save_places')
}

/** 列出某个目录下的子目录，供对话框导航。 */
export function listDirs(path: string): Promise<DirListing> {
  return getJson<DirListing>(`/api/list_dirs?path=${encodeURIComponent(path)}`)
}

/** 把文本写到已选定的绝对路径。 */
export function saveTextToPath(path: string, content: string): Promise<SaveTextResult> {
  return postJson<SaveTextResult>('/api/save_text', { path, content })
}

/** 让后端把选中笔记的封面打包成 zip，写到已选定的绝对路径。 */
export function saveZipToPath(feedIds: string[], path: string): Promise<ZipSaveResult> {
  return postJson<ZipSaveResult>('/api/download_images', { feed_ids: feedIds, save_path: path })
}

/** 路径太长时只保留末两段，给提示文案用。 */
export function shortPath(path: string): string {
  const parts = path.split('/').filter(Boolean)
  return parts.length >= 2 ? `…/${parts.slice(-2).join('/')}` : path
}
