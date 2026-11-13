<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onAppEvent } from '../events'
import { appState } from '../store'

// 全局状态条：全 Tab 共用、位于滚动区之外（天然吸顶，不随作品列表滚走）。
// 采集时它就是「任务摘要」——动作 + 关键词 + 进度 + 耗时，一行说完。
// 采集页原先吸顶的 info-bar（共获取/当前任务/状态/已完成）已合并到这里：
// 那四格里「共获取」与「已完成」同值、「当前任务」左面板已完整显示、「状态」恒为搜索中，
// 都没有独立信息量，留着只是把同一条进度念了四遍。

type CollectSt = { status: string; phase: string; note: string; saved: number; limit: number; keyword: string }

const st = ref<CollectSt>({ status: '', phase: '', note: '', saved: 0, limit: 0, keyword: '' })
const live = ref(false)
// 前端点了「开始采集」、但后端还没推第一条进度（抓完第一页才推，十几秒）时，
// 先按本地状态显示「正在启动」——否则这十几秒一直挂着「空闲」，像没点上。
const booting = computed(() => appState.collecting && !live.value)

function apply(d: Record<string, any>) {
  live.value = d.status === 'running' || d.status === 'resuming'
  st.value = {
    status: d.status || '',
    phase: d.phase || '',
    note: d.note || '',
    saved: d.saved ?? 0,
    limit: d.limit ?? 0,
    keyword: d.keyword || '',
  }
}

const kw = computed(() => st.value.keyword || appState.params.keyword || '')
const elapsedText = computed(() => (appState.elapsed ? `${appState.elapsed}s` : ''))
// 结束态留痕：正常完成念条数+耗时，停止/失败/需登录直接念后端那句
const ended = computed(() => {
  if (st.value.status === 'done') {
    const n = `已完成 · 已采集 ${st.value.saved} 条`
    return elapsedText.value ? `${n} · ${elapsedText.value}` : n
  }
  return st.value.note || ''
})
// 空闲：采集页给下一步提示，其他 Tab 保持安静
const idle = computed(() => (appState.tab === 'collect' ? '就绪 · 填好左侧条件后点开始采集' : '空闲'))

let off: (() => void) | null = null
onMounted(() => {
  off = onAppEvent((ev) => { if (ev.kind === 'collect') apply(ev.data) })
})
onBeforeUnmount(() => { if (off) { off(); off = null } })
</script>

<template>
  <div v-if="live || booting" class="status-bar busy">
    <span class="pulse"></span>
    <span class="sb-text">
      {{ booting ? '正在启动采集' : st.phase === 'search' ? '正在搜索' : '正在采集' }}<template v-if="kw">「{{ kw }}」</template><template v-if="!booting && st.phase !== 'search'"> · 已入库 {{ st.saved }}<template v-if="st.limit">/{{ st.limit }}</template> 条</template><template v-if="elapsedText"> · {{ elapsedText }}</template>
    </span>
  </div>
  <div v-else-if="ended" class="status-bar idle">
    <span class="sb-text">{{ ended }}</span>
  </div>
  <div v-else class="status-bar idle">
    <span class="sb-text">{{ idle }}</span>
  </div>
</template>

<style scoped>
.status-bar {
  font-size: 12px; line-height: 1; padding: 6px 22px; display: flex; align-items: center; gap: 8px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.status-bar.busy { background: #fff7e6; color: #b26a00; border-bottom: 1px solid #ffe9c7; }
.status-bar.idle { background: #fafafa; color: #b0b0b0; border-bottom: 1px solid #f0f0f0; }
.pulse {
  width: 8px; height: 8px; border-radius: 50%; background: #ff9f0a; flex: none;
  animation: sb-pulse 1.2s ease-in-out infinite;
}
.sb-text { overflow: hidden; text-overflow: ellipsis; }
@keyframes sb-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .25; } }
</style>
