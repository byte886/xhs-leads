<script setup lang="ts">
/**
 * 设置 › AI：三种能力各存一套配置（生成文章 / 分析图片 / 生成图片）。
 *
 * 三种能力依赖的模型完全不同：文本模型只出字、视觉模型必须能读图、图像模型只出图，
 * 所以每张能力卡各存一套 Key / Base URL / 模型，互不影响。
 * 供应商目录用 models 声明这家能干什么，每个能力的下拉只列支持它的供应商。
 */
import { onMounted, reactive, ref } from 'vue'
import { refreshSysStatus, toast } from '../../store'
import {
  apiGetLLMConfig, apiSetLLMConfig, apiClearLLMConfig, apiTestLLM,
  apiGetVisionConfig, apiSetVisionConfig, apiClearVisionConfig, apiTestVision,
  apiGetImageConfig, apiSetImageConfig, apiClearImageConfig, apiTestImageConfig,
} from '../../api'
import type { ImageConfig, LlmConfig } from '../../api'

type AiCap = 'text' | 'vision' | 'image'

interface CapForm { provider: string; api_key: string; base_url: string; model: string }

// base_url 按能力分开：同一家供应商的「文本/读图」端点和「出图」端点经常不是同一个
// （例如阿里云百炼，通义千问走 compatible-mode/v1，通义万相走 api/v1）。
const AI_PROVIDERS: {
  id: string; label: string; note: string
  base_urls: Partial<Record<AiCap, string>>
  models: Partial<Record<AiCap, string[]>>
}[] = [
  {
    id: 'deepseek', label: 'DeepSeek', note: '只有文本模型，不能读图、不能出图',
    base_urls: { text: 'https://api.deepseek.com/v1' },
    models: { text: ['deepseek-flash', 'deepseek-v4-pro'] },
  },
  {
    id: 'dashscope', label: '阿里云百炼', note: '文本 / 读图 / 出图都有，国内直连不用代理',
    base_urls: {
      text: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      vision: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      image: 'https://dashscope.aliyuncs.com/api/v1',
    },
    models: {
      text: ['qwen-max'],
      vision: ['qwen-vl-max', 'qwen-vl-plus'],
      image: ['wan2.7-image', 'wan2.7-image-pro'],
    },
  },
  {
    id: 'gemini', label: 'Google · Nano Banana', note: '只能出图，版式与画质强，需代理',
    base_urls: { image: 'https://generativelanguage.googleapis.com/v1beta' },
    models: { image: ['gemini-3.1-flash-image-preview', 'gemini-3-pro-image-preview'] },
  },
  {
    id: 'openai_compat', label: 'OpenAI 兼容 · 自定义地址',
    note: '官方、中转站、聚合网关是同一套接口，填它给的地址与模型名',
    base_urls: {},
    models: {},
  },
]

/** 这家支持哪些能力：目录里列了该能力的候选模型即支持；自定义地址三种都支持。 */
function capsOf(p: (typeof AI_PROVIDERS)[number]): AiCap[] {
  return p.id === 'openai_compat' ? ['text', 'vision', 'image'] : (Object.keys(p.models) as AiCap[])
}
function providersFor(cap: AiCap) {
  return AI_PROVIDERS.filter((p) => capsOf(p).includes(cap))
}
function modelsFor(cap: AiCap, pid: string): string[] {
  return AI_PROVIDERS.find((p) => p.id === pid)?.models[cap] || []
}
/** 反查供应商：已存配置的 Base URL 命中哪家，命中不了就当自定义地址。 */
function detectProvider(base: string): string {
  if (!base) return 'openai_compat'
  const hit = AI_PROVIDERS.find((p) =>
    Object.values(p.base_urls).some((u) => u && base.startsWith(u.replace(/\/v1$/i, ''))),
  )
  return hit?.id || 'openai_compat'
}
function baseUrlFor(cap: AiCap, pid: string): string {
  return AI_PROVIDERS.find((p) => p.id === pid)?.base_urls[cap] || ''
}
function capForm(cap: AiCap, provider: string, model = ''): CapForm {
  const p = AI_PROVIDERS.find((x) => x.id === provider) || AI_PROVIDERS[0]
  return { provider, api_key: '', base_url: baseUrlFor(cap, provider), model: model || modelsFor(cap, provider)[0] || '' }
}
function onProvider(cap: AiCap, form: CapForm) {
  const p = AI_PROVIDERS.find((x) => x.id === form.provider)
  if (!p) return
  const u = p.base_urls[cap]
  if (u) form.base_url = u
  const m = modelsFor(cap, form.provider)
  if (m.length) form.model = m[0]
}

