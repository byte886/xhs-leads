<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { appState, setStage, toast } from '../store'
import { apiCollectProgress, apiCollectStart, apiCollectStop, apiLoginStatus, apiNotes } from '../api'
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
let collectTimer: ReturnType<typeof setInterval> | null = null

// 任务式采集：开始采集 → 后台逐批提取（进度轮询）→ 采满/平台不增长/用户停止后统一评分排序展示
async function doCollect() {
  if (appState.collecting) {
    // 采集中再点 = 停止采集
    if (collectTaskId) {
      try { await apiCollectStop(collectTaskId) } catch { /* 忽略 */ }
      toast('已请求停止，正在收尾并排序…')
    }
    return
  }
  if (!appState.params.keyword.trim()) { toast('请先输入关键词', 'error'); return }
  appState.collecting = true
  appState.status = '正在采集'
  appState.selected = {}
  appState.taskLabel = buildTaskLabel()
  appState.searchNote = ''
  appState.elapsed = 0
  if (timer) clearInterval(timer)
  timer = setInterval(() => { appState.elapsed += 1 }, 1000)
  setStage('search')
  emit('collect-started')
  try {
    const r = await apiCollectStart(appState.params)
    collectTaskId = r.task_id
    appState.searchNote = '正在采集…（可随时点「停止采集」）'
    collectTimer = setInterval(async () => {
      try {
        const pr = await apiCollectProgress(collectTaskId)
        if (pr.status === 'running') {
          // 进度由顶部全局状态条（StatusBar，/api/status_now）播报，此处仅保留操作提示避免重复
          appState.searchNote = '正在采集…（可随时点「停止采集」）'
          return
        }
        // 完成 / 停止 / 失败：统一收尾
        if (collectTimer) { clearInterval(collectTimer); collectTimer = null }
        if (timer) { clearInterval(timer); timer = null }
        appState.collecting = false
        if (pr.status === 'error') {
          appState.searchNote = pr.note || '采集失败'
          appState.status = '失败'
          toast(pr.note || '采集失败', 'error')
          if (isLoginAbnormal(pr.note || '')) emit('open-login')
          return
        }
        appState.searchNote = pr.note || (pr.status === 'stopped' ? '已停止采集' : '采集完成')
        toast(pr.note || (pr.status === 'stopped' ? '已停止采集' : '采集完成'))
        // 采集完成后拉评分排序结果，自动推进到"筛选结果"阶段
        // R-6.23 对齐：标记"本次运行已产出列表"——登录往返 reload 时恢复列表、
        // 应用重启（sessionStorage 清空）不重现上次搜索（保持空白输入态）
        sessionStorage.setItem('xhs_stage_restore', '1')
        const list = await apiNotes('', 'score')
        appState.notes = list.notes
        appState.total = list.total
        appState.count = list.total
        appState.status = '展示中'
        setStage('pick')
      } catch (e2) {
        if (collectTimer) { clearInterval(collectTimer); collectTimer = null }
        if (timer) { clearInterval(timer); timer = null }
        appState.collecting = false
        toast(`查询进度失败：${(e2 as Error).message}`, 'error')
      }
    }, 1000)
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
      <div class="param-row">
        <div class="param">
          <input v-model="appState.params.keyword" placeholder="关键词，如：翡翠" @keyup.enter="doCollect" />
        </div>
        <div class="param param-num">
          <input v-model.number="appState.params.limit" type="number" min="1" max="200" title="结果上限" placeholder="上限" />
        </div>
      </div>

      <!-- 选项类：默认收起，展开后两行选完；收起时按钮与筛选开关同行 -->
      <div class="line-actions">
        <button class="filters-toggle" :class="{ open: showFilters }" @click="showFilters = !showFilters">
          <span class="f-arrow">{{ showFilters ? '▾' : '▸' }}</span>
          筛选选项
          <span v-if="activeFilters().length" class="f-count">{{ activeFilters().length }}</span>
        </button>
        <button class="btn-search" :class="{ stop: appState.collecting }" @click="doCollect">
          <span v-if="appState.collecting" class="btn-spinner" />
          {{ appState.collecting ? '停止采集' : '开始采集' }}
        </button>
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
        <!-- 已选筛选汇总：非默认条件实时提示，采集时写入任务标签 -->
        <div class="param-row">
          <div class="filters-hint">
            <span class="filters-hint-k">已选筛选</span>
            <span v-if="activeFilters().length" class="filters-hint-v">{{ activeFilters().join(' · ') }}</span>
            <span v-else class="filters-hint-none">未选择（按小红书默认：综合 / 全部 / 不限）</span>
          </div>
        </div>
      </div>
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
.param-num { flex: 0 0 92px; }
.param-num input { text-align: right; }

/* 折叠开关 + 采集按钮同行 */
.line-actions { display: flex; gap: 8px; align-items: center; }
.filters-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px dashed #d5d5d5;
  background: #fafafa;
  color: #666;
  font-size: 12px;
  border-radius: 7px;
  padding: 7px 10px;
  cursor: pointer;
  transition: all 0.15s;
}
.filters-toggle:hover { border-color: #e0b36b; color: #b05a1f; background: #fff9f0; }
.filters-toggle.open { border-style: solid; border-color: #e0b36b; color: #b05a1f; background: #fff9f0; }
.f-arrow { font-size: 10px; }
.f-count {
  background: #e0b36b;
  color: #fff;
  border-radius: 9px;
  font-size: 10px;
  font-weight: 700;
  min-width: 16px;
  height: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
}
.btn-search {
  flex: 1;
  background: var(--red);
  color: #fff;
  padding: 9px 12px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  border: 0;
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
/* 已选筛选汇总行 */
.filters-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  font-size: 11px;
  background: #F8F9FA;
  border: 1px dashed var(--line, #e5e5e5);
  border-radius: 8px;
  padding: 7px 10px;
}
.filters-hint-k { color: var(--ink-2, #888); font-weight: 600; flex-shrink: 0; }
.filters-hint-v { color: #b08a4d; font-weight: 600; }
.filters-hint-none { color: var(--ink-3, #aaa); }
</style>
