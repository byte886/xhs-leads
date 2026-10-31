<script setup lang="ts">
// ClientsPanel v1.5：客户 Tab（O1-3 评论区线索 + O1-4 聚合 + O1-5 获客指引）
// 采集完成/新增线索后由 App.vue 调 refresh() 重新拉取
import { onMounted, ref } from 'vue'
import type { ClientUser, ClientsAgg } from '../types'
import { apiClientsList, apiClientsAgg } from '../api'

const clients = ref<ClientUser[]>([])
const agg = ref<ClientsAgg | null>(null)
const clTab = ref<'all' | 'high' | '7d'>('all')

// O1-3 意向档位展示映射（与后端 intent.py 一致）
const INTENT_META: Record<string, { label: string; cls: string }> = {
  high: { label: '高意向', cls: 'i-high' },
  upper: { label: '上意向', cls: 'i-upper' },
  middle: { label: '中意向', cls: 'i-mid' },
  low: { label: '低意向', cls: 'i-low' },
}
const intentOf = (c: ClientUser) => c.intent || 'low'
const intentLabel = (c: ClientUser) => INTENT_META[intentOf(c)]?.label || '低意向'
const intentCls = (c: ClientUser) => INTENT_META[intentOf(c)]?.cls || 'i-low'

/** 客户库：按意向/时间筛选重取（all/high/7d） */
async function loadClients() {
  try {
    let intent = ''
    if (clTab.value === 'high') intent = 'high'
    const res = await apiClientsList('', intent)
    let list = res.clients || []
    if (clTab.value === '7d') {
      const weekAgo = Date.now() - 7 * 86400_000
      list = list.filter((c) => {
        const t = c.created_at ? new Date(c.created_at).getTime() : 0
        return t >= weekAgo
      })
    }
    clients.value = list
  } catch {
    clients.value = []
  }
}

/** O1-4 加载聚合数据（线索总数/意向分布/属地TOP/近7天/留联系方式） */
async function loadAgg() {
  try {
    agg.value = await apiClientsAgg()
  } catch {
    agg.value = null
  }
}

/** 外部刷新入口（App.vue 采集客户成功后调用） */
async function refresh() {
  await Promise.all([loadClients(), loadAgg()])
}

/** 挂载即加载：刷新/重进页面后客户库反映 DB 真实状态（否则显示"暂无客户"造成数据丢失假象） */
onMounted(() => { refresh() })

async function setClTab(tab: 'all' | 'high' | '7d') {
  clTab.value = tab
  await loadClients()
}

/** O1-3 统计：总数 / 高意向数 / 留联系方式命中 */
function clSummary() {
  let high = 0
  let contact = 0
  for (const c of clients.value) {
    if (c.intent === 'high') high += 1
    if (c.intent === 'high' && /微信|vx|v信|卫星|手机|电话|滴滴|私信|si me|si 我|si我|v /.test((c.matched_word || '') + (c.sample_comment || '').toLowerCase())) contact += 1
  }
  return { total: clients.value.length, high, contact }
}

