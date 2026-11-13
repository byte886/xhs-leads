<script setup lang="ts">
/**
 * AI 一键生成标题与正文 · 三步向导（配图暂只走模板路径）
 * ① 设定（选档案 + 选内容结构 + 目标字数） → ② 大纲（可编辑，先改再花钱） → ③ 配图（二选一：整篇 AI 生图 / 封面实拍 + 内页模板）
 *
 * 数据来源：档案与模板都取自「档案」Tab（账号档案 / 内容模板），向导只做消费，不自带一套
 * 硬编码结构——否则用户在模板管理里建了模板却永远用不上。
 * 配图在第 3 步收敛为**一条主路径 + 一个备选**：同时摆 AI 生图、真实封面、内页模板三套会
 * 让用户不知道最终采用哪套，且一屏放不下（用户 2026-11 反馈「布局冲突就重新设计」）。
 */
import { computed, reactive, ref, watch } from 'vue'
import { coverProxy, setTab, toast } from '../store'
import { apiAiImageFromOutline, apiAiOutline, apiContentTemplates, apiListProfiles, apiUploadImage } from '../api'
import type { AiProfile, AiOutline, ImageTemplate, TextTemplate } from '../api'

const props = defineProps<{
  open: boolean
  source: { title: string; desc: string; keyword: string; images: string[] }
}>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'apply', v: { title: string; body: string; images: string[] }): void }>()

const step = ref(1)
const busy = ref(false)
const profiles = ref<AiProfile[]>([])
const profileId = ref('')
/** 内容结构 = 档案 Tab 里自建的文字模板（不再有前端硬编码的那套） */
const textTemplates = ref<TextTemplate[]>([])
const imageTemplates = ref<ImageTemplate[]>([])
const structure = ref('')
const direction = ref('')
/** 目标字数三档（对齐视频工具「目标字数」，防止 AI 写超；图文按次选，不放档案） */
const WORD_COUNTS = [
  { id: 'short', label: '短', desc: '200 字内', target: 200 },
  { id: 'medium', label: '中', desc: '400 字左右', target: 400 },
  { id: 'long', label: '长', desc: '600 字内', target: 600 },
]
const wordCount = ref('medium')
const outline = ref<AiOutline>({ title: '', body: '', pages: [] })

/**
 * 第 3 步配图方式（互斥二选一，一次只显示一套，避免三套方案同屏打架）：
 *  - ai：整篇 AI 生图（封面 + 内页），图内不含文字
 *  - template：封面用真实素材，内页套档案里的图片模板版式
 */
const imageMode = ref<'ai' | 'template'>('template')
const generatedImages = ref<string[]>([])
const generating = ref(false)
const applying = ref(false)
const coverImage = ref('')
const contentLayouts = reactive(['', '', ''])
const autoLayout = ref(true)

/** 当前选中的文字模板（决定大纲骨架与写法） */
const activeTemplate = computed(() => textTemplates.value.find(t => t.id === structure.value))

function reset() {
  step.value = 1
  outline.value = { title: '', body: '', pages: [] }
  direction.value = ''
  wordCount.value = 'medium'
  imageMode.value = 'template'
  coverImage.value = ''
  generatedImages.value = []
  contentLayouts[0] = ''
  contentLayouts[1] = ''
  contentLayouts[2] = ''
}

/** 打开时一次拉齐：账号档案 + 内容模板（文字/图片）——都来自「档案」Tab */
async function loadAll() {
  try {
    profiles.value = (await apiListProfiles()).items
    if (!profileId.value && profiles.value.length) profileId.value = profiles.value[0].id
  } catch {
    profiles.value = []
  }
  try {
    const t = await apiContentTemplates()
    textTemplates.value = t.text || []
    imageTemplates.value = t.image || []
  } catch {
    textTemplates.value = []
    imageTemplates.value = []
  }
  if (!structure.value && textTemplates.value.length) structure.value = textTemplates.value[0].id
}

watch(() => props.open, async (v) => {
  if (!v) return
  reset()
  await loadAll()
})

/** 去「档案」Tab 新建 / 编辑档案与模板（档案是内容资产，已从设置页独立成 Tab） */
function toProfiles() {
  emit('close')
  setTab('profiles')
}

/**
 * 内页真渲染：1242×1656（3:4），按所选图片模板的骨架 + 皮肤 + 大纲正文出图。
 * 预览与「填入发布页」用的是同一张，所见即所得——不画抽象色块。
 */
const INNER_W = 1242
const INNER_H = 1656
const SANS_STACK = '"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif'
const SERIF_STACK = '"Songti SC","STSong","SimSun",serif'

/** 按像素宽度折行（中文逐字，遇 \n 强制断行） */
function wrapText(ctx: CanvasRenderingContext2D, text: string, maxW: number): string[] {
  const out: string[] = []
  let line = ''
  for (const ch of Array.from(text || '')) {
    if (ch === '\n') { if (line) out.push(line); line = ''; continue }
    const next = line + ch
    if (ctx.measureText(next).width > maxW && line) { out.push(line); line = ch } else line = next
  }
  if (line) out.push(line)
  return out
}

