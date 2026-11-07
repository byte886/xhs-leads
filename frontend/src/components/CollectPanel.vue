<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { appState, setStage, toast } from '../store'
import { apiCollectClear, apiCollectProgress, apiCollectStart, apiCollectStop, apiLoginStatus, apiNotes, apiStatusNow } from '../api'
import { onAppEvent } from '../events'
import { isLoginAbnormal } from '../lib/auth'

const emit = defineEmits<{ (e: 'collect-started'): void; (e: 'open-login'): void }>()

let timer: ReturnType<typeof setInterval> | null = null

const collectOptions = [
  { value: 'kw', label: '关键词搜索笔记', enabled: true },
  { value: 'blogger', label: '博主/对标账号采集（即将上线）', enabled: false },
  { value: 'comment', label: '评论采集（即将上线）', enabled: false },
  { value: 'link', label: '指定链接采集（即将上线）', enabled: false },
  { value: 'wm', label: '去水印下载（即将上线）', enabled: false },
  { value: 'hot', label: '热门/赛道采集（即将上线）', enabled: false },
  { value: 'user', label: '互动用户采集（即将上线）', enabled: false },
  { value: 'timer', label: '定时/计划采集（即将上线）', enabled: false }
]

const sortOptions = ['综合', '最新', '最多点赞', '最多评论', '最多收藏']
const typeOptions = ['全部', '图文', '视频']
const timeOptions = ['不限', '一天内', '一周内', '半年内']
const scopeOptions = ['不限', '已看过', '未看过', '已关注']
const distanceOptions = ['不限', '同城', '附近']

/** 选项类筛选默认收起（常用输入置顶一行，选项展开后两行内选完） */
const showFilters = ref(false)

/** 已选 chips 折叠阈值：>3 个折叠为「+N」，保持面板高度可预测（参考 Marigold/TDesign） */
const CHIP_LIMIT = 3
const chipsExpanded = ref(false)

/** 筛选条件结构化（维度 key + 展示文本），供 chips 渲染与单条件移除 */
function filterItems(): { key: 'sort' | 'noteType' | 'timeRange' | 'scope' | 'distance'; label: string }[] {
  const p = appState.params
  const items: { key: 'sort' | 'noteType' | 'timeRange' | 'scope' | 'distance'; label: string }[] = []
  if (p.sort && p.sort !== '综合') items.push({ key: 'sort', label: `排序·${p.sort}` })
  if (p.noteType && p.noteType !== '全部') items.push({ key: 'noteType', label: `类型·${p.noteType}` })
  if (p.timeRange && p.timeRange !== '不限') items.push({ key: 'timeRange', label: `时间·${p.timeRange}` })
  if (p.scope && p.scope !== '不限') items.push({ key: 'scope', label: `范围·${p.scope}` })
  if (p.distance && p.distance !== '不限') items.push({ key: 'distance', label: `距离·${p.distance}` })
  return items
}

/** chips 展示子集：折叠时只显示前 CHIP_LIMIT 个 */
function shownFilters() {
  return chipsExpanded.value ? filterItems() : filterItems().slice(0, CHIP_LIMIT)
}

/** 各维度默认值（未选状态） */
const filterDefaults: Record<'sort' | 'noteType' | 'timeRange' | 'scope' | 'distance', string> = {
  sort: '综合', noteType: '全部', timeRange: '不限', scope: '不限', distance: '不限'
}

/** 单个 chip ×：直接移除该条件，无需重开面板 */
function removeFilter(key: 'sort' | 'noteType' | 'timeRange' | 'scope' | 'distance') {
  appState.params[key] = filterDefaults[key]
}

/** 清除全部：一键重置所有筛选维度 */
function clearFilters() {
  for (const k of Object.keys(filterDefaults) as ('sort' | 'noteType' | 'timeRange' | 'scope' | 'distance')[]) {
    appState.params[k] = filterDefaults[k]
  }
  chipsExpanded.value = false
}

/** 结果上限步进：±1，clamp 1–200（输入框仍可自由输入） */
function stepLimit(d: number) {
  const cur = Number(appState.params.limit) || 20
  appState.params.limit = Math.min(200, Math.max(1, cur + d))
}

/** 已选筛选条件汇总：跳过默认值（综合/全部/不限），返回“非默认”条件列表 */
function activeFilters(): string[] {
  const p = appState.params
  const items: string[] = []
  if (p.sort && p.sort !== '综合') items.push(`排序 ${p.sort}`)
  if (p.noteType && p.noteType !== '全部') items.push(`类型 ${p.noteType}`)
  if (p.timeRange && p.timeRange !== '不限') items.push(`时间 ${p.timeRange}`)
  if (p.scope && p.scope !== '不限') items.push(`范围 ${p.scope}`)
  if (p.distance && p.distance !== '不限') items.push(`距离 ${p.distance}`)
  return items
}

