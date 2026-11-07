<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { fetchSavePlaces, listDirs, type DirEntry, type SavePlaces } from '../lib/saveApi'

// 应用内「保存位置」对话框：导出前先让用户看得见、选得到位置（含目录浏览）。
// 不使用系统对话框：CFT 外壳里浏览器与 osascript 的保存面板都不显示（已取证）。

const visible = ref(false)
const name = ref('')
const dir = ref('')
const places = ref<SavePlaces>({})
const mode = ref<'main' | 'browse'>('main')
const browsePath = ref('')
const browseParent = ref<string | null>(null)
const browseDirs = ref<DirEntry[]>([])
const error = ref('')
const nameInput = ref<HTMLInputElement | null>(null)

let resolver: ((v: string | null) => void) | null = null

const PLACE_LABELS: Record<string, string> = {
  last: '上次位置', downloads: '下载', desktop: '桌面', documents: '文档', home: '主目录',
}
const shortDir = computed(() => {
  const parts = dir.value.split('/').filter(Boolean)
  return parts.length >= 2 ? `…/${parts.slice(-2).join('/')}` : dir.value
})
/** 常用位置列表（过滤掉未返回的项，按 上次/下载/桌面/文档/主目录 排序）。 */
const placeList = computed(() => {
  const order = ['last', 'downloads', 'desktop', 'documents', 'home']
  const entries = Object.entries(places.value) as [string, string | undefined][]
  return entries
    .filter((e): e is [string, string] => !!e[1])
    .sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]))
})
const fullPath = computed(() => (dir.value ? `${dir.value}/${name.value.trim()}` : ''))

/** 打开对话框；返回用户选定的绝对路径，取消返回 null。 */
async function open(suggestedName: string): Promise<string | null> {
  name.value = suggestedName
  error.value = ''
  mode.value = 'main'
  try {
    places.value = await fetchSavePlaces()
  } catch {
    places.value = {}
  }
  dir.value = places.value.last || places.value.downloads || places.value.home || ''
  visible.value = true
  await nextTick()
  nameInput.value?.focus()
  nameInput.value?.setSelectionRange(0, suggestedName.replace(/\.[^.]+$/, '').length)
  return new Promise<string | null>((resolve) => {
    resolver = resolve
  })
}

function close(): void {
  visible.value = false
  const r = resolver
  resolver = null
  r?.(null)
}

function confirm(): void {
  const n = name.value.trim()
  if (!n) { error.value = '请填写文件名'; return }
  if (!dir.value) { error.value = '请选择保存位置'; return }
  if (!/\.[A-Za-z0-9]+$/.test(n)) { error.value = '文件名需要带扩展名'; return }
  error.value = ''
  const path = fullPath.value
  visible.value = false
  const r = resolver
  resolver = null
  r?.(path)
}

function usePlace(p: string): void {
  dir.value = p
  mode.value = 'main'
}