/** 圆角矩形路径（不依赖 ctx.roundRect，老 Chromium 也能用） */
function roundRectPath(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.max(0, Math.min(r, Math.min(w, h) / 2))
  ctx.beginPath()
  ctx.moveTo(x + rr, y)
  ctx.arcTo(x + w, y, x + w, y + h, rr)
  ctx.arcTo(x + w, y + h, x, y + h, rr)
  ctx.arcTo(x, y + h, x, y, rr)
  ctx.arcTo(x, y, x + w, y, rr)
  ctx.closePath()
}

/** 把一页大纲按模板骨架画成 3:4 真图，返回 canvas（预览与落盘共用同一绘制结果） */
function buildInnerCanvas(idx: number): HTMLCanvasElement | null {
  const tpl = imageTemplates.value.find((x) => x.id === contentLayouts[idx])
  const page = outline.value.pages[idx + 1]
  const heading = (page?.heading || '这一页讲什么').trim()
  const copy = (page?.copy || '').trim()
  const skin = tpl?.skin
  const bg = skin?.bg || '#f7f2ec'
  const primary = skin?.primary || '#e22a43'
  const skeleton = tpl?.skeleton || 'card'
  const radius = skin?.radius ?? 8
  const centered = skin?.align === 'center'
  const font = skin?.font === 'serif' ? SERIF_STACK : SANS_STACK

  const cv = document.createElement('canvas')
  cv.width = INNER_W
  cv.height = INNER_H
  const ctx = cv.getContext('2d')
  if (!ctx) return null

  ctx.fillStyle = bg
  ctx.fillRect(0, 0, INNER_W, INNER_H)

  const pad = 108
  const innerW = INNER_W - pad * 2
  const x = centered ? INNER_W / 2 : pad
  ctx.textAlign = centered ? 'center' : 'left'
  ctx.textBaseline = 'alphabetic'

  const paint = (lines: string[], startY: number, size: number, gap: number) => {
    lines.forEach((l, i) => ctx.fillText(l, x, startY + i * (size + gap)))
    return startY + (lines.length - 1) * (size + gap)
  }
  const headingLines = (size: number) => {
    ctx.fillStyle = primary
    ctx.font = `700 ${size}px ${font}`
    return wrapText(ctx, heading, innerW).slice(0, 2)
  }
  const copyLines = (size: number, alpha: number, max: number) => {
    ctx.globalAlpha = alpha
    ctx.fillStyle = primary
    ctx.font = `400 ${size}px ${font}`
    const ls = wrapText(ctx, copy, innerW).slice(0, max)
    ctx.globalAlpha = 1
    return ls
  }

  // 页眉编号：三张内页带一致的序号，看起来是一套
  if (skin?.number !== false) {
    ctx.globalAlpha = 0.55
    ctx.fillStyle = primary
    ctx.font = `600 36px ${font}`
    ctx.fillText(`${String(idx + 1).padStart(2, '0')} / 03`, x, pad)
    ctx.globalAlpha = 1
  }

  if (skeleton === 'num') {
    // 大数字：巨型序号当主视觉，标题与正文压在下面，别让下半页空着
    ctx.globalAlpha = 0.9
    ctx.fillStyle = primary
    ctx.font = `700 330px ${font}`
    ctx.fillText(String(idx + 1).padStart(2, '0'), x, 560)
    ctx.globalAlpha = 1
    paint(headingLines(104), 740, 104, 26)
    paint(copyLines(54, 0.74, 5), 940, 54, 84)
  } else if (skeleton === 'list') {
    // 清单：标题在上，正文按句拆成带圆点的条目
    paint(headingLines(84), 300, 84, 24)
    const rows = (copy || '条目一。条目二。条目三。').split(/[。；;]/).filter(Boolean).slice(0, 4)
    ctx.font = `400 48px ${font}`
    rows.forEach((row, i) => {
      const y = 480 + i * 150
      ctx.globalAlpha = 0.85
      ctx.fillStyle = primary
      ctx.beginPath()
      ctx.arc(centered ? pad : pad + 12, y - 16, 12, 0, Math.PI * 2)
      ctx.fill()
      ctx.globalAlpha = 0.78
      const ls = wrapText(ctx, row.trim(), centered ? innerW - 48 : innerW - 56).slice(0, 2)
      ls.forEach((l, k) => ctx.fillText(l, centered ? INNER_W / 2 : pad + 56, y + k * 70))
      ctx.globalAlpha = 1
    })
  } else if (skeleton === 'card') {
    // 卡片：正文装进一张圆角块
    paint(headingLines(84), 280, 84, 24)
    const boxY = 420
    const boxH = INNER_H - boxY - pad - 60
    ctx.globalAlpha = 0.16
    ctx.fillStyle = primary
    roundRectPath(ctx, pad, boxY, innerW, boxH, radius * 3)
    ctx.fill()
    ctx.globalAlpha = 1
    ctx.textAlign = 'left'
    ctx.fillStyle = primary
    ctx.font = `400 54px ${font}`
    const body = wrapText(ctx, copy, innerW - 96).slice(0, 8)
    body.forEach((l, i) => ctx.fillText(l, pad + 48, boxY + 96 + i * 84))
    ctx.textAlign = centered ? 'center' : 'left'
  } else if (skeleton === 'compare') {
    // 对比：左右两栏，左标题右正文，中间一条竖线
    const colW = (innerW - 60) / 2
    ctx.globalAlpha = 0.14
    ctx.fillStyle = primary
    roundRectPath(ctx, pad, 300, colW, INNER_H - 300 - pad - 60, radius * 3)
    ctx.fill()
    roundRectPath(ctx, pad + colW + 60, 300, colW, INNER_H - 300 - pad - 60, radius * 3)
    ctx.fill()
    ctx.globalAlpha = 1
    ctx.textAlign = 'left'
    ctx.fillStyle = primary
    ctx.font = `700 68px ${font}`
    wrapText(ctx, heading, colW - 64).slice(0, 3)
      .forEach((l, i) => ctx.fillText(l, pad + 32, 400 + i * 96))
    ctx.font = `400 46px ${font}`
    ctx.globalAlpha = 0.8
    wrapText(ctx, copy, colW - 64).slice(0, 8)
      .forEach((l, i) => ctx.fillText(l, pad + colW + 92, 400 + i * 76))
    ctx.globalAlpha = 1
    ctx.textAlign = centered ? 'center' : 'left'
  } else if (skeleton === 'split') {
    // 分屏：上 40% 色块压标题（反白），下半留正文
    ctx.fillStyle = primary
    ctx.fillRect(0, 0, INNER_W, Math.round(INNER_H * 0.4))
    ctx.fillStyle = bg
    ctx.font = `700 104px ${font}`
    wrapText(ctx, heading, innerW).slice(0, 2)
      .forEach((l, i) => ctx.fillText(l, x, 250 + i * 130))
    ctx.fillStyle = primary
    paint(copyLines(54, 0.8, 6), Math.round(INNER_H * 0.4) + 160, 54, 30)
  } else {
    // quote 金句：正文当大字居中，上下引号
    ctx.fillStyle = primary
    ctx.globalAlpha = 0.25
    ctx.font = `700 220px ${font}`
    ctx.fillText('\u201c', x, 460)
    ctx.globalAlpha = 1
    ctx.font = `700 88px ${font}`
    const body = wrapText(ctx, copy || heading, innerW - 40).slice(0, 6)
    body.forEach((l, i) => ctx.fillText(l, x, 620 + i * 130))
    ctx.globalAlpha = 0.6
    ctx.font = `600 44px ${font}`
    wrapText(ctx, heading, innerW).slice(0, 2).forEach((l, i) => ctx.fillText(l, x, 1160 + i * 66))
    ctx.globalAlpha = 1
  }

  // 页脚：模板名 + 分隔线，让整套图有署名感
  if (skin?.divider !== false) {
    ctx.globalAlpha = 0.22
    ctx.strokeStyle = primary
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.moveTo(pad, INNER_H - 150)
    ctx.lineTo(INNER_W - pad, INNER_H - 150)
    ctx.stroke()
    ctx.globalAlpha = 0.55
    ctx.fillStyle = primary
    ctx.font = `500 32px ${font}`
    ctx.fillText(tpl?.name || '内容模板', x, INNER_H - pad)
    ctx.globalAlpha = 1
  }

  return cv
}

