<script setup lang="ts">
// FlowBar v1.5：工作流 4-Tab（采集 → 创作 → 客户 → 设置）+ 全局共享状态条
// 状态条 = 共享观察落点：登录态 / CDP / AI Key 一屏可见，任何 Tab 都能看到，点击跳对应设置
import { onMounted } from 'vue'
import { appState, setTab, refreshSysStatus } from '../store'
import type { Tab } from '../store'
import { apiLoginStatus, apiCdpStatus, apiGetLLMConfig } from '../api'

const tabs: { id: Tab; label: string; hint: string }[] = [
  { id: 'collect', label: '采集', hint: '关键词采集笔记 · 评分排序' },
  { id: 'create', label: '创作', hint: '克隆 + AI 文稿 + 发布前质检' },
  { id: 'clients', label: '客户', hint: '线索 / 聚合 / 获客指引' },
  { id: 'settings', label: '设置', hint: 'AI Key / CDP / 账号人设' },
]

function jumpToSettings() {
  setTab('settings')
}

onMounted(async () => {
  // 一次性拉取全局共享状态（幂等；失败静默，保持旧值）
  try {
    const lg = await apiLoginStatus()
    appState.login = { logged_in: !!lg.logged_in, nickname: lg.nickname || '' }
  } catch { /* 后端未就绪 */ }
  try {
    const c = await apiCdpStatus()
    refreshSysStatus({ cdp: { ok: c.ok, state: c.state, chrome_found: c.chrome_found, profile_dir: c.profile_dir, port: c.port } })
  } catch { /* 未就绪 */ }
  try {
    const m = await apiGetLLMConfig()
    refreshSysStatus({ aiKey: !!m.configured })
  } catch { /* 未就绪 */ }
})
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
          :title="t.hint"
          @click="setTab(t.id)"
        >
          <span class="dot">{{ i + 1 }}</span>{{ t.label }}
        </button>
      </template>
      <div class="flow-hint">{{ tabs.find(t => t.id === appState.tab)?.hint }}</div>
    </div>

    <!-- 全局共享状态条（跨页观察：登录 / CDP / AI Key） -->
    <div class="sysbar">
      <button class="sys-item" :class="appState.login.logged_in ? 'ok' : 'bad'" @click="appState.login.logged_in ? jumpToSettings() : undefined" title="点设置页查看连接详情">
        <span class="dot-s" :class="appState.login.logged_in ? 'on' : 'off'"></span>
        登录 {{ appState.login.logged_in ? (appState.login.nickname || '已登录') : '未登录' }}
      </button>
      <button class="sys-item" :class="appState.sys.cdp.ok ? 'ok' : (appState.sys.cdp.state === 'not_configured' ? 'bad' : 'mid')" @click="jumpToSettings()">
        <span class="dot-s" :class="appState.sys.cdp.ok ? 'on' : 'off'"></span>
        CDP {{ appState.sys.cdp.ok ? '就绪' : (appState.sys.cdp.state === 'not_configured' ? '未配置' : '检测中') }}
      </button>
      <button class="sys-item" :class="appState.sys.aiKey ? 'ok' : 'bad'" @click="jumpToSettings()">
        <span class="dot-s" :class="appState.sys.aiKey ? 'on' : 'off'"></span>
        AI Key {{ appState.sys.aiKey ? '已配置' : '未配置' }}
      </button>
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
.flow-hint { margin-left: auto; font-size: 12px; color: #aaa; }
.sysbar {
  display: flex;
  gap: 8px;
  margin-top: 8px;
  flex-wrap: wrap;
}
.sys-item {
  border: 1px solid #ececec;
  background: #fafafa;
  font-size: 12px;
  color: #666;
  border-radius: 8px;
  padding: 4px 10px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  transition: all 0.15s;
}
.sys-item:hover { border-color: #d5d5d5; }
.sys-item.ok { border-color: #bfe8d0; background: #f2fbf5; color: #1a7f4c; }
.sys-item.bad { border-color: #f3c1c1; background: #fdf3f3; color: #b03030; }
.sys-item.mid { border-color: #f0d9a8; background: #fff9f0; color: #8a5a1f; }
.dot-s {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.dot-s.on { background: #22c55e; }
.dot-s.off { background: #e5484d; }
</style>
