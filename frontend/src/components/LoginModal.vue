<script setup lang="ts">
import { ref } from 'vue'
import { apiRefreshCookieFromChrome } from '../api'
import { appState, toast } from '../store'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const autoReading = ref(false)
const autoMsg = ref('')

// 方案 A：直接读日常 Chrome 的 Cookie 库（钥匙串解密），无需手动扫码/复制粘贴
async function autoReadChrome() {
  if (autoReading.value) return
  autoReading.value = true
  autoMsg.value = ''
  try {
    const r = await apiRefreshCookieFromChrome()
    if (r.ok) {
      appState.login = { logged_in: true, nickname: r.account || '已登录' }
      appState.cookieRefreshTick += 1
      toast(r.note || '已从 Chrome 自动恢复登录态')
      emit('close')
    } else if (r.need_auth) {
      autoMsg.value = '首次使用需要授权：系统已弹出钥匙串授权窗口，请在其中输入密码并点「始终允许」，然后点击下方「我已授权，重试」'
    } else {
      autoMsg.value = r.error || '自动读取失败，可改用下方扫码方式'
    }
  } catch (e) {
    autoMsg.value = (e as Error).message || '自动读取失败'
  } finally {
    autoReading.value = false
  }
}

function retryAutoRead() {
  autoMsg.value = ''
  autoReadChrome()
}

// 打开小红书首页登录页：用系统默认浏览器（日常 Chrome）新窗口打开，登录后 Cookie 落在日常 Chrome，
// 用户切回 APP 刷新时由「刷新自动读取」链路（方案 A：读日常 Chrome + 钥匙串解密）恢复。
function openLoginPage() {
  window.open('https://www.xiaohongshu.com/', '_blank', 'noopener')
}
</script>

<template>
  <div v-if="props.open" class="modal-mask" @click.self="emit('close')">
    <div class="modal qr-modal">
      <h3>登录小红书</h3>
      <p class="qr-tip">
        未检测到有效登录态（Cookie 缺失或已失效）。请点击下方链接打开小红书首页完成登录；
        登录完成后切回本页并刷新，系统将自动读取 Chrome 登录态恢复。
      </p>
      <div class="login-row">
        <button class="btn login-link" @click="openLoginPage">
          <span class="link-icon">↗</span> 打开小红书登录页
        </button>
        <p class="login-sub">点击后会在浏览器新窗口打开小红书首页（https://www.xiaohongshu.com）。<br />
          <strong>请务必使用 Chrome 浏览器（默认个人资料 Profile）完成登录，并始终保持同一个 Profile</strong>，否则本工具无法自动读取到登录态。</p>
      </div>
      <div class="auto-row">
        <button class="btn auto-btn" @click="autoReadChrome" :disabled="autoReading">
          {{ autoReading ? '正在读取…' : '立即读取 Chrome 登录态' }}
        </button>
        <button v-if="autoMsg && autoMsg.includes('重试')" class="btn auto-retry" @click="retryAutoRead">我已授权，重试</button>
        <p v-if="autoMsg" class="auto-msg">{{ autoMsg }}</p>
      </div>
      <p class="refresh-hint">已在小红书完成登录？<strong>刷新本页面</strong>即可自动恢复（也可点上方按钮立即读取）。</p>
      <button class="close" @click="emit('close')">关闭</button>
    </div>
  </div>
</template>

<style scoped>
.qr-modal { width: 400px; text-align: center; }
.qr-tip { font-size: 13px; color: #666; line-height: 1.6; margin: 8px 0 12px; }
.login-row { margin: 0 0 12px; }
.login-link {
  min-width: 220px; padding: 10px 18px; font-size: 14px;
  background: #ff2442; color: #fff; border-radius: 8px; cursor: pointer; border: none;
}
.login-link:hover { background: #e01e3a; }
.link-icon { margin-right: 4px; }
.login-sub { font-size: 12px; color: #999; line-height: 1.6; margin-top: 8px; }
.login-sub strong { color: #c0392b; }
.auto-row { margin: 0 0 8px; }
.auto-btn { min-width: 220px; }
.auto-retry { margin-top: 8px; }
.auto-msg { font-size: 12px; color: #c0392b; line-height: 1.5; margin-top: 8px; padding: 0 8px; }
.refresh-hint { font-size: 12px; color: #888; line-height: 1.5; margin-top: 6px; }
.refresh-hint strong { color: #ff2442; }
</style>
