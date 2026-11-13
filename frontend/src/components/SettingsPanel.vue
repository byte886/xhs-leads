<script setup lang="ts">
// SettingsPanel v2.0：设置 Tab（左侧分类导航 + 右侧逐项一行配置）
import { computed, onMounted, onBeforeUnmount, reactive, ref } from 'vue'
import { appState, refreshSysStatus, toast } from '../store'
import {
  apiGetLLMConfig, apiSetLLMConfig, apiClearLLMConfig, apiTestLLM,
  apiGetVisionConfig, apiSetVisionConfig, apiClearVisionConfig, apiTestVision,
  apiGetImageConfig, apiSetImageConfig, apiClearImageConfig, apiTestImageConfig,
  apiLicenseStatus, apiLicenseActivate,
  apiMirrorOpen, apiMirrorClose,
  apiListAccounts, apiAddAccount, apiDeleteAccount, apiRenameAccount,
  apiRefreshIdentity, apiScanLogin,
} from '../api'
import type { AccountItem } from '../types'
import type { ImageConfig, LlmConfig } from '../api'

// ---- 左侧分类导航 ----
// 只留 AI 可进入：其余分类置灰、不可点击也不可切入。
// 「窗口与调试」整块已删除（壳窗口 Tab 开关、引擎实时窗口开关都不再需要）。
const cats = [
  { id: 'ai', label: 'AI', on: true },
  { id: 'account', label: '账号与登录', on: false },
  { id: 'collect', label: '采集设置', on: false },
  { id: 'about', label: '关于与授权', on: false },
]
const activeCat = ref('ai')

// 全局请求间隔：单值基准 + 自动窄随机抖动（Scrapy download_delay 同思路）。
// UI 如实显示随机区间，避免用户把随机误读为"多处不同值"。
const intervalBase = computed(() => (appState.params.interval / 1000).toFixed(1))
const intervalMax = computed(() => ((appState.params.interval + 800) / 1000).toFixed(1))

// ---- 账号与登录 ----
// 登录态不再在设置页展示/管理（登录统一由右侧调试窗承担，左窗不弹登录框）：
// 账号卡片显示 @小红书号 即已登录，没有就是未登录；需要登录由采集/发布撞墙时
// 自动在右窗开登录页。故这里只保留多账号管理。

// ---- 多账号管理 ----
const accounts = ref<AccountItem[]>([])
const acctBusy = ref(false)
const newAcctId = ref('')
const newAcctName = ref('')
const editingAcct = ref('')
const editName = ref('')

/** role 的业务含义（注册表原值是 mixed/collect/publish，界面别直接露技术词） */
const ROLE_LABEL: Record<string, string> = {
  mixed: '通用', collect: '采集', publish: '发布',
}
function roleLabel(role: string): string {
  return ROLE_LABEL[role] || role || '通用'
}

async function loadAccounts() {
  try {
    const r = await apiListAccounts()
    accounts.value = r.accounts
  } catch (e) {
    toast('账号列表加载失败：' + (e as Error).message, 'error')
  }
}

function startRename(a: AccountItem) {
  editingAcct.value = a.account_id
  editName.value = a.name || a.account_id
}
function cancelRename() {
  editingAcct.value = ''
  editName.value = ''
}
async function saveRename(aid: string) {
  if (acctBusy.value) return
  acctBusy.value = true
  try {
    const r = await apiRenameAccount(aid, editName.value.trim())
    if (!r.ok) {
      toast(r.error || '改名失败', 'error')
      return
    }
    toast(`已改名为「${r.name}」`)
    cancelRename()
    await loadAccounts()
  } catch (e) {
    toast('改名失败：' + (e as Error).message, 'error')
  } finally {
    acctBusy.value = false
  }
}

async function refreshIdentityFn(aid: string) {
  if (acctBusy.value) return
  acctBusy.value = true
  try {
    const r = await apiRefreshIdentity(aid)
    if (!r.ok) {
      toast(r.error || '识别失败', 'error')
      return
    }
    const parts = [r.nickname, r.red_id ? '@' + r.red_id : ''].filter(Boolean)
    toast(`已识别：${parts.join(' ')}`)
    await loadAccounts()
  } catch (e) {
    toast('识别失败：' + (e as Error).message, 'error')
  } finally {
    acctBusy.value = false
  }
}

