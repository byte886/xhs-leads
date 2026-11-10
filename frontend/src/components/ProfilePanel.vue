<script setup lang="ts">
// ProfilePanel：账号档案独立 Tab（内容资产，不放在「设置」里）
// 档案是图文生成的事实底座：越具体，标题/正文/配图越像这家店，而不是通用模板。
import { computed, onMounted, reactive, ref } from 'vue'
import { toast } from '../store'
import {
  apiListProfiles, apiSaveProfile, apiDeleteProfile,
  apiAiOptions, apiSaveAiOptions, apiResetAiOptions,
} from '../api'
import type { AiProfile, AiOptions } from '../api'
import TemplatePanel from './TemplatePanel.vue'

// 第 4 Tab 内的二级子页：账号档案 / 内容模板（顶级 Tab 不增加）
const sub = ref<'profiles' | 'templates'>('profiles')

const profileFields = (): AiProfile => ({
  id: '', name: '', nickname: '', brand: '', industry: '',
  audience: '', selling_points: '', products: '', address: '',
  tone: '', avoid_words: '',
})
const profiles = ref<AiProfile[]>([])
const profileSearch = ref('')
const profileOpen = ref(false)
const profileSaving = ref(false)
const loading = ref(true)
const profileForm = reactive<AiProfile>(profileFields())
const shownProfiles = computed(() => {
  const q = profileSearch.value.trim()
  if (!q) return profiles.value
  return profiles.value.filter(p => [p.name, p.brand, p.industry].some(v => (v || '').includes(q)))
})

async function loadProfiles() {
  loading.value = true
  try {
    profiles.value = (await apiListProfiles()).items
  } catch {
    profiles.value = []
  } finally {
    loading.value = false
  }
}
function newProfile() {
  Object.assign(profileForm, profileFields())
  profileOpen.value = true
}
// 旧档案可能存着不在候选里的值（比如「珠宝 · 翡翠手镯」）。
// 不能静默清空，也不能装作它在下拉里：保留原值，并标一句自定义。
function isCustom(v: string, list: string[]) {
  const t = (v || '').trim()
  return !!t && !list.includes(t)
}
function editProfile(p: AiProfile) {
  Object.assign(profileForm, profileFields(), p)
  profileOpen.value = true
}
async function saveProfile() {
  if (!profileForm.name.trim() && !profileForm.nickname.trim()) {
    toast('档案名称必填', 'error')
    return
  }
  profileSaving.value = true
  try {
    const r = await apiSaveProfile({ ...profileForm })
    Object.assign(profileForm, r.item)
    await loadProfiles()
    toast('档案已保存')
  } catch (e) {
    toast(`保存失败：${(e as Error).message}`, 'error')
  } finally {
    profileSaving.value = false
  }
}
async function removeProfile(id: string) {
  try {
    await apiDeleteProfile(id)
    if (profileForm.id === id) profileOpen.value = false
    await loadProfiles()
    toast('档案已删除')
  } catch (e) {
    toast(`删除失败：${(e as Error).message}`, 'error')
  }
}

// ---- 行业 / 口吻 选项库 ----
// 为什么是「选项」而不是纯自由文本：这两个字段会拼进提示词，
// 自由填写时同一家店容易写成「翡翠手镯 / 翡翠 / 翡翠手饰」三种，生成风格会漂。
const opt = reactive<AiOptions>({ industries: [], tones: [] })
const optDefaults = ref<AiOptions>({ industries: [], tones: [] })
const optOpen = ref(false)
const optSaving = ref(false)
const optDraft = reactive<AiOptions>({ industries: [], tones: [] })
const optNew = reactive<Record<OptKey, string>>({ industries: '', tones: '' })

async function loadOptions() {
  try {
    const r = await apiAiOptions()
    opt.industries = r.industries
    opt.tones = r.tones
    optDefaults.value = r.defaults
  } catch {
    /* 选项库拉不到不影响档案本身：下拉为空，仍可手填 */
  }
}

function openOptions() {
  optDraft.industries = [...opt.industries]
  optDraft.tones = [...opt.tones]
  optNew.industries = ''
  optNew.tones = ''
  optOpen.value = true
}

type OptKey = 'industries' | 'tones'

const OPT_COLS: { k: OptKey; t: string }[] = [
  { k: 'industries', t: '行业' },
  { k: 'tones', t: '人设口吻' },
]

function addOption(key: OptKey) {
  const v = (optNew[key] || '').trim()
  if (!v) return
  if (optDraft[key].includes(v)) {
    toast('已经有这一项了', 'error')
    return
  }
  optDraft[key].push(v)
  optNew[key] = ''
}