/** 即时预览：canvas 转 dataURL，改模板/大纲时由 computed 整体重算 */
function renderInnerPage(idx: number): string {
  const cv = buildInnerCanvas(idx)
  return cv ? cv.toDataURL('image/png') : ''
}

/** 3 张内页预览：内容/模板/大纲一变就整体重算，避免重复渲染 */
const innerPreviews = computed(() => contentLayouts.map((_, i) => renderInnerPage(i)))

/** canvas → PNG File（toBlob 是异步的，包一层 Promise） */
function canvasToFile(cv: HTMLCanvasElement, idx: number): Promise<File> {
  return new Promise((resolve, reject) => {
    cv.toBlob((blob) => {
      if (!blob) { reject(new Error('第 ' + (idx + 1) + ' 张内页导出失败')); return }
      resolve(new File([blob], `inner_${idx + 1}.png`, { type: 'image/png' }))
    }, 'image/png')
  })
}

/**
 * 把 3 张内页 canvas 走上传接口落盘成真实文件（/app/images/...），返回持久路径。
 * data URI 只用于即时预览，不能当真实文件——手动发布到 Chrome 时需要落盘文件。
 * 任一张失败就抛出，由调用方如实报错，不悄悄降级。
 */
async function uploadInnerPages(): Promise<string[]> {
  const paths: string[] = []
  for (let i = 0; i < 3; i++) {
    const cv = buildInnerCanvas(i)
    if (!cv) continue
    const file = await canvasToFile(cv, i)
    const r = await apiUploadImage(file)
    if (!r.ok || !r.path) throw new Error(r.note || `第 ${i + 1} 张内页落盘失败`)
    paths.push(r.path)
  }
  return paths
}

