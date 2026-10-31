<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Note } from '../types'
import { coverProxy } from '../store'
import { appState, selectedCount, toast } from '../store'

const props = defineProps<{
  notes: Note[]
  selectable?: boolean
  filterText?: string
}>()

const emit = defineEmits<{
  (e: 'menu', payload: { note: Note; x: number; y: number }): void
  (e: 'clone', note: Note): void
  (e: 'clients', note: Note): void
}>()

const filtered = computed(() => {
  const q = (props.filterText || '').trim().toLowerCase()
  if (!q) return props.notes
  // 多字段筛选：标题 / 作者昵称 / 笔记ID（feed_id），任一命中即显示
  return props.notes.filter(n =>
    (n.title || '').toLowerCase().includes(q) ||
    (n.nickname || '').toLowerCase().includes(q) ||
    (n.feed_id || '').toLowerCase().includes(q)
  )
})

function fmt(n: number | null | undefined): string {
  const v = n || 0
  return v >= 10000 ? (v / 10000).toFixed(1) + '万' : String(v)
}

/** 封面图：本地代理优先（同源无防盗链 403），图床直连兜底（no-referrer 绕过 Referer 校验） */
function coverOf(note: Note): string {
  return note.local_cover || coverProxy(note.cover_url || note.local_images?.[0] || '')
}

const broken = new Set<string>()
function onImgError(note: Note) {
  broken.add(note.feed_id)
}function phStyle(note: Note): Record<string, string> {
  const palette = ['#9A6A2F', '#7B8C6B', '#B4553D', '#5A7D8C', '#8C6B7B']
  let h = 0
  for (const ch of note.feed_id || '') h = (h * 31 + ch.charCodeAt(0)) % 997
  return { background: palette[h % palette.length] }
}

function onCardClick(note: Note, ev: MouseEvent) {
  if (props.selectable) {
    // 勾选模式：点击卡片切换选中；点右上角勾选标记同样切换
    const feedId = note.feed_id
    if (appState.selected[feedId]) delete appState.selected[feedId]
    else appState.selected[feedId] = true
    return
  }
  emit('menu', { note, x: ev.clientX, y: ev.clientY })
}

function isSel(note: Note) {
  return !!appState.selected[note.feed_id]
}

function openOrigin(note: Note) {
  const url = note.origin ||
    `https://www.xiaohongshu.com/explore/${note.feed_id}${note.xsec_token ? `?xsec_token=${note.xsec_token}` : ''}`
  window.open(url, '_blank')
}

function selectAll() {
  // 全选图文：只选中图文笔记（视频笔记暂不可克隆，仅图文可进克隆/导出）
  const visible = filtered.value
  const pics = visible.filter(n => n.note_type !== 'video')
  const vids = visible.length - pics.length
  pics.forEach(n => { appState.selected[n.feed_id] = true })
  toast(vids > 0 ? `已全选 ${pics.length} 条图文（${vids} 条视频已排除）` : `已全选 ${pics.length} 条图文`)
}

function clearSelect() {
  appState.selected = {}
  toast('已清空选择')
}

// 下载图片包（P3）：后端把选中笔记的本地封面打包 zip，走 blob 下载
async function downloadImages() {
  const ids = Object.keys(appState.selected)
  if (!ids.length) { toast('请先勾选笔记'); return }
  let resp
  try {
    resp = await fetch('/api/download_images', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ feed_ids: ids }),
    })
  } catch {
    toast('打包失败：网络错误')
    return
  }
  if (!resp.ok) {
    toast('打包失败：' + ((await resp.text()) || resp.status).toString().slice(0, 80))
    return
  }
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `xhs_images_${Date.now()}.zip`
  document.body.appendChild(a)
  a.click()
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url) }, 1000)
  let missing = 0
  try { missing = JSON.parse(resp.headers.get('X-Missing') || '[]').length } catch { /* 忽略 */ }
  toast(missing > 0 ? `已打包 ${ids.length - missing} 张图片（${missing} 张缺失），文件已保存到浏览器下载目录` : `已打包 ${ids.length} 张图片，文件已保存到浏览器下载目录`)
}