function delOption(key: OptKey, i: number) {
  optDraft[key].splice(i, 1)
}

async function saveOptions() {
  optSaving.value = true
  try {
    const r = await apiSaveAiOptions({
      industries: optDraft.industries.map(s => s.trim()).filter(Boolean),
      tones: optDraft.tones.map(s => s.trim()).filter(Boolean),
    })
    opt.industries = r.industries
    opt.tones = r.tones
    optOpen.value = false
    toast('选项已保存')
  } catch (e) {
    toast(`保存失败：${(e as Error).message}`, 'error')
  } finally {
    optSaving.value = false
  }
}

async function resetOptions() {
  try {
    const r = await apiResetAiOptions()
    optDraft.industries = [...r.industries]
    optDraft.tones = [...r.tones]
    toast('已恢复默认选项')
  } catch (e) {
    toast(`恢复失败：${(e as Error).message}`, 'error')
  }
}

onMounted(() => {
  loadProfiles()
  loadOptions()
})
</script>

<template>
  <div class="pf-page">
    <div class="pf-subnav" role="tablist" aria-label="内容资产子页">
      <button role="tab" :class="{ on: sub === 'profiles' }" @click="sub = 'profiles'">账号档案</button>
      <button role="tab" :class="{ on: sub === 'templates' }" @click="sub = 'templates'">内容模板</button>
    </div>

    <div v-show="sub === 'profiles'" class="pf-sub">
    <header class="pf-head">
      <div>
        <h1>账号档案</h1>
        <p>生成的事实底座。越具体，标题、正文、配图越像你的店，而不是通用模板。</p>
      </div>
      <div class="pf-actions">
        <button class="btn sm" @click="openOptions">管理选项</button>
        <button class="btn primary sm" @click="newProfile">＋ 新建档案</button>
      </div>
    </header>

    <div class="pf-toolbar">
      <input v-model="profileSearch" class="pf-search" placeholder="搜索档案、品牌、行业…" />
      <span class="pf-count">{{ profiles.length }} 个档案</span>
    </div>

    <div v-if="loading" class="pf-skel">
      <div v-for="i in 2" :key="i" class="sk" />
    </div>

    <div v-else-if="!profiles.length" class="pf-empty">
      <b>还没有档案</b>
      <p>新建后即可在「一键生成」里选用。留空的字段不参与生成，不会被编造。</p>
    </div>

    <div v-else-if="!shownProfiles.length" class="pf-empty">
      <b>没有匹配的档案</b>
      <p>换个关键词，或清空搜索框。</p>
    </div>

    <div v-else class="pf-grid">
      <div
        v-for="p in shownProfiles"
        :key="p.id"
        class="pf-card"
        :class="{ active: profileOpen && profileForm.id === p.id }"
        @click="editProfile(p)"
      >
        <div class="c-top">
          <b>{{ p.name }}</b>
          <span class="c-tag">{{ p.industry || '未填行业' }}</span>
        </div>
        <div class="c-sub">{{ p.nickname || '—' }} · {{ p.brand || '未填品牌' }}</div>
        <div class="c-sell">{{ p.selling_points || '未填核心卖点' }}</div>
        <div class="c-foot">
          <span>{{ p.updated_at || '' }}</span>
          <a href="#" @click.prevent.stop="removeProfile(p.id)">删除</a>
        </div>
      </div>
    </div>

    <div v-if="profileOpen" class="pf-editor">
      <div class="e-title">
        <b>{{ profileForm.id ? '编辑档案' : '新建档案' }}</b>
        <div>
          <button class="btn primary sm" :disabled="profileSaving" @click="saveProfile">
            {{ profileSaving ? '保存中…' : '保存档案' }}
          </button>
          <button class="btn sm" @click="profileOpen = false">收起</button>
        </div>
      </div>

      <div class="e-group">基础身份</div>
      <div class="e-grid">
        <label class="field"><span>档案名称 *</span><input v-model="profileForm.name" placeholder="如：翡翠手镯主号" /></label>
        <label class="field"><span>昵称 / 称呼</span><input v-model="profileForm.nickname" placeholder="如：老周 / 周掌柜" /></label>
        <label class="field"><span>品牌 / 门店名</span><input v-model="profileForm.brand" placeholder="如：人民路玉器行" /></label>
        <label class="field">
          <span>行业</span>
          <input v-model="profileForm.industry" list="opt-industries" placeholder="从列表选，或直接填" />
          <em v-if="isCustom(profileForm.industry, opt.industries)" class="f-note">自定义值，不在候选列表里（保留不影响生成）</em>
        </label>
      </div>

      <div class="e-group">经营卖点</div>
      <div class="e-grid">
        <label class="field"><span>目标受众</span><input v-model="profileForm.audience" placeholder="如：本地送礼人群 / 新手玩家" /></label>
        <label class="field"><span>核心卖点</span><input v-model="profileForm.selling_points" placeholder="如：一手货源、可复检、免费改圈" /></label>
      </div>
      <div class="e-grid one">
        <label class="field"><span>产品与价格</span><textarea v-model="profileForm.products" rows="2" placeholder="主营产品、价位区间、套餐…" /></label>
        <label class="field"><span>门店地址 / 到店方式</span><textarea v-model="profileForm.address" rows="2" placeholder="如：樊城区人民路 xx 号，可到店看货" /></label>
      </div>

      <div class="e-group">表达风格</div>
      <div class="e-grid">
        <label class="field">
          <span>人设口吻</span>
          <input v-model="profileForm.tone" list="opt-tones" placeholder="从列表选，或直接填" />
          <em v-if="isCustom(profileForm.tone, opt.tones)" class="f-note">自定义值，不在候选列表里（保留不影响生成）</em>
        </label>
        <label class="field"><span>避雷词（绝对不写）</span><input v-model="profileForm.avoid_words" placeholder="如：保值升值、开光、招财" /></label>
      </div>
    </div>

    <!-- 行业 / 口吻的下拉候选（input 用 list 引用） -->
    <datalist id="opt-industries">
      <option v-for="v in opt.industries" :key="'i' + v" :value="v" />
    </datalist>
    <datalist id="opt-tones">
      <option v-for="v in opt.tones" :key="'t' + v" :value="v" />
    </datalist>
    </div>

    <!-- 内容模板子页：文字模板 + 图片模板 -->
    <div v-show="sub === 'templates'" class="pf-sub">
      <TemplatePanel />
    </div>

    <!-- 选项维护弹层 -->
    <div v-if="optOpen" class="pf-mask" @click.self="optOpen = false">
      <div class="pf-modal">
        <div class="m-head">
          <b>行业与口吻选项</b>
          <button class="m-x" @click="optOpen = false">关闭</button>
        </div>
        <p class="m-tip">
          这两个框的候选从这里来，也能直接手填。改的是公共列表：保存后所有档案的下拉都会更新，已经填在档案里的旧文字不会被改掉。
        </p>

        <div class="m-cols">
          <div v-for="col in OPT_COLS" :key="col.k" class="m-col">
            <div class="m-col-h">
              <b>{{ col.t }}</b>
              <span>{{ optDraft[col.k].length }} 项</span>
            </div>
            <div class="m-list">
              <div v-for="(v, i) in optDraft[col.k]" :key="col.k + i" class="m-row">
                <input v-model="optDraft[col.k][i]" class="m-in" />
                <button class="m-del" :aria-label="'删除' + v" title="删除这一项" @click="delOption(col.k, i)">×</button>
              </div>
              <p v-if="!optDraft[col.k].length" class="m-none">空列表，保存后会回落成默认选项。</p>
            </div>
            <div class="m-add">
              <input
                v-model="optNew[col.k]"
                :placeholder="'新增一个' + col.t"
                @keyup.enter="addOption(col.k)"
              />
              <button class="btn sm" @click="addOption(col.k)">添加</button>
            </div>
          </div>
        </div>

        <div class="m-foot">
          <button class="btn sm" @click="resetOptions">恢复默认</button>
          <div>
            <button class="btn sm" @click="optOpen = false">取消</button>
            <button class="btn primary sm" :disabled="optSaving" @click="saveOptions">
              {{ optSaving ? '保存中…' : '保存选项' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pf-page { padding: 22px 24px 28px; }

/* ---- 二级子页切换：segmented control，选中白底轻阴影（不用红，与顶级 Tab 区分层级） ---- */
.pf-subnav {
  display: inline-flex; gap: 2px;
  background: var(--panel-2); border: 1px solid var(--line);
  border-radius: var(--r-ctl); padding: 3px; margin-bottom: 18px;
}
.pf-subnav button {
  border: 0; background: transparent; cursor: pointer;
  font-size: 13px; font-weight: 600; color: var(--ink-3);
  padding: 6px 18px; border-radius: 4px;
  transition: background .15s, color .15s, box-shadow .15s;
}
.pf-subnav button:hover { color: var(--ink); }
.pf-subnav button:focus-visible { outline: 2px solid var(--ring); outline-offset: 1px; }
.pf-subnav button.on { background: var(--panel); color: var(--ink); box-shadow: 0 1px 3px rgba(43,30,24,.12); }
.pf-sub { min-width: 0; }
.tpl-placeholder {
  border: 1px dashed var(--line); border-radius: var(--r-card);
  padding: 52px 24px; text-align: center;
}
.tpl-placeholder b { font-size: 16px; color: var(--ink-2); display: block; margin-bottom: 10px; }
.tpl-placeholder p { font-size: 13px; line-height: 1.8; color: var(--ink-3); margin: 0; }

/* ---- 页头：标题与说明竖排，右侧只放一个动作 ---- */
.pf-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.pf-head h1 { font-size: 22px; font-weight: 700; color: var(--ink); letter-spacing: -.01em; }
.pf-head p { margin-top: 6px; font-size: 13px; color: var(--ink-3); line-height: 1.6; max-width: 62ch; }
.pf-actions { display: flex; gap: 8px; flex-shrink: 0; }

.pf-toolbar { display: flex; align-items: center; gap: 12px; margin-top: 18px; }
.pf-search {
  flex: 0 1 360px; min-width: 0; font-size: 13px; padding: 8px 11px;
  background: var(--panel-2); border: 1px solid transparent; border-radius: var(--r-ctl);
}
.pf-search:focus { background: var(--panel); border-color: var(--red); outline: 2px solid var(--ring); }
.pf-count { flex-shrink: 0; font-size: 12px; color: var(--ink-3); font-variant-numeric: tabular-nums; }

/* ---- 列表：两列，卡片仅在下沿留一线缝隙 ---- */
.pf-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 12px; }
.pf-card {
  border: 1px solid var(--line); border-radius: var(--r-card);
  padding: 14px 15px; background: var(--panel); cursor: pointer;
  transition: border-color .15s, box-shadow .15s;
}
.pf-card:hover { border-color: rgba(226, 42, 67, .32); }
/* 选中态不用品牌红：红框在表单语境里读作“校验错误”，
   且与同一张卡上的「删除」撞成同一个红。改用深描边 + 底色 */
.pf-card.active {
  border-color: rgba(43, 30, 24, .34);
  background: var(--panel-2);
  box-shadow: 0 0 0 1px rgba(43, 30, 24, .28) inset;
}
.c-top { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
.c-top b {
  font-size: 14px; font-weight: 700; color: var(--ink);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
/* 胶囊只留给分类标签，全站唯一的全圆角色；用中性底，
   橙色留给真正的警告（行业是身份，不是警示） */
.c-tag {
  flex-shrink: 0; font-size: 11.5px; color: var(--ink-2);
  background: var(--panel-2); padding: 2px 9px; border-radius: var(--r-pill);
  max-width: 44%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.c-sub { font-size: 12px; color: var(--ink-3); margin-top: 6px; }
.c-sell {
  font-size: 13px; color: var(--ink-2); margin-top: 8px; line-height: 1.55;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.c-foot {
  display: flex; align-items: baseline; justify-content: space-between;
  margin-top: 10px; padding-top: 9px; border-top: 1px solid var(--line);
  font-size: 11.5px; color: var(--ink-3); font-variant-numeric: tabular-nums;
}
/* 删除走「墨 → 砖红」，不再用品牌红：选中是 accent，危险是 danger */
.c-foot a { color: var(--ink-3); text-decoration: none; transition: color .15s; }
.c-foot a:hover { color: var(--danger); }

/* ---- 空态 / 加载态 ---- */
.pf-empty { margin-top: 12px; padding: 40px 20px; text-align: center; border: 1px solid var(--line); border-radius: var(--r-card); }
.pf-empty b { display: block; font-size: 14px; color: var(--ink); }
.pf-empty p { margin-top: 6px; font-size: 12.5px; color: var(--ink-3); }
.pf-skel { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 12px; }
.sk {
  height: 108px; border-radius: var(--r-card);
  background: linear-gradient(90deg, var(--panel-2), #fbf8f5, var(--panel-2));
  background-size: 200% 100%; animation: sk 1.3s linear infinite;
}
@keyframes sk { to { background-position: -200% 0; } }
@media (prefers-reduced-motion: reduce) { .sk { animation: none; } }

/* ---- 编辑器 ---- */
.pf-editor {
  margin-top: 16px; padding: 18px 20px 20px;
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--r-card);
}
.e-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.e-title > b { font-size: 15px; font-weight: 700; color: var(--ink); }
.e-title > div { display: flex; gap: 8px; }
/* 分组标题：靠上留白与字色分段，不用左侧彩条 */
/* 分组标题：靠字重与上留白分段，不用彩色 */
.e-group {
  margin: 20px 0 10px; font-size: 12px; font-weight: 700;
  color: var(--ink-2); letter-spacing: .04em;
}
.e-group:first-of-type { margin-top: 14px; }
.e-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.e-grid.one { grid-template-columns: 1fr; margin-top: 12px; }
.field { display: flex; flex-direction: column; gap: 6px; }
.field > span { font-size: 12px; color: var(--ink-3); }
.field input, .field textarea { margin-top: 0; width: 100%; box-sizing: border-box; font-size: 13px; resize: none; }
/* 自定义值提示：只是说明，不是警告，所以用墨色不用琥珀 */
.f-note { font-style: normal; font-size: 11.5px; color: var(--ink-3); line-height: 1.5; }

@media (max-width: 720px) {
  .pf-grid, .pf-skel, .e-grid, .m-cols { grid-template-columns: 1fr; }
  .m-col + .m-col { border-left: 0; border-top: 1px solid var(--line); }
}

/* ---- 选项维护弹层：两列平铺，中间一条分隔线（不是彩色竖条） ---- */
.pf-mask {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(28, 20, 16, .28);
  display: flex; align-items: center; justify-content: center; padding: 24px;
}
.pf-modal {
  width: min(760px, 100%); max-height: 82vh; overflow: hidden;
  display: flex; flex-direction: column;
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--r-card);
  box-shadow: 0 18px 48px rgba(28, 20, 16, .16);
}
.m-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 15px 20px 12px; border-bottom: 1px solid var(--line);
}
.m-head b { font-size: 15px; font-weight: 700; color: var(--ink); }
.m-x { border: 0; background: transparent; cursor: pointer; font-size: 12.5px; color: var(--ink-3); }
.m-x:hover { color: var(--ink); }
.m-tip { padding: 11px 20px; font-size: 12px; color: var(--ink-3); line-height: 1.65; }
.m-cols { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); min-height: 0; flex: 1; overflow: hidden; }
.m-col { display: flex; flex-direction: column; min-height: 0; padding: 0 14px; }
.m-col + .m-col { border-left: 1px solid var(--line); }
.m-col-h {
  display: flex; align-items: baseline; justify-content: space-between;
  padding: 6px 4px 8px;
}
.m-col-h b { font-size: 12px; font-weight: 700; color: var(--ink-2); letter-spacing: .04em; }
.m-col-h span { font-size: 11.5px; color: var(--ink-3); font-variant-numeric: tabular-nums; }
/* 两列列表等高：左列多就自己滚，右列少也贴底对齐 */
.m-list { flex: 1; overflow-y: auto; min-height: 220px; max-height: 44vh; }
.m-row { display: flex; align-items: center; gap: 4px; padding: 2px 0; }
/* 默认无描边：这一列看起来是「一串名字」，不是一排表单 */
.m-in {
  flex: 1; min-width: 0; margin: 0; font-size: 13px; padding: 5px 7px;
  border: 1px solid transparent; border-radius: var(--r-ctl);
  background: transparent; color: var(--ink);
}
.m-in:hover { background: var(--panel-2); }
.m-in:focus { outline: 2px solid var(--ring); border-color: var(--red); background: var(--panel); }
.m-del {
  flex-shrink: 0; width: 22px; height: 22px; line-height: 1; padding: 0;
  border: 0; background: transparent; cursor: pointer;
  font-size: 15px; color: var(--ink-3); border-radius: var(--r-ctl);
}
.m-del:hover { color: var(--danger); background: var(--danger-soft); }
.m-none { padding: 10px 4px; font-size: 12px; color: var(--ink-3); }
.m-add { display: flex; gap: 8px; padding: 10px 0 14px; border-top: 1px solid var(--line); }
.m-add input {
  flex: 1; min-width: 0; margin: 0; font-size: 13px; padding: 6px 9px;
  border: 1px solid var(--line); border-radius: var(--r-ctl);
}
.m-add input:focus { outline: 2px solid var(--ring); border-color: var(--red); }
.m-foot {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; padding: 12px 20px; border-top: 1px solid var(--line);
}
.m-foot > div { display: flex; gap: 8px; }
</style>
