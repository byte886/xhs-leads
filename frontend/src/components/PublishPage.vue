<script setup lang="ts">
import { computed, ref } from 'vue'
import { appState, coverProxy, setStage, setTab, toast } from '../store'
import { apiClone, apiPublish, apiSaveDraft, apiUploadImage, apiDrafts, apiDraftDelete, apiGetLLMConfig, apiAiGenerate, apiLogs, apiPublishStats } from '../api'
import type { DraftItem } from '../api'
import type { LogItem, Note } from '../types'

const emit = defineEmits<{ (e: 'back'): void }>()

const title = ref('')
const body = ref('')
const tags = ref('')
const visible = ref('公开可见')
const account = ref('')
const images = ref<string[]>([])
const feedId = ref('')
const videoFile = ref('')  // 视频克隆：原视频本地路径（/videos/...）；空=图文
const videoUrl = ref('')   // 原视频远端地址（仅展示用）
const publishing = ref(false)
const loading = ref(false)

// ---- 图片去重（URL 主路径去重）：克隆载荷/草稿/追加均经此过滤 ----
// 重复来源：克隆提取时封面与正文同图、CDN 同图不同签名 query / 处理参数变体（!nd_dft 等）。
// key 取 CDN 主路径（去 query、去末尾 ! 处理参数），相同即视为同一张图，保留首次出现。
function imgKey(u: string): string {
  const s = (u || '').trim()
  if (!s) return ''
  return s.split('?')[0].replace(/![^/]*$/, '')
}

function dedupe(list: string[]): string[] {
  const seen = new Set<string>()
  return (list || []).filter(u => {
    const k = imgKey(u)
    if (!k || seen.has(k)) return false
    seen.add(k)
    return true
  })
}


// 草稿库（简约版：列表 / 恢复续编 / 删除）
const drafts = ref<DraftItem[]>([])
const draftPanelOpen = ref(false)
const draftLoading = ref(false)

async function loadDrafts() {
  draftLoading.value = true
  try {
    const r = await apiDrafts()
    drafts.value = r.ok ? r.drafts : []
  } catch {
    drafts.value = []
  } finally {
    draftLoading.value = false
  }
}

function openDraftPanel() {
  draftPanelOpen.value = true
  loadDrafts()
}

function restoreDraft(d: DraftItem) {
  title.value = d.title || ''
  body.value = d.desc || ''
  tags.value = d.tags.join(' ')
  visible.value = d.visibility || '公开可见'
  images.value = dedupe(d.images)
  feedId.value = d.feed_id || ''
  draftPanelOpen.value = false
  toast('已恢复草稿，可继续编辑后发布或重新保存')
}

async function removeDraft(id: number) {
  const r = await apiDraftDelete(id).catch(() => ({ ok: false }))
  toast(r.ok ? '草稿已删除' : '删除失败')
  loadDrafts()
}

const tCount = computed(() => title.value.length)
const bCount = computed(() => body.value.length)

type CloneTarget = Note | { feed_id: string; title: string; desc: string; images: string[]; originUrl?: string }

async function openFor(note: CloneTarget) {
  loading.value = true
  feedId.value = note.feed_id || ''
  videoFile.value = ''
  videoUrl.value = ''
  aiKeyword.value = ('keyword' in note ? (note as Note).keyword : '') || ''  // 记录来源关键词，AI 生成按品类对齐
  try {
    if ('images' in note && note.images) {
      // 已有克隆载荷：直接填充（去重）
      title.value = note.title || ''
      body.value = note.desc || ''
      images.value = dedupe(note.images)
    } else {
      // 秒开：先用列表已有数据（标题/正文/封面占位），后台拉克隆载荷补图，不阻塞编辑
      title.value = note.title || ''
      body.value = (note as Note).desc || ''
      const localCover = (note as Note).local_cover || (note as Note).local_images?.[0] || ''
      images.value = dedupe(localCover ? [coverProxy(localCover)] : [])
      loading.value = false
      apiClone(note.feed_id)
        .then(p => {
          const locals = (p.images_local || []).filter(Boolean)
          if (locals.length) images.value = dedupe(locals)
          else if (p.images?.length) images.value = dedupe(p.images)
          if (p.title) title.value = p.title
          if (p.desc) body.value = p.desc
          if (p.video_file) videoFile.value = p.video_file
          if (p.video_url) videoUrl.value = p.video_url
        })
        .catch(() => {
          /* 后台补图失败则保留占位封面，不打断编辑 */
        })
      return
    }
    tags.value = ''
    visible.value = '公开可见'
    account.value = ''
  } catch (e) {
    toast(`克隆失败：${(e as Error).message}`)
  } finally {
    loading.value = false
  }
}