const textForm = reactive<CapForm>(capForm('text', 'deepseek', 'deepseek-flash'))
const visForm = reactive<CapForm>(capForm('vision', 'dashscope', 'qwen-vl-max'))
const imgForm = reactive<CapForm>(capForm('image', 'dashscope', 'wan2.7-image'))
const textCfg = ref<LlmConfig>()
const visCfg = ref<LlmConfig>()
const imgCfg = ref<ImageConfig>()
const textSaving = ref(false)
const visSaving = ref(false)
const imgSaving = ref(false)

// 生成文章 / 分析图片走同一套 OpenAI 兼容配置接口，只有路径不同（profile: text / vision）
const LLM_API = {
  text: { get: apiGetLLMConfig, set: apiSetLLMConfig, clear: apiClearLLMConfig, test: apiTestLLM },
  vision: { get: apiGetVisionConfig, set: apiSetVisionConfig, clear: apiClearVisionConfig, test: apiTestVision },
}

function capState(cap: 'text' | 'vision') {
  return {
    form: cap === 'text' ? textForm : visForm,
    saving: cap === 'text' ? textSaving : visSaving,
    cfg: cap === 'text' ? textCfg : visCfg,
  }
}

async function loadLlmCap(cap: 'text' | 'vision') {
  const st = capState(cap)
  try {
    st.cfg.value = await LLM_API[cap].get()
  } catch {
    st.cfg.value = undefined
  }
  if (cap === 'text') refreshSysStatus({ aiKey: !!st.cfg.value?.configured })
}

function fillLlmCap(cap: 'text' | 'vision') {
  const st = capState(cap)
  st.form.api_key = ''
  if (st.cfg.value?.configured) {
    st.form.provider = detectProvider(st.cfg.value.openai_base_url)
    st.form.base_url = st.cfg.value.openai_base_url
    st.form.model = st.cfg.value.default_model
  }
}

async function saveLlmCap(cap: 'text' | 'vision') {
  const st = capState(cap)
  if (!st.form.base_url || !st.form.model) {
    toast('请填写 Base URL 与模型名称', 'error')
    return
  }
  st.saving.value = true
  try {
    st.cfg.value = await LLM_API[cap].set({
      api_key: st.form.api_key,
      openai_base_url: st.form.base_url,
      default_model: st.form.model,
    })
    fillLlmCap(cap)
    toast(`${cap === 'text' ? '生成文章' : '分析图片'}配置已保存`)
  } catch (e) {
    toast(`保存失败：${(e as Error).message}`, 'error')
  } finally {
    st.saving.value = false
  }
}

async function clearLlmCap(cap: 'text' | 'vision') {
  const st = capState(cap)
  st.saving.value = true
  try {
    st.cfg.value = await LLM_API[cap].clear()
    fillLlmCap(cap)
    toast(`${cap === 'text' ? '生成文章' : '分析图片'}配置已清除`)
  } catch (e) {
    toast(`清除失败：${(e as Error).message}`, 'error')
  } finally {
    st.saving.value = false
  }
}

async function testLlmCap(cap: 'text' | 'vision') {
  const st = capState(cap)
  try {
    const r = await LLM_API[cap].test({
      api_key: st.form.api_key,
      openai_base_url: st.form.base_url,
      default_model: st.form.model,
    })
    if (r.ok) {
      const extra = r.model_available === false ? '（模型不在清单，可忽略）' : ''
      toast(`连接成功：可用模型 ${r.count ?? 0} 个${extra}`)
    } else {
      toast(`测试失败：${r.error || '未知原因'}`, 'error')
    }
  } catch (e) {
    toast(`测试失败：${(e as Error).message}`, 'error')
  }
}