async function addAccountFn() {
  if (acctBusy.value) return
  acctBusy.value = true
  try {
    const r = await apiAddAccount(newAcctId.value.trim(), 'mixed', newAcctName.value.trim())
    if (!r.ok) {
      toast(r.error || '新增失败', 'error')
      return
    }
    toast(`已新增「${r.name || r.account_id}」（CDP 端口 ${r.debug_port}）`)
    newAcctId.value = ''
    newAcctName.value = ''
    await loadAccounts()
  } catch (e) {
    toast('新增失败：' + (e as Error).message, 'error')
  } finally {
    acctBusy.value = false
  }
}

async function removeAccountFn(aid: string) {
  if (acctBusy.value) return
  acctBusy.value = true
  try {
    const r = await apiDeleteAccount(aid)
    if (!r.ok) {
      toast(r.error || '删除失败', 'error')
      return
    }
    toast(r.note || '已删除')
    await loadAccounts()
  } catch (e) {
    toast('删除失败：' + (e as Error).message, 'error')
  } finally {
    acctBusy.value = false
  }
}

async function openAccountFn(aid: string) {
  if (acctBusy.value) return
  acctBusy.value = true
  try {
    const r = await apiMirrorOpen(aid)
    if (!r.ok) {
      toast(r.error || '开窗失败', 'error')
      return
    }
    toast(`已打开账号 ${aid}，未登录可在窗口扫码`)
    await loadAccounts()
  } catch (e) {
    toast('开窗失败：' + (e as Error).message, 'error')
  } finally {
    acctBusy.value = false
  }
}

async function scanLoginFn(aid: string) {
  if (acctBusy.value) return
  acctBusy.value = true
  try {
    const r = await apiScanLogin(aid)
    if (!r.ok) {
      toast(r.error || '扫码登录失败', 'error')
      return
    }
    toast(r.note || '已打开扫码窗口，请扫码登录')
    await loadAccounts()
  } catch (e) {
    toast('扫码登录失败：' + (e as Error).message, 'error')
  } finally {
    acctBusy.value = false
  }
}

async function closeAccountFn(aid: string) {
  if (acctBusy.value) return
  acctBusy.value = true
  try {
    const r = await apiMirrorClose(aid)
    if (!r.ok) {
      toast(r.error || '收窗失败', 'error')
      return
    }
    toast(r.note || '已收起')
    await loadAccounts()
  } catch (e) {
    toast('收窗失败：' + (e as Error).message, 'error')
  } finally {
    acctBusy.value = false
  }
}

// ---- 授权状态 ----
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

// ---- AI · 三种能力：生成文章 / 分析图片 / 生成图片 ----
// 三种能力依赖的模型完全不同：文本模型只出字、视觉模型必须能读图、图像模型只出图，
// 所以每张能力卡各存一套 Key / Base URL / 模型，互不影响。
// 供应商目录用 models 声明这家能干什么，每个能力的下拉只列支持它的供应商。

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

// ---- 档案已拆成独立 Tab（ProfilePanel.vue），设置页不再承载档案 ----

onMounted(async () => {
  await Promise.all([loadLlmCap('text'), loadLlmCap('vision'), loadImg(), loadLicense(), loadAccounts()])
  fillLlmCap('text')
  fillLlmCap('vision')
  fillImg()
})
</script>