/** O1-3 导出线索 CSV（昵称/主页链接/评论原文/命中词/IP属地/意向） */
function exportClientsCsv() {
  const head = ['昵称', '主页链接', '评论原文', '命中词', 'IP属地', '意向', '采集时间']
  const rows = clients.value.map((c) => [
    c.nickname || '', c.profile || '', (c.sample_comment || '').replace(/"/g, '""'),
    c.matched_word || '', c.ip_location || '', intentLabel(c), (c.created_at || '').slice(0, 10),
  ])
  const csv = '\ufeff' + [head, ...rows].map((r) => r.map((v) => `"${v}"`).join(',')).join('\n')
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  a.download = `线索导出_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}

/** O1-5 去联系：跳转用户主页，由用户自行沟通（工具不代写话术、不自动发送） */
function goTalk(c: ClientUser) {
  if (c.profile) window.open(c.profile, '_blank')
  // eslint-disable-next-line no-alert
  alert('已打开该用户主页。工具不提供话术、不代发私信：请自行沟通，注意平台私信限额与导流词红线（详见「获客指引」）。')
}

/** O1-5 获客指引折叠态 */
const guideOpen = ref(true)

defineExpose({ refresh })
</script>

<template>
  <div>
    <div class="cl-empty-wrap" v-if="!clients.length">
      <div class="big">暂无客户</div>
      <p>在采集页卡片上点「采集客户」抓取笔记评论区的互动用户，会在这里按意向分级展示（高意向置顶）</p>
    </div>
    <template v-else>
      <div class="info-bar">
        <div class="info-item"><span class="k">线索总数</span><span class="v">{{ agg?.total ?? clSummary().total }}</span></div>
        <div class="info-item"><span class="k">高意向</span><span class="v" style="color:#FF2442">{{ clSummary().high }}</span></div>
        <div class="info-item"><span class="k">留联系方式命中</span><span class="v" style="color:#00A86B">{{ agg?.contact ?? clSummary().contact }}</span></div>
        <div class="info-item"><span class="k">采集说明</span><span class="v small">高意向置顶；「去联系」跳转用户主页，沟通与话术由你自行完成，工具不代写、不自动发送</span></div>
        <div style="margin-left:auto;display:flex;gap:8px">
          <button class="btn sm primary" @click="exportClientsCsv">导出线索 CSV</button>
        </div>
      </div>

      <!-- O1-5 获客指引：怎么办 + 风险在哪（不做话术生成） -->
      <div class="guide-card" :class="{ closed: !guideOpen }">
        <div class="guide-head" @click="guideOpen = !guideOpen">
          <span class="guide-title">🛡️ 获客指引（怎么办 + 风险在哪）</span>
          <span class="guide-toggle">{{ guideOpen ? '收起 ▲' : '展开 ▼' }}</span>
        </div>
        <div v-if="guideOpen" class="guide-body">
          <div class="guide-block">
            <div class="guide-h">怎么办</div>
            <ul class="guide-ul">
              <li>对方在评论区主动问需求 = 有对话基础，是优先触达对象</li>
              <li>建议先在其评论区回应引导，再考虑私信（<b>被动回复优先</b>，主动私信限制多、风险高）</li>
              <li>点「去联系」打开其主页，沟通内容由你自行组织</li>
            </ul>
          </div>
          <div class="guide-block">
            <div class="guide-h">风险在哪（平台规则，2026 现行）</div>
            <ul class="guide-ul">
              <li><b>私信限额</b>：未认证号每日主动私信 ≤5 个陌生人，认证号 ≤20；10 分钟内 ≥10 条触发风控；未互关用户对方未回复前只能发 1 条</li>
              <li><b>导流词红线</b>：微信 / VX / 薇 / 溦 / 电话 / QQ / 邮箱 / 二维码（含谐音）一律禁止——2025-01 起平台要求商家交换联系方式只能走官方「私信获客工具」</li>
              <li><b>同文案重复发送</b>会被屏蔽；批量群发营销为平台打击对象</li>
            </ul>
          </div>
        </div>
      </div>

      <!-- O1-4 聚合区：意向分布 / 属地 TOP / 近 7 天产出 -->
      <div v-if="agg" class="agg-grid">
        <div class="agg-card">
          <div class="agg-title">意向分布</div>
          <div class="agg-dist">
            <div v-for="d in agg.intent_dist" :key="d.intent" class="agg-dist-row">
              <span class="agg-dist-label">{{ d.label }}</span>
              <div class="agg-bar"><div class="agg-bar-fill" :class="'a-' + d.intent" :style="{ width: agg.total ? Math.max(4, (d.count / agg.total) * 100) + '%' : '0%' }"></div></div>
              <span class="agg-dist-count">{{ d.count }}</span>
            </div>
          </div>
        </div>
        <div class="agg-card">
          <div class="agg-title">属地分布 TOP</div>
          <div v-if="agg.ip_top.length" class="agg-ip">
            <div v-for="t in agg.ip_top" :key="t.ip" class="agg-ip-row">
              <span class="agg-ip-name">📍 {{ t.ip }}</span><span class="agg-ip-count">{{ t.count }} 人</span>
            </div>
          </div>
          <div v-else class="agg-empty">暂无属地数据</div>
        </div>
        <div class="agg-card agg-week-card">
          <div class="agg-title">近 7 天产出</div>
          <div class="agg-week">
            <div v-for="w in agg.week" :key="w.date" class="agg-week-col">
              <div class="agg-week-bar" :style="{ height: Math.max(4, (w.count / Math.max(1, agg.total)) * 60) + 'px' }">{{ w.count || '' }}</div>
              <div class="agg-week-day">{{ w.date.slice(5) }}</div>
            </div>
          </div>
        </div>
      </div>

      <div style="display:flex;gap:6px;margin-bottom:10px">
        <button class="cl-tab" :class="{ on: clTab === 'all' }" @click="setClTab('all')">全部 {{ clients.length }}</button>
        <button class="cl-tab" :class="{ on: clTab === 'high' }" @click="setClTab('high')">高意向 {{ clSummary().high }}</button>
        <button class="cl-tab" :class="{ on: clTab === '7d' }" @click="setClTab('7d')">近 7 天</button>
      </div>
      <div class="cl-list">
        <div v-for="c in clients" :key="c.uid" class="cl-row">
          <div class="cl-av au-ph">{{ (c.nickname || '客')[0] }}</div>
          <div class="cl-main">
            <div class="cl-top">
              <span class="cl-nick">{{ c.nickname || '未知用户' }}</span>
              <span class="cl-intent" :class="intentCls(c)">{{ intentLabel(c) }}</span>
              <span v-if="c.matched_word" class="cl-word">命中「{{ c.matched_word }}」</span>
              <span v-if="c.ip_location" class="cl-ip">📍 {{ c.ip_location }}</span>
              <span v-if="c.comment_time" class="cl-time">{{ c.comment_time }}</span>
              <a v-if="c.profile" :href="c.profile" target="_blank" class="cl-link" @click.stop>主页 ↗</a>
            </div>
            <div class="cl-cmt">“{{ (c.sample_comment || '—').slice(0, 90) }}”</div>
            <div class="cl-foot">足迹 {{ c.comment_count }} 次 · 关联 {{ c.note_count }} 篇 · 采集于 {{ (c.created_at || '').slice(0, 10) }}</div>
          </div>
          <div class="cl-act">
            <button class="btn sm" @click="goTalk(c)">去联系</button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.cl-empty-wrap { padding: 60px 0; text-align: center; }
.cl-empty-wrap .big { font-size: 18px; font-weight: 700; margin-bottom: 8px; }
.cl-empty-wrap p { font-size: 13px; color: var(--ink-2, #888); }
/* ===== O1-3 客户库：评论区线索列表 ===== */
.cl-tab {
  border: 1px solid var(--line, #ececec);
  background: var(--bg-card, #fff);
  color: var(--ink-2, #888);
  border-radius: 8px;
  padding: 5px 12px;
  font-size: 12px;
  cursor: pointer;
}
.cl-tab.on {
  border-color: #e0b36b;
  color: #b08a4d;
  font-weight: 600;
  background: #fff8ef;
}
.cl-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cl-row {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--bg-card, #fff);
  border: 1px solid var(--line, #ececec);
  border-radius: 12px;
  padding: 12px 14px;
}
.cl-row:hover {
  border-color: #e0b36b;
}
.cl-av {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  flex-shrink: 0;
  font-size: 16px;
}
.cl-main {
  flex: 1;
  min-width: 0;
}
.cl-top {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.cl-nick {
  font-weight: 600;
  font-size: 14px;
}
.cl-intent {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 6px;
  color: #fff;
}
.cl-intent.i-high { background: #FF2442; }
.cl-intent.i-upper { background: #FF9D5C; }
.cl-intent.i-mid { background: #F79009; }
.cl-intent.i-low { background: #9AA5B1; }
.cl-word {
  font-size: 11px;
  color: #b08a4d;
  background: #fff8ef;
  border-radius: 6px;
  padding: 2px 6px;
}
.cl-ip {
  font-size: 11px;
  color: var(--ink-2, #888);
}
.cl-time {
  font-size: 11px;
  color: var(--ink-2, #999);
}
.cl-link {
  margin-left: auto;
  font-size: 12px;
  color: #b08a4d;
  text-decoration: none;
}
.cl-cmt {
  margin-top: 5px;
  font-size: 12px;
  color: var(--ink-2, #666);
  line-height: 1.5;
}
.cl-foot {
  margin-top: 4px;
  font-size: 11px;
  color: var(--ink-2, #999);
}
.cl-act {
  flex-shrink: 0;
}
/* ===== O1-5 获客指引卡片 ===== */
.guide-card {
  background: linear-gradient(135deg, #fff9f0, #fff3e0);
  border: 1px solid #f0d9a8;
  border-radius: 12px;
  margin-bottom: 12px;
  overflow: hidden;
}
.guide-card.closed .guide-body { display: none; }
.guide-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 700;
  color: #8a5a1f;
}
.guide-toggle { font-size: 11px; color: #b08a4d; font-weight: 500; }
.guide-body {
  display: grid;
  grid-template-columns: 1fr 1.6fr;
  gap: 10px;
  padding: 0 14px 12px;
}
.guide-block { font-size: 12px; }
.guide-h { font-weight: 700; color: #8a5a1f; margin-bottom: 6px; }
.guide-ul { margin: 0; padding-left: 16px; color: #5b4632; line-height: 1.7; }
/* ===== O1-4 聚合区 ===== */
.agg-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1.4fr;
  gap: 10px;
  margin-bottom: 12px;
}
.agg-card {
  background: var(--bg-card, #fff);
  border: 1px solid var(--line, #ececec);
  border-radius: 12px;
  padding: 12px 14px;
}
.agg-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--ink-1, #222);
  margin-bottom: 10px;
}
.agg-dist {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.agg-dist-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.agg-dist-label {
  width: 44px;
  color: var(--ink-2, #666);
  flex-shrink: 0;
}
.agg-bar {
  flex: 1;
  height: 10px;
  background: var(--bg-2, #f4f5f6);
  border-radius: 5px;
  overflow: hidden;
}
.agg-bar-fill {
  height: 100%;
  border-radius: 5px;
}
.agg-bar-fill.a-high { background: #FF2442; }
.agg-bar-fill.a-upper { background: #FF9D5C; }
.agg-bar-fill.a-middle { background: #F79009; }
.agg-bar-fill.a-low { background: #9AA5B1; }
.agg-dist-count {
  width: 20px;
  text-align: right;
  font-weight: 600;
  color: var(--ink-1, #222);
}
.agg-ip {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.agg-ip-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  padding: 3px 0;
  border-bottom: 1px dashed var(--line, #f0f0f0);
}
.agg-ip-name { color: var(--ink-1, #333); }
.agg-ip-count { color: var(--ink-2, #888); }
.agg-empty {
  font-size: 12px;
  color: var(--ink-2, #aaa);
  padding: 12px 0;
}
.agg-week {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  height: 76px;
}
.agg-week-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  height: 100%;
}
.agg-week-bar {
  width: 100%;
  max-width: 26px;
  min-height: 4px;
  background: linear-gradient(180deg, #e0b36b, #b08a4d);
  border-radius: 4px 4px 0 0;
  font-size: 10px;
  color: #b08a4d;
  text-align: center;
}
.agg-week-day {
  font-size: 10px;
  color: var(--ink-2, #999);
  margin-top: 4px;
}
</style>