defineExpose({ openFor })

function removeImg(i: number) {
  images.value.splice(i, 1)
}

// 拖拽排序（原生 HTML5 DnD）：拖放后重排 images 数组，发布/草稿按新顺序生效
const dragIndex = ref(-1)
function onDragStart(i: number) {
  dragIndex.value = i
}
function onDragOver(ev: DragEvent) {
  ev.preventDefault()
}
function onDrop(i: number) {
  if (dragIndex.value >= 0 && dragIndex.value !== i) {
    const [moved] = images.value.splice(dragIndex.value, 1)
    images.value.splice(i, 0, moved)
  }
  dragIndex.value = -1
}
function onDragEnd() {
  dragIndex.value = -1
}

// 图片全图预览（lightbox）
const previewUrl = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const appending = ref(false)

function openPreview(url: string) {
  previewUrl.value = url
}

function triggerAdd() {
  fileInput.value?.click()
}

async function onAddImages(ev: Event) {
  const input = ev.target as HTMLInputElement
  const files = [...(input.files || [])]
  input.value = '' // 允许重复选同一文件
  if (!files.length) return
  appending.value = true
  let added = 0
  for (const f of files) {
    const r = await apiUploadImage(f).catch(() => ({ ok: false, note: '上传失败', path: undefined }))
    if (r.ok && r.path) {
      // 追加时同样走主路径去重：重复图不再追加
      const merged = dedupe([...images.value, r.path])
      if (merged.length === images.value.length) {
        toast('该图片已在列表中，未追加（重复）')
      } else {
        images.value = merged
        added++
      }
    } else {
      toast(`追加失败：${r.note || '未知原因'}`)
    }
  }
  appending.value = false
  if (added) toast(`已追加 ${added} 张图片（共 ${images.value.length} 张）`)
}

function back() {
  setStage('pick')
  setTab('collect')
  emit('back')
}

// ---- F9 发布前质检（v1.5：违规词 / 结构 / 图片二创 / 同质化人工确认） ----
// 本地确定性规则：违规词扫描 + 结构检查；图片二创与同质化做「人工确认」提示（发布永远手动）
const Q_ABS_WORDS = ['最好', '最全', '最强', '第一名', '唯一', '全网第一', '国家级', '百分百', '100%', '绝对有效', '顶级', '极致', '最佳', '无敌', '根治', '治愈', '疗效', '永不反弹', '最低', '最低价', '最便宜', '全网最低', '全网最便宜', '史上最低']
const Q_DRAIN_WORDS = ['微信', 'vx', 'v信', '薇信', '溦信', '威信', '加我', '电话', '手机号', 'qq', 'QQ', '邮箱', '二维码', '私聊', '主页有', '扣扣', 'v我', 'v 我']
const qcOpen = ref(false)
const qcResult = ref<{ checked: boolean; abs: string[]; drain: string[]; structure: string[] }>({ checked: false, abs: [], drain: [], structure: [] })

function runQC() {
  const titleText = title.value || ''
  const bodyText = body.value || ''
  const tagText = tags.value || ''
  const abs: string[] = []
  const drain: string[] = []
  const structure: string[] = []
  const all = titleText + ' ' + bodyText + ' ' + tagText
  for (const w of Q_ABS_WORDS) if (all.includes(w)) abs.push(w)
  for (const w of Q_DRAIN_WORDS) if (all.toLowerCase().includes(w.toLowerCase())) drain.push(w)
  if (titleText.trim().length < 4) structure.push('标题过短（建议 ≥4 字，前 12 字抓眼球）')
  if (bodyText.trim().length < 50) structure.push('正文过短（建议 ≥50 字，含分段）')
  if (bodyText.trim() && !/[\n\r]/.test(bodyText.trim())) structure.push('正文无分段（建议 3 段式：痛点 → 干货 → 引导）')
  if (!images.value.length) structure.push('未添加图片（图文笔记建议 ≥1 张）')
  qcResult.value = { checked: true, abs, drain, structure }
  return { abs, drain, structure }
}

