<script setup lang="ts">
// 客户库 Tab：跨天/跨会话累积的互动用户总览（不带 feedId，看全库）
// 清空是显式破坏性动作 -> 两次点击确认，避免误删历史线索
import { onMounted, ref, watch } from 'vue'
import ClientsPanel from './ClientsPanel.vue'
import { apiClientsClear, apiClientsList } from '../api'
import { appState, toast } from '../store'

const panelRef = ref<InstanceType<typeof ClientsPanel> | null>(null)
const total = ref(0)
const clearing = ref(false)
const armed = ref(false)
let disarmTimer: ReturnType<typeof setTimeout> | null = null

async function loadTotal() {
  try {
    const r = await apiClientsList()
    total.value = r.total
  } catch {
    total.value = 0
  }
}

async function refreshAll() {
  await panelRef.value?.refresh()
  await loadTotal()
}

function arm() {
  armed.value = true
  if (disarmTimer) clearTimeout(disarmTimer)
  disarmTimer = setTimeout(() => { armed.value = false }, 4000)
}

async function onClear() {
  if (clearing.value) return
  if (!armed.value) { arm(); return }
  if (disarmTimer) { clearTimeout(disarmTimer); disarmTimer = null }
  armed.value = false
  clearing.value = true
  try {
    const r = await apiClientsClear()
    toast(`已清空客户库 ${r.removed} 条`)
    await refreshAll()
  } catch {
    toast('清空客户库失败')
  } finally {
    clearing.value = false
  }
}

onMounted(() => { refreshAll() })
watch(() => appState.tab, (t) => { if (t === 'clients') refreshAll() })
</script>

<template>
  <div class="lib-wrap">
    <div class="lib-head">
      <div>
        <div class="lib-t">客户库</div>
        <div class="lib-sub">跨次采集累积 · 按人去重 · 同一人取最高意向（共 {{ total }} 人）</div>
      </div>
      <button class="lib-clear" :class="{ armed }" :disabled="clearing" @click="onClear">
        {{ clearing ? '清空中…' : armed ? '再点一次确认清空' : '清空客户库' }}
      </button>
    </div>
    <ClientsPanel ref="panelRef" />
  </div>
</template>

<style scoped>
.lib-wrap { padding: 20px 26px 22px; }
.lib-head {
  display: flex; align-items: flex-start; justify-content: space-between;
  gap: 12px; margin-bottom: 14px;
}
.lib-t { font-size: 18px; font-weight: 700; }
.lib-sub { margin-top: 4px; font-size: 12px; color: var(--ink-3, #888); }
.lib-clear {
  border: 1px solid var(--line, #ececec);
  background: var(--bg-card, #fff);
  color: var(--ink-2, #888);
  border-radius: 8px;
  padding: 6px 12px;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}
.lib-clear:hover:not(:disabled) { border-color: #e3b1b1; color: #b35454; }
.lib-clear.armed { border-color: #d9534f; color: #d9534f; font-weight: 600; background: #fdf1f1; }
.lib-clear:disabled { opacity: .6; cursor: default; }
</style>