/** 任务标签：关键词 + 全部已选筛选条件（对齐小红书搜索筛选） */
function buildTaskLabel(): string {
  const p = appState.params
  const f = activeFilters()
  return `${p.keyword}${f.length ? ' · ' + f.join(' · ') : '（未选筛选）'}`
}

// 登录 UI 改版（用户拍板）：采集中失败/异常（登录失效/被踢/验证码风控）→ emit open-login
// → App 弹 APP 内部强制登录对话框（不可关闭），点「去登录」才切小红书登录页。
// R-6.21e：统一判定取自 lib/auth（交互异常驱动，无后台轮询）。

let collectTaskId = ''
let offCollect: (() => void) | null = null
// R-6.53z8c：取消进度轮询——改为订阅后端 SSE（collect 事件），状态变化才推。

/** R-6.53z5：进度轮询独立成函数——「用户点开始」与「页面重载后接回在跑任务」共用同一套收尾逻辑 */
/** 采集收尾（done / stopped / error 三态共用）：按钮复位 + 拉最终列表。 */
async function finalizeCollect(pr: any) {
  if (timer) { clearInterval(timer); timer = null }
  appState.collecting = false
  if (pr.status === 'error') {
    appState.status = '失败'
    toast(pr.note || '采集失败', 'error')
    if (isLoginAbnormal(pr.note || '')) emit('open-login')
    return
  }
  toast(pr.note || (pr.status === 'stopped' ? '已停止采集' : '采集完成'))
  // 采集完成后拉评分排序结果，自动推进到"筛选结果"阶段
  sessionStorage.setItem('xhs_stage_restore', '1')
  const list = await apiNotes('', 'score')
  appState.notes = list.notes
  appState.total = list.total
  appState.count = list.total
  appState.status = '展示中'
  setStage('pick')
}

/** 刷新列表（节流合并：多条进度事件在 400ms 内只拉一次，仍由事件触发）。 */
let refreshTimer: ReturnType<typeof setTimeout> | null = null
function scheduleListRefresh() {
  if (refreshTimer) return
  refreshTimer = setTimeout(async () => {
    refreshTimer = null
    try {
      // 过程中按入库时间正序：新卡片只往后追加、旧卡不动；完成时 finalize 再按评分排
      const live = await apiNotes('', 'time_asc')
      if (live.notes) { appState.notes = live.notes; appState.total = live.total; appState.count = live.total }
    } catch { /* 单次列表刷新失败不阻断采集状态 */ }
  }, 400)
}

/** 收到一条采集状态：按状态分派（进行中只更新数字，终态统一收尾）。 */
async function handleCollectUpdate(pr: any) {
  if (pr.status === 'ended_need_login') {
    // R-6.53z8p：撞到登录墙 = 本次采集就此结束（已采保留）。
    // 必须让左窗知道，否则它会一直停在「正在采集」；同时弹登录引导，用户登录后可重新采集。
    appState.endedNeedLogin = true
    emit('open-login')
    const note = pr.note || '需要登录，本次采集结束'
    toast(note, 'error')
    appState.status = '需要登录'
    await finalizeCollect(pr)
    return
  }
  if (pr.status === 'running') {
    appState.endedNeedLogin = false
    scheduleListRefresh()
    return
  }
  await finalizeCollect(pr)
}

/** 订阅采集事件（按 task_id 过滤）；断线重连后补拉一次进度（一次性同步）。 */
function armProgressPolling(taskId: string) {
  collectTaskId = taskId
  if (offCollect) { offCollect(); offCollect = null }
  offCollect = onAppEvent((ev) => {
    if (ev.kind === '__open__') {
      apiCollectProgress(taskId).then(handleCollectUpdate).catch(() => { /* 重连补拉失败等下一事件 */ })
      return
    }
    if (ev.kind !== 'collect') return
    const d = ev.data
    if (d.task_id && d.task_id !== collectTaskId) return
    handleCollectUpdate(d)
  })
  // 立即同步一次当前状态（挂载/重连的初始对齐，不是轮询）
  apiCollectProgress(taskId).then(handleCollectUpdate).catch(() => { /* 后端未就绪 */ })
}

