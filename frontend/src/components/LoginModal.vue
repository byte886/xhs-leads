<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { apiLoginBegin, apiLoginStatus } from '../api'
import { appState, toast } from '../store'

// R-6.6（用户拍板：强制登录）：未登录时壳窗口永远停留在小红书真实登录页（置顶，
// 无「稍后再说/关闭」跳过路径，登录页内关闭按钮由 launcher 守卫 JS 注入隐藏）。
// 守卫在服务就绪后自动导航登录页，本弹窗仅作兜底/过渡：打开即自动触发 login_begin，
// 登录成功（后端判定）后自动关闭。截图镜像链路（apiMirror*）已退役。

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const failed = ref('')

let loginTimer: ReturnType<typeof setInterval> | null = null

async function goLogin() {
  failed.value = ''
  try {
    const r = await apiLoginBegin()
    if (r.already_logged_in) {
      toast('已登录')
      emit('close')
      return
    }
    // pending 已置位：launcher 守卫接管导航，本弹窗随后被登录页覆盖/自动关闭
  } catch (e) {
    failed.value = '打开登录页失败：' + (e as Error).message
  }
}

function startTimers() {
  stopTimers()
  // 登录态轮询：登录成功自动关本窗（状态条同步更新）
  loginTimer = setInterval(async () => {
    try {
      const lg = await apiLoginStatus()
      if (lg.logged_in) {
        appState.login = { logged_in: true, nickname: lg.nickname || '', kicked: false }
        appState.cookieRefreshTick += 1
        toast('登录成功')
        stopTimers()
        emit('close')
      }
    } catch { /* 后端未就绪静默 */ }
  }, 1000)
}

function stopTimers() {
  if (loginTimer) { clearInterval(loginTimer); loginTimer = null }
}

onMounted(() => {
  if (props.open) {
    startTimers()
    goLogin() // 强制登录：打开即触发，不等用户点击
  }
})
watch(() => props.open, (v) => { if (v) { startTimers(); goLogin() } else stopTimers() })
onBeforeUnmount(stopTimers)
</script>

<template>
  <div v-if="props.open" class="modal-mask">
    <div class="modal login-guide">
      <h3>登录小红书</h3>
      <p class="guide-tip">
        登录为强制步骤：壳窗口即将切换到小红书<strong>真实登录页</strong>——
        支持<strong>扫码 / 手机号+验证码 / 微信任一途径</strong>，验证码通过「获取验证码」获取，
        均为小红书原生页面操作；未登录期间窗口将始终停留在登录页，登录成功后自动返回本工具，
        登录态注入采集引擎（仅存于独立 profile）。
      </p>
      <div class="waiting">
        <p class="waiting-line">正在打开小红书登录页…</p>
        <p class="waiting-sub">请在窗口中完成登录，完成后将自动返回本窗口。</p>
      </div>
      <p v-if="failed" class="auto-msg">{{ failed }}</p>
    </div>
  </div>
</template>

<style scoped>
.login-guide { width: 480px; }
.guide-tip { font-size: 13px; color: #666; line-height: 1.7; margin: 6px 0 14px; text-align: left; }
.guide-tip strong { color: #ff2442; }
.waiting { text-align: center; padding: 14px 0 8px; }
.waiting-line { font-size: 14px; color: #333; margin-bottom: 6px; }
.waiting-sub { font-size: 12px; color: #888; line-height: 1.6; }
.auto-msg { font-size: 12px; color: #c0392b; line-height: 1.5; margin-top: 6px; text-align: left; }
</style>
