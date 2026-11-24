<script setup lang="ts">
// ClientsPanel v1.5：客户 Tab（O1-3 评论区线索 + O1-4 聚合 + O1-5 获客指引）
// 采集完成/新增线索后由 App.vue 调 refresh() 重新拉取
import { computed, onMounted, ref } from 'vue'
import type { ClientUser } from '../types'
import { apiClientsList, apiContactOpen } from '../api'
import { toast } from '../store'

// 两种用法：卡片右键弹层传 feedId（只看这一篇）；「客户库」Tab 不传（看跨会话累积的全库）
const props = defineProps<{ feedId?: string }>()
const isGlobal = computed(() => !props.feedId)

const clients = ref<ClientUser[]>([])
const clTab = ref<'all' | 'high'>('all')



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
    const res = await apiClientsList(props.feedId || '', intent)
    // 一人可有多条评论，逐条展示；「XX关注」这类动作记录老数据里可能还在，逐条挡掉
    clients.value = (res.clients || [])
      .map((c) => ({
        ...c,
        comments: (c.comments || []).filter(
          (cm) => !(cm.trim().endsWith('关注') && cm.trim().length <= 24),
        ),
      }))
      .filter((c) => c.comments.length > 0)
  } catch {
    clients.value = []
  }
}

/** 外部刷新入口（App.vue 采集客户成功后调用） */
async function refresh() {
  await loadClients()
}

/** 挂载即加载：刷新/重进页面后客户库反映 DB 真实状态（否则显示"暂无客户"造成数据丢失假象） */
onMounted(() => { refresh() })

async function setClTab(tab: 'all' | 'high') {
  clTab.value = tab
  await loadClients()
}

/** 高意向人数（筛选用） */
const highCount = computed(() => clients.value.filter((c) => c.intent === 'high').length)

/** 意向分布 / 属地 TOP：本地按人去重统计，不再走独立的聚合接口 */
const aggLocal = computed(() => {
  const dist: Record<string, number> = { high: 0, upper: 0, middle: 0, low: 0 }
  const ip: Record<string, number> = {}
  for (const c of clients.value) {
    const it = c.intent || 'low'
    dist[it] = (dist[it] || 0) + 1
    const loc = (c.ip_location || '').trim()
    if (loc) ip[loc] = (ip[loc] || 0) + 1
  }
  return {
    total: clients.value.length,
    intent_dist: [
      { intent: 'high', label: '高意向', count: dist.high },
      { intent: 'upper', label: '上意向', count: dist.upper },
      { intent: 'middle', label: '中意向', count: dist.middle },
      { intent: 'low', label: '低意向', count: dist.low },
    ],
    ip_top: Object.entries(ip).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([k, v]) => ({ ip: k, count: v })),
  }
})

/** O1-5 去联系：后端在采集窗打开用户主页并点聊天按钮，落到私信会话页（工具不代写、不代发）。 */
const talkingUid = ref('')
async function goTalk(c: ClientUser) {
  if (!c.profile) {
    toast('该客户缺少主页链接，无法打开私信', 'error')
    return
  }
  if (talkingUid.value) return
  talkingUid.value = c.uid
  try {
    const r = await apiContactOpen(c.profile)
    toast(r.note || '已打开私信会话页')
  } catch (e) {
    toast('去联系失败：' + (e as Error).message, 'error')
  } finally {
    talkingUid.value = ''
  }
}

/** O1-5 获客指引折叠态 */
const guideOpen = ref(true)

/** 当前列表实际人数（App 用它播报采集结果，保证状态条与明细同一口径） */
function count() {
  return clients.value.length
}

defineExpose({ refresh, count })
</script>