// 任务式采集：开始采集 → 后台逐批提取（事件推送进度）→ 采满/平台不增长/用户停止后统一评分排序展示
async function doCollect() {
  if (appState.collecting) {
    // 采集中再点 = 停止采集
    if (collectTaskId) {
      try { await apiCollectStop(collectTaskId) } catch { /* 忽略 */ }
      toast('已请求停止，正在收尾并排序…')
      if (!offCollect) {
        // 未订阅事件（如后端未就绪）时不会再有收尾回调，这里自行复位，
        // 否则按钮会永远卡在「停止」
        appState.collecting = false
        appState.status = '空闲'
        if (timer) { clearInterval(timer); timer = null }
      }
    }
    return
  }
  if (!appState.params.keyword.trim()) { toast('请先输入关键词', 'error'); return }
  appState.collecting = true
  appState.endedNeedLogin = false
  appState.status = '正在采集'
  appState.selected = {}
  // R-6.53w：开始新采集先清空旧列表——采集窗口干净进入「正在采集」，
  // 不再挂着上一次的卡片（旧列表要等采集完成才刷新，期间观感像没开始）
  appState.notes = []
  appState.total = 0
  appState.count = 0
  appState.taskLabel = buildTaskLabel()
  appState.elapsed = 0
  if (timer) clearInterval(timer)
  timer = setInterval(() => { appState.elapsed += 1 }, 1000)
  setStage('search')
  emit('collect-started')
  try {
    // z8p：清库与开始采集拆成两个动作，前端按顺序显式调用（行为与原来一致）
    await apiCollectClear()
    const r = await apiCollectStart(appState.params)
    collectTaskId = r.task_id
    armProgressPolling(r.task_id)
  } catch (e) {
    if (timer) { clearInterval(timer); timer = null }
    const msg = (e as Error).message
    toast(`启动采集失败：${msg}`, 'error')
    appState.status = '失败'
    if (isLoginAbnormal(msg)) emit('open-login')
    appState.collecting = false
  }
}

onMounted(async () => {
  try {
    const lg = await apiLoginStatus()
    appState.login = { logged_in: !!lg.logged_in, nickname: lg.nickname || '', kicked: !!lg.kicked }
  } catch { /* 后端未就绪时静默 */ }
  // R-6.53z5：页面重载（登录往返回 APP、刷新）后 store 是空的，但后端可能仍在采集。
  // 必须把「停止」态接回来：否则按钮显示「开始采集」而任务在跑，用户一点就顶掉它。
  try {
    const sn = await apiStatusNow()
    if (sn.collecting && sn.task_id) {
      appState.collecting = true
      appState.status = '正在采集'
      if (sn.keyword) appState.params.keyword = sn.keyword
      if (sn.limit) appState.params.limit = sn.limit
      appState.taskLabel = buildTaskLabel()
      if (timer) clearInterval(timer)
      timer = setInterval(() => { appState.elapsed += 1 }, 1000)
      setStage('search')
      armProgressPolling(sn.task_id)
    }
  } catch { /* 后端未就绪时静默 */ }
  // R-6.23 对齐：恢复最近一次采集结果仅限"本次运行内采集过"（登录守卫导航
  // 回 APP 重载后 store 清空、列表消失的场景），由 sessionStorage 标记区分——
  // 应用重启后 sessionStorage 清空，不恢复历史搜索（保持空白输入态）。
  if (sessionStorage.getItem('xhs_stage_restore')) {
    try {
      const list = await apiNotes('', 'score')
      if (list.total > 0) {
        appState.notes = list.notes
        appState.total = list.total
        appState.count = list.total
        appState.status = '展示中'
        setStage('pick')
      }
    } catch { /* 后端未就绪时静默 */ }
  }
})
</script>