// ---- 生成图片（独立配置，接口与生文/识图不同：走 /images 出图）----
async function loadImg() {
  try {
    imgCfg.value = await apiGetImageConfig()
  } catch {
    imgCfg.value = undefined
  }
}
function fillImg() {
  imgForm.api_key = ''
  if (imgCfg.value?.configured) {
    imgForm.provider = imgCfg.value.provider || 'dashscope'
    imgForm.base_url = imgCfg.value.base_url || ''
    imgForm.model = imgCfg.value.model || ''
  }
}
async function saveImg() {
  if (!imgForm.model) {
    toast('请选择生图模型', 'error')
    return
  }
  imgSaving.value = true
  try {
    imgCfg.value = await apiSetImageConfig({
      provider: imgForm.provider,
      api_key: imgForm.api_key,
      base_url: imgForm.base_url,
      model: imgForm.model,
    })
    fillImg()
    toast('生成图片配置已保存')
  } catch (e) {
    toast(`保存失败：${(e as Error).message}`, 'error')
  } finally {
    imgSaving.value = false
  }
}
async function clearImg() {
  imgSaving.value = true
  try {
    imgCfg.value = await apiClearImageConfig()
    fillImg()
    toast('生成图片配置已清除')
  } catch (e) {
    toast(`清除失败：${(e as Error).message}`, 'error')
  } finally {
    imgSaving.value = false
  }
}
async function testImg() {
  try {
    const r = await apiTestImageConfig({
      provider: imgForm.provider,
      api_key: imgForm.api_key,
      base_url: imgForm.base_url,
      model: imgForm.model,
    })
    if (r.ok) toast('连接成功')
    else toast(`测试失败：${r.error || '未知原因'}`, 'error')
  } catch (e) {
    toast(`测试失败：${(e as Error).message}`, 'error')
  }
}

onMounted(async () => {
  await Promise.all([loadLlmCap('text'), loadLlmCap('vision'), loadImg()])
  fillLlmCap('text')
  fillLlmCap('vision')
  fillImg()
})
</script>