<template>
  <div>
    <div class="cl-empty-wrap" v-if="!clients.length">
      <div class="big">{{ isGlobal ? '客户库还没有客户' : '这篇笔记还没有采集到客户' }}</div>
      <p v-if="isGlobal">在采集页卡片上右键点「采集客户（本篇评论区）」，抓到的互动用户会按意向分级累积到这里（跨次采集按人去重）</p>
      <p v-else>在采集页卡片上右键点「采集客户（本篇评论区）」，抓到的互动用户会按意向分级列在这里</p>
    </div>
    <template v-else>
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
              <li>点「去联系」打开与该用户的私信会话页，沟通内容由你自行组织</li>
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

      <!-- 聚合区：意向分布 / 属地 TOP（都基于当前列表按人计） -->
      <div v-if="clients.length" class="agg-grid">
        <div class="agg-card">
          <div class="agg-title">意向分布</div>
          <div class="agg-dist">
            <div v-for="d in aggLocal.intent_dist" :key="d.intent" class="agg-dist-row">
              <span class="agg-dist-label">{{ d.label }}</span>
              <div class="agg-bar"><div class="agg-bar-fill" :class="'a-' + d.intent" :style="{ width: aggLocal.total ? Math.max(4, (d.count / aggLocal.total) * 100) + '%' : '0%' }"></div></div>
              <span class="agg-dist-count">{{ d.count }}</span>
            </div>
          </div>
        </div>
        <div class="agg-card">
          <div class="agg-title">属地分布 TOP</div>
          <div v-if="aggLocal.ip_top.length" class="agg-ip">
            <div v-for="t in aggLocal.ip_top" :key="t.ip" class="agg-ip-row">
              <span class="agg-ip-name">📍 {{ t.ip }}</span><span class="agg-ip-count">{{ t.count }} 人</span>
            </div>
          </div>
          <div v-else class="agg-empty">暂无属地数据</div>
        </div>
      </div>

      <div style="display:flex;gap:6px;margin-bottom:10px">
        <button class="cl-tab" :class="{ on: clTab === 'all' }" @click="setClTab('all')">全部 {{ clients.length }}</button>
        <button class="cl-tab" :class="{ on: clTab === 'high' }" @click="setClTab('high')">高意向 {{ highCount }}</button>
      </div>
      <div class="cl-list">
        <div v-for="c in clients" :key="c.uid" class="cl-row">
          <div class="cl-main">
            <div class="cl-top">
              <span class="cl-nick">{{ c.nickname || '未知用户' }}</span>
              <span class="cl-intent" :class="intentCls(c)">{{ intentLabel(c) }}</span>
              <span v-if="c.matched_word" class="cl-word">命中「{{ c.matched_word }}」</span>
              <span v-if="c.ip_location" class="cl-ip">📍 {{ c.ip_location }}</span>
              <span v-if="c.comment_time" class="cl-time">{{ c.comment_time }}</span>
            </div>
            <div v-for="(cm, ci) in c.comments" :key="ci" class="cl-cmt">“{{ cm.slice(0, 90) }}”</div>
            <div class="cl-foot">足迹 {{ c.comment_count }} 次 · 关联 {{ c.note_count }} 篇 · 采集于 {{ (c.created_at || '').slice(0, 10) }}</div>
          </div>
          <div class="cl-act">
            <a v-if="c.profile" :href="c.profile" target="_blank" class="cl-home" @click.stop>主页</a>
            <button class="cl-talk" :disabled="talkingUid === c.uid" @click="goTalk(c)">
              {{ talkingUid === c.uid ? '打开中…' : '去联系' }}
            </button>
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
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 7px;
}
.cl-home {
  font-size: 12px;
  color: #b08a4d;
  text-decoration: none;
}
.cl-home:hover { text-decoration: underline; }
.cl-talk {
  min-width: 76px;
  padding: 7px 14px;
  border: 1px solid var(--red);
  border-radius: 6px;
  background: var(--red);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.2;
  text-align: center;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.cl-talk:hover:not(:disabled) { background: #e51f3a; border-color: #e51f3a; }
.cl-talk:disabled { opacity: 0.6; cursor: default; }
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
</style>