async function genOutline() {
  if (!profileId.value) {
    toast('请先选择账号档案（档案决定口吻与卖点）', 'error')
    return
  }
  busy.value = true
  try {
    // 结构名取所选文字模板；模板里的 prompt（「怎么写」）并入生成要求，避免模板白建
    const extra = [activeTemplate.value?.prompt, direction.value].filter(Boolean).join('；')
    const r = await apiAiOutline({
      profile_id: profileId.value,
      structure: activeTemplate.value?.name || '避坑清单',
      word_count: WORD_COUNTS.find(w => w.id === wordCount.value)?.target || 400,
      note: {
        title: props.source.title || '',
        desc: props.source.desc || '',
        keyword: [props.source.keyword, extra].filter(Boolean).join(' '),
      },
    })
    outline.value = {
      title: r.outline?.title || '',
      body: r.outline?.body || '',
      pages: (r.outline?.pages || []).slice(0, 4),
    }
    step.value = 2
  } catch (e) {
    // 生文失败如实告诉用户，不拿演示稿冒充成功
    toast(`生文失败：${(e as Error).message || '接口无响应'}`, 'error')
  } finally {
    busy.value = false
  }
}

/** 内页可用的图片模板：全部来自「档案 › 内容模板 › 图片模板」 */
function layoutsFor() {
  return imageTemplates.value
}

/** 骨架中文名（与后端 templates_store.SKELETONS 的 6 种对应） */
const SKELETON_NAMES: Record<string, string> = {
  num: '大数字', list: '清单', card: '卡片', compare: '对比', split: '分屏', quote: '金句',
}

function skeletonName(s: string) {
  return SKELETON_NAMES[s] || s
}

/** 第 i 张内页当前所选模板的骨架，用于抽象版式预览的差异 */

/** 自动错开：3 张内页尽量分到不同骨架（骨架不足时循环兜底），规避「模板脸」 */
function assignLayouts() {
  const all = imageTemplates.value
  if (!all.length) return
  const used = new Set<string>()
  for (let i = 0; i < 3; i++) {
    let pick = all.find(t => !used.has(t.skeleton))
    if (!pick) pick = all[i % all.length]
    used.add(pick.skeleton)
    contentLayouts[i] = pick.id
  }
}

/**
 * 进第 3 步：只排内页版式。
 * 封面不预填原笔记首图——封面是「人把关」的那一处，替用户默认选上会造成
 * 「看起来已经定好、其实没人看过」的假象；空槽 + 明确引导更诚实。
 */
function prefillStep3() {
  assignLayouts()
}

watch(step, (v) => { if (v === 3) prefillStep3() })

function onAutoToggle() {
  if (autoLayout.value) assignLayouts()
}

/** 封面点选：选中再点取消 */
function pickCover(url: string) {
  coverImage.value = coverImage.value === url ? '' : url
}

/**
 * 按当前（可能已编辑）大纲逐页 AI 生图：1 封面 + 3 内页，3:4。
 * 直接调真实生图接口，返回什么就显示什么；为空或失败如实报错。
 */
async function generateImages() {
  if (!outline.value.pages.length) {
    toast('大纲没有分页，无法生图', 'error')
    return
  }
  generating.value = true
  try {
    const r = await apiAiImageFromOutline({
      profile_id: profileId.value,
      pages: outline.value.pages,
    })
    const imgs = r.images || []
    if (!imgs.length) {
      toast('生图接口没有返回图片，请检查生图模型配置', 'error')
      return
    }
    generatedImages.value = imgs
  } catch (e) {
    toast(`生图失败：${(e as Error).message || '接口无响应'}`, 'error')
  } finally {
    generating.value = false
  }
}

function removeGenerated(i: number) {
  generatedImages.value.splice(i, 1)
}

async function apply() {
  if (applying.value) return
  // AI 模式：取生成的封面 + 内页；模板模式：封面实拍 + 3 张内页落盘成真实文件
  let imgs: string[] = []
  let note = ''
  if (imageMode.value === 'ai') {
    imgs = [...generatedImages.value]
  } else {
    applying.value = true
    try {
      // 内页 canvas 落盘成真实文件路径（data URI 只用于即时预览，不能当文件发布）
      const inner = imageTemplates.value.length ? await uploadInnerPages() : []
      imgs = [coverImage.value, ...inner].filter(Boolean) as string[]
    } catch (e) {
      toast(`内页落盘失败：${(e as Error).message}`, 'error')
      return
    } finally {
      applying.value = false
    }
    // 封面必须由用户从真实素材里点选，不能拿模板凑，缺了就明说
    if (!coverImage.value && props.source.images.length) {
      note = '；封面还没选，需在发布页从真实素材里补一张'
    }
  }
  emit('apply', {
    title: outline.value.title.slice(0, 20),
    body: outline.value.body,
    images: imgs,
  })
  emit('close')
  toast(imgs.length ? `已填入发布页（${imgs.length} 张图）${note}` : '已填入发布页，可在发布页补图')
}

const totalChars = computed(() => (outline.value.body || '').length)
</script>

