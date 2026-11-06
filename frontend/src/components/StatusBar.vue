<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onAppEvent } from '../events'
import { appState } from '../store'

// R 批 v1.4：全局状态提示条 = 实时播报「正在干什么」（不落盘，区别于审计日志）。
// 数据源 GET /api/status_now（后端聚合采集任务注册表）；2s 轮询；空闲时隐藏。

const st = ref<{ action: string; detail: string; collecting: boolean; phase: string; collected: number; saved: number; limit: number; keyword: string }>({ action: '空闲', detail: '', collecting: false, phase: '', collected: 0, saved: 0, limit: 0, keyword: '' })
// 前端点了「开始采集」、但后端还没推第一条进度（要等抓完第一页卡片，十几秒）时，
// 状态条先按本地状态显示「正在启动」——否则这十几秒一直挂着「空闲」，像没点上。
const booting = computed(() => appState.collecting && !st.value.collecting)
// R-6.53z8c：状态条改为事件驱动——后端在状态变化时推一次，不再每 2 秒查询。
function apply(d: Record<string, any>) {
  const busy = d.status === 'running' || d.status === 'resuming'
  st.value = busy
    ? {
        // 搜索阶段的 collected 是「已扫描的候选卡数」，可能远大于目标条数，
        // 不能当「第几条」念（曾显示「第 20/3 条」）；入库数看 saved。
        action: d.phase === 'search' ? '正在搜索' : '正在采集',
        detail: d.note || '',
        collecting: true,
        phase: d.phase || '',
        collected: d.collected ?? 0,
        saved: d.saved ?? 0,
        limit: d.limit ?? 0,
        keyword: d.keyword || '',
      }
    : {
        // 结束也有留痕：完成显示「已完成 · 已采集 3 条」，停止直接念后端那句话
        action: d.status === 'done' ? '已完成' : d.status === 'stopped' ? '' : '空闲',
        detail: d.note || '',
        collecting: false,
        phase: '',
        collected: 0,
        saved: 0,
        limit: 0,
        keyword: '',
      }
}

let off: (() => void) | null = null
onMounted(() => {
  off = onAppEvent((ev) => { if (ev.kind === 'collect') apply(ev.data) })
})
onBeforeUnmount(() => { if (off) { off(); off = null } })
</script>

<template>
  <div v-if="booting" class="status-bar busy">
    <span class="pulse"></span>
    <span class="sb-text">正在启动采集<template v-if="appState.params.keyword">「{{ appState.params.keyword }}」</template>…</span>
  </div>
  <div v-else-if="st.collecting" class="status-bar busy">
    <span class="pulse"></span>
    <span class="sb-text">
      <template v-if="st.phase === 'search'">
        {{ st.action }}<template v-if="st.keyword">「{{ st.keyword }}」</template>
        · 已扫描 {{ st.collected }} 张<template v-if="st.limit">，目标 {{ st.limit }} 条</template>
      </template>
      <template v-else>
        {{ st.action }}<template v-if="st.keyword">「{{ st.keyword }}」</template>
        · 已入库 {{ st.saved }}<template v-if="st.limit">/{{ st.limit }}</template> 条
      </template>
      <template v-if="st.detail"> · {{ st.detail }}</template>
    </span>
  </div>
  <div v-else class="status-bar idle">
    <span class="sb-text">{{ st.action }}<template v-if="st.detail">{{ st.action ? ' · ' : '' }}{{ st.detail }}</template></span>
  </div>
</template>

<style scoped>
.status-bar {
  font-size: 12px; line-height: 1; padding: 6px 22px; display: flex; align-items: center; gap: 8px;
}
.status-bar.busy { background: #fff7e6; color: #b26a00; border-bottom: 1px solid #ffe9c7; }
.status-bar.idle { background: #fafafa; color: #b0b0b0; border-bottom: 1px solid #f0f0f0; }
.pulse {
  width: 8px; height: 8px; border-radius: 50%; background: #ff9f0a;
  animation: sb-pulse 1.2s ease-in-out infinite;
}
@keyframes sb-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .25; } }
</style>