function exportJSON() {
  const data = props.notes.map(n => ({
    feed_id: n.feed_id, title: n.title, nickname: n.nickname,
    liked_count: n.liked_count, collected_count: n.collected_count,
    comment_count: n.comment_count, desc: n.desc, cover_url: n.cover_url,
    origin: n.xsec_token ? `https://www.xiaohongshu.com/explore/${n.feed_id}?xsec_token=${n.xsec_token}` : ''
  }))
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `xhs_notes_${Date.now()}.json`
  document.body.appendChild(a)   // 必须挂载 DOM 才会真正触发下载
  a.click()
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(a.href) }, 1000)  // 延迟 revoke，等下载启动
  toast(`已导出 ${data.length} 条 JSON，文件已保存到浏览器下载目录`)
}

function exportCSV() {
  const rows = props.notes.map(n => [
    n.title || '', n.nickname || '', n.liked_count || 0,
    n.collected_count || 0, n.comment_count || 0,
    n.desc ? n.desc.replace(/[\r\n]+/g, ' ') : ''
  ])
  const csv = ['标题,作者,点赞,收藏,评论,正文摘要', ...rows.map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(','))].join('\n')
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `xhs_notes_${Date.now()}.csv`
  document.body.appendChild(a)   // 必须挂载 DOM 才会真正触发下载
  a.click()
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(a.href) }, 1000)  // 延迟 revoke
  toast(`已导出 ${dataCount()} 条 CSV，文件已保存到浏览器下载目录`)
}

function dataCount() {
  return props.notes.length
}

const selCount = computed(() => selectedCount())