<template>
  <div v-if="open" class="wizard-mask">
    <div class="wizard">
      <header class="wz-head">
        <div class="wz-title">
          <span class="ai-dot">AI</span>
          <div>
            <h2>一键生成标题与正文</h2>
            <p>{{ source.title || '未命名原笔记' }} · 依据账号档案重写标题 / 正文</p>
          </div>
        </div>
        <button class="wz-close" @click="emit('close')">×</button>
      </header>

      <nav class="wz-steps">
        <div class="stp" :class="{ cur: step === 1, done: step > 1 }"><i>1</i>设定</div>
        <div class="line" />
        <div class="stp" :class="{ cur: step === 2, done: step > 2 }"><i>2</i>大纲</div>
        <div class="line" />
        <div class="stp" :class="{ cur: step === 3 }"><i>3</i>配图</div>
      </nav>

      <main class="wz-body">
        <!-- ① 设定 -->
        <section v-if="step === 1">
          <div class="grp">
            <div class="grp-head">
              <b>选择账号档案</b>
              <a href="#" @click.prevent="toProfiles">去档案里新建 / 编辑 →</a>
            </div>
            <div v-if="!profiles.length" class="empty">
              还没有账号档案。档案提供行业、卖点、口吻、避雷词，是生成不跑偏的前提。
              <button class="btn primary sm" @click="toProfiles">去创建档案</button>
            </div>
            <div v-else class="prof-pick">
              <button
                v-for="p in profiles"
                :key="p.id"
                class="pick-card"
                :class="{ on: profileId === p.id }"
                @click="profileId = p.id"
              >
                <b>{{ p.name }}</b>
                <span class="badge">{{ p.industry || '未填行业' }}</span>
                <em>{{ p.selling_points || '未填核心卖点' }}</em>
              </button>
            </div>
          </div>

          <div class="grp">
            <div class="grp-head">
              <b>内容结构</b>
              <a href="#" @click.prevent="toProfiles">管理文字模板 →</a>
            </div>
            <div v-if="!textTemplates.length" class="empty">
              还没有文字模板。文字模板定义「怎么写」（如避坑先抛问题再给判断），是生成骨架的来源。
              <button class="btn primary sm" @click="toProfiles">去新建文字模板</button>
            </div>
            <div v-else class="struct-grid">
              <button
                v-for="t in textTemplates"
                :key="t.id"
                class="pick-card struct"
                :class="{ on: structure === t.id }"
                @click="structure = t.id"
              >
                <b>{{ t.name }}</b>
                <span v-if="t.scene" class="badge">{{ t.scene }}</span>
                <em>{{ t.desc || t.prompt }}</em>
              </button>
            </div>
          </div>

          <div class="grp">
            <div class="grp-head"><b>目标字数</b><span class="hint">控制正文长度、防止写太长；默认中</span></div>
            <div class="wc-grid">
              <button
                v-for="w in WORD_COUNTS"
                :key="w.id"
                class="pick-card wc"
                :class="{ on: wordCount === w.id }"
                @click="wordCount = w.id"
              >
                <b>{{ w.label }}</b>
                <em>{{ w.desc }}</em>
              </button>
            </div>
          </div>

          <div class="grp">
            <div class="grp-head"><b>补充方向</b><span class="hint">可留空；填了会叠加到生成要求里</span></div>
            <input v-model="direction" class="wz-input" placeholder="例如：强调 5000 元以内、本地上门看货、只讲糯种" />
            <div class="src-note">
              <span>原笔记</span>
              <p>{{ source.desc ? source.desc.slice(0, 140) + (source.desc.length > 140 ? '…' : '') : '（无正文）' }}</p>
            </div>
          </div>

        </section>

        <!-- ② 大纲 -->
        <section v-else-if="step === 2">
          <div class="grp-head lead">
            <b>先改大纲，再生成配图</b>
            <span class="hint">行业内的共识：人在大纲和封面这两处把关，纯 AI 直出的笔记跑不出量</span>
          </div>

          <div class="grp">
            <label class="fld">
              <span>标题 <em class="cnt" :class="{ over: (outline.title || '').length > 20 }">{{ (outline.title || '').length }}/20</em></span>
              <input v-model="outline.title" maxlength="30" class="wz-input" />
            </label>
            <label class="fld">
              <span>正文 <em class="cnt">{{ totalChars }}/1000</em></span>
              <textarea v-model="outline.body" rows="8" class="wz-input" />
            </label>
          </div>

          <div class="grp">
            <div class="grp-head"><b>分页规划</b><span class="hint">封面 1 张 + 内页 3 张（统一 3:4，1242×1656）</span></div>
            <div class="page-list">
              <div v-for="(pg, i) in outline.pages" :key="i" class="page-item">
                <span class="pg-tag">{{ pg.type === 'cover' ? '封面' : '内页 ' + i }}</span>
                <div class="pg-fields">
                  <input v-model="pg.heading" class="wz-input" placeholder="小标题（封面 ≤9 字）" />
                  <textarea v-model="pg.copy" rows="2" class="wz-input" placeholder="页面文案" />
                </div>
              </div>
            </div>
          </div>

        </section>

        <!-- ③ 配图：二选一（同屏只显示一套，避免三套方案打架、一屏放不下） -->
        <section v-else>
          <div class="grp-head lead">
            <b>选择配图方式</b>
            <span class="hint">二选一；只显示当前方式</span>
          </div>

          <div class="mode-grid">
            <button class="pick-card mode" :class="{ on: imageMode === 'ai' }" disabled title="生图能力接入中">
              <b>AI 整篇生图 <span class="beta">接入中</span></b>
              <em>生图暂不可用，先用右侧「封面实拍 + 内页模板」.</em>
            </button>
            <button class="pick-card mode" :class="{ on: imageMode === 'template' }" @click="imageMode = 'template'">
              <b>封面实拍 + 内页模板</b>
              <em>封面用真实素材避免模板脸，内页套档案里自建的图片模板版式。</em>
            </button>
          </div>

          <!-- 方式 A：AI 整篇生图 -->
          <div v-if="imageMode === 'ai'" class="grp gen-grp">
            <div class="grp-head">
              <b>封面 + 内页</b>
              <span class="hint">逐页生成 3:4 图；图内不含文字</span>
            </div>
            <button class="btn primary" :disabled="generating || !outline.pages.length" @click="generateImages">
              {{ generating ? '正在生图（约 10–30 秒）…' : (generatedImages.length ? '重新生图' : '开始 AI 生图') }}
            </button>
            <div v-if="!generatedImages.length" class="gen-empty">
              尚未生图。点上方按钮按当前大纲生成；生成后可删单张、重新生图。
            </div>
            <div v-else class="gen-grid">
              <div v-for="(img, i) in generatedImages" :key="i" class="gen-cell">
                <span class="gen-tag">{{ i === 0 ? '封面' : '内页 ' + i }}</span>
                <img :src="coverProxy(img)" alt="" />
                <button class="gen-x" @click="removeGenerated(i)">×</button>
              </div>
            </div>
          </div>

          <!-- 方式 B：封面实拍 + 内页模板 -->
          <template v-else>
            <div class="warn">
              封面不套通用模板（平台严查模板化与虚假实拍）；内页 3 张用不同骨架，规避「模板脸」。
            </div>

            <div class="cover-slot">
              <div class="cv-head">
                <b>封面 3:4</b>
                <span class="real-tag">真实素材</span>
                <span class="hint">在右栏点选原笔记图，或上传门店 / 产品实拍</span>
              </div>
              <div class="cv-box">
                <div class="cv-thumb">
                  <img v-if="coverImage" :src="coverProxy(coverImage)" alt="" />
                  <div v-else class="slot-empty">
                    请点选右栏原笔记图<br />或上传门店 / 产品实拍
                  </div>
                </div>
                <div class="cv-meta">
                  <template v-if="outline.pages[0]">
                    <p class="sc-h">{{ outline.pages[0].heading }}</p>
                    <p class="sc-c">{{ outline.pages[0].copy }}</p>
                  </template>
                  <p class="cv-spec">建议 1242×1656（3:4），顶部留 15% 安全区；文字压在画面上，单图文字占比 ≤40%</p>
                  <div v-if="source.images.length" class="cv-src">
                    <span class="cv-src-t">从原笔记图点选（再点一次取消）</span>
                    <div class="src-imgs">
                      <button
                        v-for="(u, k) in source.images.slice(0, 9)"
                        :key="k"
                        class="thumb"
                        :class="{ on: coverImage === u }"
                        @click="pickCover(u)"
                      >
                        <img :src="coverProxy(u)" alt="" />
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div class="grp ip">
              <div class="grp-head">
                <b>内页模板（3 张）</b>
                <label v-if="imageTemplates.length" class="auto-line">
                  <input type="checkbox" v-model="autoLayout" @change="onAutoToggle" />
                  自动错开骨架
                </label>
                <a v-else href="#" @click.prevent="toProfiles">去新建图片模板 →</a>
              </div>
              <div v-if="!imageTemplates.length" class="empty">
                还没有图片模板。图片模板决定内页长什么样（骨架 + 配色），请先到「档案 › 内容模板」新建。
                <button class="btn primary sm" @click="toProfiles">去新建图片模板</button>
              </div>
              <div v-else class="ip-grid">
                <div v-for="i in 3" :key="i" class="ip-slot">
                  <div class="ip-head"><b>内页 {{ i }}</b></div>
                  <div class="ip-preview">
                    <img :src="innerPreviews[i - 1]" alt="" />
                  </div>
                  <select v-model="contentLayouts[i - 1]" class="wz-input layout-select">
                    <option v-for="l in layoutsFor()" :key="l.id" :value="l.id">
                      {{ l.name }} · {{ skeletonName(l.skeleton) }}
                    </option>
                  </select>
                  <p v-if="outline.pages[i]" class="sc-h">{{ outline.pages[i].heading }}</p>
                  <p v-if="outline.pages[i]" class="sc-c">{{ outline.pages[i].copy }}</p>
                </div>
              </div>
            </div>

          </template>
        </section>
      </main>

      <footer class="wz-foot">
        <span class="foot-note">内容由 AI 辅助生成，发布前请复核，并勾选「含 AI 生成内容」标识</span>
        <div class="foot-btns">
          <button v-if="step > 1" class="btn" @click="step--">上一步</button>
          <button v-if="step === 1" class="btn primary" :disabled="busy || !profiles.length" @click="genOutline">
            {{ busy ? '正在生成大纲…' : '生成大纲' }}
          </button>
          <button v-if="step === 2" class="btn primary" :disabled="busy" @click="step = 3">下一步：配图</button>
          <button v-if="step === 2" class="btn" :disabled="busy" @click="genOutline">重新生成</button>
          <button v-if="step === 3" class="btn primary" :disabled="applying" @click="apply">{{ applying ? '正在落盘…' : '填入发布页' }}</button>
        </div>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.wizard-mask {
  position: fixed; inset: 0; z-index: 900;
  background: rgba(0, 0, 0, .35);
  display: flex; align-items: stretch; justify-content: center;
}
.wizard {
  width: 100%; max-width: 960px; max-height: 100%; background: var(--panel);
  display: flex; flex-direction: column; overflow: hidden;
  box-shadow: 0 12px 40px rgba(0, 0, 0, .16);
}
.wz-head {
  display: flex; align-items: flex-start; justify-content: space-between;
  padding: 20px 26px 10px;
}
.wz-title { display: flex; gap: 12px; align-items: flex-start; }
.ai-dot {
  width: 34px; height: 34px; border-radius: 10px; flex-shrink: 0;
  background: var(--red-soft); color: var(--red); font-weight: 800; font-size: 14px;
  display: flex; align-items: center; justify-content: center;
}
.wz-title h2 { margin: 0; font-size: 18px; color: var(--ink); }
.wz-title p { margin: 4px 0 0; font-size: 12px; color: var(--ink-3); }
.wz-close {
  border: 0; background: transparent; font-size: 22px; color: var(--ink-3); cursor: pointer; line-height: 1;
}
.wz-steps { display: flex; align-items: center; gap: 10px; padding: 0 26px 12px; }
.stp {
  display: flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--ink-3);
}
.stp i {
  width: 20px; height: 20px; border-radius: 50%; font-style: normal;
  display: flex; align-items: center; justify-content: center;
  background: var(--panel-2); color: var(--ink-3); font-size: 11px; font-weight: 700;
}
.stp.cur { color: var(--red); font-weight: 700; }
.stp.cur i { background: var(--red); color: var(--panel); }
.stp.done i { background: rgba(47, 122, 85, .1); color: var(--green); }
.line { flex: 1; height: 1px; background: var(--line); }
.wz-body { flex: 1; min-height: 0; overflow-y: auto; padding: 6px 26px 18px; }
.grp {
  border: 1px solid var(--line); border-radius: 14px; padding: 16px; margin-bottom: 14px;
}

