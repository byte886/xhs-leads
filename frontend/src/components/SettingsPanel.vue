<script setup lang="ts">
// SettingsPanel v1.5：设置 Tab（AI 模型 Key 门控 / CDP 连接 / 账号人设）
// 三卡收敛一页；配置后通过 appState.sys 全局状态条即时反映（共享观察）
import { onMounted, reactive, ref } from 'vue'
import { refreshSysStatus, toast } from '../store'
import {
  apiGetLLMConfig, apiSetLLMConfig, apiClearLLMConfig, apiTestLLM,
  apiCdpStatus, apiCdpSetup, apiCdpOpenInspect, apiCdpPickChrome,
  apiLicenseStatus, apiLicenseActivate,
} from '../api'

// ---- 授权状态（薄层：free 恒放行，展示模式/功能清单/指纹摘要） ----
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
  if (!actKey.value.trim()) { toast('请输入激活码'); return }
  actBusy.value = true
  try {
    const r = await apiLicenseActivate(actKey.value.trim())
    if (r.ok) {
      toast('激活成功（激活码与设备绑定）')
      actKey.value = ''
      await loadLicense()
    } else {
      toast(r.note || '激活失败')
    }
  } catch (e) {
    toast(`激活失败：${(e as Error).message}`)
  } finally {
    actBusy.value = false
  }
}

// ---- AI Key ----
const aiCfg = ref<{ configured: boolean; openai_base_url: string; default_model: string; llm_api_key_masked: string }>()
const aiForm = reactive({ api_key: '', openai_base_url: '', default_model: '' })
const aiSaving = ref(false)

async function loadAi() {
  try {
    aiCfg.value = await apiGetLLMConfig()
  } catch {
    aiCfg.value = { configured: false, openai_base_url: '', default_model: '', llm_api_key_masked: '' }
  }
  refreshSysStatus({ aiKey: !!aiCfg.value.configured })
}
function fillAiForm() {
  aiForm.api_key = ''
  aiForm.openai_base_url = aiCfg.value?.openai_base_url || ''
  aiForm.default_model = aiCfg.value?.default_model || ''
}
async function saveAi() {
  if (!aiForm.openai_base_url || !aiForm.default_model) {
    toast('请填写 Base URL 与模型名称')
    return
  }
  aiSaving.value = true
  try {
    await apiSetLLMConfig({ ...aiForm })
    await loadAi()
    fillAiForm()
    toast('AI 配置已保存')
  } catch (e) {
    toast(`保存失败：${(e as Error).message}`)
  } finally {
    aiSaving.value = false
  }
}
async function clearAi() {
  // 显式清除已配置 Key/Base/模型（空 key 保存=保留是保护设计，清除走独立接口）
  // 可逆低风险操作（重新配置即可），直接执行 + toast 提示，不用原生 confirm（CDP 自动化/跨平台兼容问题）
  aiSaving.value = true
  try {
    await apiClearLLMConfig()
    await loadAi()
    fillAiForm()
    toast('AI 配置已清除（可随时重新配置）')
  } catch (e) {
    toast(`清除失败：${(e as Error).message}`)
  } finally {
    aiSaving.value = false
  }
}
async function testAi() {
  try {
    const r = await apiTestLLM({ ...aiForm })
    if (r.ok) {
      const extra = r.model_available === false ? '（模型不在清单，可忽略）' : ''
      toast(`连接成功：可用模型 ${r.count ?? 0} 个${extra}`)
    } else {
      toast(`测试失败：${r.error || '未知原因'}`)
    }
  } catch (e) {
    toast(`测试失败：${(e as Error).message}`)
  }
}

// ---- CDP 连接 ----
const cdp = ref<{ ok: boolean; port: number; port_ready: boolean; chrome_running: boolean; chrome_found: boolean; state: string; chrome_path: string; profile_dir: string }>()
const cdpBusy = ref('')

async function loadCdp() {
  try {
    cdp.value = await apiCdpStatus()
  } catch {
    cdp.value = { ok: false, port: 0, port_ready: false, chrome_running: false, chrome_found: false, state: 'unknown', chrome_path: '', profile_dir: '' }
  }
  refreshSysStatus({
    cdp: { ok: cdp.value.ok, state: cdp.value.state, chrome_found: cdp.value.chrome_found, profile_dir: cdp.value.profile_dir, port: cdp.value.port },
  })
}
async function cdpVerify() {
  cdpBusy.value = 'verify'
  try {
    const r = await apiCdpSetup(false)
    toast(r.ok ? (r.note || 'CDP 验证通过') : `失败：${r.note || '未知原因'}`)
    await loadCdp()
  } catch (e) {
    toast(`失败：${(e as Error).message}`)
  } finally {
    cdpBusy.value = ''
  }
}
async function cdpRestart() {
  cdpBusy.value = 'restart'
  try {
    const r = await apiCdpSetup(true)
    toast(r.ok ? (r.note || '已重启调试连接') : `失败：${r.note || '未知原因'}`)
    await loadCdp()
  } catch (e) {
    toast(`失败：${(e as Error).message}`)
  } finally {
    cdpBusy.value = ''
  }
}
async function cdpOpenInspect() {
  await apiCdpOpenInspect().catch(() => {})
}
async function cdpPick() {
  const r = await apiCdpPickChrome().catch(() => ({ ok: false, note: '无法打开文件选择' }))
  if (r.ok) toast('已选择 Chrome，保存后生效')
  else toast(r.note || '未选择')
  await loadCdp()
}

