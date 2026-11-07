<script setup lang="ts">
// FlowBar：工作流 4-Tab（采集 → 创作 → 客户 → 设置）
// 登录态 / AI Key 状态徽标已移除：这些状态在「设置」页查看；
// 登录失效由 App 的强制登录对话框提示，无需顶部常驻徽标。
import { appState, setTab } from '../store'
import type { Tab } from '../store'

const tabs: { id: Tab; label: string }[] = [
  { id: 'collect', label: '采集' },
  { id: 'create', label: '创作' },
  { id: 'clients', label: '客户' },
  { id: 'settings', label: '设置' },
]
</script>

<template>
  <div class="flow-wrap">
    <!-- 4-Tab 工作流导航 -->
    <div class="flow">
      <template v-for="(t, i) in tabs" :key="t.id">
        <span v-if="i > 0" class="arrow">›</span>
        <button
          class="tab"
          :class="{ cur: appState.tab === t.id }"
          @click="setTab(t.id)"
        >
          <span class="dot">{{ i + 1 }}</span>{{ t.label }}
        </button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.flow-wrap {
  background: #fff;
  border-bottom: 1px solid #ececec;
  padding: 10px 22px 8px;
}
.flow {
  display: flex;
  align-items: center;
  gap: 2px;
}
.tab {
  border: 0;
  background: transparent;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: #888;
  padding: 8px 16px;
  border-radius: 9px;
  transition: all 0.15s;
}
.tab:hover { background: #f7f7f8; color: #333; }
.tab.cur { background: #fff1e6; color: #b05a1f; }
.dot {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #eee;
  color: #999;
  font-size: 11px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.tab.cur .dot { background: #e0b36b; color: #fff; }
.arrow { color: #ccc; font-size: 14px; padding: 0 2px; }
</style>
