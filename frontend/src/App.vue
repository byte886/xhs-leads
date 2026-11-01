<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { appState, setStage, setTab, toast } from './store'
import type { Note } from './types'
import FlowBar from './components/FlowBar.vue'
import CollectPanel from './components/CollectPanel.vue'
import WorkArea from './components/WorkArea.vue'
import CardMenu from './components/CardMenu.vue'
import PublishPage from './components/PublishPage.vue'
import ClientsPanel from './components/ClientsPanel.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import ForceLogin from './components/ForceLogin.vue'
import StatusBar from './components/StatusBar.vue'
import { apiLoginStatus, apiCleanupImages, apiNotes } from './api'

// v1.5 工作流 4-Tab：采集 / 创作 / 客户 / 设置（创作、客户、设置用 v-show 保持状态）
const publishRef = ref<InstanceType<typeof PublishPage> | null>(null)
const clientsRef = ref<InstanceType<typeof ClientsPanel> | null>(null)

const menuNote = ref<Note | null>(null)
const menuPos = ref({ x: 0, y: 0 })

// 登录 UI 改版（用户拍板）：未登录 / 启动被踢 / 采集中被踢与异常 → 统一弹 APP 内部
// **强制登录对话框**（ForceLogin，不可关闭），点「去登录」才切小红书登录页。
// 主动重登（设置页「重新登录」）由 SettingsPanel 直接触发（force=false，可关闭）。
const forceOpen = ref(false)

let kickTimer: ReturnType<typeof setInterval> | null = null

function checkLogin() {
  apiLoginStatus(8000).then((lg) => {
    appState.login = { logged_in: !!lg.logged_in, nickname: lg.nickname || '', kicked: !!lg.kicked }
    // 未登录或被踢 → 强制登录对话框（采集中不抢占：让当前采集收尾，失败路径再弹）
    if ((!lg.logged_in || lg.kicked) && !appState.collecting) forceOpen.value = true
  }).catch(() => { /* 后端未就绪静默 */ })
}

// 运行时被踢兜底：① launcher 守卫 dispatchEvent('xhs-leads:kicked') 通知；② 本页 30s 周期轮询
// （后端 _LOGIN_CACHE 30s 缓存兜底，轮询打缓存不反复连 CDP）。命中 kicked → 弹强制对话框。
function onKickedEvent() {
  apiLoginStatus(8000).then((lg) => {
    if (!lg.kicked) return // 事件到但状态已恢复（竞态）→ 忽略
    appState.login = { logged_in: false, nickname: '', kicked: true }
    forceOpen.value = true
  }).catch(() => {})
}

function startKickWatch() {
  if (kickTimer) clearInterval(kickTimer)
  kickTimer = setInterval(() => {
    apiLoginStatus(8000).then((lg) => {
      appState.login = { logged_in: !!lg.logged_in, nickname: lg.nickname || '', kicked: !!lg.kicked }
      if (lg.kicked) forceOpen.value = true
    }).catch(() => {})
  }, 30000)
}

function openMenu(payload: { note: Note; x: number; y: number }) {
  menuNote.value = payload.note
  menuPos.value = { x: payload.x, y: payload.y }
}

function closeMenu() {
  menuNote.value = null
}

async function openPublish(note: Note) {
  menuNote.value = null
  setTab('create')
  // PublishPage 为 v-show 常驻，v-if 挂载只在首次；确保 openFor 目标为最新笔记
  await nextTick()
  await publishRef.value?.openFor(note)
}

// 单条克隆（对齐视频：克隆走卡片单条，不批量）
async function cloneOne(note: Note) {
  // 产品以图文为主：视频仅设计留位，暂不支持一键克隆（后端视频分支保留，未来解锁）
  if (note.note_type === 'video') {
    toast('视频笔记暂不支持一键克隆（当前产品以图文为主）')
    return
  }
  await openPublish(note)
}

// 采集客户（P4/O1-3）：真实浏览器抓取笔记评论区互动用户，入库去重；完成后切客户 Tab 并刷新
async function collectClients(note: Note) {
  const t = (note.title || '该笔记').slice(0, 12)
  toast(`正在采集「${t}…」的互动用户…`)
  try {
    const resp = await fetch('/api/clients', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ feed_id: note.feed_id, xsec_token: note.xsec_token || '' }),
    })
    if (!resp.ok) {
      let msg = `HTTP ${resp.status}`
      try {
        const body = await resp.json()
        if (body?.detail) msg = String(body.detail)
      } catch { /* 非 JSON 错误体 */ }
      toast('采集失败：' + msg.slice(0, 120))
      // 登录态/被踢/风控（403 验证码）异常 → 弹 APP 强制登录对话框（用户拍板：异常也弹）
      if (msg.includes('登录态') || msg.includes('验证码') || msg.includes('被踢')) forceOpen.value = true
      return
    }
    const data = await resp.json()
    const sum = data.summary || {}
    const hi = sum['高意向'] ? `，高意向 ${sum['高意向']} 条` : ''
    toast(data.total > 0
      ? `采集到 ${data.total} 位互动用户（新增 ${data.new}）${hi}，已入库`
      : '未采集到互动用户（可能无评论或需登录）')
    if (data.total > 0) {
      setTab('clients')
      await nextTick()
      await clientsRef.value?.refresh()
    }
  } catch {
    toast('采集失败：网络错误')
  }
}

