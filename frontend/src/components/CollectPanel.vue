<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { appState, setStage, toast } from '../store'
import { apiCollectProgress, apiCollectStart, apiCollectStop, apiLoginStatus, apiNotes } from '../api'

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

// 登录 UI 改版（用户拍板）：采集中失败/异常（登录态 / 被踢 / 验证码风控）→ emit open-login
// → App 弹 APP 内部强制登录对话框（不可关闭），点「去登录」才切小红书登录页。
function isLoginAbnormal(msg: string): boolean {
  return msg.includes('登录态') || msg.includes('被踢') || msg.includes('验证码')
}

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
  if (!appState.params.keyword.trim()) { toast('请先输入关键词'); return }
  appState.collecting = true
  appState.status = '采集中'
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
    appState.searchNote = `采集中 0/${r.limit}…（可随时点「停止采集」）`
    collectTimer = setInterval(async () => {
      try {
        const pr = await apiCollectProgress(collectTaskId)
        if (pr.status === 'running') {
          appState.searchNote = `采集中 ${pr.collected}/${pr.limit}…（可随时点「停止采集」）`
          return
        }
        // 完成 / 停止 / 失败：统一收尾
        if (collectTimer) { clearInterval(collectTimer); collectTimer = null }
        if (timer) { clearInterval(timer); timer = null }
        appState.collecting = false
        if (pr.status === 'error') {
          appState.searchNote = pr.note || '采集失败'
          appState.status = '失败'
          toast(pr.note || '采集失败')
          if (isLoginAbnormal(pr.note || '')) emit('open-login')
          return
        }
        appState.searchNote = pr.note || (pr.status === 'stopped' ? '已停止采集' : '采集完成')
        toast(pr.note || (pr.status === 'stopped' ? '已停止采集' : '采集完成'))
        // 采集完成后拉评分排序结果，自动推进到"筛选结果"阶段
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
        toast(`查询进度失败：${(e2 as Error).message}`)
      }
    }, 1000)
  } catch (e) {
    if (timer) { clearInterval(timer); timer = null }
    const msg = (e as Error).message
    toast(`启动采集失败：${msg}`)
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
})
</script>

<template>
  <aside class="panel">
    <!-- 采集功能 -->
    <section>
      <div class="sec-title">采集功能 <span class="tag">MVP</span></div>
      <div class="param">
        <label>采集方式</label>
        <select style="width:100%" @change="() => toast('演示：其余采集方式即将上线')">
          <option v-for="o in collectOptions" :key="o.value" :value="o.value" :disabled="!o.enabled" :selected="o.value === 'kw'">
            {{ o.label }}
          </option>
        </select>
      </div>
    </section>

    <!-- 搜索参数 -->
    <section>
      <div class="sec-title">搜索参数</div>
      <div class="param-grid">
        <div class="param">
          <label>关键词</label>
          <input v-model="appState.params.keyword" placeholder="输入关键词，如：翡翠" @keyup.enter="doCollect" />
        </div>
        <div class="param-row">
          <div class="param">
            <label>结果上限</label>
            <input v-model.number="appState.params.limit" type="number" min="1" max="200" />
          </div>
          <div class="param">
            <label>搜索排序</label>
            <select v-model="appState.params.sort" style="width:100%">
              <option v-for="s in sortOptions" :key="s">{{ s }}</option>
            </select>
          </div>
        </div>
        <div class="param-row">
          <div class="param">
            <label>笔记类型</label>
            <select v-model="appState.params.noteType" style="width:100%">
              <option v-for="t in typeOptions" :key="t">{{ t }}</option>
            </select>
          </div>
          <div class="param">
            <label>发布时间</label>
            <select v-model="appState.params.timeRange" style="width:100%">
              <option v-for="t in timeOptions" :key="t">{{ t }}</option>
            </select>
          </div>
        </div>
        <div class="param-row">
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
        <div class="param-row">
          <label class="fast-toggle" title="快速档：逐条打开笔记详情/作者主页补全互动与粉丝数时不做拟人等待，速度快约一半；默认关闭（拟人节奏，更安全）">
            <input type="checkbox" v-model="appState.params.fast" />
            <span>极速采集（约快一半，节奏更密）</span>
          </label>
        </div>
        <div class="param-row">
          <div class="param">
            <label>全局请求间隔（ms）</label>
            <input v-model.number="appState.params.interval" type="number" min="300" step="100" />
          </div>
        </div>
        <button class="btn-search" :class="{ stop: appState.collecting }" @click="doCollect">
          <span v-if="appState.collecting" class="btn-spinner" />
          {{ appState.collecting ? '停止采集' : '开始采集' }}
        </button>
      </div>
    </section>
  </aside>
</template>

<style scoped>
/* 搜索中按钮：转圈 + 禁用（淘宝式：点了等结果，无"停止"） */
.btn-search:disabled {
  opacity: 0.7;
  cursor: default;
}
/* 极速采集开关 */
.fast-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #666;
  cursor: pointer;
  margin-top: 2px;
  user-select: none;
}
.fast-toggle input {
  accent-color: #ff2442;
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
