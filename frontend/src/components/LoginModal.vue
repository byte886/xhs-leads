<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { apiMirrorOpen, apiMirrorScreenshotUrl, apiMirrorEvent, apiLoginStatus } from '../api'
import { appState, toast } from '../store'

// R 批 v1.4：登录小窗 = 完整小红书登录页镜像（引擎独立 profile）
// 画面=CDP 截图流（不落盘），交互=事件转发（click/type，途径不限：扫码/手机号/微信都在页面里）。

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const imgRef = ref<HTMLImageElement | null>(null)
const shotUrl = ref('')
const loading = ref(false)
const inputText = ref('')
const typing = ref(false)
const failed = ref('')

let shotTimer: ReturnType<typeof setInterval> | null = null
let loginTimer: ReturnType<typeof setInterval> | null = null

async function openMirror() {
  failed.value = ''
  try {
    const r = await apiMirrorOpen()
    if (!r.ok) {
      failed.value = '镜像打开失败：' + (r as any).error || '引擎未就绪'
      return
    }
    refreshShot()
    startTimers()
  } catch (e) {
    failed.value = '镜像打开失败：' + (e as Error).message
  }
}

function refreshShot() {
  if (!props.open) return
  shotUrl.value = apiMirrorScreenshotUrl()
}

function startTimers() {
  stopTimers()
  // 画面轮询（镜像=截图流）：2s 一帧，登录页/验证码变化即时可见
  shotTimer = setInterval(refreshShot, 2000)
  // 登录态轮询：登录成功自动关小窗
  loginTimer = setInterval(async () => {
    try {
      const lg = await apiLoginStatus()
      if (lg.logged_in) {
        appState.login = { logged_in: true, nickname: lg.nickname || '' }
        appState.cookieRefreshTick += 1
        toast('登录成功')
        stopTimers()
        emit('close')
      }
    } catch { /* 后端未就绪静默 */ }
  }, 3000)
}

function stopTimers() {
  if (shotTimer) { clearInterval(shotTimer); shotTimer = null }
  if (loginTimer) { clearInterval(loginTimer); loginTimer = null }
}

// 点击镜像画面 → 转发 click（坐标按截图原始宽等比换算，不硬编码视口尺寸）
function onMirrorClick(e: MouseEvent) {
  const img = imgRef.value
  if (!img) return
  const scale = img.naturalWidth > 0 ? img.naturalWidth / img.clientWidth : 1
  const x = Math.round(e.offsetX * scale)
  const y = Math.round(e.offsetY * scale)
  apiMirrorEvent({ type: 'click', x, y }).catch(() => {})
}

// 填入输入（手机号/验证码等）：先由用户在画面点击输入框聚焦，再转发 type（拟人节奏在后端）
async function sendType() {
  if (!inputText.value || typing.value) return
  typing.value = true
  try {
    const r = await apiMirrorEvent({ type: 'type', text: inputText.value })
    if (!r.ok) toast(r.error || '输入失败')
  } catch (e) {
    toast('输入失败：' + (e as Error).message)
  } finally {
    typing.value = false
  }
}

onMounted(() => { if (props.open) openMirror() })
watch(() => props.open, (v) => { if (v) openMirror(); else stopTimers() })
onBeforeUnmount(stopTimers)
</script>

<template>
  <div v-if="props.open" class="modal-mask" @click.self="emit('close')">
    <div class="modal mirror-modal">
      <h3>登录小红书</h3>
      <p class="qr-tip">
        未检测到有效登录态。请在下方面板完成登录（<strong>扫码 / 手机号+验证码 / 微信任一途径</strong>）——
        画面即小红书网页登录页，点击画面即可操作；登录成功后本窗自动关闭。
      </p>

      <!-- 镜像画面：CDP 截图流 + 点击事件转发 -->
      <div class="mirror-frame" :class="{ dim: loading }">
        <img
          ref="imgRef"
          v-if="shotUrl"
          :src="shotUrl"
          alt="登录页镜像"
          class="mirror-img"
          draggable="false"
          @click="onMirrorClick"
          @load="loading = false"
          @error="failed = '镜像画面加载失败（引擎可能未就绪）'"
        />
        <div v-else class="mirror-empty">画面加载中…</div>
        <div class="mirror-hint">← 点击画面即操作登录页（扫码/手机号/验证码/微信，与网页版一致）</div>
      </div>

      <!-- 输入转发：手机号/验证码文本填入（先点击画面中的输入框聚焦，再点「填入」） -->
      <div class="type-row">
        <input
          v-model="inputText"
          class="type-input"
          placeholder="输入手机号/验证码等文本，然后点「填入」（先点击画面中对应输入框）"
          @keyup.enter="sendType"
        />
        <button class="btn type-btn" :disabled="typing" @click="sendType">
          {{ typing ? '正在填入…' : '填入' }}
        </button>
      </div>

      <p v-if="failed" class="auto-msg">{{ failed }}</p>
      <p class="refresh-hint">登录成功后自动关闭本窗并继续采集。登录态仅存于引擎独立 profile，不依赖系统 Chrome。</p>
      <button class="close" @click="emit('close')">关闭</button>
    </div>
  </div>
</template>

<style scoped>
.mirror-modal { width: 560px; }
.qr-tip { font-size: 13px; color: #666; line-height: 1.6; margin: 6px 0 10px; text-align: left; }
.qr-tip strong { color: #ff2442; }
.mirror-frame {
  position: relative; border: 1px solid #e5e5e5; border-radius: 8px;
  overflow: hidden; background: #f7f7f7; text-align: center; margin-bottom: 10px;
}
.mirror-img {
  width: 100%; max-height: 440px; object-fit: contain; cursor: crosshair;
  display: block; user-select: none;
}
.mirror-empty { padding: 90px 0; color: #999; font-size: 13px; }
.mirror-hint {
  font-size: 11px; color: #aaa; padding: 4px 0 6px;
}
.type-row { display: flex; gap: 8px; margin-bottom: 6px; }
.type-input {
  flex: 1; padding: 8px 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 13px;
}
.type-btn { min-width: 88px; background: #ff2442; color: #fff; border: none; border-radius: 6px; cursor: pointer; }
.type-btn:disabled { opacity: .6; cursor: default; }
.auto-msg { font-size: 12px; color: #c0392b; line-height: 1.5; margin-top: 6px; text-align: left; }
.refresh-hint { font-size: 12px; color: #888; line-height: 1.5; margin-top: 6px; text-align: left; }
</style>
