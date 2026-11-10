<script setup lang="ts">
// FlowBar：工作流 5-Tab（采集 → 创作 → 客户 → 档案 → 设置）
// 登录态 / AI Key 状态徽标已移除：这些状态在「设置」页查看；
// 登录失效由 App 的强制登录对话框提示，无需顶部常驻徽标。
import { appState, setTab } from '../store'
import type { Tab } from '../store'

const tabs: { id: Tab; label: string }[] = [
  { id: 'collect', label: '采集' },
  { id: 'create', label: '创作' },
  { id: 'clients', label: '客户' },
  { id: 'profiles', label: '档案' },
  { id: 'settings', label: '设置' },
]
</script>

<template>
  <div class="flow-wrap">
    <!-- 5-Tab 工作流导航 -->
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
  background: var(--panel);
  border-bottom: 1px solid var(--line);
  padding: 10px 22px 8px;
}
.flow {
  display: flex;
  align-items: center;
  gap: 2px;
}
/* 当前项只用一个 accent（小红书面板上的米橙棕是第二强调色，已去掉） */
.tab {
  border: 0;
  background: transparent;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink-3);
  padding: 7px 13px;
  border-radius: var(--r-ctl);
  transition: background .15s, color .15s;
}
.tab:hover { background: var(--panel-2); color: var(--ink); }
.tab:focus-visible { outline: 2px solid var(--ring); outline-offset: 1px; }
.tab.cur { background: var(--red-soft); color: var(--red); }
.dot {
  width: 19px;
  height: 19px;
  border-radius: 50%;
  background: var(--panel-2);
  color: var(--ink-3);
  font-size: 11px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.tab.cur .dot { background: var(--red); color: #fff; }
.arrow { color: rgba(43, 30, 24, .22); font-size: 13px; padding: 0 1px; }
</style>