async function loadBrowse(path: string): Promise<void> {
  try {
    const r = await listDirs(path)
    browsePath.value = r.path
    browseParent.value = r.parent
    browseDirs.value = r.dirs
    error.value = ''
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function openBrowse(): Promise<void> {
  mode.value = 'browse'
  await loadBrowse(dir.value || places.value.home || '')
}

function pickFolder(p: string): void {
  dir.value = p
  mode.value = 'main'
}

// script setup 的顶层绑定默认不对外暴露，父组件用 ref 调用 open 必须显式声明
defineExpose({ open })
</script>

<template>
  <div v-if="visible" class="sd-mask" @click.self="close">
    <div class="sd">
      <div class="sd-head">
        <h3>{{ mode === 'main' ? '保存文件' : '选择文件夹' }}</h3>
        <button class="sd-x" @click="close" title="取消">×</button>
      </div>

      <template v-if="mode === 'main'">
        <label class="sd-label">文件名</label>
        <input ref="nameInput" v-model="name" class="sd-input" @keyup.enter="confirm" />

        <label class="sd-label">保存位置</label>
        <div class="sd-places">
          <button
            v-for="[k, p] in placeList" :key="k"
            :class="['sd-chip', { on: dir === p }]"
            @click="usePlace(p)"
          >{{ PLACE_LABELS[k] || k }}</button>
          <button class="sd-chip browse" @click="openBrowse">浏览…</button>
        </div>
        <div class="sd-dir" :title="dir">{{ shortDir }}</div>

        <div class="sd-full" :title="fullPath">{{ fullPath }}</div>
      </template>

      <template v-else>
        <div class="sd-dir" :title="browsePath">{{ browsePath }}</div>
        <div class="sd-list">
          <div v-if="browseParent" class="sd-item up" @click="loadBrowse(browseParent)">
            ↑ 上一级
          </div>
          <div v-for="d in browseDirs" :key="d.path" class="sd-item" @click="loadBrowse(d.path)">
            📁 {{ d.name }}
          </div>
          <div v-if="!browseDirs.length" class="sd-empty">这个文件夹里没有子文件夹</div>
        </div>
        <button class="sd-pick" @click="pickFolder(browsePath)">保存到这个文件夹</button>
        <button class="sd-back" @click="mode = 'main'">返回（不换位置）</button>
      </template>

      <div v-if="error" class="sd-err">{{ error }}</div>

      <div v-if="mode === 'main'" class="sd-foot">
        <button class="sd-btn ghost" @click="close">取消</button>
        <button class="sd-btn primary" @click="confirm">保存</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sd-mask { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 210; display: flex; align-items: center; justify-content: center; }
.sd { width: 420px; max-width: 92vw; max-height: 86vh; overflow-y: auto; background: #fff; border-radius: 14px; padding: 18px 20px 16px; box-shadow: 0 12px 40px rgba(0,0,0,.25); }
.sd-head { display: flex; align-items: center; margin-bottom: 12px; }
.sd-head h3 { font-size: 15px; margin: 0; }
.sd-x { margin-left: auto; border: none; background: none; font-size: 20px; line-height: 1; color: #888; cursor: pointer; }
.sd-label { display: block; font-size: 12px; color: #666; margin: 8px 0 5px; }
.sd-input { width: 100%; box-sizing: border-box; padding: 7px 9px; font-size: 13px; border: 1px solid #ddd; border-radius: 6px; }
.sd-places { display: flex; flex-wrap: wrap; gap: 6px; }
.sd-chip { padding: 5px 10px; font-size: 12px; border: 1px solid #e2e2e2; border-radius: 6px; background: #fafafa; color: #444; cursor: pointer; }
.sd-chip.on { border-color: var(--red); color: var(--red); background: #fff5f6; font-weight: 600; }
.sd-chip.browse { color: #666; }
.sd-dir { margin-top: 8px; font-size: 12px; color: #666; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sd-full { margin-top: 6px; padding: 7px 9px; background: #f6f6f6; border-radius: 6px; font-size: 12px; color: #333; word-break: break-all; }
.sd-list { margin-top: 6px; max-height: 240px; overflow-y: auto; border: 1px solid #eee; border-radius: 8px; }
.sd-item { padding: 7px 10px; font-size: 12px; color: #333; cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sd-item:hover { background: #fafafa; }
.sd-item.up { color: #888; border-bottom: 1px solid #f2f2f2; }
.sd-empty { padding: 10px; font-size: 12px; color: #999; }
.sd-pick { margin-top: 10px; width: 100%; padding: 8px; font-size: 12px; font-weight: 600; color: var(--red); background: #fff5f6; border: 1px solid #f6d5da; border-radius: 8px; cursor: pointer; }
.sd-back { margin-top: 6px; width: 100%; padding: 7px; font-size: 12px; color: #666; background: #f6f6f6; border: none; border-radius: 8px; cursor: pointer; }
.sd-err { margin-top: 8px; font-size: 12px; color: #c0392b; }
.sd-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.sd-btn { padding: 7px 16px; font-size: 13px; border-radius: 8px; cursor: pointer; border: 1px solid transparent; }
.sd-btn.ghost { background: #f4f4f4; color: #555; }
.sd-btn.primary { background: var(--red); color: #fff; font-weight: 600; }
</style>
