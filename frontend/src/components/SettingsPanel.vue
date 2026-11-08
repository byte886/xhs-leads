<script setup lang="ts">
// SettingsPanel v2.0：设置 Tab（左侧分类导航 + 右侧逐项一行配置）
// R-6.46 功能域分组 → R-6.47 两栏布局：账号 / 采集 / AI 能力 / 窗口与调试 / 关于与授权
import { computed, onMounted, onBeforeUnmount, reactive, ref } from 'vue'
import { appState, refreshSysStatus, toast } from '../store'
import {
  apiGetLLMConfig, apiSetLLMConfig, apiClearLLMConfig, apiTestLLM,
  apiLicenseStatus, apiLicenseActivate,
  apiMirrorWindow, apiMirrorOpen, apiMirrorClose,
  apiLoginStatus, apiLoginBegin,
  apiGetAppConfig, apiSetAppConfig,
  apiListAccounts, apiAddAccount, apiDeleteAccount, apiRenameAccount,
  apiRefreshIdentity,
} from '../api'
import type { AccountItem } from '../types'

// ---- 左侧分类导航 ----
const cats = [
  { id: 'account', label: '账号与登录' },
  { id: 'collect', label: '采集设置' },
  { id: 'ai', label: 'AI 能力' },
  { id: 'window', label: '窗口与调试' },
  { id: 'about', label: '关于与授权' },
]
const activeCat = ref('account')

// 全局请求间隔：单值基准 + 自动窄随机抖动（Scrapy download_delay 同思路）。
// UI 如实显示随机区间，避免用户把随机误读为"多处不同值"。
const intervalBase = computed(() => (appState.params.interval / 1000).toFixed(1))
const intervalMax = computed(() => ((appState.params.interval + 800) / 1000).toFixed(1))

// ---- 账号与登录 ----
const loggedIn = ref(false)
const loginText = ref('检测中…')

async function loadAcct() {
  try {
    const lg = await apiLoginStatus()
    loggedIn.value = !!lg.logged_in
    appState.login = { logged_in: !!lg.logged_in, nickname: lg.nickname || '', kicked: !!lg.kicked }
    loginText.value = lg.kicked ? '登录已失效（被踢）' : (lg.logged_in ? '已登录' : '未登录')
  } catch {
    loginText.value = '检测失败'
  }
}

async function relogin() {
  try {
    const r = await apiLoginBegin(false) // 主动重登：登录页带叉可关闭（登录态不变）
    if (r.already_logged_in) {
      toast('当前已登录，无需重新登录')
    } else {
      toast('正在打开小红书登录页（右上角可关闭）…')
      await loadAcct()
    }
  } catch (e) {
    toast('重新登录失败：' + (e as Error).message, 'error')
  }
}

// ---- 多账号管理 ----
const accounts = ref<AccountItem[]>([])
const acctBusy = ref(false)
const newAcctId = ref('')
const newAcctName = ref('')
// 行内改名（R-6.69）：改的是展示名，不动目录、不动登录态
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

// ---- 调试视图 ----
const dbgOpen = ref(false)
const dbgNote = ref('')
const dbgBusy = ref(false)
const dbgFailed = ref('')

async function toggleDebug() {
  if (dbgBusy.value) return
  dbgBusy.value = true
  dbgFailed.value = ''
  const target = !dbgOpen.value
  try {
    const r = await apiMirrorWindow(target)
    if (!r.ok) {
      dbgFailed.value = '切换失败：' + (r.error || '引擎未就绪')
      return
    }
    dbgOpen.value = target
    dbgNote.value = r.note || ''
  } catch (e) {
    dbgFailed.value = '切换失败：' + (e as Error).message
  } finally {
    dbgBusy.value = false
  }
}

// ---- 壳窗口双 Tab ----
const collectTabVisible = ref(false)
const collectTabBusy = ref(false)

async function loadAppCfg() {
  try {
    const r = await apiGetAppConfig()
    if (r.ok) collectTabVisible.value = !!r.collect_tab_visible
  } catch {
    /* 后端旧版本无此接口：保持默认值 */
  }
}