/* 第 3 步：配图方式二选一 */
.mode-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-bottom: 14px; }
.mode { position: relative; }
.mode.on { border-color: var(--red); background: var(--panel); box-shadow: 0 2px 8px var(--ring); }
.gen-empty {
  margin-top: 10px; padding: 18px 12px; border: 1px dashed var(--line); border-radius: 10px;
  font-size: 12px; color: var(--ink-3); text-align: center; line-height: 1.7;
}
.grp-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-bottom: 12px; }
.grp-head b { font-size: 14px; color: var(--ink); }
.grp-head a { font-size: 12px; color: var(--red); text-decoration: none; }
.hint { font-size: 11.5px; color: var(--ink-3); }
.grp-head.lead { border: 0; padding: 4px 2px 10px; margin: 0; }
.prof-pick { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.pick-card {
  text-align: left; border: 1px solid var(--line); background: var(--panel-2); border-radius: 10px;
  padding: 12px; cursor: pointer; display: flex; flex-direction: column; gap: 5px;
}
.pick-card:hover { border-color: var(--ring); background: var(--panel); }
.pick-card.on { border-color: var(--red); background: var(--panel); box-shadow: 0 2px 8px var(--ring); }
.pick-card:disabled { opacity: .55; cursor: not-allowed; }
.pick-card:disabled:hover { border-color: var(--line); background: #fff; }
.beta { font-size: 10px; font-weight: 600; color: #b05a1f; background: #fff3e6; border-radius: 4px; padding: 1px 5px; margin-left: 4px; vertical-align: 1px; }
.pick-card b { font-size: 13.5px; color: var(--ink); }
.pick-card .badge {
  align-self: flex-start; font-size: 11px; color: var(--red); background: var(--red-soft);
  padding: 1px 8px; border-radius: 999px;
}
.pick-card em { font-style: normal; font-size: 11.5px; color: var(--ink-3); line-height: 1.5; }
.struct-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.wc-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; max-width: 560px; }
.wc-grid .wc { align-items: center; text-align: center; }
.struct em { margin-top: 2px; }
.wz-input {
  width: 100%; box-sizing: border-box; border: 1px solid var(--line); border-radius: 8px;
  padding: 8px 10px; font-size: 13px; background: var(--panel); font-family: inherit;
}
.wz-input:focus { outline: none; border-color: var(--red); }
textarea.wz-input { resize: vertical; line-height: 1.7; }
.fld { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.fld > span { font-size: 12px; color: var(--ink-3); display: flex; justify-content: space-between; }
.cnt { font-style: normal; color: var(--ink-3); font-size: 11px; }
.cnt.over { color: var(--danger); font-weight: 700; }
/* 本组件内的空态是「卡片内提示」，必须覆盖全局 .empty 的 60vh 整页空态高度 */
.empty {
  font-size: 12.5px; color: var(--ink-3); line-height: 1.7;
  display: flex; flex-direction: column; gap: 10px; align-items: flex-start;
  height: auto; justify-content: flex-start; text-align: left;
}
.src-note { margin-top: 12px; border-top: 1px dashed var(--line); padding-top: 10px; }
.src-note span { font-size: 11.5px; color: var(--red); font-weight: 700; }
.src-note p { margin: 4px 0 0; font-size: 12px; color: var(--ink-3); line-height: 1.7; }
.page-list { display: flex; flex-direction: column; gap: 10px; }
.page-item { display: flex; gap: 12px; align-items: flex-start; }
.pg-tag {
  flex-shrink: 0; width: 56px; font-size: 11.5px; font-weight: 700; color: var(--red);
  background: var(--red-soft); border-radius: 8px; padding: 6px 0; text-align: center;
}
.pg-fields { flex: 1; display: flex; flex-direction: column; gap: 8px; }
.warn {
  background: rgba(183, 122, 22, .1); border: 1px solid rgba(183, 122, 22, .25); color: var(--red);
  border-radius: 10px; padding: 10px 12px; font-size: 12px; line-height: 1.7; margin-bottom: 14px;
}

/* 封面真实素材位 */
.cover-slot {
  border: 1px solid var(--line); border-radius: 14px; padding: 14px; margin-bottom: 14px;
  background: var(--panel);
}
.cv-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.cv-head b { font-size: 14px; color: var(--ink); }
.real-tag {
  font-size: 11px; font-weight: 700; color: var(--red); background: var(--red-soft);
  padding: 2px 9px; border-radius: 999px;
}
.cv-box { display: flex; gap: 16px; align-items: flex-start; }
.cv-thumb {
  width: 150px; flex-shrink: 0; aspect-ratio: 3 / 4; border: 1px dashed var(--line);
  border-radius: 10px; overflow: hidden; background: var(--panel-2);
  display: flex; align-items: center; justify-content: center;
}
.cv-thumb img { width: 100%; height: 100%; object-fit: cover; }
.cv-meta { flex: 1; min-width: 0; }
.cv-spec {
  margin: 12px 0 0; font-size: 11.5px; color: var(--ink-3); line-height: 1.7;
  border-top: 1px dashed var(--line); padding-top: 10px;
}
.cv-src { margin-top: 12px; }
.cv-src-t { font-size: 11.5px; color: var(--ink-3); }

/* 内页模板 */
.auto-line { font-size: 12px; color: var(--ink-3); display: flex; align-items: center; gap: 5px; }
.ip-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.ip-slot { display: flex; flex-direction: column; gap: 8px; }
.ip-head b { font-size: 12.5px; color: var(--ink); }
.ip-preview {
  height: 180px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel-2);
  overflow: hidden; display: flex; justify-content: center;
}
/* 3:4 缩略图按高度撑满、宽度自适应，完整显示版式而不是裁成横条 */
.ip-preview img { height: 100%; width: auto; display: block; }
.layout-select { font-size: 12px; padding: 6px 8px; }
.slot-empty { font-size: 12px; color: var(--ink-3); text-align: center; line-height: 1.7; }
.sc-h { margin: 0; font-size: 12.5px; font-weight: 700; color: var(--ink); }
.sc-c { margin: 3px 0 0; font-size: 11.5px; color: var(--ink-3); line-height: 1.6; }
.src-imgs { display: flex; gap: 8px; flex-wrap: wrap; }
.thumb {
  width: 64px; height: 84px; padding: 0; border: 2px solid transparent; border-radius: 8px;
  overflow: hidden; cursor: pointer; background: var(--panel-2);
}
.thumb img { width: 100%; height: 100%; object-fit: cover; }
.thumb.on { border-color: var(--red); }
.wz-foot {
  border-top: 1px solid var(--line); padding: 12px 26px;
  display: flex; align-items: center; justify-content: space-between; gap: 12px; background: var(--panel);
}
.foot-note { font-size: 11.5px; color: var(--ink-3); }
.foot-btns { display: flex; gap: 8px; }

/* AI 生图结果 */
.gen-grp .btn { margin-bottom: 4px; }
.gen-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }
.gen-cell { position: relative; min-width: 0; }
.gen-cell img {
  width: 100%; aspect-ratio: 3 / 4; object-fit: cover; display: block;
  border-radius: 8px; border: 1px solid var(--line);
}
.gen-tag {
  display: inline-block; font-size: 11px; font-weight: 700; color: var(--red);
  background: var(--red-soft); padding: 2px 8px; border-radius: 999px; margin-bottom: 6px;
}
.gen-x {
  position: absolute; top: 26px; right: 6px; width: 22px; height: 22px; border: 0;
  border-radius: 999px; background: rgba(0, 0, 0, .55); color: var(--panel);
  cursor: pointer; font-size: 14px; line-height: 1;
}
</style>
