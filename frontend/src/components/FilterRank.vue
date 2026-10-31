<script setup lang="ts">
import { computed } from 'vue'
import type { Note } from '../types'
import { coverProxy } from '../store'

const props = defineProps<{ notes: Note[] }>()
const emit = defineEmits<{ (e: 'clone', note: Note): void; (e: 'to-pick'): void }>()

const broken = new Set<string>()

const ranked = computed(() =>
  [...props.notes].sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 20)
)

function srcOf(n: Note): string {
  return coverProxy(n.local_cover || n.cover_url)
}

function fmt(n: number | null | undefined): string {
  const v = n || 0
  return v >= 10000 ? (v / 10000).toFixed(1) + '万' : String(v)
}

/** 打开小红书原作品页（新 tab）：有 xsec_token 走详情直链，无则 explore 兜底 */
function openOrigin(n: Note) {
  const token = n.xsec_token
  const base = token
    ? `https://www.xiaohongshu.com/explore/${n.feed_id}?xsec_token=${encodeURIComponent(token)}`
    : `https://www.xiaohongshu.com/explore/${n.feed_id}`
  window.open(base, '_blank')
}
</script>

<template>
  <div class="rank">
    <div v-for="(n, i) in ranked" :key="n.feed_id" class="rank-item" :class="{ hot: i === 0 }">
      <div class="rank-thumb">
        <img v-if="srcOf(n) && !broken.has(n.feed_id)" :src="srcOf(n)" alt="" loading="lazy" @error="broken.add(n.feed_id)" />
        <div v-else style="width:100%;height:100%;background:#9A6A2F;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700">
          {{ (n.title || '图')[0] }}
        </div>
      </div>
      <div class="score">{{ n.score?.toFixed(1) ?? '—' }}</div>
      <div class="bar" :style="{ background: n.cover_url ? '#9A6A2F' : '#9A6A2F' }"></div>
      <div class="info">
        <div class="t">{{ n.title || '（无标题）' }}</div>
        <div class="d">{{ n.nickname || '未知作者' }} · 点赞 {{ fmt(n.liked_count) }} · {{ i === 0 ? '最值得模仿' : '高评分候选' }}</div>
      </div>
      <div class="actions">
        <button class="b-btn primary" @click="emit('clone', n)">一键克隆</button>
        <button class="b-btn" @click="openOrigin(n)">打开原文</button>
        <button class="b-btn" @click="() => {}">采集客户</button>
      </div>
    </div>
    <div style="text-align:center;margin:16px 0">
      <button class="b-btn primary" @click="emit('to-pick')">下一步：选择内容（勾选目标）</button>
    </div>
  </div>
</template>