<template>
  <aside class="panel">
    <!-- 常用输入：采集方式 / 关键词 / 结果上限（一行一条，不占多余标题行） -->
    <section class="collect-sec">
      <div class="param-line">
        <span class="p-label">采集方式</span>
        <select class="p-select" @change="() => toast('演示：其余采集方式即将上线')">
          <option v-for="o in collectOptions" :key="o.value" :value="o.value" :disabled="!o.enabled" :selected="o.value === 'kw'">
            {{ o.label }}
          </option>
        </select>
      </div>
      <!-- 关键词 + 结果上限：一行 -->
      <div class="param-row">
        <div class="param">
          <input v-model="appState.params.keyword" placeholder="关键词，如：翡翠" @keyup.enter="doCollect" />
        </div>
        <div class="param param-num">
          <span class="num-label">上限</span>
          <button class="num-step" type="button" @click="stepLimit(-1)">−</button>
          <input v-model.number="appState.params.limit" type="number" min="1" max="200" />
          <button class="num-step" type="button" @click="stepLimit(1)">+</button>
        </div>
      </div>

      <!-- 筛选条件：独立按钮（展开/收起面板）+ 已选 chips 区（每项可单独 × 移除） -->
      <div class="filters-line" :class="{ open: showFilters }" @click="showFilters = !showFilters">
        <span class="fl-label">筛选条件</span>
        <span class="fl-count">{{ filterItems().length ? `已选 ${filterItems().length} 项` : '未设置' }}</span>
        <span class="fl-arrow">▾</span>
      </div>

      <!-- 已选条件 chips：浅金胶囊、尾部 × 单个移除；>3 个折叠为 +N；可一键清除全部 -->
      <div v-if="filterItems().length" class="chips-area">
        <button v-for="f in shownFilters()" :key="f.key" class="chip" type="button" @click.stop="removeFilter(f.key)">
          <span class="chip-label">{{ f.label }}</span><span class="chip-x">×</span>
        </button>
        <button v-if="filterItems().length > CHIP_LIMIT && !chipsExpanded" class="chip chip-more" type="button" @click.stop="chipsExpanded = true">
          +{{ filterItems().length - CHIP_LIMIT }}
        </button>
        <button v-else-if="filterItems().length > CHIP_LIMIT" class="chip chip-more" type="button" @click.stop="chipsExpanded = false">
          收起
        </button>
        <button class="chip-clear" type="button" @click.stop="clearFilters()">清除全部</button>
      </div>

      <div v-if="showFilters" class="opt-area">
        <div class="opt-grid">
          <div class="param">
            <label>搜索排序</label>
            <select v-model="appState.params.sort" style="width:100%">
              <option v-for="s in sortOptions" :key="s">{{ s }}</option>
            </select>
          </div>
          <div class="param">
            <label>发布时间</label>
            <select v-model="appState.params.timeRange" style="width:100%">
              <option v-for="t in timeOptions" :key="t">{{ t }}</option>
            </select>
          </div>
          <div class="param">
            <label>笔记类型</label>
            <select v-model="appState.params.noteType" style="width:100%">
              <option v-for="t in typeOptions" :key="t">{{ t }}</option>
            </select>
          </div>
          <div class="param">
            <label>搜索范围</label>
            <select v-model="appState.params.scope" style="width:100%">
              <option v-for="t in scopeOptions" :key="t">{{ t }}</option>
            </select>
          </div>
          <div class="param">
            <label>位置距离</label>
            <select v-model="appState.params.distance" style="width:100%">
              <option v-for="t in distanceOptions" :key="t">{{ t }}</option>
            </select>
          </div>
        </div>
      </div>

      <!-- 开始采集：独立整行 -->
      <button
        class="btn-search"
        :class="{ stop: appState.collecting }"
        @click="doCollect"
      >
        <span v-if="appState.collecting" class="btn-spinner" />
        {{ appState.collecting ? '停止' : '开始采集' }}
      </button>
    </section>
  </aside>
</template>

<style scoped>
/* 紧凑布局：一行内联采集方式；输入控件收紧高度；选项区折叠 */
.collect-sec { display: flex; flex-direction: column; gap: 8px; }

