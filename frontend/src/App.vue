<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { onAppEvent } from './events'
import { appState, setStage, setTab, toast } from './store'
import type { Note } from './types'
import FlowBar from './components/FlowBar.vue'
import CollectPanel from './components/CollectPanel.vue'
import WorkArea from './components/WorkArea.vue'
import PublishPage from './components/PublishPage.vue'
import ClientsPanel from './components/ClientsPanel.vue'
import ProfilePanel from './components/ProfilePanel.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import StatusBar from './components/StatusBar.vue'
import { apiLoginStatus, apiLoginBegin, apiCleanupImages, apiNotes } from './api'
import { isLoginAbnormal } from './lib/auth'

// 4 个常驻 Tab：采集 / 客户 / 档案 / 设置；「一键克隆」不占 Tab，改为弹出层
const publishRef = ref<InstanceType<typeof PublishPage> | null>(null)
const publishOpen = ref(false)
const clientsOpen = ref(false)
const clientsFeedId = ref('')
const clientsRef = ref<InstanceType<typeof ClientsPanel> | null>(null)


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

async function openPublish(note: Note) {
  // 一键克隆改成弹出层：不切 Tab，关闭即回采集页；PublishPage 常驻以保留编辑状态
  publishOpen.value = true
  await nextTick()
  await publishRef.value?.openFor(note)
}

// 弹层 Esc 关闭（不监听遮罩点击，避免误触关掉正在编辑的内容）
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    if (publishOpen.value) publishOpen.value = false
    else if (clientsOpen.value) closeClients()
  }
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

// 采集客户（P4/O1-3）：真实浏览器抓取笔记评论区互动用户，入库去重
// 过程呈现对齐「开始采集」：点下去即打开弹层 → 转圈 + 已用时 → 完成 / 异常停止三态
const collectState = ref({ running: false, text: '', err: '', elapsed: 0 })
let collectClock: ReturnType<typeof setInterval> | null = null

function stopCollectClock() {
  if (collectClock) { clearInterval(collectClock); collectClock = null }
}

function closeClients() {
  clientsOpen.value = false
  stopCollectClock()
  collectState.value = { running: false, text: '', err: '', elapsed: 0 }
}
onUnmounted(stopCollectClock)