<template>
  <div>
    <div class="settings-layout">
      <!-- 左侧分类导航 -->
      <div class="settings-nav">
        <button
          v-for="c in cats"
          :key="c.id"
          class="nav-item"
          :class="{ cur: activeCat === c.id, off: !c.on }"
          :disabled="!c.on"
          @click="c.on && (activeCat = c.id)"
        >{{ c.label }}</button>
      </div>

      <!-- 右侧内容区 -->
      <div class="settings-body">
        <!-- 账号与登录 -->
        <section v-if="activeCat === 'account'">
          <!-- 多账号管理 -->
          <div class="set-row">
            <div class="row-main">
              <span class="row-label">账号管理（{{ accounts.length }}）</span>
              <span class="cur-val">每个账号独立进程 / profile / 登录态 / CDP 端口</span>
            </div>

            <div class="acct-list">
              <div v-for="a in accounts" :key="a.account_id" class="acct-item">
                <div class="acct-row">
                  <!-- 主标题 = 自动派生名称（平台昵称 → @小红书号 → 内部 id），不需要人工起名 -->
                  <div class="acct-main">
                    <span class="acct-name" :title="a.account_id">
                      {{ a.display_name || a.account_id }}
                      <span v-if="a.name && a.name !== a.account_id" class="acct-note">{{ a.name }}</span>
                    </span>
                    <span class="acct-sub">
                      <span v-if="a.red_id" class="acct-redid" title="小红书号，可在 App「我」页面核对">@{{ a.red_id }}</span>
                      <span class="acct-id">{{ a.account_id }}</span>
                      <span class="acct-role">{{ roleLabel(a.role) }}</span>
                    </span>
                  </div>
                  <div class="acct-tags">
                    <span class="tag-pill" :class="{ run: a.running }">
                      {{ a.running ? '运行中' : '已停止' }}
                    </span>
                    <span v-if="a.running" class="tag-pill" :class="{ hl: !a.headless }">
                      {{ a.headless ? '无头' : '有头' }}
                    </span>
                    <span class="port-pill">CDP {{ a.debug_port ?? '—' }}</span>
                  </div>
                </div>

                <!-- 备注：行内输入，只写注册表 name 当后缀，不搬目录不动登录态；
                     主名称永远来自平台，备注只是给人添个「哪个店」的记号 -->
                <div v-if="editingAcct === a.account_id" class="acct-rename">
                  <input
                    v-model="editName"
                    class="acct-name-input"
                    placeholder="备注（可选），如「襄阳店」「二号店」"
                    maxlength="24"
                    @keyup.enter="saveRename(a.account_id)"
                    @keyup.esc="cancelRename"
                  />
                  <button class="btn primary sm" :disabled="acctBusy" @click="saveRename(a.account_id)">保存</button>
                  <button class="btn sm" @click="cancelRename">取消</button>
                </div>
                <div v-else class="acct-ops">
                  <button class="btn sm" :disabled="acctBusy" @click="startRename(a)">备注</button>
                  <button
                    v-if="a.account_id !== 'default'"
                    class="btn primary sm"
                    :disabled="acctBusy"
                    :title="a.red_id ? '登录态过期可重新扫码' : '打开有头窗口扫码登录'"
                    @click="scanLoginFn(a.account_id)"
                  >扫码登录</button>
                  <button
                    v-if="a.running && !a.red_id"
                    class="btn sm"
                    :disabled="acctBusy"
                    title="读一次该账号的昵称与小红书号，用来认出这是哪个号"
                    @click="refreshIdentityFn(a.account_id)"
                  >识别身份</button>
                  <button
                    v-if="!a.running"
                    class="btn sm"
                    :disabled="acctBusy"
                    @click="openAccountFn(a.account_id)"
                  >开窗</button>
                  <button
                    v-else
                    class="btn sm"
                    :disabled="acctBusy || a.account_id === 'default'"
                    :title="a.account_id === 'default' ? '主账号承载 APP 壳窗，不可收起' : ''"
                    @click="closeAccountFn(a.account_id)"
                  >收窗</button>
                  <button
                    v-if="a.account_id !== 'default'"
                    class="btn danger sm"
                    :disabled="acctBusy"
                    @click="removeAccountFn(a.account_id)"
                  >删除</button>
                </div>
              </div>
            </div>

            <div class="add-acct">
              <input
                v-model="newAcctId"
                class="acct-id-input"
                placeholder="内部 id 留空自动生成"
                @keyup.enter="addAccountFn"
              />
              <input
                v-model="newAcctName"
                class="acct-name-input"
                placeholder="备注（可选），如「襄阳店」"
                maxlength="24"
              />
              <button class="btn primary sm" :disabled="acctBusy" @click="addAccountFn">
                新增账号
              </button>
            </div>
            <p class="set-hint">
              名字不用你起：开窗扫码登录后，APP 会自动记下这个号的<b>小红书昵称</b>和
              <b>小红书号</b>，列表和窗口标题都按它显示；还没登录的账号先显示自动生成的唯一 id。
              备注只是给自己加个记号（如「襄阳店」），可留空、可随时改，不搬目录、不影响登录态。
              内部 id 才是目录名（仅小写字母/数字/_-，≤32 字符）。
              删除会把 profile 移入回收区（可回滚），不直接清除登录态。
            </p>
          </div>
        </section>

        <!-- 采集设置 -->
        <section v-if="activeCat === 'collect'">
          <div class="set-row">
            <div class="row-main">
              <span class="row-label">极速采集</span>
              <label class="switch">
                <input type="checkbox" v-model="appState.params.fast" />
                <span class="track"><span class="thumb" /></span>
              </label>
              <span class="cur-val">{{ appState.params.fast ? '已开启' : '已关闭' }}</span>
            </div>
            <p class="set-hint">
              开启后跳过拟人等待，采集快约一倍（20 条约 5 分钟 → 2 分钟）；节奏更密、被平台风控的概率略升。
              建议默认关闭，赶数据时再开。
            </p>
          </div>
          <div class="set-row">
            <div class="row-main">
              <span class="row-label">全局请求间隔</span>
              <input v-model.number="appState.params.interval" type="number" min="1000" max="10000" step="100" class="num-input" />
              <span class="unit">毫秒</span>
              <span class="cur-val">实际每次 {{ intervalBase }}–{{ intervalMax }} 秒（随机）</span>
            </div>
            <p class="set-hint">
              每次页面操作之间的间隔。安全范围 1–10 秒，默认 1 秒（真人浏览节奏）。
              调大 = 采集更慢更稳；下限 1 秒受系统保护（不能更快，防风控）。开启极速采集时自动跳过该等待。
            </p>
          </div>
        </section>

                <!-- AI：三种能力各存一套配置（生成文章 / 分析图片 / 生成图片） -->
        <section v-if="activeCat === 'ai'">
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
        </section>