async function toggleCollectTab() {
  if (collectTabBusy.value) return
  collectTabBusy.value = true
  try {
    const target = !collectTabVisible.value
    const r = await apiSetAppConfig({ collect_tab_visible: target })
    if (r.ok) {
      collectTabVisible.value = target
      toast(target ? '已开启：采集画面前置显示于右侧采集窗（空闲/新任务即时生效）' : '已关闭：采集后台完成，空闲采集页已收起（即时生效）')
    } else {
      toast('保存失败：' + (r.error || '未知错误'), 'error')
    }
  } catch (e) {
    toast('保存失败：' + (e as Error).message, 'error')
  } finally {
    collectTabBusy.value = false
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
    toast('请填写 Base URL 与模型名称', 'error')
    return
  }
  aiSaving.value = true
  try {
    await apiSetLLMConfig({ ...aiForm })
    await loadAi()
    fillAiForm()
    toast('AI 配置已保存')
  } catch (e) {
    toast(`保存失败：${(e as Error).message}`, 'error')
  } finally {
    aiSaving.value = false
  }
}
async function clearAi() {
  aiSaving.value = true
  try {
    await apiClearLLMConfig()
    await loadAi()
    fillAiForm()
    toast('AI 配置已清除（可随时重新配置）')
  } catch (e) {
    toast(`清除失败：${(e as Error).message}`, 'error')
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
      toast(`测试失败：${r.error || '未知原因'}`, 'error')
    }
  } catch (e) {
    toast(`测试失败：${(e as Error).message}`, 'error')
  }
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
    else toast('保存失败', 'error')
  } catch {
    toast('保存失败：网络错误', 'error')
  } finally {
    personaSaving.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadAi(), loadPersona(), loadLicense(), loadAcct(), loadAppCfg(), loadAccounts()])
  fillAiForm()
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
          :class="{ cur: activeCat === c.id }"
          @click="activeCat = c.id"
        >{{ c.label }}</button>
      </div>

      <!-- 右侧内容区 -->
      <div class="settings-body">
        <!-- 账号与登录 -->
        <section v-if="activeCat === 'account'">
          <div class="set-row">
            <div class="row-main">
              <span class="row-label">登录状态</span>
              <span class="acct-status" :class="{ on: loggedIn }">
                {{ loginText }}{{ appState.login.nickname ? ' · ' + appState.login.nickname : '' }}
              </span>
              <button class="btn primary sm" @click="relogin">重新登录</button>
            </div>
            <p class="set-hint">
              「重新登录」是唯一主动入口：进入小红书官方登录页（右上角「✕」可关闭，登录态不变）。
              未登录 / 被踢 / 采集异常时，APP 会自动弹出强制登录对话框（不可关闭）。
            </p>
          </div>

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

        <!-- AI 能力 -->
        <section v-if="activeCat === 'ai'">
          <div class="set-row">
            <div class="row-main">
              <span class="row-label">API Key</span>
              <span class="limit">{{ aiCfg?.llm_api_key_masked ? '已配置 ' + aiCfg.llm_api_key_masked + '（留空保留）' : '必填' }}</span>
            </div>
            <input v-model="aiForm.api_key" type="password" placeholder="sk-..." class="full-input" />
          </div>
          <div class="set-row">
            <div class="row-main">
              <span class="row-label">Base URL（OpenAI 兼容端点）</span>
            </div>
            <input v-model="aiForm.openai_base_url" placeholder="https://api.openai.com/v1" class="full-input" />
          </div>
          <div class="set-row">
            <div class="row-main">
              <span class="row-label">模型名称</span>
            </div>
            <input v-model="aiForm.default_model" placeholder="gpt-4o-mini / deepseek-chat / claude-…" class="full-input" />
            <div class="set-actions">
              <button class="btn" @click="testAi">测试连接</button>
              <button class="btn primary" :disabled="aiSaving" @click="saveAi">{{ aiSaving ? '保存中…' : '保存配置' }}</button>
              <button v-if="aiCfg?.configured" class="btn danger" :disabled="aiSaving" @click="clearAi">清除配置</button>
            </div>
            <div class="gate-msg" :class="{ ok: aiCfg?.configured }">
              {{ aiCfg?.configured ? '✅ 已配置 AI 模型 —— 文稿生成 / 发布前质检可用' : '⚠ 当前未配置 AI 模型 —— AI 功能处于灰态' }}
            </div>
          </div>
          <div class="set-row">
            <div class="row-main">
              <span class="row-label">行业 / 主营品类</span>
            </div>
            <input v-model="persona.industry" placeholder="如：珠宝翡翠、护肤、餐饮…" class="full-input" />
          </div>
          <div class="set-row">
            <div class="row-main">
              <span class="row-label">人设口吻</span>
            </div>
            <input v-model="persona.tone" placeholder="如：干货型 / 分享型 / 探店型…" class="full-input" />
          </div>
          <div class="set-row">
            <div class="row-main">
              <span class="row-label">常发主题</span>
            </div>
            <input v-model="persona.topics" placeholder="逗号分隔，如：避坑指南、性价比测评…" class="full-input" />
            <p class="set-hint">AI 生成标题/正文/标签时保持人设一致，避免"宝子们/家人们/谁懂啊"式通用 AI 味。</p>
            <div class="set-actions">
              <button class="btn primary" :disabled="personaSaving || !personaLoaded" @click="savePersona">{{ personaSaving ? '保存中…' : '保存人设' }}</button>
            </div>
          </div>
        </section>

        <!-- 窗口与调试 -->
        <section v-if="activeCat === 'window'">
          <div class="set-row">
            <div class="row-main">
              <span class="row-label">壳窗口 Tab（采集画面显示）</span>
              <label class="switch">
                <input type="checkbox" :checked="collectTabVisible" @change="toggleCollectTab" />
                <span class="track"><span class="thumb" /></span>
              </label>
              <span class="cur-val">{{ collectTabVisible ? '已开启' : '已关闭' }}</span>
            </div>
            <p class="set-hint">
              左侧是应用窗口（固定不可关），右侧是采集窗口——采集与登录都在这里。开 = 采集画面前置可见（调试/演示）；
              关 = 采集后台完成、不抢前台（平时推荐）。开关即时生效。
            </p>
          </div>
          <div class="set-row">
            <div class="row-main">
              <span class="row-label">调试视图（引擎实时窗口）</span>
              <button v-if="!dbgOpen" class="btn primary sm" :disabled="dbgBusy" @click="toggleDebug">打开实时引擎窗口</button>
              <button v-else class="btn danger sm" :disabled="dbgBusy" @click="toggleDebug">关闭实时窗口（回无头）</button>
            </div>
            <p class="set-hint">
              切换引擎为独立小窗（真实浏览器窗口 960×720），采集/登录时实时可见每一步操作（滚动、点击、跳页）——不是截图。
              切换会重启引擎一次（登录态自动保留）。口径：调试手动开 / 平时关。
            </p>
            <div v-if="dbgOpen" class="dbg-open">
              <p class="dbg-note">✔ 实时窗口已打开：桌面可见引擎小窗，采集开始后即可观察全过程。</p>
              <p v-if="dbgNote" class="dbg-note">{{ dbgNote }}</p>
            </div>
            <p v-if="dbgFailed" class="dbg-fail">{{ dbgFailed }}</p>
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
.settings-layout { display: flex; gap: 16px; align-items: flex-start; }
.settings-nav {
  width: 148px; flex-shrink: 0;
  display: flex; flex-direction: column; gap: 4px;
  position: sticky; top: 0;
}
.nav-item {
  text-align: left; padding: 10px 14px; border-radius: 10px;
  border: 1px solid transparent; background: transparent;
  font-size: 13px; color: #555; cursor: pointer; user-select: none;
  transition: all .15s;
}
.nav-item:hover { background: #f5f5f7; color: #222; }
.nav-item.cur {
  background: #fff; color: #ff2442; font-weight: 700;
  border-color: #ffe2e8; box-shadow: 0 1px 4px rgba(0,0,0,.04);
  border-left: 3px solid #ff2442;
}
.settings-body { flex: 1; min-width: 0; }

/* 每项一行 */
.set-row {
  background: var(--bg-card, #fff);
  border: 1px solid var(--line, #ececec);
  border-radius: 12px;
  padding: 14px 16px;
  margin-bottom: 12px;
}
.row-main { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.row-label { font-size: 13px; font-weight: 700; color: #333; min-width: 150px; }
.set-hint { font-size: 12px; color: var(--ink-3, #999); line-height: 1.6; margin: 8px 0 0; }
.cur-val { font-size: 12px; color: #b08a4d; font-weight: 600; }
.unit { font-size: 12px; color: #888; }
.num-input { width: 110px; }
.full-input { width: 100%; box-sizing: border-box; margin-top: 8px; }
textarea {
  width: 100%; box-sizing: border-box; resize: vertical; margin-top: 8px;
  border: 1px solid var(--line, #e5e5e5); border-radius: 8px;
  padding: 8px 10px; font-size: 12.5px; font-family: ui-monospace, Menlo, monospace;
}
input:not([type="checkbox"]) {
  border: 1px solid var(--line, #e5e5e5);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
  background: #fff;
}

/* 开关 */
.switch { display: inline-flex; align-items: center; cursor: pointer; }
.switch input { display: none; }
.switch .track {
  width: 38px; height: 22px; border-radius: 999px;
  background: #d9d9de; position: relative; transition: background .18s;
  display: inline-block;
}
.switch .thumb {
  position: absolute; top: 3px; left: 3px; width: 16px; height: 16px;
  border-radius: 50%; background: #fff; transition: left .18s;
  box-shadow: 0 1px 3px rgba(0,0,0,.2);
}
.switch input:checked + .track { background: #ff2442; }
.switch input:checked + .track .thumb { left: 19px; }

/* 状态元素 */
.acct-status {
  font-size: 13px; font-weight: 600; color: #c0392b;
  padding: 4px 12px; border-radius: 999px; background: #fdf0ef;
}
.acct-status.on { color: #1a7f4c; background: #eaf7ef; }

/* 多账号列表：主标题=业务名（大、深），副标题=昵称/内部id/用途（小、灰） */
.acct-list { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.acct-item {
  display: flex; flex-direction: column; gap: 8px;
  border: 1px solid var(--line, #ececec); border-radius: 10px;
  padding: 10px 12px; background: #fafafa;
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
.limit { color: #b08a4d; font-weight: 500; font-size: 12px; }
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
.dbg-note { font-size: 12px; color: #1a7f4c; margin: 8px 0 0; }
.dbg-fail { font-size: 12px; color: #c0392b; margin: 8px 0 0; }

@media (max-width: 720px) {
  .settings-layout { flex-direction: column; }
  .settings-nav { width: 100%; flex-direction: row; overflow-x: auto; position: static; }
  .nav-item { white-space: nowrap; }
}
</style>