async function collectClients(note: Note) {
  const t = (note.title || '该笔记').slice(0, 12)
  clientsOpen.value = true
  clientsFeedId.value = note.feed_id
  collectState.value = { running: true, text: `正在采集「${t}…」的评论区互动用户`, err: '', elapsed: 0 }
  stopCollectClock()
  collectClock = setInterval(() => { collectState.value.elapsed += 1 }, 1000)

  const ac = new AbortController()
  // 评论须滚到底再提取，评论多的笔记本身就要几十秒，超时给足余量
  const capTimer = setTimeout(() => ac.abort(), 420_000)
  const fail = (msg: string, ms = 120) => {
    stopCollectClock()
    // 被平台判异常访问（300013）时后端已自动退登 → 左窗直接提示重新扫码，不铺平台长文案
    const needLogin = isLoginAbnormal(msg)
    const tip = needLogin ? '需要重新登录：请在右侧窗口重新扫码' : `采集失败：${msg.slice(0, ms)}`
    collectState.value = { running: false, text: tip, err: '1', elapsed: collectState.value.elapsed }
    toast(tip, 'error')
    // 不再调 ensureRightLogin：后端被限时已经把右窗清 cookie 并停在 /login，
    // 这里再发 login_begin 会让守卫再导航一次，与"停在登录页"来回抢页面 → 视觉上闪烁。
  }

  try {
    const resp = await fetch('/api/clients', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ feed_id: note.feed_id, xsec_token: note.xsec_token || '' }),
      signal: ac.signal,
    })
    clearTimeout(capTimer)
    if (!resp.ok) {
      let msg = `HTTP ${resp.status}`
      try {
        const body = await resp.json()
        if (body?.detail) msg = String(body.detail)
      } catch { /* 非 JSON 错误体 */ }
      fail(msg)
      return
    }
    const data = await resp.json()
    stopCollectClock()
    // 先刷新列表，再用列表实数播报（后端返回的条数含「XX关注」这类非评论动作，不能用它报数）
    await nextTick()
    await clientsRef.value?.refresh()
    // 采集成功不再留常驻播报条：转圈停即视为完成，列表本身反映结果
    // （失败仍保留红色错误条，异常必须可见）
    collectState.value = { running: false, text: '', err: '', elapsed: 0 }
    // 只有明显低于平台声明数才提示：评论区滚到底有 DOM 上限、也含已删评论，
    // 正常覆盖 95% 左右；偏少通常意味着采集被打断（例如采集中操作了右侧窗口）。
    if (data.low_coverage) {
      toast(`只采到 ${data.total} / 平台显示 ${data.declared} 条，可能被打断，可重试（采集中别操作右侧窗口）`, 'error')
    }
  } catch (e) {
    clearTimeout(capTimer)
    const aborted = (e as { name?: string })?.name === 'AbortError'
    fail(aborted ? '采集超时（420 秒），已停止' : '网络错误，已停止')
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
  window.addEventListener('keydown', onKeydown)
  onUnmounted(() => {
    if (offEvents) { offEvents(); offEvents = null }
    window.removeEventListener('keydown', onKeydown)
  })
  // 显式 ?stage=pick 调试/自动化入口：默认白屏规则不变（不带参仍不加载历史笔记），
  // 仅当 URL 显式带参时进入列表视图（UI 自动化/演示用，dev 与生产构建均可用）。
  // 本机 127.0.0.1 桌面应用，用户看到的是本机自己的采集数据，无对外暴露面。
  const urlStage = new URLSearchParams(location.search).get('stage')
  if (urlStage === 'pick') {
    try {
      const r = await apiNotes()
      if (r.total > 0) {
        appState.notes = r.notes
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
        <WorkArea @clone="cloneOne" @clients="collectClients" />
      </main>
    </div>

    <!-- 一键克隆弹层：不占 Tab，关闭即回采集页；v-show 常驻以保留编辑状态 -->
    <div v-show="publishOpen" class="publish-modal">
      <div class="publish-dialog">
        <div class="publish-bar">
          <span class="publish-bar-t">一键克隆</span>
          <button class="btn publish-bar-close" @click="publishOpen = false">关闭</button>
        </div>
        <div class="publish-body">
          <PublishPage ref="publishRef" @open-login="ensureRightLogin" />
        </div>
      </div>
    </div>

    <!-- 采集客户弹层：不占 Tab，采集完成即弹出；关闭回采集页 -->
    <div v-show="clientsOpen" class="publish-modal">
      <div class="publish-dialog">
        <div class="publish-bar">
          <span class="publish-bar-t">采集客户</span>
          <button class="btn publish-bar-close" @click="closeClients">关闭</button>
        </div>
        <div class="publish-body">
          <div v-if="collectState.text" class="cl-status" :class="{ run: collectState.running, err: collectState.err }">
            <span v-if="collectState.running" class="cl-spin" aria-hidden="true" />
            <span class="cl-status-t">{{ collectState.text }}</span>
            <span v-if="collectState.running" class="cl-status-time">{{ collectState.elapsed }}s</span>
          </div>
          <div style="max-width:1100px;margin:0 auto">
            <ClientsPanel ref="clientsRef" :feed-id="clientsFeedId" />
          </div>
        </div>
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

    <!-- 登录不在本窗呈现：登录页只在右窗（采集调试窗）打开，左窗 APP 全程不被遮挡 -->

    <!-- Toast -->
    <transition name="toast-fade">
      <div v-if="appState.toastMsg" class="toast" :class="appState.toastType">{{ appState.toastMsg }}</div>
    </transition>
  </div>
</template>

<style scoped>
.toast-fade-enter-active, .toast-fade-leave-active { transition: opacity .2s; }
.toast-fade-enter-from, .toast-fade-leave-to { opacity: 0; }
/* 一键克隆弹层：大尺寸居中面板，宽度贴近原来宽屏的创作页 */
.publish-modal {
  position: fixed; inset: 0; z-index: 70;
  display: flex; align-items: center; justify-content: center;
  padding: 3vh 2vw;
  background: rgba(24, 20, 18, .42);
}
.publish-dialog {
  display: flex; flex-direction: column;
  width: min(1180px, 96vw); height: min(90vh, 100%);
  background: #fff; border-radius: 12px; overflow: hidden;
  box-shadow: 0 28px 68px rgba(0, 0, 0, .3);
}
.publish-bar {
  flex: none; display: flex; align-items: center; gap: 10px;
  height: 46px; padding: 0 16px 0 18px;
  border-bottom: 1px solid var(--line);
}
.publish-bar-t { font-size: 14px; font-weight: 600; color: var(--ink); }
.publish-bar-close { margin-left: auto; }
.publish-body { flex: 1; overflow: auto; padding: 18px 22px 22px; }
.cl-status {
  display: flex; align-items: center; gap: 9px;
  max-width: 1100px; margin: 0 auto 14px;
  padding: 9px 12px; border-radius: 7px;
  background: #F6F2EC; border: 1px solid var(--line);
  font-size: 12.5px; color: var(--ink-2);
}
.cl-status.run { background: #FFF7EE; border-color: #F0D3B6; color: #8A5A22; }
.cl-status.err { background: #FDECEC; border-color: #F2C9C9; color: #A33; }
.cl-status-t { flex: 1; line-height: 1.45; }
.cl-status-time { font-variant-numeric: tabular-nums; opacity: .75; }
.cl-spin {
  width: 13px; height: 13px; flex: none; border-radius: 50%;
  border: 2px solid currentColor; border-top-color: transparent;
  animation: cl-spin .8s linear infinite;
}
@keyframes cl-spin { to { transform: rotate(360deg); } }

</style>
