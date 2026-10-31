<script setup lang="ts">
import { ref, watch } from 'vue'
import type { Note } from '../types'
import { toast } from '../store'

const props = defineProps<{ note: Note | null; x: number; y: number }>()
const emit = defineEmits<{
  (e: 'clone', note: Note): void
  (e: 'close'): void
}>()

const pos = ref({ left: 0, top: 0 })

watch(
  () => [props.note, props.x, props.y] as const,
  () => {
    if (!props.note) return
    const mw = 210
    const mh = 132
    pos.value = {
      left: Math.min(props.x, window.innerWidth - mw - 8),
      top: Math.min(props.y, window.innerHeight - mh - 8)
    }
  },
  { immediate: true }
)

function clone() {
  if (props.note) emit('clone', props.note)
  emit('close')
}

function clients() {
  if (props.note) toast(`演示：采集「${(props.note.title || '').slice(0, 12)}…」的互动用户名单`)
  emit('close')
}

function openOrigin() {
  if (!props.note) return
  const token = props.note.xsec_token
  const base = token
    ? `https://www.xiaohongshu.com/explore/${props.note.feed_id}?xsec_token=${encodeURIComponent(token)}`
    : `https://www.xiaohongshu.com/explore/${props.note.feed_id}`
  window.open(base, '_blank')
  emit('close')
}
</script>

<template>
  <div v-if="note" class="menu" :style="{ left: pos.left + 'px', top: pos.top + 'px' }">
    <div class="mi danger" @click="clone">一键克隆（抄内容）</div>
    <div class="mi amber" @click="clients">一键采集客户（采人）</div>
    <div class="mi" @click="openOrigin">打开原作品</div>
  </div>
</template>
