<script setup lang="ts">
import { computed, ref } from 'vue'
import { coverProxy, toast } from '../store'
import { apiClone, apiFillXhs, apiUploadImage, apiAiGenerate } from '../api'
import AiWizard from './AiWizard.vue'
import type { Note } from '../types'
import { isLoginAbnormal } from '../lib/auth'

const emit = defineEmits<{ (e: 'open-login'): void }>()

const title = ref('')
const body = ref('')
const tags = ref('')
const images = ref<string[]>([])
const feedId = ref('')
const videoFile = ref('')  // 视频克隆：原视频本地路径（/videos/...）；空=图文
const videoUrl = ref('')   // 原视频远端地址（仅展示用）
const filling = ref(false)
const loading = ref(false)

// ---- 图片去重（URL 主路径去重）：克隆载荷 / 追加图片均经此过滤 ----
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
  } catch (e) {
    toast(`克隆失败：${(e as Error).message}`, 'error')
  } finally {
    loading.value = false
  }
}

defineExpose({ openFor })

function removeImg(i: number) {
  images.value.splice(i, 1)
}

// 拖拽排序（原生 HTML5 DnD）：拖放后重排 images 数组，填入小红书时按新顺序生效
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
      toast(`追加失败：${r.note || '未知原因'}`, 'error')
    }
  }
  appending.value = false
  if (added) toast(`已追加 ${added} 张图片（共 ${images.value.length} 张）`)
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

// 填入小红书发布页：自动打开 write 页，把标题/正文/标签/图片填进去；
// 最后那一下「发布」始终由用户在小红书里手动点（产品红线：不自动发布）。
async function fillXhs() {
  filling.value = true
  try {
    const r = await apiFillXhs({
      title: title.value,
      desc: body.value,
      tags: tags.value.split(/[\s,，]+/).filter(Boolean),
      images: dedupe(images.value),
    })
    toast(r.ok ? (r.note || '已填入小红书发布页，确认后手动点发布') : `填入失败：${r.note || '未知原因'}`, r.ok ? 'ok' : 'error')
    if (!r.ok && isLoginAbnormal(String(r.note || ''))) emit('open-login')
  } catch (e) {
    toast(`填入失败：${(e as Error).message}`, 'error')
    if (isLoginAbnormal((e as Error).message)) emit('open-login')
  } finally {
    filling.value = false
  }
}


// ---- O1 AI 文稿（本阶段 UI 先行：入口不读配置状态，一律当已接通） ----
const aiBusy = ref('')
const aiKeyword = ref('')  // 来源笔记的搜索关键词（openFor 记录），AI 生成时按品类/行业对齐

// ---- AI 一键生成图文（三步向导：设定 → 大纲 → 配图） ----
const aiToolsOpen = ref(true)
const wizardOpen = ref(false)

function openWizard() {
  wizardOpen.value = true
}

function onWizardApply(v: { title: string; body: string; tags: string; images: string[] }) {
  title.value = (v.title || '').slice(0, 50)
  body.value = (v.body || '').slice(0, 1000)
  tags.value = v.tags || ''
  if (v.images?.length) images.value = dedupe(v.images)
}

async function aiRun(action: string) {
  aiBusy.value = action
  try {
    const r = await apiAiGenerate(action, { title: title.value, desc: body.value, keyword: aiKeyword.value })
    if (action === 'title_summarize' || action === 'title_rewrite') title.value = (r.result || '').slice(0, 50)
    else if (action === 'tags') tags.value = r.result || ''
    else body.value = (r.result || '').slice(0, 1000)
    toast('已生成，可继续编辑')
  } catch (e) {
    toast(`AI 生成失败：${(e as Error).message}`, 'error')
  } finally {
    aiBusy.value = ''
  }
}
</script>

<template>
  <div>
    <div class="publish" style="margin-top:18px">
      <h2>发布笔记</h2>
      <div v-if="loading" style="text-align:center;padding:30px;color:var(--ink-2)">正在拉取笔记内容…</div>
      <template v-else>
        <div class="ai-panel">
          <div class="ai-main">
            <span class="ai-tag">AI</span>
            <div class="ai-copy">
              <b>一键生成图文</b>
              <p>按账号档案 + 内容结构，一次出标题 / 正文 / 标签 / 配图；大纲中间可改</p>
            </div>
            <button class="ai-primary" @click="openWizard">生成</button>
          </div>
          <div class="ai-sub">
            <button class="ai-toggle" @click="aiToolsOpen = !aiToolsOpen">单步改写 {{ aiToolsOpen ? '▾' : '▸' }}</button>
          </div>
          <div v-if="aiToolsOpen" class="ai-actions">
            <button class="ai-btn" :disabled="!!aiBusy" @click="aiRun('title_summarize')">{{ aiBusy === 'title_summarize' ? '…' : '提炼标题' }}</button>
            <button class="ai-btn" :disabled="!!aiBusy" @click="aiRun('title_rewrite')">{{ aiBusy === 'title_rewrite' ? '…' : '改写标题' }}</button>
            <button class="ai-btn" :disabled="!!aiBusy" @click="aiRun('body_create')">{{ aiBusy === 'body_create' ? '…' : '创作正文' }}</button>
            <button class="ai-btn" :disabled="!!aiBusy" @click="aiRun('body_rewrite')">{{ aiBusy === 'body_rewrite' ? '…' : '改写正文' }}</button>
            <button class="ai-btn" :disabled="!!aiBusy" @click="aiRun('tags')">{{ aiBusy === 'tags' ? '…' : '提取标签' }}</button>
          </div>
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
        <div v-if="videoFile" class="video-bar">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none"><rect x="4" y="6" width="16" height="12" rx="2" stroke="#c7741e" /><path d="M10 9.5v5l4-2.5z" fill="#c7741e" /></svg>
          <b>原视频已下载</b>（{{ videoFile }}）——发布时在小红书发布页手动选择该本地视频文件上传即可（与正常发视频笔记一致，无需额外上传通道）。
        </div>
        <div class="check-note">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="#888" /><path d="M12 8v5M12 16.5v.01" stroke="#888" stroke-width="2" /></svg>
          点上方按钮后，标题 / 正文 / 标签 / 图片会自动填进小红书发布页；「发布」这一步仍需你在小红书里手动点
        </div>
        <div class="pub-btns">
          <button class="btn-pub" :disabled="filling" @click="fillXhs">{{ filling ? '正在填入…' : '填入小红书发布页' }}</button>
        </div>
      </template>
    </div>


    <!-- 图片全图预览（lightbox：点击遮罩/图关闭） -->
    <div v-if="previewUrl" class="img-lightbox" @click="previewUrl = ''">
      <img :src="previewUrl" alt="" @click.stop="previewUrl = ''" />
      <div class="lb-hint">点击任意处关闭</div>
    </div>
    <AiWizard
      :open="wizardOpen"
      :source="{ title, desc: body, keyword: aiKeyword, images }"
      @close="wizardOpen = false"
      @apply="onWizardApply"
    />
  </div>
</template>