// ============ 分数可解释：hover tooltip + 点击弹「评分说明」 ============
const scoreModal = ref<Note | null>(null)
const hoverTip = ref<Note | null>(null)
const DIMS = [
  { key: 'like_fan_ratio', label: '点赞粉丝比', max: 40, weight: '权重 40%', hint: '赞/粉比越高，内容穿透粉丝圈层的能力越强（赞/粉 > 3 为爆款级）' },
  { key: 'low_fan', label: '低粉权重', max: 25, weight: '权重 25%', hint: '粉丝越少得分越高——专挖被低估的小博主（<500 满分，5000 归零）' },
  { key: 'interaction_health', label: '互动健康度', max: 20, weight: '权重 20%', hint: '收藏+评论+转发相对点赞越活跃越健康（对齐平台 CES：评论/转发权重高于点赞）' },
  { key: 'freshness', label: '时效性', max: 15, weight: '权重 15%', hint: '30 天内发布满分，90 天归零——追热点优先' },
]
function dimsOf(note: Note) {
  const dims = note.score_info?.dimensions || {}
  return DIMS.map(d => ({ ...d, val: dims[d.key as keyof typeof dims] ?? 0 }))
}
function tagConclusion(tag: string | null): string {
  switch (tag) {
    case '低粉爆款🔥': return '低粉爆款：点赞>500 且 赞/粉>3——粉丝少但互动爆发，最值得模仿'
    case '低粉': return '低粉：粉丝<500，数据被低估，值得关注'
    case '低赞': return '低赞：点赞≤300，互动不足，暂不推荐'
    case '高赞': return '高赞：点赞>500 但赞/粉比未过 3，非低粉爆款'
    case '普通': return '普通：数据中规中矩，参考价值一般'
    default: return '评分失败/数据不完整，无法给出结论'
  }
}
function openScore(note: Note) {
  scoreModal.value = note
}
function closeScore() {
  scoreModal.value = null
}
function fmtPub(ts: string | number | null): string {
  if (ts == null) return '未知'
  const n = Number(ts)
  if (!Number.isFinite(n)) return String(ts)
  const d = new Date(n > 1e12 ? n : n * 1000)
  if (Number.isNaN(d.getTime())) return String(ts)
  const days = Math.floor((Date.now() - d.getTime()) / 86400000)
  if (days < 1) return '今天'
  if (days < 30) return `${days} 天前`
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
</script>

<template>
  <!-- 顶部批量条（对齐视频：全选图文 / 清空选择 / 下载图片包 / 导出JSON，克隆走卡片单条） -->
  <div class="batch" v-if="selectable && notes.length" style="margin-bottom:14px;justify-content:flex-end">
    <div style="display:flex;gap:8px">
      <button class="b-btn" @click="selectAll">全选图文</button>
      <button class="b-btn" @click="clearSelect">清空选择</button>
      <button class="b-btn" @click="downloadImages">下载图片包</button>
      <button class="b-btn" @click="exportJSON">导出 JSON</button>
      <button class="b-btn" @click="exportCSV">导出 CSV</button>
    </div>
  </div>
  <div class="grid">
    <div
      v-for="note in filtered"
      :key="note.feed_id"
      class="card"
      :class="{ sel: isSel(note) }"
      @click="onCardClick(note, $event)"
    >
      <div class="cover">
        <img
          v-if="coverOf(note) && !broken.has(note.feed_id)"
          :src="coverOf(note)"
          alt=""
          loading="lazy"
          referrerpolicy="no-referrer"
          @error="onImgError(note)"
        />
        <div v-else class="ph" :style="phStyle(note)">{{ (note.title || '图')[0] }}</div>
        <span class="badge-note">{{ note.note_type === 'video' ? '视频' : '图文' }}</span>
        <span v-if="note.score != null" class="score-wrap" @mouseenter="hoverTip = note" @mouseleave="hoverTip = null">
          <span class="score-badge" :class="{ hot: note.score >= 90 }" @click.stop="openScore(note)">{{ note.score.toFixed(1) }}</span>
          <span v-if="note.score_info" class="score-tip-icon" @click.stop="openScore(note)">ⓘ</span>
          <div v-if="note.score_info && hoverTip === note" class="score-tip">
            <div class="tip-title">分数构成 · {{ note.score.toFixed(1) }}</div>
            <div v-for="d in dimsOf(note)" :key="d.key" class="tip-row">
              <span class="tip-lbl">{{ d.label }}</span>
              <span class="tip-val">{{ d.val }}/{{ d.max }}</span>
            </div>
            <div class="tip-foot">点击查看评分说明</div>
          </div>
        </span>
        <button class="open-link" title="打开原作品（新窗口）" @click.stop="openOrigin(note)">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4">
            <path d="M7 17 17 7M9 7h8v8" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
        <span class="check">✓</span>
        <!-- 悬停浮层：单条操作（一键克隆仅图文，采集客户图文+视频均可） -->
        <div class="hover-actions">
          <button
            class="ha-btn clone"
            :class="{ off: note.note_type === 'video' }"
            :title="note.note_type === 'video' ? '视频暂不支持克隆（图文为主，视频仅设计留位）' : '一键克隆（抄内容）'"
            @click.stop="note.note_type === 'video' ? toast('视频笔记暂不支持一键克隆（当前产品以图文为主）') : emit('clone', note)"
          >一键克隆</button>
          <button class="ha-btn client" title="采集客户（采人）" @click.stop="emit('clients', note)">采集客户</button>
        </div>
      </div>
      <div class="meta">
        <div class="title">{{ note.title || '（无标题）' }}</div>
        <div class="row">
          <span class="author">
            <span class="avatar"></span>{{ note.nickname || '未知作者' }}
          </span>
          <span class="tag" :class="{ hot: (note.score ?? 0) >= 90 }">{{ (note.score ?? 0) >= 90 ? '最值得模仿' : '高评分候选' }}</span>
          <span class="interact">
            <svg width="11" height="11" viewBox="0 0 24 24" :fill="(note.liked_count || 0) > 10000 ? '#FF2442' : '#888'">
              <path d="M12 21s-7.5-4.6-10-9.2C.4 8.6 2.8 5 6.4 5c2 0 3.6 1 4.8 2.6C12.4 6 14 5 16 5c3.6 0 6 3.6 4.4 6.8C18.9 16.4 12 21 12 21z" />
            </svg>
            {{ fmt(note.liked_count) }}
          </span>
        </div>
      </div>
    </div>
  </div>
  <div v-if="!filtered.length" style="text-align:center;color:#AAA;padding:40px 0">暂无笔记（{{ props.filterText ? '无匹配结果（标题/作者/UID）' : '先点「开始采集」' }}）</div>

  <!-- 评分说明弹窗 -->
  <div v-if="scoreModal" class="score-modal-mask" @click.self="closeScore">
    <div class="score-modal">
      <div class="sm-head">
        <div class="sm-score">{{ (scoreModal.score ?? 0).toFixed(1) }}</div>
        <div class="sm-tag">{{ scoreModal.tag || '未评分' }}</div>
        <button class="sm-close" @click="closeScore">×</button>
      </div>
      <div class="sm-dim" v-for="d in dimsOf(scoreModal)" :key="d.key">
        <div class="sm-row">
          <span class="sm-lbl">{{ d.label }}</span>
          <span class="sm-w">{{ d.weight }}</span>
          <span class="sm-val">{{ d.val }}/{{ d.max }}</span>
        </div>
        <div class="sm-bar"><div class="sm-bar-in" :style="{ width: Math.min(100, d.val / d.max * 100) + '%' }"></div></div>
        <div class="sm-hint">{{ d.hint }}</div>
      </div>
      <div class="sm-raw">
        点赞 {{ fmt(scoreModal.liked_count) }} · 收藏 {{ fmt(scoreModal.collected_count) }} · 评论 {{ fmt(scoreModal.comment_count) }} · 转发 {{ fmt(scoreModal.shared_count) }} · 粉丝 {{ fmt(scoreModal.fans_count) }} · 发布 {{ fmtPub(scoreModal.publish_time) }}
      </div>
      <div class="sm-concl">{{ tagConclusion(scoreModal.tag) }}</div>
    </div>
  </div>
</template>

<style scoped>
.score-wrap { position: relative; display: inline-flex; align-items: center; gap: 2px; }
.score-tip-icon { cursor: pointer; font-size: 11px; color: #fff; opacity: .85; background: rgba(0,0,0,.45); border-radius: 50%; width: 14px; height: 14px; display: inline-flex; align-items: center; justify-content: center; line-height: 1; }
.score-tip { position: absolute; top: calc(100% + 6px); right: 0; z-index: 60; min-width: 190px; background: #fff; color: #333; border-radius: 8px; padding: 10px 12px; box-shadow: 0 6px 20px rgba(0,0,0,.18); font-size: 12px; }
.tip-title { font-weight: 600; color: #111; margin-bottom: 6px; }
.tip-row { display: flex; justify-content: space-between; gap: 10px; padding: 2px 0; }
.tip-lbl { color: #555; }
.tip-val { color: #111; font-weight: 600; }
.tip-foot { margin-top: 6px; padding-top: 6px; border-top: 1px dashed #ddd; color: #999; font-size: 11px; }
.score-modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 200; display: flex; align-items: center; justify-content: center; }
.score-modal { width: 380px; max-width: 92vw; max-height: 86vh; overflow-y: auto; background: #fff; border-radius: 14px; padding: 20px; box-shadow: 0 12px 40px rgba(0,0,0,.25); }
.sm-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 14px; }
.sm-score { font-size: 34px; font-weight: 700; color: #111; }
.sm-tag { color: #9A6A2F; font-weight: 600; }
.sm-close { margin-left: auto; border: none; background: none; font-size: 20px; cursor: pointer; color: #888; line-height: 1; }
.sm-dim { margin-bottom: 12px; }
.sm-row { display: flex; align-items: baseline; gap: 8px; }
.sm-lbl { font-weight: 600; color: #222; font-size: 13px; }
.sm-w { color: #999; font-size: 11px; }
.sm-val { margin-left: auto; color: #333; font-weight: 600; font-size: 13px; }
.sm-bar { height: 6px; background: #eee; border-radius: 3px; margin: 5px 0 4px; overflow: hidden; }
.sm-bar-in { height: 100%; background: linear-gradient(90deg, #9A6A2F, #C8965A); border-radius: 3px; }
.sm-hint { color: #777; font-size: 11px; line-height: 1.5; }
.sm-raw { margin-top: 10px; padding-top: 10px; border-top: 1px solid #eee; color: #666; font-size: 12px; line-height: 1.6; }
.sm-concl { margin-top: 10px; padding: 10px 12px; background: #FFF7E6; border-radius: 8px; color: #8a6a2f; font-size: 12px; line-height: 1.6; }
</style>
