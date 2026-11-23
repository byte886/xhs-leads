<script setup lang="ts">
/**
 * 设置 › 关于与授权：授权状态（免费版/激活版）+ 激活码输入。
 *
 * 注：设置页左侧该分类当前置灰（on: false），本组件只在被打开时才会挂载并发请求。
 */
import { onMounted, ref } from 'vue'
import { toast } from '../../store'
import { apiLicenseActivate, apiLicenseStatus } from '../../api'

const lic = ref<{ mode: string; features: string[]; machine_fingerprint: string; has_activation: boolean }>()
const actKey = ref('')
const actBusy = ref(false)

async function loadLicense() {
  try {
    const r = await apiLicenseStatus()
    lic.value = r.status
  } catch {
    lic.value = undefined
  }
}

async function activateLicense() {
  if (!actKey.value.trim()) { toast('请输入激活码', 'error'); return }
  actBusy.value = true
  try {
    const r = await apiLicenseActivate(actKey.value.trim())
    if (r.ok) {
      toast('激活成功（激活码与设备绑定）')
      actKey.value = ''
      await loadLicense()
    } else {
      toast(r.note || '激活失败', 'error')
    }
  } catch (e) {
    toast(`激活失败：${(e as Error).message}`, 'error')
  } finally {
    actBusy.value = false
  }
}

onMounted(loadLicense)
</script>

<template>
  <div>
    <div class="set-row" v-if="lic">
      <div class="row-main">
        <span class="row-label">授权状态</span>
        <span class="lic-tag" :class="{ on: lic.mode === 'activation' && lic.has_activation }">
          {{ lic.mode === 'free' ? '免费版' : (lic.has_activation ? '激活版' : '未激活') }}
        </span>
        <span class="lic-fp" :title="lic.machine_fingerprint">版本 2.0.0 · 设备指纹 {{ lic.machine_fingerprint.slice(0, 12) }}…</span>
        <span v-if="lic.has_activation && lic.mode !== 'free'" class="lic-ok">✓ 已激活（机器码绑定）</span>
      </div>
      <p class="set-hint">已开通功能：{{ lic.features.join(' / ') }}</p>
    </div>
    <div class="set-row" v-if="lic && lic.mode !== 'free'">
      <div class="row-main">
        <span class="row-label">激活码</span>
        <input v-model="actKey" placeholder="粘贴卖家提供的激活码" class="full-input" @keyup.enter="activateLicense" />
        <button class="btn primary sm" :disabled="actBusy" @click="activateLicense">{{ actBusy ? '激活中…' : '激活' }}</button>
      </div>
      <p class="set-hint">激活码由卖家按设备指纹签发，一台设备一个码。</p>
    </div>
  </div>
</template>

<style scoped>
.lic-tag { padding: 2px 10px; border-radius: 999px; background: #e8f5e9; color: #2e7d32; font-weight: 600; font-size: 12px; }
.lic-tag.on { background: #e3f2fd; color: #1565c0; }
.lic-ok { color: #2e7d32; font-weight: 600; font-size: 12px; }
.lic-fp { color: #888; font-family: monospace; font-size: 11.5px; }
</style>