/** 展开质检面板时用当前表单值实时检查（发布时还会再跑一次强确认） */
function toggleQC() {
  qcOpen.value = !qcOpen.value
  if (qcOpen.value) runQC()
}

const qcPassed = computed(() => {
  const r = qcResult.value
  return r.checked && r.abs.length === 0 && r.drain.length === 0 && r.structure.length === 0
})

async function publish() {
  const r = runQC()
  // 高风险：导流词 / 绝对化用语 → 强确认（不硬拦，发布永远手动，但必须明示风险）
  const risk = [...r.drain, ...r.abs]
  if (risk.length) {
    const ok = window.confirm(`发布前质检发现高风险项：\n${risk.join('、')}\n\n按平台规则这些词可能触发限流/下架。仍要继续发布吗？`)
    if (!ok) return
  }
  // 提示级：结构缺失 + 图片二创 + 同质化 → 人工确认
  const tips: string[] = [...r.structure]
  if (images.value.length) tips.push('图片请确认已二创（调色/裁剪/加字/重排）或已获授权——克隆原图直接发布有搬运降权风险')
  if (tips.length && !window.confirm(`发布前提醒：\n${tips.join('\n')}\n\n已确认内容为你原创/已获授权并处理上述提醒？`)) return

  publishing.value = true
  try {
    // 前置检查：后端发布前校验账号与上传通道（现有护栏）
    const rr = await apiPublish({
      title: title.value,
      desc: body.value,
      tags: tags.value.split(/[\s,，]+/).filter(Boolean).map(t => (t.startsWith('#') ? t : '#' + t)),
      images: dedupe(images.value),
      visible: visible.value
    })
    toast(rr.ok ? '发布成功' : `发布失败：${rr.note || '未知原因'}`)
  } catch (e) {
    toast(`发布失败：${(e as Error).message}`)
  } finally {
    publishing.value = false
  }
}

async function saveDraft() {
  try {
    const r = await apiSaveDraft({
      title: title.value,
      desc: body.value,
      tags: tags.value.split(/[\s,，]+/).filter(Boolean).map(t => (t.startsWith('#') ? t : '#' + t)),
      images: dedupe(images.value),
      visible: visible.value,
      feed_id: feedId.value
    })
    toast(r.ok ? r.note || '草稿已保存（长期保留）' : `保存失败：${r.note || '未知原因'}`)
  } catch (e) {
    toast(`保存失败：${(e as Error).message}`)
  }
}

// ---- P1 数据回流：发布记录 + 回写互动数据（浏览/赞/藏/评/转） ----
const logs = ref<LogItem[]>([])
const logBusy = ref(0)                          // 正在回写的记录 id
const logUrlInput = ref<Record<string, string>>({})  // 每条记录的已发布链接输入（key=记录 id 字符串）

async function loadLogs() {
  try { logs.value = await apiLogs() } catch { logs.value = [] }
}
loadLogs()

function loadLogsOnOpen(ev: Event) {
  const el = ev.target as HTMLDetailsElement
  if (el.open) loadLogs()
}

function parseStats(lg: LogItem): Record<string, string> | null {
  const s = lg.stats_json
  if (typeof s !== 'string' || !s) return null
  try { return JSON.parse(s) } catch { return null }
}

async function writeback(lg: LogItem) {
  const id = String(lg.id)
  logBusy.value = Number(id)
  try {
    const r = await apiPublishStats(Number(id), logUrlInput.value[id] || '')
    if (r.ok && r.stats) {
      toast('已回写互动数据（可在发布记录中查看）')
      await loadLogs()
    } else {
      toast(`回写失败：${(r as { detail?: string }).detail || (r as { note?: string }).note || '未知原因'}`)
    }
  } catch (e) {
    toast(`回写失败：${(e as Error).message}`)
  } finally {
    logBusy.value = 0
  }
}

// ---- O1 AI 文稿（Key 门控：未配置时按钮灰态 + 后端 400；配置入口收敛到「设置」Tab） ----
const aiCfg = ref<{ configured: boolean; openai_base_url: string; default_model: string; llm_api_key_masked: string }>()
const aiBusy = ref('')
const aiKeyword = ref('')  // 来源笔记的搜索关键词（openFor 记录），AI 生成时按品类/行业对齐

