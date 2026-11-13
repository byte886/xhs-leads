<script setup lang="ts">
import { ref, watch } from 'vue'
import type { Note } from '../types'
import { toast } from '../store'

const props = defineProps<{ note: Note | null; x: number; y: number }>()
const emit = defineEmits<{
  (e: 'clone', note: Note): void
  (e: 'clients', note: Note): void
  (e: 'author', note: Note): void
  (e: 'close'): void
}>()

const pos = ref({ left: 0, top: 0 })

watch(
  () => [props.note, props.x, props.y] as const,
  () => {
    if (!props.note) return
    const mw = 210
    const mh = 170
    pos.value = {
      left: Math.min(props.x, window.innerWidth - mw - 8),
      top: Math.min(props.y, window.innerHeight - mh - 8)
    }
  },
  { immediate: true }
)

/** 一键克隆：视频笔记后端分支未解锁，这里直接拦下并给明确原因 */
function clone() {
  if (!props.note) return
  if (props.note.note_type === 'video') {
    toast('视频笔记暂不支持一键克隆（当前产品以图文为主）', 'error')
    emit('close')
    return
  }
  emit('clone', props.note)
  emit('close')
}

function clients() {
  if (props.note) emit('clients', props.note)
  emit('close')
}

function author() {
  if (props.note) emit('author', props.note)
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
  <div
    v-if="note"
    class="menu"
    :style="{ left: pos.left + 'px', top: pos.top + 'px' }"
    @contextmenu.prevent
  >
    <div class="mi danger" :class="{ off: note.note_type === 'video' }" @click.stop="clone">
      一键克隆（抄内容）<span v-if="note.note_type === 'video'" class="mi-hint">仅图文</span>
    </div>
    <div class="mi amber" @click.stop="clients">采集客户（本篇评论区）</div>
    <div class="mi amber" @click.stop="author">采作者（该作者多篇合并）</div>
    <div class="mi sep" @click.stop="openOrigin">打开原作品</div>
  </div>
</template>