// ---- 账号人设 ----
const persona = reactive({ industry: '', tone: '', topics: '' })
const personaLoaded = ref(false)
const personaSaving = ref(false)

async function loadPersona() {
  try {
    const r = await fetch('/api/persona')
    const d = await r.json()
    persona.industry = d.industry || ''
    persona.tone = d.tone || ''
    persona.topics = d.topics || ''
  } catch {
    /* 未就绪：保持空 */
  }
  personaLoaded.value = true
}
async function savePersona() {
  personaSaving.value = true
  try {
    const r = await fetch('/api/persona', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(persona),
    })
    const d = await r.json()
    if (d.ok) toast('人设已保存（AI 生成保持该口吻）')
    else toast('保存失败')
  } catch {
    toast('保存失败：网络错误')
  } finally {
    personaSaving.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadAi(), loadCdp(), loadPersona(), loadLicense()])
  fillAiForm()
})
</script>

<template>
  <div>
    <div class="page-title">设置 · AI 模型 / CDP 连接 / 账号人设</div>
    <div class="page-desc">三个配置项收敛一页：AI Key 门控（文稿/质检共用）、CDP 连接（采集/克隆前置）、账号人设（AI 生成保持行业口吻）。未配置项按钮灰态并引导。</div>

    <div v-if="lic" class="lic-bar">
      <span class="lic-tag" :class="{ on: lic.mode === 'activation' && lic.has_activation }">
        {{ lic.mode === 'free' ? '免费版' : (lic.has_activation ? '激活版' : '未激活') }}
      </span>
      <span>已开通功能：{{ lic.features.join(' / ') }}</span>
      <span class="lic-fp" :title="lic.machine_fingerprint">设备指纹：{{ lic.machine_fingerprint.slice(0, 12) }}…</span>
      <span v-if="lic.has_activation && lic.mode !== 'free'" class="lic-ok">✓ 已激活（机器码绑定）</span>
    </div>

    <div v-if="lic && lic.mode !== 'free'" class="lic-bar lic-act">
      <input v-model="actKey" placeholder="粘贴卖家提供的激活码" style="flex:1;min-width:240px" @keyup.enter="activateLicense" />
      <button class="btn primary sm" :disabled="actBusy" @click="activateLicense">{{ actBusy ? '激活中…' : '激活' }}</button>
      <span class="set-hint" style="margin:0">激活码由卖家按设备指纹签发，一台设备一个码</span>
    </div>

    <div class="set-grid">
      <!-- AI 模型 -->
      <div class="set-card">
        <div class="set-card-h">AI 模型（Key 门控）</div>
        <label class="f-label">API Key <span class="limit">{{ aiCfg?.llm_api_key_masked ? '已配置 ' + aiCfg.llm_api_key_masked + '（留空保留）' : '必填' }}</span></label>
        <input v-model="aiForm.api_key" type="password" placeholder="sk-..." style="width:100%" />
        <label class="f-label">Base URL（OpenAI 兼容端点）</label>
        <input v-model="aiForm.openai_base_url" placeholder="https://api.openai.com/v1" style="width:100%" />
        <label class="f-label">模型名称</label>
        <input v-model="aiForm.default_model" placeholder="gpt-4o-mini / deepseek-chat / claude-…" style="width:100%" />
        <p class="set-hint">兼容 OpenAI / DeepSeek / 通义 / 中转站 / Claude（base_url 填 api.anthropic.com/v1）。未配置时，创作页 AI 工具条与发布前质检灰态。</p>
        <div class="set-actions">
          <button class="btn" @click="testAi">测试连接</button>
          <button class="btn primary" :disabled="aiSaving" @click="saveAi">{{ aiSaving ? '保存中…' : '保存配置' }}</button>
          <button v-if="aiCfg?.configured" class="btn danger" :disabled="aiSaving" @click="clearAi">清除配置</button>
        </div>
        <div class="gate-msg" :class="{ ok: aiCfg?.configured }">
          {{ aiCfg?.configured ? '✅ 已配置 AI 模型 —— 文稿生成 / 发布前质检可用' : '⚠ 当前未配置 AI 模型 —— AI 功能处于灰态' }}
        </div>
      </div>

      <!-- CDP 连接 -->
      <div class="set-card">
        <div class="set-card-h">CDP 连接（Chrome 路径 / Profile）</div>
        <label class="f-label">Chrome 路径</label>
        <div class="cdp-row">
          <input :value="cdp?.chrome_path || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'" readonly placeholder="未检测到 Chrome" />
          <button class="btn sm" @click="cdpPick">浏览指定</button>
        </div>
        <label class="f-label">Profile（独立调试 Profile，已登录小红书）</label>
        <input :value="cdp?.profile_dir || '（未检测）'" readonly style="width:100%" />
        <label class="f-label">调试端口</label>
        <input :value="cdp?.port ? String(cdp.port) : '—'" readonly style="width:100%" />
        <div class="cdp-status">
          <span v-if="cdp?.ok" class="ok">✓ Chrome 已检测到 · Profile 已登录小红书 · 调试可用</span>
          <span v-else-if="cdp?.state === 'not_configured'" class="warn">未配置：请按「引导教程」开启远程调试（每个 Profile 勾选一次即持久化）</span>
          <span v-else class="warn">检测中 / 未就绪：{{ cdp?.state || '…' }}</span>
        </div>
        <p class="set-hint">生产环境：macOS / Windows 双脚本带参数启动 Chrome 打开配置页；未找到 Chrome 时 APP 提示并提供链接，配置好后采集/克隆按钮才可用。</p>
        <div class="set-actions">
          <button class="btn" :disabled="cdpBusy === 'restart'" @click="cdpRestart">{{ cdpBusy === 'restart' ? '重启中…' : '重启调试连接' }}</button>
          <button class="btn" @click="cdpOpenInspect">引导教程</button>
          <button class="btn primary" :disabled="cdpBusy === 'verify'" @click="cdpVerify">{{ cdpBusy === 'verify' ? '验证中…' : '保存并验证' }}</button>
        </div>
      </div>

      <!-- 账号人设 -->
      <div class="set-card">
        <div class="set-card-h">账号人设（AI 生成口吻）</div>
        <label class="f-label">行业 / 主营品类</label>
        <input v-model="persona.industry" placeholder="如：珠宝翡翠、护肤、餐饮…" style="width:100%" />
        <label class="f-label">人设口吻</label>
        <input v-model="persona.tone" placeholder="如：干货型 / 分享型 / 探店型…" style="width:100%" />
        <label class="f-label">常发主题</label>
        <input v-model="persona.topics" placeholder="逗号分隔，如：避坑指南、性价比测评…" style="width:100%" />
        <p class="set-hint">AI 生成标题/正文/标签时保持人设一致，避免"宝子们/家人们/谁懂啊"式通用 AI 味。</p>
        <div class="set-actions">
          <button class="btn primary" :disabled="personaSaving || !personaLoaded" @click="savePersona">{{ personaSaving ? '保存中…' : '保存人设' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page-title { font-size: 16px; font-weight: 700; margin-bottom: 4px; }
.page-desc { font-size: 12.5px; color: var(--ink-2, #888); margin-bottom: 16px; line-height: 1.6; }
.set-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
@media (max-width: 1100px) { .set-grid { grid-template-columns: 1fr; } }
.lic-bar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; padding: 9px 14px; margin-bottom: 14px; border: 1px solid #e6e6eb; border-radius: 8px; background: #fafafa; font-size: 13px; color: #444; }
.lic-bar.lic-act { gap: 10px; }
.lic-tag { padding: 2px 10px; border-radius: 999px; background: #e8f5e9; color: #2e7d32; font-weight: 600; }
.lic-tag.on { background: #e3f2fd; color: #1565c0; }
.lic-ok { color: #2e7d32; font-weight: 600; }
.lic-fp { color: #888; font-family: monospace; font-size: 12px; }
.set-card {
  background: var(--bg-card, #fff);
  border: 1px solid var(--line, #ececec);
  border-radius: 12px;
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.set-card-h { font-size: 14px; font-weight: 700; margin-bottom: 4px; }
.f-label { font-size: 12px; color: var(--ink-2, #666); font-weight: 600; margin-top: 6px; }
.f-label .limit { color: #b08a4d; font-weight: 500; margin-left: 4px; }
input {
  border: 1px solid var(--line, #e5e5e5);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
  background: #fff;
}
.set-hint { font-size: 12px; color: var(--ink-3, #aaa); line-height: 1.6; margin: 4px 0 0; }
.set-actions { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
.cdp-row { display: flex; gap: 8px; }
.cdp-row input { flex: 1; min-width: 0; }
.cdp-status { font-size: 12px; margin-top: 6px; }
.cdp-status .ok { color: #1a7f4c; font-weight: 600; }
.cdp-status .warn { color: #b45409; }
.gate-msg {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #FFF7E6;
  border: 1px solid #FFE2B8;
  color: #B45409;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12px;
  margin-top: 10px;
}
.gate-msg.ok { background: #F2FBF5; border-color: #BFE8D0; color: #1a7f4c; }
</style>