<!-- 关于与授权 -->
        <section v-if="activeCat === 'about'">
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
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 两栏布局：左侧分类导航 + 右侧逐项列表 */
.settings-layout { display: flex; gap: 18px; align-items: flex-start; }
.settings-nav {
  width: 152px; flex-shrink: 0;
  display: flex; flex-direction: column; gap: 2px;
  position: sticky; top: 0;
}
.nav-item {
  text-align: left; padding: 9px 12px; border-radius: var(--r-ctl);
  border: 0; background: transparent;
  font-size: 13px; color: var(--ink-3); cursor: pointer; user-select: none;
  transition: color .15s, background .15s;
}
.nav-item:hover { color: var(--ink); background: var(--panel-2); }
/* 选中用底色 + 内描边表达，不用左侧彩条（>1px 竖条是反模式） */
.nav-item.cur {
  background: var(--panel); color: var(--red); font-weight: 600;
  box-shadow: inset 0 0 0 1px rgba(226, 42, 67, .2);
}
/* 暂不开放的分区：置灰、不可点、不响应 hover */
.nav-item.off {
  color: var(--ink-3); opacity: .45; cursor: default;
}
.nav-item.off:hover { color: var(--ink-3); background: transparent; }
.settings-body { flex: 1; min-width: 0; }

