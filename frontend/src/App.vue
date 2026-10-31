<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { appState, setStage, setTab, toast } from './store'
import type { Note } from './types'
import FlowBar from './components/FlowBar.vue'
import CollectPanel from './components/CollectPanel.vue'
import WorkArea from './components/WorkArea.vue'
import CardMenu from './components/CardMenu.vue'
import PublishPage from './components/PublishPage.vue'
import ClientsPanel from './components/ClientsPanel.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import LoginModal from './components/LoginModal.vue'
import { apiLoginStatus, apiRefreshCookieFromChrome, apiCleanupImages, apiNotes } from './api'

// v1.5 工作流 4-Tab：采集 / 创作 / 客户 / 设置（创作、客户、设置用 v-show 保持状态）
const publishRef = ref<InstanceType<typeof PublishPage> | null>(null)
const clientsRef = ref<InstanceType<typeof ClientsPanel> | null>(null)

const menuNote = ref<Note | null>(null)
const menuPos = ref({ x: 0, y: 0 })

const loginOpen = ref(false)
// 登录态检测改为「按需触发」：不再自动调用/定时轮询
// （每次检测都会发起 Chrome CDP 连接 → 必弹「远程调试授权」窗）。
// 触发点：用户主动打开登录引导；或搜索/采集失败提示后手动打开。
function checkLogin() {
  apiLoginStatus().then((lg) => {
    appState.login = { logged_in: !!lg.logged_in, nickname: lg.nickname || '' }
    if (!lg.logged_in && !appState.collecting) loginOpen.value = true
  }).catch(() => { /* 后端未就绪静默 */ })
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
      if (msg.includes('登录态')) loginOpen.value = true
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

// 启动/刷新时自动检测登录态（生产 profile 模式：独立 Chrome 自动拉起+自动注入，无 CDP 授权弹窗代价）。
// 未登录 → 自动尝试方案 A（读日常 Chrome Cookie + 钥匙串解密 + 校验落盘）：
//   成功 → 登录态恢复、弹窗不出现；失败 → 弹登录引导（含登录链接 + 刷新自动恢复提示）。
// 用户在小红书完成登录后切回本页刷新，即重走本链路完成恢复。
onMounted(async () => {
  let lg: any = null
  try { lg = await apiLoginStatus() } catch { lg = null }
  const loggedIn = !!(lg && lg.logged_in)
  appState.login = { logged_in: loggedIn, nickname: (lg && lg.nickname) || '' }
  if (!loggedIn) {
    // 先弹登录引导（用户立即可见），后台同时自动尝试方案 A 读取日常 Chrome：
    // 成功 → 自动关闭弹窗并恢复；失败 → 弹窗保持，按引导「登录 → 回来刷新」完成恢复
    if (!appState.collecting) loginOpen.value = true
    try {
      const r = await apiRefreshCookieFromChrome()
      if (r && r.ok) {
        appState.login = { logged_in: true, nickname: r.account || '已登录' }
        appState.cookieRefreshTick += 1
        loginOpen.value = false
        toast(r.note || '已自动从 Chrome 恢复登录态')
      }
    } catch { /* 后台读取失败：弹窗引导保持 */ }
  }
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
</script>

<template>
  <div>
    <FlowBar />

    <!-- 采集 Tab：左侧面板 + 采集主区 -->
    <div v-show="appState.tab === 'collect'" class="workbench">
      <CollectPanel @open-login="loginOpen = true" />
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

    <!-- 设置 Tab：AI Key / CDP / 账号人设 -->
    <div v-show="appState.tab === 'settings'" class="work">
      <div style="max-width:1100px;margin:0 auto;padding:22px 26px">
        <SettingsPanel />
      </div>
    </div>

    <CardMenu :note="menuNote" :x="menuPos.x" :y="menuPos.y" @clone="openPublish" @close="closeMenu" />

    <!-- 登录二维码弹窗（Cookie 失效时自动弹出 / 可手动触发） -->
    <LoginModal :open="loginOpen" @close="loginOpen = false" />

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
