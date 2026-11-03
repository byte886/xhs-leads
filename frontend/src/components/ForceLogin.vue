<script setup lang="ts">
// ForceLogin：APP 内部强制登录对话框（登录 UI 改版·用户拍板）
// 语义：未登录 / 启动被踢 / 采集中被踢与异常 三类场景统一弹此对话框——
// 告知当前状态 → 用户点「去登录」→ apiLoginBegin(force=true) → launcher 守卫导航小红书
// 真实登录页（无叉）→ 登录成功回 APP 复查：已登录 → emit('done') 关闭本窗进工作台；
// 仍未登录 → 保持强制（同一逻辑）。主动重登（设置页「重新登录」）不经过本窗。
// R-6.26 弹框重设计：结构按 Material/Carbon 对话框规范（标题→正文→主操作→辅助行），
// 文案按 UX Writing 规范（平静、面向解决、去技术术语——不出现 Cookie/风控/强制等词）。
import { onBeforeUnmount, ref, watch } from 'vue'
import { apiCollapseDebugWindows, apiLoginBegin, apiLoginStatus } from '../api'
import { appState, toast } from '../store'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'done'): void }>()

const stateText = ref('正在检查登录状态…')
const busy = ref(false)
// R-6.22f：用户是否已主动点「去登录」——只有走完登录流程且复查真实有效才关窗
const wentLogin = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

function stopTimer() {
  if (timer) { clearInterval(timer); timer = null }
}

// 打开即复查登录态（与登录成功回 APP 复查同一逻辑）：
//   - **R-6.22f 关键规则：打开后不自动关窗**。自动关依赖 login_status 判定，而风控/
//     页面渲染时序会让判定抖动（任务页刚导航时风控文案未渲染 → risk_verify=false +
//     logged_in=true 假阳性 → 闪关，实测用户「登录框没弹/闪一下就回采集」）。强制登录
//     语义 = 用户**主动**完成登录（点「去登录」→ 官方登录页 → 扫码 → 回 APP 复查真实
//     有效）才关闭；未操作时保持打开并持续提示状态。
//   - 复查状态分派：
//       wentLogin=false（用户尚未操作）→ 仅更新文案（面向用户、去术语）：
//         已登录（logged_in 且非风控）→「登录状态正常，点「去登录」确认后继续」；
//         风控需验证（risk_verify）→ 引导点「去登录」扫码验证；
//         被踢/未登录 → 对应文案。
//       wentLogin=true（已点「去登录」，从登录页回 APP 复查）→ 仅真实有效登录
//         （logged_in 且非 risk_verify）→ 关窗进工作台；仍风控/未登录 → 保持并提示。
function startCheck() {
  stopTimer()
  timer = setInterval(async () => {
    try {
      const lg = await apiLoginStatus()
      appState.login = { logged_in: !!lg.logged_in, nickname: lg.nickname || '', kicked: !!lg.kicked }
      const realOk = !!lg.logged_in && !lg.risk_verify
      if (wentLogin.value && realOk) {
        stopTimer()
        appState.cookieRefreshTick += 1
        emit('done')
        return
      }
      if (lg.kicked) stateText.value = '登录状态已失效，请重新登录后继续'
      else if (lg.risk_verify) stateText.value = '登录已失效，重新登录后才能继续采集'
      else if (realOk) stateText.value = '登录状态正常，点「去登录」确认后继续'
      else stateText.value = '尚未登录小红书，登录后才能开始采集'
    } catch {
      stateText.value = '服务未就绪，正在重试…'
    }
  }, 1000)
}

async function goLogin() {
  if (busy.value) return
  busy.value = true
  wentLogin.value = true // R-6.22f：用户主动去登录 → 复查真实有效才允许关窗
  stateText.value = '正在打开登录页…'
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
    wentLogin.value = false
    // R-6.22g：登录引导弹出即收起右窗采集调试窗（壳窗保留）——「需要登录时
    // 右侧要么关闭要么有提示」；扫码成功后再点采集由后端按登录态恢复。
    apiCollapseDebugWindows().catch(() => {})
    startCheck()
  } else {
    stopTimer()
  }
})
onBeforeUnmount(stopTimer)
</script>

<template>
  <div v-if="props.open" class="force-mask" role="dialog" aria-modal="true" aria-labelledby="fl-title">
    <div class="force-box">
      <div class="force-icon" aria-hidden="true">
        <svg width="26" height="26" viewBox="0 0 256 256" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
          <path d="M141.66,133.66l-40,40a8,8,0,0,1-11.32-11.32L116.69,136H24a8,8,0,0,1,0-16h92.69L90.34,93.66a8,8,0,0,1,11.32-11.32l40,40A8,8,0,0,1,141.66,133.66ZM200,32H136a8,8,0,0,0,0,16h56V208H136a8,8,0,0,0,0,16h64a8,8,0,0,0,8-8V40A8,8,0,0,0,200,32Z"/>
        </svg>
      </div>
      <h3 id="fl-title">需要登录小红书</h3>
      <p class="force-state">{{ stateText }}</p>
      <button class="btn-primary" :disabled="busy" @click="goLogin">
        {{ busy ? '正在打开…' : '去登录' }}
      </button>
      <p class="force-tip">将跳转小红书官方登录页 · 扫码或手机号均可</p>
    </div>
  </div>
</template>

<style scoped>
.force-mask {
  position: fixed;
  inset: 0;
  z-index: 300;
  background: rgba(30, 30, 34, 0.35);
  backdrop-filter: blur(3px);
  display: flex;
  align-items: center;
  justify-content: center;
}
.force-box {
  width: 380px;
  background: #fff;
  border-radius: 16px;
  padding: 32px 28px 24px;
  text-align: center;
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.14);
}
.force-icon {
  width: 52px;
  height: 52px;
  margin: 0 auto 14px;
  border-radius: 14px;
  background: #ff2442;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}
.force-box h3 {
  font-size: 17px;
  font-weight: 600;
  color: #222;
  margin: 0 0 10px;
}
.force-state {
  font-size: 14px;
  color: #444;
  line-height: 1.6;
  margin: 0 0 24px;
}
.btn-primary {
  width: 100%;
  padding: 12px 0;
  border: none;
  border-radius: 10px;
  background: #ff2442;
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s ease;
}
.btn-primary:hover:not(:disabled) { background: #e6203a; }
.btn-primary:disabled { opacity: 0.6; cursor: default; }
.force-tip {
  font-size: 12px;
  color: #999;
  margin: 14px 0 0;
}
</style>