/* 每项一行 */
/* 字段行：在一张卡的内部逐行排，不各占一张卡（密度靠行高与一条分隔线） */
.set-row {
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--line);
  border-radius: 0;
  padding: 12px 0 13px;
  margin: 0;
}
.set-row:last-of-type { border-bottom: 0; padding-bottom: 0; }
.row-main { display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }
.row-label { flex: 0 0 auto; font-size: 13px; font-weight: 700; color: var(--ink); }
/* 行内说明推到最右，控件仍紧跟标签（不把两者一起推开） */
.row-main .limit { margin-left: auto; }
.set-hint { font-size: 12px; color: var(--ink-3); line-height: 1.65; margin: 8px 0 0; }
.cur-val { font-size: 12px; color: var(--ink-2); font-weight: 600; }
.unit { font-size: 12px; color: var(--ink-3); }
/* 控件不拉满整行：下拉一类的短值不该有 600px 宽 */
.set-row select.full-input { max-width: 340px; }
.set-row input.full-input { max-width: 640px; }
.num-input { width: 110px; }
.full-input { width: 100%; box-sizing: border-box; margin-top: 8px; }
textarea {
  width: 100%; box-sizing: border-box; resize: vertical; margin-top: 8px;
  border: 1px solid var(--line, #e5e5e5); border-radius: 8px;
  padding: 8px 10px; font-size: 12.5px; font-family: ui-monospace, Menlo, monospace;
}
input:not([type="checkbox"]) {
  border: 1px solid var(--line);
  border-radius: var(--r-ctl);
  padding: 8px 10px;
  font-size: 13px;
  background: var(--panel);
}


/* 状态元素 */
/* 多账号列表：主标题=业务名（大、深），副标题=昵称/内部id/用途（小、灰） */
.acct-list { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.acct-item {
  display: flex; flex-direction: column; gap: 8px;
  border: 1px solid var(--line); border-radius: var(--r-card);
  padding: 10px 12px; background: var(--panel-2);
}
.acct-row {
  display: flex; align-items: flex-start; justify-content: space-between;
  gap: 12px; flex-wrap: wrap;
}
.acct-main { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.acct-name {
  font-size: 14px; font-weight: 700; color: #222;
  max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.acct-sub { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.acct-note {
  font-size: 11px; font-weight: 400; color: #666; margin-left: 6px;
}
.acct-redid {
  font-size: 12px; font-weight: 600; color: #b45409;
  background: #fdf2e2; padding: 1px 8px; border-radius: 999px;
  font-family: ui-monospace, Menlo, monospace;
}
.acct-nick {
  font-size: 12px; font-weight: 600; color: #b45409;
  background: #fdf2e2; padding: 1px 8px; border-radius: 999px;
  max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.acct-id {
  font-size: 11px; color: #999;
  font-family: ui-monospace, Menlo, monospace;
}
.acct-role { font-size: 11px; color: #888; }
.acct-tags { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.tag-pill {
  font-size: 11px; font-weight: 600; color: #888;
  padding: 2px 9px; border-radius: 999px; background: #eee;
}
.tag-pill.run { color: #1a7f4c; background: #eaf7ef; }
.tag-pill.hl { color: #b45409; background: #fdf2e2; }
.port-pill {
  font-size: 11px; color: #666; font-family: ui-monospace, Menlo, monospace;
  background: #f0f0f3; padding: 2px 8px; border-radius: 6px;
}
.acct-ops { display: flex; gap: 6px; flex-wrap: wrap; }
.acct-rename { display: flex; gap: 8px; align-items: center; }
.add-acct {
  display: flex; gap: 8px; margin-top: 12px; align-items: center; flex-wrap: wrap;
}
.acct-name-input { flex: 1 1 200px; margin-top: 0 !important; }
.acct-id-input { flex: 1 1 160px; margin-top: 0 !important; font-family: ui-monospace, Menlo, monospace; }
.set-actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.import-result { font-size: 12px; color: #1a7f4c; margin: auto 0 0 4px; }
/* 行内辅助说明：中性灰，不做第二强调色 */
.limit { color: var(--ink-3); font-weight: 500; font-size: 12px; }
.lic-tag { padding: 2px 10px; border-radius: 999px; background: #e8f5e9; color: #2e7d32; font-weight: 600; font-size: 12px; }
.lic-tag.on { background: #e3f2fd; color: #1565c0; }
.lic-ok { color: #2e7d32; font-weight: 600; font-size: 12px; }
.lic-fp { color: #888; font-family: monospace; font-size: 11.5px; }
.gate-msg {
  display: flex; align-items: center; gap: 8px;
  background: #FFF7E6; border: 1px solid #FFE2B8; color: #B45409;
  border-radius: 8px; padding: 10px 12px; font-size: 12px; margin-top: 12px;
}
.gate-msg.ok { background: #F2FBF5; border-color: #BFE8D0; color: #1a7f4c; }

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

@media (max-width: 720px) {
  .settings-layout { flex-direction: column; }
  .settings-nav { width: 100%; flex-direction: row; overflow-x: auto; position: static; }
  .nav-item { white-space: nowrap; }
}
</style>
