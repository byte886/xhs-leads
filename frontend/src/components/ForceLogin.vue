<script setup lang="ts">
// ForceLogin：APP 内部强制登录对话框（登录 UI 改版·用户拍板）
// 语义：未登录 / 启动被踢 / 采集中被踢与异常 三类场景统一弹此对话框（不可关闭）——
// 告知当前状态 → 用户点「去登录」→ apiLoginBegin(force=true) → launcher 守卫导航小红书
// 真实登录页（无叉）→ 登录成功回 APP 复查：已登录 → emit('done') 关闭本窗进工作台；
// 仍未登录 → 保持强制（同一逻辑）。主动重登（设置页「重新登录」）不经过本窗。
import { onBeforeUnmount, ref, watch } from 'vue'
import { apiLoginBegin, apiLoginStatus } from '../api'
import { appState, toast } from '../store'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'done'): void }>()

const stateText = ref('正在检查登录状态…')
const busy = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

function stopTimer() {
  if (timer) { clearInterval(timer); timer = null }
}

// 打开即复查登录态（与登录成功回 APP 复查同一逻辑）：已登录 → 关窗；未登录/被踢 → 保持
function startCheck() {
  stopTimer()
  timer = setInterval(async () => {
    try {
      const lg = await apiLoginStatus()
      appState.login = { logged_in: !!lg.logged_in, nickname: lg.nickname || '', kicked: !!lg.kicked }
      if (lg.logged_in) {
        stopTimer()
        appState.cookieRefreshTick += 1
        emit('done')
        return
      }
      if (lg.kicked) stateText.value = '登录已被平台踢出（登录超限 / 风控），需要重新登录'
      else stateText.value = '当前未登录小红书，无法采集与发布内容'
    } catch {
      stateText.value = '后端未就绪，正在重试…'
    }
  }, 1000)
}

async function goLogin() {
  if (busy.value) return
  busy.value = true
  stateText.value = '正在打开小红书登录页…'
  try {
    const r = await apiLoginBegin(true) // 强制：登录页无叉，登录成功才回
    if (r.already_logged_in) {
      stopTimer()
      emit('done')
      return
    }
    // pending 已置位：launcher 守卫接管导航；本窗被壳窗口登录页覆盖
  } catch (e) {
    busy.value = false
    stateText.value = '打开登录页失败：' + (e as Error).message
    return
  }
  // 登录页导航后本窗不可见（壳窗口被登录页覆盖）；登录成功回 APP 时 onMounted 复查
}

watch(() => props.open, (v) => {
  if (v) {
    stateText.value = '正在检查登录状态…'
    busy.value = false
    startCheck()
  } else {
    stopTimer()
  }
})
onBeforeUnmount(stopTimer)
</script>

<template>
  <div v-if="props.open" class="force-mask">
    <div class="force-box">
      <div class="force-logo">X</div>
      <h3>需要登录小红书</h3>
      <p class="force-state">{{ stateText }}</p>
      <p class="force-tip">
        点击「去登录」将切换到小红书<strong>官方登录页</strong>（扫码 / 手机号+验证码均可）。
        登录完成后自动返回本工具；未完成登录将一直停留在此对话框。
      </p>
      <button class="btn-primary" :disabled="busy" @click="goLogin">
        {{ busy ? '正在打开…' : '去登录' }}
      </button>
      <p class="force-lock">当前状态强制要求登录，此对话框不可关闭</p>
    </div>
  </div>
</template>

<style scoped>
.force-mask {
  position: fixed;
  inset: 0;
  z-index: 300;
  background: rgba(245, 245, 248, 0.96);
  display: flex;
  align-items: center;
  justify-content: center;
}
.force-box {
  width: 400px;
  background: #fff;
  border: 1px solid #ececec;
  border-radius: 16px;
  padding: 34px 30px;
  text-align: center;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.08);
}
.force-logo {
  width: 46px;
  height: 46px;
  margin: 0 auto 14px;
  border-radius: 12px;
  background: #ff2442;
  color: #fff;
  font-size: 22px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}
.force-box h3 { font-size: 17px; color: #222; margin: 0 0 10px; }
.force-state { font-size: 13px; color: #c0392b; line-height: 1.6; margin: 0 0 10px; }
.force-tip { font-size: 12.5px; color: #666; line-height: 1.7; margin: 0 0 20px; text-align: left; }
.force-tip strong { color: #ff2442; }
.btn-primary {
  width: 100%;
  padding: 11px 0;
  border: none;
  border-radius: 10px;
  background: #ff2442;
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
}
.btn-primary:disabled { opacity: 0.6; cursor: default; }
.force-lock { font-size: 11.5px; color: #aaa; margin: 14px 0 0; }
</style>
