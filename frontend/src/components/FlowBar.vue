<script setup lang="ts">
// FlowBar：2 个平级功能选项卡（采集 / 设置；客户库走「采集客户」弹层，档案页暂不露出）
// 它们是并列的功能入口，不是先后步骤——所以用分段选项卡呈现，不带序号、不带箭头。
// 登录态 / AI Key 状态徽标已移除：这些状态在「设置」页查看。
import { appState, setTab } from '../store'
import type { Tab } from '../store'

const tabs: { id: Tab; label: string }[] = [
  { id: 'collect', label: '采集' },
  { id: 'settings', label: '设置' },
]
</script>

<template>
  <div class="flow-wrap">
    <nav class="tabs" role="tablist" aria-label="功能导航">
      <button
        v-for="t in tabs"
        :key="t.id"
        class="tab"
        :class="{ cur: appState.tab === t.id }"
        role="tab"
        :aria-selected="appState.tab === t.id"
        @click="setTab(t.id)"
      >{{ t.label }}</button>
    </nav>
  </div>
</template>

<style scoped>
.flow-wrap {
  background: var(--panel);
  border-bottom: 1px solid var(--line);
  padding: 9px 22px;
}
/* 分段控件：浅底槽 + 选中项浮起成白色卡片。
   比「带序号的胶囊 + 箭头」弱化流程感，五个入口看起来是并列的。 */
.tabs {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  background: var(--panel-2);
  border-radius: var(--r-ctl);
  padding: 3px;
}
.tab {
  border: 0;
  background: transparent;
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--ink-3);
  padding: 5px 16px;
  border-radius: 4px;
  transition: background .15s, color .15s, box-shadow .15s;
}
.tab:hover:not(.cur) { color: var(--ink); }
.tab:focus-visible { outline: 2px solid var(--ring); outline-offset: 1px; }
.tab.cur {
  background: var(--panel);
  color: var(--red);
  box-shadow: 0 1px 3px rgba(28, 20, 16, .08);
}
</style>
