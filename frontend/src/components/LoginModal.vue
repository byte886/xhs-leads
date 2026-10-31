<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { apiLoginBegin, apiLoginStatus } from '../api'
import { appState, toast } from '../store'

// R-6.5（用户拍板）：登录交互从「截图镜像小窗」升级为「壳窗口内打开真实小红书登录页」。
// 本弹窗仅做引导：用户点「前往登录」→ 后端 login_begin 置 pending → launcher 登录守卫
// 把整个壳窗口导航到小红书登录页（原生扫码 / 手机号+获取验证码，验证码真实走「获取验证码」），
// 登录成功 → Cookie 注入引擎 → 窗口自动回本 APP 前端。截图镜像链路（apiMirror*）已退役。

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const opening = ref(false)
const openingDone = ref(false)
const failed = ref('')

let loginTimer: ReturnType<typeof setInterval> | null = null

async function goLogin() {
  if (opening.value) return
  opening.value = true
  failed.value = ''
  try {
    const r = await apiLoginBegin()
    if (r.already_logged_in) {
      toast('已登录')
      emit('close')
      return
    }
    openingDone.value = true
  } catch (e) {
    failed.value = '打开登录页失败：' + (e as Error).message
    opening.value = false
  }
}

function startTimers() {
  stopTimers()
  // 登录态轮询：登录成功自动关本窗（状态条同步更新）
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
  if (loginTimer) { clearInterval(loginTimer); loginTimer = null }
}

onMounted(() => { if (props.open) startTimers() })
watch(() => props.open, (v) => { if (v) startTimers(); else stopTimers() })
onBeforeUnmount(stopTimers)
</script>

<template>
  <div v-if="props.open" class="modal-mask" @click.self="emit('close')">
    <div class="modal login-guide">
      <h3>登录小红书</h3>
      <p class="guide-tip">
        未检测到有效登录态。点击「前往登录」后，壳窗口将切换到小红书登录页——
        支持<strong>扫码 / 手机号+验证码 / 微信任一途径</strong>，验证码通过「获取验证码」获取，
        均为小红书原生页面操作；登录成功后窗口自动返回本工具，登录态注入采集引擎（仅存于独立 profile）。
      </p>

      <div v-if="!openingDone" class="actions">
        <button class="btn go-btn" :disabled="opening" @click="goLogin">
          {{ opening ? '正在打开…' : '前往登录' }}
        </button>
        <button class="btn later-btn" @click="emit('close')">稍后再说</button>
      </div>
      <div v-else class="waiting">
        <p class="waiting-line">正在打开小红书登录页…</p>
        <p class="waiting-sub">请在窗口中完成登录（扫码或手机号+验证码），完成后自动返回本窗口。</p>
      </div>

      <p v-if="failed" class="auto-msg">{{ failed }}</p>
      <button class="close" @click="emit('close')">关闭</button>
    </div>
  </div>
</template>

<style scoped>
.login-guide { width: 480px; }
.guide-tip { font-size: 13px; color: #666; line-height: 1.7; margin: 6px 0 14px; text-align: left; }
.guide-tip strong { color: #ff2442; }
.actions { display: flex; gap: 10px; margin-bottom: 8px; }
.go-btn {
  flex: 1; padding: 10px 0; background: #ff2442; color: #fff;
  border: none; border-radius: 6px; font-size: 14px; cursor: pointer;
}
.go-btn:disabled { opacity: .6; cursor: default; }
.later-btn {
  flex: 1; padding: 10px 0; background: #fff; color: #666;
  border: 1px solid #ddd; border-radius: 6px; font-size: 14px; cursor: pointer;
}
.waiting { text-align: center; padding: 14px 0 8px; }
.waiting-line { font-size: 14px; color: #333; margin-bottom: 6px; }
.waiting-sub { font-size: 12px; color: #888; line-height: 1.6; }
.auto-msg { font-size: 12px; color: #c0392b; line-height: 1.5; margin-top: 6px; text-align: left; }
</style>