function fmtTime(s: number) {
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}

// 启动检查（登录 UI 改版）：等待后端/引擎就绪后再判定，避免 fetch 挂起/误判。
// 最多轮询 10 轮 × (8s 超时 + 2s 间隔) ≈ 引擎冷启动窗口；全部失败视为未登录（无引擎无法工作，弹窗引导）。
const SLEEP = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function ensureLoginChecked() {
  let lg: any = null
  for (let i = 0; i < 10; i++) {
    try { lg = await apiLoginStatus(8000); break } catch { await SLEEP(2000) }
  }
  const loggedIn = !!(lg && lg.logged_in)
  const kicked = !!(lg && lg.kicked)
  appState.login = { logged_in: loggedIn, nickname: (lg && lg.nickname) || '', kicked }
  if ((!loggedIn || kicked) && !appState.collecting) {
    forceOpen.value = true
  }
}

// 启动/刷新时自动检测登录态（登录 UI 改版：未登录/被踢 → APP 内部强制登录对话框；
// 不依赖 launcher 直接导航登录页——launcher 仅负责 pending 后导航登录页）。
onMounted(async () => {
  window.addEventListener('xhs-leads:kicked', onKickedEvent)
  startKickWatch()
  await ensureLoginChecked()
  // 搜索结果用完即失效：启动时不加载历史 notes（每次进入保持空白，仅当次采集后展示）
  // 启动时清理无引用本地图片（与后端 startup 双保险，防旧图堆积；fire-and-forget 不阻塞）
  apiCleanupImages().catch(() => {})
  // 显式 ?stage=pick 调试入口：不改变默认白屏规则，仅当 URL 显式带参时进入列表视图（UI 自动化/演示用）
  const urlStage = new URLSearchParams(location.search).get('stage')
  if (urlStage === 'pick') {
    try {
      const r = await apiNotes()
      if (r.total > 0) {
        appState.notes = r.notes
        appState.total = r.total
        appState.count = r.total
        appState.status = '展示中'
      }
    } catch { /* 列表加载失败：仍进入 pick 空态 */ }
    setStage('pick')
    setTab('collect')
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('xhs-leads:kicked', onKickedEvent)
  if (kickTimer) { clearInterval(kickTimer); kickTimer = null }
})
</script>

<template>
  <div>
    <FlowBar />
    <StatusBar />

    <!-- 采集 Tab：左侧面板 + 采集主区 -->
    <div v-show="appState.tab === 'collect'" class="workbench">
      <CollectPanel @open-login="forceOpen = true" />
      <main class="work">
        <WorkArea @menu="openMenu" @clone="cloneOne" @clients="collectClients" />
      </main>
    </div>

    <!-- 创作 Tab：克隆编辑器 + AI 工具条 + 发布前质检（v-show 保持编辑状态） -->
    <div v-show="appState.tab === 'create'" class="publish-wrap">
      <PublishPage ref="publishRef" />
    </div>

    <!-- 客户 Tab：评论区线索 + 聚合 + 获客指引 -->
    <div v-show="appState.tab === 'clients'" class="work">
      <div style="max-width:1100px;margin:0 auto;padding:22px 26px">
        <ClientsPanel ref="clientsRef" />
      </div>
    </div>

    <!-- 设置 Tab：账号与登录 / AI Key / CDP / 账号人设 -->
    <div v-show="appState.tab === 'settings'" class="work">
      <div style="max-width:1100px;margin:0 auto;padding:22px 26px">
        <SettingsPanel />
      </div>
    </div>

    <CardMenu :note="menuNote" :x="menuPos.x" :y="menuPos.y" @clone="openPublish" @close="closeMenu" />

    <!-- APP 内部强制登录对话框（未登录 / 启动被踢 / 采集中被踢与异常；不可关闭） -->
    <ForceLogin :open="forceOpen" @done="forceOpen = false" />

    <!-- 全局点击关闭菜单 -->
    <div v-if="menuNote" style="position:fixed;inset:0;z-index:55" @click="closeMenu"></div>

    <!-- Toast -->
    <transition name="toast-fade">
      <div v-if="appState.toastMsg" class="toast">{{ appState.toastMsg }}</div>
    </transition>
  </div>
</template>

<style scoped>
.toast-fade-enter-active, .toast-fade-leave-active { transition: opacity .2s; }
.toast-fade-enter-from, .toast-fade-leave-to { opacity: 0; }
.publish-wrap { padding: 0 22px; }
</style>
