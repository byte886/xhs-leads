<script setup lang="ts">
// SettingsPanel v2.0：设置 Tab（左侧分类导航 + 右侧逐项一行配置）
// R-6.46 功能域分组 → R-6.47 两栏布局：账号 / 采集 / AI 能力 / 窗口与调试 / 关于与授权
import { onMounted, onBeforeUnmount, reactive, ref } from 'vue'
import { appState, refreshSysStatus, toast } from '../store'
import {
  apiGetLLMConfig, apiSetLLMConfig, apiClearLLMConfig, apiTestLLM,
  apiLicenseStatus, apiLicenseActivate,
  apiMirrorWindow,
  apiLoginStatus, apiLoginBegin, apiCookieInfo, apiImportCookieText,
  apiGetAppConfig, apiSetAppConfig,
} from '../api'
import type { CookieItem } from '../api'

// ---- 左侧分类导航 ----
const cats = [
  { id: 'account', label: '账号与登录' },
  { id: 'collect', label: '采集设置' },
  { id: 'ai', label: 'AI 能力' },
  { id: 'window', label: '窗口与调试' },
  { id: 'about', label: '关于与授权' },
]
const activeCat = ref('account')

// ---- 账号与登录 ----
const loggedIn = ref(false)
const loginText = ref('检测中…')
const cookieItems = ref<CookieItem[]>([])
const maskOn = ref(true) // 默认隐藏完整值（脱敏），开关可显示原文
const importText = ref('')
const importResult = ref('')

async function loadAcct() {
  try {
    const lg = await apiLoginStatus()
    loggedIn.value = !!lg.logged_in
    appState.login = { logged_in: !!lg.logged_in, nickname: lg.nickname || '', kicked: !!lg.kicked }
    loginText.value = lg.kicked ? '登录已失效（被踢）' : (lg.logged_in ? '已登录' : '未登录')
  } catch {
    loginText.value = '检测失败'
  }
  try {
    const r = await apiCookieInfo()
    cookieItems.value = r.cookies || []
  } catch {
    cookieItems.value = []
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

function maskValue(v: string) {
  if (!v) return '—'
  if (v.length <= 6) return '******'
  return v.slice(0, 3) + '****' + v.slice(-3)
}

function fmtExp(exp?: number | null) {
  if (!exp) return '会话级'
  const d = new Date(exp * 1000)
  if (Number.isNaN(d.getTime())) return String(exp)
  return d.toLocaleDateString('zh-CN') + (exp * 1000 < Date.now() ? '（已过期）' : '')
}

async function doImport() {
  if (!importText.value.trim()) return
  importResult.value = '导入中…'
  try {
    const r = await apiImportCookieText(importText.value)
    importResult.value = r.ok ? '已导入并校验' : (r.error || '导入失败')
    if (r.ok) {
      importText.value = ''
      await loadAcct()
      appState.cookieRefreshTick += 1
    }
  } catch (e) {
    importResult.value = '导入失败：' + (e as Error).message
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
      toast(target ? '已开启：采集画面前置显示于 Tab2（空闲/新任务即时生效）' : '已关闭：采集后台完成，空闲采集页已收起（即时生效）')
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
  await Promise.all([loadAi(), loadPersona(), loadLicense(), loadAcct(), loadAppCfg()])
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
          <div class="set-row">
            <details class="cookie-fold">
              <summary>
                Cookie（只读 · {{ cookieItems.length }} 条）
                <span class="mask-toggle" @click.prevent="maskOn = !maskOn">{{ maskOn ? '显示值' : '隐藏值' }}</span>
              </summary>
              <table class="cookie-table">
                <thead>
                  <tr><th>名称</th><th>值</th><th>域</th><th>有效期</th></tr>
                </thead>
                <tbody>
                  <tr v-for="c in cookieItems" :key="c.name + c.domain">
                    <td class="mono">{{ c.name }}</td>
                    <td class="mono">{{ maskOn ? maskValue(c.value) : c.value }}</td>
                    <td class="mono">{{ c.domain }}</td>
                    <td>{{ fmtExp(c.expires) }}</td>
                  </tr>
                </tbody>
              </table>
              <p class="set-hint">会话仅存本机 data/cookies.json；值默认脱敏（防截图外发泄露），可点「显示值」查看完整内容。</p>
            </details>
          </div>
          <div class="set-row">
            <div class="row-main">
              <span class="row-label">导入 Cookie（附加调试）</span>
            </div>
            <textarea v-model="importText" rows="2" placeholder="name=value; name2=value2 或每行一条（域固定 .xiaohongshu.com）"></textarea>
            <div class="set-actions">
              <button class="btn" :disabled="!importText.trim()" @click="doImport">导入并校验</button>
              <span v-if="importResult" class="import-result">{{ importResult }}</span>
            </div>
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
              <span class="cur-val">{{ (appState.params.interval / 1000).toFixed(1) }} 秒</span>
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
              APP 壳窗口固定两个 Tab：Tab1 应用/登录（固定不可关），Tab2 采集。开 = 采集画面前置可见（调试/演示）；
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
.cookie-fold { border-top: 1px dashed #ececec; padding-top: 8px; margin-top: 4px; }
.cookie-fold summary { font-size: 12.5px; font-weight: 600; color: #555; cursor: pointer; user-select: none; }
.mask-toggle {
  margin-left: 10px; font-weight: 600; color: #1565c0; cursor: pointer; font-size: 12px;
}
.cookie-table {
  width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12px;
}
.cookie-table th {
  text-align: left; color: #888; font-weight: 600; padding: 5px 8px;
  border-bottom: 1px solid #ececec;
}
.cookie-table td {
  padding: 6px 8px; border-bottom: 1px solid #f4f4f4; color: #333; vertical-align: top;
  word-break: break-all;
}
.cookie-table .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11.5px; }
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