<template>
  <div>
    <!-- 生成文章 -->
    <div class="ai-block">
      <div class="blk-head">
        <div>
          <h3>生成文章</h3>
          <p>标题 / 正文 / 标签 / 分页文案，要文本模型</p>
        </div>
        <span class="blk-badge" :class="{ on: textCfg?.configured }">{{ textCfg?.configured ? '✓ 可用' : '未配置' }}</span>
      </div>
      <div class="set-row">
        <div class="row-main"><span class="row-label">供应商</span></div>
        <select v-model="textForm.provider" class="full-input" @change="onProvider('text', textForm)">
          <option v-for="p in providersFor('text')" :key="p.id" :value="p.id">{{ p.label }}</option>
        </select>
        <p class="set-hint">{{ AI_PROVIDERS.find(p => p.id === textForm.provider)?.note }}</p>
      </div>
      <div class="set-row">
        <div class="row-main">
          <span class="row-label">API Key</span>
          <span class="limit">{{ textCfg?.llm_api_key_masked ? '已配置 ' + textCfg.llm_api_key_masked + '（留空保留）' : '必填' }}</span>
        </div>
        <input v-model="textForm.api_key" type="password" placeholder="sk-..." class="full-input" />
      </div>
      <div class="set-row">
        <div class="row-main"><span class="row-label">Base URL</span></div>
        <input v-model="textForm.base_url" class="full-input" placeholder="https://api.deepseek.com/v1" />
      </div>
      <div class="set-row">
        <div class="row-main">
          <span class="row-label">模型</span>
          <span class="limit">可手填，下拉是候选</span>
        </div>
        <input v-model="textForm.model" class="full-input" list="text-models" placeholder="deepseek-flash" />
        <datalist id="text-models">
          <option v-for="m in modelsFor('text', textForm.provider)" :key="m" :value="m" />
        </datalist>
        <div class="set-actions">
          <button class="btn" @click="testLlmCap('text')">测试连接</button>
          <button class="btn primary" :disabled="textSaving" @click="saveLlmCap('text')">{{ textSaving ? '保存中…' : '保存配置' }}</button>
          <button v-if="textCfg?.configured" class="btn danger" :disabled="textSaving" @click="clearLlmCap('text')">清除</button>
        </div>
      </div>
    </div>

    <!-- 分析图片 -->
    <div class="ai-block">
      <div class="blk-head">
        <div>
          <h3>分析图片</h3>
          <p>读图片内容，判断品类 / 卖点 / 是否合规，要视觉模型（能吃图）</p>
        </div>
        <span class="blk-badge" :class="{ on: visCfg?.configured }">{{ visCfg?.configured ? '✓ 可用' : '未配置' }}</span>
      </div>
      <div class="set-row">
        <div class="row-main"><span class="row-label">供应商</span></div>
        <select v-model="visForm.provider" class="full-input" @change="onProvider('vision', visForm)">
          <option v-for="p in providersFor('vision')" :key="p.id" :value="p.id">{{ p.label }}</option>
        </select>
        <p class="set-hint">{{ AI_PROVIDERS.find(p => p.id === visForm.provider)?.note }}</p>
      </div>
      <div class="set-row">
        <div class="row-main">
          <span class="row-label">API Key</span>
          <span class="limit">{{ visCfg?.llm_api_key_masked ? '已配置 ' + visCfg.llm_api_key_masked + '（留空保留）' : '必填' }}</span>
        </div>
        <input v-model="visForm.api_key" type="password" placeholder="视觉模型的 Key（与生文可不同）" class="full-input" />
      </div>
      <div class="set-row">
        <div class="row-main"><span class="row-label">Base URL</span></div>
        <input v-model="visForm.base_url" class="full-input" placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
      </div>
      <div class="set-row">
        <div class="row-main">
          <span class="row-label">模型</span>
          <span class="limit">必须支持图片输入</span>
        </div>
        <input v-model="visForm.model" class="full-input" list="vision-models" placeholder="qwen-vl-max" />
        <datalist id="vision-models">
          <option v-for="m in modelsFor('vision', visForm.provider)" :key="m" :value="m" />
        </datalist>
        <div class="set-actions">
          <button class="btn" @click="testLlmCap('vision')">测试连接</button>
          <button class="btn primary" :disabled="visSaving" @click="saveLlmCap('vision')">{{ visSaving ? '保存中…' : '保存配置' }}</button>
          <button v-if="visCfg?.configured" class="btn danger" :disabled="visSaving" @click="clearLlmCap('vision')">清除</button>
        </div>
      </div>
    </div>

    <!-- 生成图片 -->
    <div class="ai-block">
      <div class="blk-head">
        <div>
          <h3>生成图片</h3>
          <p>封面 / 内容页配图，要图像模型，按张计费</p>
        </div>
        <span class="blk-badge" :class="{ on: imgCfg?.configured }">{{ imgCfg?.configured ? '✓ 可用' : '未配置' }}</span>
      </div>
      <div class="set-row">
        <div class="row-main"><span class="row-label">供应商</span></div>
        <select v-model="imgForm.provider" class="full-input" @change="onProvider('image', imgForm)">
          <option v-for="p in providersFor('image')" :key="p.id" :value="p.id">{{ p.label }}</option>
        </select>
        <p class="set-hint">{{ AI_PROVIDERS.find(p => p.id === imgForm.provider)?.note }}</p>
      </div>
      <div class="set-row">
        <div class="row-main">
          <span class="row-label">API Key</span>
          <span class="limit">{{ imgCfg?.api_key_masked ? '已配置 ' + imgCfg.api_key_masked + '（留空保留）' : '必填' }}</span>
        </div>
        <input v-model="imgForm.api_key" type="password" placeholder="图像模型的 Key（与生文可不同）" class="full-input" />
      </div>
      <div class="set-row">
        <div class="row-main"><span class="row-label">Base URL</span></div>
        <input v-model="imgForm.base_url" class="full-input" placeholder="https://dashscope.aliyuncs.com/api/v1" />
      </div>
      <div class="set-row">
        <div class="row-main">
          <span class="row-label">模型</span>
          <span class="limit">只能填能出图的模型</span>
        </div>
        <input v-model="imgForm.model" class="full-input" list="image-models" placeholder="wan2.7-image" />
        <datalist id="image-models">
          <option v-for="m in modelsFor('image', imgForm.provider)" :key="m" :value="m" />
        </datalist>
        <div class="set-actions">
          <button class="btn" @click="testImg">测试连接</button>
          <button class="btn primary" :disabled="imgSaving" @click="saveImg">{{ imgSaving ? '保存中…' : '保存配置' }}</button>
          <button v-if="imgCfg?.configured" class="btn danger" :disabled="imgSaving" @click="clearImg">清除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ---- AI 分区：生文 / 生图 两条独立链路（供应商 + Key 各自独立）---- */
.ai-block {
  background: var(--panel); border: 1px solid var(--line);
  border-radius: var(--r-card); padding: 17px 18px; margin-bottom: 12px;
}
.blk-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
.blk-head h3 { margin: 0; font-size: 15px; font-weight: 700; color: var(--ink); }
.blk-head p { margin: 5px 0 0; font-size: 12px; color: var(--ink-3); line-height: 1.6; }
.blk-badge {
  flex-shrink: 0; font-size: 11.5px; font-weight: 700; color: var(--ink-3);
  background: var(--panel-2); border-radius: var(--r-pill); padding: 4px 10px;
}
.blk-badge.on { color: var(--green); background: rgba(47, 122, 85, .09); }
</style>