.param-line {
  display: flex;
  align-items: center;
  gap: 8px;
}
.p-label { font-size: 12px; color: var(--ink-2); flex-shrink: 0; }
.p-select {
  flex: 1;
  min-width: 0;
  padding: 6px 8px;
  font-size: 13px;
  border: 1px solid var(--line, #e5e5e5);
  border-radius: 7px;
  background: #fff;
  color: #333;
}
.p-select:focus, .param input:focus, .param select:focus { outline: none; border-color: #e0b36b; }

.param-row { display: flex; gap: 8px; }
.param-row > * { flex: 1; min-width: 0; }
.param input, .param select {
  width: 100%;
  box-sizing: border-box;
  padding: 7px 9px;
  font-size: 13px;
  border: 1px solid var(--line, #e5e5e5);
  border-radius: 7px;
  background: #fff;
  color: #333;
}
.param-num {
  flex: 0 0 106px;
  display: flex;
  align-items: center;
  gap: 2px;
  border: 1px solid var(--line, #e5e5e5);
  border-radius: 7px;
  background: #fff;
  padding: 0 5px;
}
.param-num input {
  border: 0;
  box-shadow: none;
  padding: 7px 0;
  text-align: center;
  font-size: 13px;
  width: 30px;
  min-width: 0;
}
.param-num input:focus { border: 0; }
/* 隐藏原生上下箭头，用自定义步进按钮（跨浏览器一致） */
.param-num input::-webkit-outer-spin-button,
.param-num input::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
.num-label { font-size: 12px; color: var(--ink-2); flex-shrink: 0; margin-right: 1px; }
.num-step {
  width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 5px;
  background: #f4f4f4;
  color: #555;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  flex-shrink: 0;
  padding: 0;
  user-select: none;
}
.num-step:hover { background: #e8e8e8; color: #111; }
.num-step:active { background: #ddd; }

/* 筛选条件一行：下拉控件视觉（白底实线边框 + 右侧醒目倒三角按钮） */
.filters-line {
  display: flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #d9d9d9;
  background: #fff;
  border-radius: 7px;
  padding: 6px 8px;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s, background 0.15s;
  user-select: none;
}
.filters-line:hover { border-color: #e0b36b; box-shadow: 0 1px 4px rgba(224, 179, 107, .25); }
.filters-line.open { border-color: #e0b36b; background: #fffaf0; }
.fl-label { flex-shrink: 0; font-size: 12px; color: #666; font-weight: 600; }
/* 已选数量/未设置提示：弹性占位，把箭头顶到右侧 */
.fl-count { flex: 1; min-width: 0; text-align: left; font-size: 11px; color: #b08a4d; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* 醒目倒三角：金色底胶囊按钮，一眼可点 */
.fl-arrow {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #f6e6c8;
  color: #a5651f;
  border-radius: 5px;
  font-size: 12px;
  line-height: 1;
  transition: background 0.15s, transform 0.15s;
}
.filters-line:hover .fl-arrow { background: #e0b36b; color: #fff; }
.filters-line.open .fl-arrow { background: #e0b36b; color: #fff; transform: rotate(180deg); }

/* 已选条件 chips 区：浅金胶囊、自动换行、每项可单独移除 */
.chips-area {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-top: 8px;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #8a6420;
  background: #fdf3e0;
  border: 1px solid #e8d5ac;
  border-radius: 999px;
  padding: 3px 8px;
  cursor: default;
  transition: border-color 0.15s, background 0.15s;
}
.chip:hover { border-color: #e0b36b; background: #fdecc9; }
.chip-x {
  cursor: pointer;
  color: #a5651f;
  font-weight: 700;
  font-size: 13px;
  line-height: 1;
  padding: 0 1px;
  border-radius: 50%;
}
.chip-x:hover { color: #c2410c; background: rgba(194, 65, 12, .12); }
.chip-more { cursor: pointer; background: #fff; border: 1px dashed #d9c79a; color: #a5651f; font-weight: 600; }
.chip-more:hover { border-color: #e0b36b; background: #fffaf0; }
.chip-clear {
  cursor: pointer;
  font-size: 11px;
  color: #999;
  background: none;
  border: none;
  padding: 3px 2px;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.chip-clear:hover { color: #c2410c; }

/* 开始采集：独立整行 */
.btn-search {
  width: 100%;
  box-sizing: border-box;
  background: var(--red);
  color: #fff;
  padding: 10px 0;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  border: 0;
  white-space: nowrap;
}
.btn-search:hover { background: #E51F3A; }
.btn-search.stop { background: #333; }
.btn-search.stop:hover { background: #111; }

/* 选项区：两行内选完 */
.opt-area { display: flex; flex-direction: column; gap: 8px; margin-top: 2px; }
.opt-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}
.opt-grid .param label { font-size: 11px; color: var(--ink-2); display: block; margin-bottom: 3px; }
.opt-grid .param select {
  width: 100%;
  box-sizing: border-box;
  padding: 6px 7px;
  font-size: 12px;
  border: 1px solid var(--line, #e5e5e5);
  border-radius: 7px;
  background: #fff;
  color: #333;
}

/* 搜索中按钮：转圈 + 禁用（淘宝式：点了等结果，无"停止"） */
.btn-search:disabled {
  opacity: 0.7;
  cursor: default;
}
/* R-6.53z8c：等待登录不是「不可用」，是「等用户扫码」——保持可见度，别看着像坏了 */
.btn-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  margin-right: 6px;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: #fff;
  border-radius: 50%;
  animation: btn-spin 0.8s linear infinite;
  vertical-align: -1px;
}
@keyframes btn-spin {
  to { transform: rotate(360deg); }
}
/* 极小视口（换小屏/窄窗口兜底）：筛选选项区改 2 列，避免过挤 */
@media (max-width: 700px) {
  .opt-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