async function loadAiCfg() {
  try {
    aiCfg.value = await apiGetLLMConfig()
  } catch {
    aiCfg.value = { configured: false, openai_base_url: '', default_model: '', llm_api_key_masked: '' }
  }
}
loadAiCfg()

function gotoAiSettings() {
  setTab('settings')
}

async function aiRun(action: string) {
  if (!aiCfg.value?.configured) {
    toast('未配置 AI 模型：请先到「设置」Tab 配置 API Key / Base URL / 模型')
    return
  }
  aiBusy.value = action
  try {
    const r = await apiAiGenerate(action, { title: title.value, desc: body.value, keyword: aiKeyword.value })
    if (action === 'title_summarize' || action === 'title_rewrite') title.value = (r.result || '').slice(0, 50)
    else if (action === 'tags') tags.value = r.result || ''
    else body.value = (r.result || '').slice(0, 1000)
    toast('已生成，可继续编辑')
  } catch (e) {
    toast(`AI 生成失败：${(e as Error).message}`)
  } finally {
    aiBusy.value = ''
  }
}
</script>

<template>
  <div>
    <div style="background:var(--panel);border-bottom:1px solid var(--line);padding:12px 26px;font-size:13px;color:var(--ink-2)">
      发布编辑页 ·
      <a
        href="#"
        style="color:var(--red)"
        @click.prevent="toast('已复制原作品链接')"
      >复制原作品链接</a>
    </div>
    <div class="publish" style="margin-top:22px">
      <h2>发布笔记</h2>
      <div v-if="loading" style="text-align:center;padding:30px;color:var(--ink-2)">正在拉取笔记内容…</div>
      <template v-else>
        <div class="ai-bar">
          <span class="ai-tag" :class="{ off: !aiCfg?.configured }">AI</span>
          <button class="ai-btn" :disabled="!aiCfg?.configured || !!aiBusy" @click="aiRun('title_summarize')">{{ aiBusy === 'title_summarize' ? '…' : 'AI 提炼标题' }}</button>
          <button class="ai-btn" :disabled="!aiCfg?.configured || !!aiBusy" @click="aiRun('title_rewrite')">{{ aiBusy === 'title_rewrite' ? '…' : 'AI 改写标题' }}</button>
          <button class="ai-btn" :disabled="!aiCfg?.configured || !!aiBusy" @click="aiRun('body_create')">{{ aiBusy === 'body_create' ? '…' : 'AI 创作正文' }}</button>
          <button class="ai-btn" :disabled="!aiCfg?.configured || !!aiBusy" @click="aiRun('body_rewrite')">{{ aiBusy === 'body_rewrite' ? '…' : 'AI 改写正文' }}</button>
          <button class="ai-btn" :disabled="!aiCfg?.configured || !!aiBusy" @click="aiRun('tags')">{{ aiBusy === 'tags' ? '…' : 'AI 提取标签' }}</button>
          <button class="ai-btn ghost" @click="gotoAiSettings">AI 设置{{ aiCfg?.configured ? '' : '（未配置）' }} → 设置</button>
        </div>
        <div class="pub-field">
          <label>标题 <span class="limit">{{ tCount }}/50</span></label>
          <input v-model="title" maxlength="50" style="width:100%" />
        </div>
        <div class="pub-field">
          <label>正文 <span class="limit">{{ bCount }}/1000</span></label>
          <textarea v-model="body" rows="5" maxlength="1000" style="width:100%;resize:vertical"></textarea>
        </div>
        <div class="pub-field">
          <label>话题标签 <span class="limit">空格分隔</span></label>
          <input v-model="tags" placeholder="#翡翠 #手镯 #珠宝" style="width:100%" />
        </div>
        <!-- F9 发布前质检（折叠；发布按钮自动跑 + 高风险强确认） -->
        <div class="qc-card">
          <div class="qc-head" @click="toggleQC">
            <span class="qc-title">
              🔍 发布前质检
              <span v-if="!qcResult.checked" class="qc-badge idle">未检查（点开即查）</span>
              <span v-else-if="qcPassed" class="qc-badge ok">无风险项</span>
              <span v-else class="qc-badge warn">{{ qcResult.abs.length + qcResult.drain.length + qcResult.structure.length }} 项提醒</span>
            </span>
            <span class="qc-toggle">{{ qcOpen ? '收起 ▲' : '展开 ▼' }}</span>
          </div>
          <div v-if="qcOpen" class="qc-body">
            <div class="qc-item" :class="{ pass: !qcResult.drain.length && !qcResult.abs.length }">
              <div class="qc-t">① 违规词识别（绝对化用语 / 导流词，含谐音）</div>
              <div v-if="!qcResult.drain.length && !qcResult.abs.length" class="qc-ok">✓ 未发现违规词</div>
              <div v-else class="qc-fail">
                <span v-if="qcResult.drain.length">导流词：{{ qcResult.drain.join('、') }}（平台导流红线，可能屏蔽/限流）</span>
                <span v-if="qcResult.abs.length">绝对化用语：{{ qcResult.abs.join('、') }}（广告法风险）</span>
              </div>
            </div>
            <div class="qc-item" :class="{ pass: !qcResult.structure.length }">
              <div class="qc-t">② 结构检查（标题/正文/分段/配图）</div>
              <div v-if="!qcResult.structure.length" class="qc-ok">✓ 结构完整</div>
              <div v-else class="qc-fail">{{ qcResult.structure.join('；') }}</div>
            </div>
            <div class="qc-item">
              <div class="qc-t">③ 图片二创提醒</div>
              <div class="qc-warn">⚠ 克隆图片直接发布 = 搬运风险（可能下架/降权）。建议调色 / 裁剪 / 加字 / 重排后发布，或补充实拍素材。</div>
            </div>
            <div class="qc-item">
              <div class="qc-t">④ 原创确认（同质化）</div>
              <div class="qc-warn">⚠ 平台 2026 原创审核升级：仅"微改"参考爆文仍判同质化。发布前请确认：内容为你原创表达 / 已获授权。</div>
            </div>
            <div class="qc-sum">发布时自动再次检查：高风险项（导流词/绝对化用语）会弹窗强确认；其余提醒确认后可发布。<b>发布永远手动。</b></div>
          </div>
        </div>
        <div class="pub-field">
          <label>图片 <span class="limit">拖拽调序 / 点击看全图 / 删改 / 追加</span></label>
          <div class="pub-imgs">
            <div
              v-for="(img, i) in images"
              :key="i"
              class="pub-img"
              :class="{ dragging: dragIndex === i }"
              draggable="true"
              @dragstart="onDragStart(i)"
              @dragover="onDragOver"
              @drop="onDrop(i)"
              @dragend="onDragEnd"
            >
              <img v-if="img" :src="coverProxy(img)" alt="" @click="openPreview(coverProxy(img))" />
              <span class="x" @click="removeImg(i)">×</span>
            </div>
            <div class="pub-img add" :class="{ busy: appending }" @click="triggerAdd">{{ appending ? '…' : '＋' }}</div>
          </div>
          <input ref="fileInput" type="file" accept="image/*" multiple style="display:none" @change="onAddImages" />
        </div>
        <div class="pub-row">
          <div class="pub-field">
            <label>可见范围</label>
            <select v-model="visible" style="width:100%">
              <option>公开可见</option><option>仅粉丝可见</option><option>仅自己可见</option>
            </select>
          </div>
          <div class="pub-field">
            <label>发布账号</label>
            <select v-model="account" style="width:100%">
              <option value="">{{ appState.login.nickname || '当前账号' }}（当前）</option>
              <option disabled>账号 2（未配置）</option>
              <option disabled>账号 3（未配置）</option>
            </select>
          </div>
        </div>
        <div v-if="videoFile" class="video-bar">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none"><rect x="4" y="6" width="16" height="12" rx="2" stroke="#c7741e" /><path d="M10 9.5v5l4-2.5z" fill="#c7741e" /></svg>
          <b>原视频已下载</b>（{{ videoFile }}）——发布时在小红书发布页手动选择该本地视频文件上传即可（与正常发视频笔记一致，无需额外上传通道）。
        </div>
        <div class="check-note">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="#888" /><path d="M12 8v5M12 16.5v.01" stroke="#888" stroke-width="2" /></svg>
          发布前会自动检查账号和上传通道（视频原话）
        </div>
        <div class="pub-btns">
          <button class="btn-back" @click="back">返回作品列表</button>
          <button class="btn-draft" @click="saveDraft">保存草稿</button>
          <button class="btn-draft" @click="openDraftPanel">草稿库</button>
          <button class="btn-pub" :disabled="publishing" @click="publish">{{ publishing ? '发布中…' : '发布至' }}</button>
        </div>
      </template>
    </div>

    <!-- 草稿库弹窗（简约版：列表 / 点击恢复续编 / × 删除） -->
    <div v-if="draftPanelOpen" class="draft-overlay" @click.self="draftPanelOpen = false">
      <div class="draft-panel">
        <div class="draft-head">
          <b>草稿库</b>
          <span class="draft-close" @click="draftPanelOpen = false">×</span>
        </div>
        <div v-if="draftLoading" class="draft-empty">加载中…</div>
        <div v-else-if="!drafts.length" class="draft-empty">
          暂无草稿<br /><span class="dim">发布页编辑后点「保存草稿」即可保存（长期保留）</span>
        </div>
        <div v-else class="draft-list">
          <div v-for="d in drafts" :key="d.id" class="draft-item" @click="restoreDraft(d)">
            <img v-if="d.images[0]" class="draft-thumb" :src="coverProxy(d.images[0])" alt="" @error="($event.target as HTMLImageElement).style.display = 'none'" />
            <span v-else class="draft-thumb draft-thumb-empty">无图</span>
            <div class="draft-meta">
              <div class="draft-title">{{ d.title || '（无标题）' }}</div>
              <div class="draft-sub">{{ d.created_at?.replace('T', ' ') }} · {{ d.visibility }} · {{ d.images.length }} 图</div>
            </div>
            <button class="draft-del" @click.stop="removeDraft(d.id)">删除</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 发布记录 + 数据回流（P1）：回写已发布笔记互动数据（展开时自动刷新） -->
    <details class="pub-log-panel" style="margin:18px 26px 0;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 18px"
             @toggle="loadLogsOnOpen($event)">
      <summary style="cursor:pointer;font-size:13px;color:var(--ink-2)">
        发布记录（{{ logs.length }}）
        <button v-if="!logs.length" class="mini-btn" @click.prevent="loadLogs" style="margin-left:8px">刷新</button>
      </summary>
      <div v-if="!logs.length" class="dim" style="padding:8px 0;font-size:12px">暂无发布记录。发布后在此显示，可回写浏览/赞/藏/评/转数据。</div>
      <div v-for="lg in logs" :key="String(lg.id)" class="pub-log-item" style="border-top:1px dashed var(--line);padding:8px 0;margin-top:6px">
        <div style="display:flex;gap:10px;align-items:center;font-size:12px">
          <span style="font-weight:600">{{ String(lg.title || '(无标题)').slice(0, 24) }}</span>
          <span class="dim">{{ String(lg.created_at || '').replace('T', ' ') }}</span>
          <span :style="{ color: lg.status === 'success' ? 'var(--green, #16a34a)' : '#b45309' }">{{ lg.status === 'success' ? '已发布' : '待手动发布' }}</span>
        </div>
        <div style="display:flex;gap:8px;margin-top:6px">
          <input v-model="logUrlInput[lg.id as number]" placeholder="粘贴已发布笔记链接（可选，建议填）"
                 style="flex:1;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:5px 8px;font-size:12px;color:var(--ink-1)" />
          <button :disabled="logBusy === lg.id" @click="writeback(lg)"
                  style="border:1px solid var(--line);border-radius:6px;padding:5px 12px;font-size:12px;background:var(--bg);color:var(--ink-1);cursor:pointer">
            {{ logBusy === lg.id ? '回写中…' : '回写数据' }}
          </button>
        </div>
        <div v-if="parseStats(lg)" class="dim" style="font-size:12px;margin-top:4px">
          浏览 {{ parseStats(lg)?.views || '-' }} · 赞 {{ parseStats(lg)?.liked_count || '-' }} ·
          藏 {{ parseStats(lg)?.collected_count || '-' }} · 评 {{ parseStats(lg)?.comment_count || '-' }} ·
          转 {{ parseStats(lg)?.shared_count || '-' }} · 抓于 {{ (parseStats(lg)?.fetched_at || '').replace('T', ' ') }}
        </div>
      </div>
    </details>

    <!-- 图片全图预览（lightbox：点击遮罩/图关闭） -->
    <div v-if="previewUrl" class="img-lightbox" @click="previewUrl = ''">
      <img :src="previewUrl" alt="" @click.stop="previewUrl = ''" />
      <div class="lb-hint">点击任意处关闭</div>
    </div>
  </div>
</template>
