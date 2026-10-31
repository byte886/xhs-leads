<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { apiStatusNow } from '../api'

// R 批 v1.4：全局状态提示条 = 实时播报「正在干什么」（不落盘，区别于审计日志）。
// 数据源 GET /api/status_now（后端聚合采集任务注册表）；2s 轮询；空闲时隐藏。

const st = ref<{ action: string; detail: string; collecting: boolean; collected?: number; limit?: number; keyword?: string }>({ action: '空闲', detail: '', collecting: false })
let timer: ReturnType<typeof setInterval> | null = null

async function poll() {
  try {
    st.value = await apiStatusNow()
  } catch { /* 后端未就绪：保持旧值 */ }
}

onMounted(() => { poll(); timer = setInterval(poll, 2000) })
onBeforeUnmount(() => { if (timer) { clearInterval(timer); timer = null } })
</script>

<template>
  <div v-if="st.collecting" class="status-bar busy">
    <span class="pulse"></span>
    <span class="sb-text">
      {{ st.action }}<template v-if="st.keyword">「{{ st.keyword }}」</template>
      第 {{ st.collected }}/{{ st.limit }} 条<template v-if="st.detail"> · {{ st.detail }}</template>
    </span>
  </div>
  <div v-else class="status-bar idle">
    <span class="sb-text">{{ st.action }}</span>
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
