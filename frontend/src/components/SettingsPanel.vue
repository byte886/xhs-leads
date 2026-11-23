<script setup lang="ts">
// SettingsPanel v2.1：设置 Tab —— 只做左侧分类导航 + 右侧分区装配，各分区逻辑在 ./settings/ 子组件
import { ref } from 'vue'
import AiSettings from './settings/AiSettings.vue'
import AccountSettings from './settings/AccountSettings.vue'
import CollectSettings from './settings/CollectSettings.vue'
import AboutSettings from './settings/AboutSettings.vue'
// 逐项行与表单控件的共用样式（scoped 不作用于子组件内部，故提到全局并限定 .settings-body）
import './settings/settings-rows.css'

// ---- 左侧分类导航 ----
// 只留 AI 可进入：其余分类置灰、不可点击也不可切入。
// 「窗口与调试」整块已删除（壳窗口 Tab 开关、引擎实时窗口开关都不再需要）。
const cats = [
  { id: 'ai', label: 'AI', on: true },
  { id: 'account', label: '账号与登录', on: false },
  { id: 'collect', label: '采集设置', on: false },
  { id: 'about', label: '关于与授权', on: false },
]
const activeCat = ref('ai')
</script>

<template>
  <div>
    <div class="settings-layout">
      <!-- 左侧分类导航 -->
      <div class="settings-nav">
        <button
          v-for="c in cats"
          :key="c.id"
          class="nav-item"
          :class="{ cur: activeCat === c.id, off: !c.on }"
          :disabled="!c.on"
          @click="c.on && (activeCat = c.id)"
        >{{ c.label }}</button>
      </div>

      <!-- 右侧内容区：分区只在被切入时挂载，各自管自己的状态与请求 -->
      <div class="settings-body">
        <section v-if="activeCat === 'account'"><AccountSettings /></section>
        <section v-if="activeCat === 'collect'"><CollectSettings /></section>
        <section v-if="activeCat === 'ai'"><AiSettings /></section>
        <section v-if="activeCat === 'about'"><AboutSettings /></section>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 两栏布局：左侧分类导航 + 右侧逐项列表 */
.settings-layout { display: flex; gap: 18px; align-items: flex-start; }
.settings-nav {
  width: 152px; flex-shrink: 0;
  display: flex; flex-direction: column; gap: 2px;
  position: sticky; top: 0;
}
.nav-item {
  text-align: left; padding: 9px 12px; border-radius: var(--r-ctl);
  border: 0; background: transparent;
  font-size: 13px; color: var(--ink-3); cursor: pointer; user-select: none;
  transition: color .15s, background .15s;
}
.nav-item:hover { color: var(--ink); background: var(--panel-2); }
/* 选中用底色 + 内描边表达，不用左侧彩条（>1px 竖条是反模式） */
.nav-item.cur {
  background: var(--panel); color: var(--red); font-weight: 600;
  box-shadow: inset 0 0 0 1px rgba(226, 42, 67, .2);
}
/* 暂不开放的分区：置灰、不可点、不响应 hover */
.nav-item.off {
  color: var(--ink-3); opacity: .45; cursor: default;
}
.nav-item.off:hover { color: var(--ink-3); background: transparent; }
.settings-body { flex: 1; min-width: 0; }

@media (max-width: 720px) {
  .settings-layout { flex-direction: column; }
  .settings-nav { width: 100%; flex-direction: row; overflow-x: auto; position: static; }
  .nav-item { white-space: nowrap; }
}
</style>
