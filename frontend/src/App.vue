<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { onAppEvent } from './events'
import { appState, setStage, setTab, toast } from './store'
import type { Note } from './types'
import FlowBar from './components/FlowBar.vue'
import CollectPanel from './components/CollectPanel.vue'
import WorkArea from './components/WorkArea.vue'
import CardMenu from './components/CardMenu.vue'
import PublishPage from './components/PublishPage.vue'
import ClientsPanel from './components/ClientsPanel.vue'
import ProfilePanel from './components/ProfilePanel.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import StatusBar from './components/StatusBar.vue'
import { apiLoginStatus, apiLoginBegin, apiCleanupImages, apiNotes } from './api'
import { isLoginAbnormal } from './lib/auth'

// v1.5 工作流 4-Tab：采集 / 创作 / 客户 / 设置（创作、客户、设置用 v-show 保持状态）
const publishRef = ref<InstanceType<typeof PublishPage> | null>(null)
const clientsRef = ref<InstanceType<typeof ClientsPanel> | null>(null)

const menuNote = ref<Note | null>(null)
const menuPos = ref({ x: 0, y: 0 })

// 登录形态（用户拍板·双窗职责分离）：
// - **右窗（采集调试窗）是唯一登录落点**，左窗 APP 全程不弹登录框、不被登录页覆盖。
// - **启动/定时不自动挂等待页**：未登录时左窗照常显示 APP，不主动开右窗
//   （否则二维码会过期、页面卡死，用户还得手动刷新，没有意义）。
// - 只在用户**主动操作确实需要登录**时才发信号 apiLoginBegin(force=true)：
//   点「开始采集」撞墙、采集/发布失败提示登录。launcher 守卫据此在右窗打开
//   小红书登录页（右窗缺失时按采集窗同一路径现开标准窗口）；扫码完成后右窗
//   自行跳转，左窗界面不中断。
let offEvents: (() => void) | null = null

function ensureRightLogin() {
  // 仅由用户主动动作触发，不存在轮询风暴，无需去重；catch 防后端未就绪时的未处理拒绝。
  apiLoginBegin(true).catch(() => {})
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
    toast('视频笔记暂不支持一键克隆（当前产品以图文为主）', 'error')
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
      toast('采集失败：' + msg.slice(0, 120), 'error')
      if (isLoginAbnormal(msg)) ensureRightLogin()
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
    toast('采集失败：网络错误', 'error')
  }
}

function fmtTime(s: number) {
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}

// 启动检查：等待后端/引擎就绪后再判定登录态，避免 fetch 挂起/误判。
// 最多轮询 10 轮 × (8s 超时 + 2s 间隔) ≈ 引擎冷启动窗口；全部失败视为未登录。
// 这里**只更新状态、不自动开登录页**——未登录时左窗照常显示 APP，等用户主动
// 采集/发布撞墙时才在右窗引导登录。
const SLEEP = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function ensureLoginChecked() {
  let lg: any = null
  for (let i = 0; i < 10; i++) {
    try { lg = await apiLoginStatus(8000); break } catch { await SLEEP(2000) }
  }
  const loggedIn = !!(lg && lg.logged_in)
  const kicked = !!(lg && lg.kicked)
  appState.login = { logged_in: loggedIn, nickname: (lg && lg.nickname) || '', kicked }
}

// 启动只做登录态判定（左窗 APP 不弹框、不遮挡、不自动开右窗等待页）。
onMounted(async () => {
  await ensureLoginChecked()
  // 搜索结果用完即失效：启动时不加载历史 notes（每次进入保持空白，仅当次采集后展示）
  // 启动时清理无引用本地图片（与后端 startup 双保险，防旧图堆积；fire-and-forget 不阻塞）
  apiCleanupImages().catch(() => {})
  // 只在两种真实信号到达时才响应：
  //   ① collect 事件 need_login（用户主动采集撞墙 = 确实需要登录）→ 右窗打开登录页
  //   ② login 事件 logged_in（扫码成功 = 登录态已恢复）→ 同步左窗状态
  offEvents = onAppEvent((ev) => {
    if (ev.kind === 'collect' && ev.data.need_login) {
      appState.login = { logged_in: false, nickname: '', kicked: false }
      ensureRightLogin()
    }
    if (ev.kind === 'login' && ev.data.logged_in) {
      appState.login = { logged_in: true, nickname: '', kicked: false }
    }
  })
  onUnmounted(() => { if (offEvents) { offEvents(); offEvents = null } })
  // 显式 ?stage=pick 调试/自动化入口：默认白屏规则不变（不带参仍不加载历史笔记），
  // 仅当 URL 显式带参时进入列表视图（UI 自动化/演示用，dev 与生产构建均可用）。
  // 本机 127.0.0.1 桌面应用，用户看到的是本机自己的采集数据，无对外暴露面。
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
</script>

<template>
  <div class="app-shell">
    <FlowBar />
    <StatusBar />

    <!-- 采集 Tab：左侧面板 + 采集主区 -->
    <div v-show="appState.tab === 'collect'" class="workbench">
      <CollectPanel @open-login="ensureRightLogin" />
      <main class="work">
        <WorkArea @menu="openMenu" @clone="cloneOne" @clients="collectClients" />
      </main>
    </div>

    <!-- 创作 Tab：克隆编辑器 + AI 工具条 + 发布前质检（v-show 保持编辑状态） -->
    <div v-show="appState.tab === 'create'" class="publish-wrap">
      <PublishPage ref="publishRef" @open-login="ensureRightLogin" />
    </div>

    <!-- 客户 Tab：评论区线索 + 聚合 + 获客指引 -->
    <div v-show="appState.tab === 'clients'" class="work">
      <div style="max-width:1100px;margin:0 auto;padding:36px 26px 22px">
        <ClientsPanel ref="clientsRef" />
      </div>
    </div>

    <!-- 档案 Tab：账号档案（生成的事实底座，内容资产而非软件配置） -->
    <div v-show="appState.tab === 'profiles'" class="work">
      <div style="max-width:1100px;margin:0 auto;padding:20px 26px 22px">
        <ProfilePanel />
      </div>
    </div>

    <!-- 设置 Tab：账号与登录 / AI 供应商 / 窗口与授权 -->
    <div v-show="appState.tab === 'settings'" class="work">
      <div style="max-width:1100px;margin:0 auto;padding:36px 26px 22px">
        <SettingsPanel />
      </div>
    </div>

    <CardMenu :note="menuNote" :x="menuPos.x" :y="menuPos.y" @clone="openPublish" @close="closeMenu" />

    <!-- 登录不在本窗呈现：登录页只在右窗（采集调试窗）打开，左窗 APP 全程不被遮挡 -->

    <!-- 全局点击关闭菜单 -->
    <div v-if="menuNote" style="position:fixed;inset:0;z-index:55" @click="closeMenu"></div>

    <!-- Toast -->
    <transition name="toast-fade">
      <div v-if="appState.toastMsg" class="toast" :class="appState.toastType">{{ appState.toastMsg }}</div>
    </transition>
  </div>
</template>

<style scoped>
.toast-fade-enter-active, .toast-fade-leave-active { transition: opacity .2s; }
.toast-fade-enter-from, .toast-fade-leave-to { opacity: 0; }
.publish-wrap { padding: 0 22px; overflow: auto; }
</style>
