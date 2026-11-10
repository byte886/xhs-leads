<script setup lang="ts">
/**
 * AI 一键生成图文 · 三步向导
 * ① 设定（选档案 + 选结构） → ② 大纲（可编辑，先改再花钱） → ③ 配图（封面真实图 + 内页模板版式）
 * 设计依据：RedInk 的 input→outline→generating→result 线性流转 + 门店视频工具的三步向导；
 * 内页走自建 HTML 模板、自动错开版式，封面不套通用模板（平台 2026 严查模板化/虚假实拍）。
 */
import { computed, reactive, ref, watch } from 'vue'
import { coverProxy, setTab, toast } from '../store'
import { apiAiImageFromOutline, apiAiOutline, apiListProfiles } from '../api'
import type { AiProfile, AiOutline } from '../api'

const props = defineProps<{
  open: boolean
  source: { title: string; desc: string; keyword: string; images: string[] }
}>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'apply', v: { title: string; body: string; tags: string; images: string[] }): void }>()

/** 内容结构：决定页数与叙事骨架（对齐行业里的「18 种种草结构」，先收敛到 5 个常用） */
const STRUCTURES = [
  { id: 'pitfall', name: '避坑清单', desc: '先列 3 个常见坑，再给判断方法，最容易建立信任' },
  { id: 'compare', name: '对比测评', desc: '两档实物对比，用参数、价差、上手感说话' },
  { id: 'single', name: '单品深讲', desc: '一只/一件从头讲到尾，适合高客单' },
  { id: 'store', name: '门店实拍', desc: '真实货架与上手细节，弱化模板感' },
  { id: 'new', name: '上新到货', desc: '新货 + 数量 + 到店看货，偏成交' },
]

/**
 * 内页自建版式（HTML 模板渲染，确认后接入）。
 * 每个版式标注适用结构；同一篇 3 张内页默认错开不同版式，规避「模板脸」同质化。
 */
const LAYOUTS = [
  { id: 'num', name: '大数字标题', desc: '顶部大序号 + 小标题', for: ['pitfall', 'new', 'single'] },
  { id: 'list', name: '清单条目', desc: '三条短句竖排', for: ['pitfall', 'new', 'store'] },
  { id: 'card', name: '卡片叠层', desc: '圆角卡片 + 重点加粗', for: ['pitfall', 'compare', 'single'] },
  { id: 'compare', name: '左右对比', desc: '两栏参数对照', for: ['compare'] },
  { id: 'split', name: '上图下文', desc: '图片居顶 + 文字块', for: ['store', 'single', 'compare'] },
  { id: 'quote', name: '金句大字', desc: '居中大字 + 留白', for: ['pitfall', 'single', 'new'] },
]

const step = ref(1)
const busy = ref(false)
const errMsg = ref('')
const profiles = ref<AiProfile[]>([])
const profileId = ref('')
const structure = ref('pitfall')
const direction = ref('')
/** 目标字数三档（对齐视频工具「目标字数」，防止 AI 写超；图文按次选，不放档案） */
const WORD_COUNTS = [
  { id: 'short', label: '短', desc: '200 字内', target: 200 },
  { id: 'medium', label: '中', desc: '400 字左右', target: 400 },
  { id: 'long', label: '长', desc: '600 字内', target: 600 },
]
const wordCount = ref('medium')
const outline = ref<AiOutline>({ title: '', body: '', tags: '', pages: [] })

// 第 3 步状态：AI 生图（封面+3内页）+ 原封面备选 + 3 内页版式
const generatedImages = ref<string[]>([])
const generating = ref(false)
const coverImage = ref('')
const contentLayouts = reactive(['', '', ''])
const autoLayout = ref(true)

const activeProfile = computed(() => profiles.value.find(p => p.id === profileId.value))

function reset() {
  step.value = 1
  errMsg.value = ''
  outline.value = { title: '', body: '', tags: '', pages: [] }
  direction.value = ''
  wordCount.value = 'medium'
  coverImage.value = ''
  generatedImages.value = []
  contentLayouts[0] = ''
  contentLayouts[1] = ''
  contentLayouts[2] = ''
}

async function loadProfiles() {
  try {
    profiles.value = (await apiListProfiles()).items
    if (!profileId.value && profiles.value.length) profileId.value = profiles.value[0].id
  } catch {
    profiles.value = []
  }
}

watch(() => props.open, async (v) => {
  if (!v) return
  reset()
  await loadProfiles()
})

function toSettings() {
  emit('close')
  setTab('settings')
}

async function genOutline() {
  if (!profileId.value) {
    toast('请先选择账号档案（档案决定口吻与卖点）', 'error')
    return
  }
  busy.value = true
  errMsg.value = ''
  try {
    const r = await apiAiOutline({
      profile_id: profileId.value,
      structure: STRUCTURES.find(s => s.id === structure.value)?.name || '避坑清单',
      word_count: WORD_COUNTS.find(w => w.id === wordCount.value)?.target || 400,
      note: {
        title: props.source.title || '',
        desc: props.source.desc || '',
        keyword: [props.source.keyword, direction.value].filter(Boolean).join(' '),
      },
    })
    outline.value = {
      title: r.outline?.title || '',
      body: r.outline?.body || '',
      tags: r.outline?.tags || '',
      pages: (r.outline?.pages || []).slice(0, 4),
    }
    step.value = 2
  } catch (e) {
    errMsg.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

/** 当前结构可用的内页版式 */
function layoutsFor() {
  return LAYOUTS.filter(l => l.for.includes(structure.value))
}

/** 自动错开：3 张内页尽量分到不同版式（不足时循环兜底） */
function assignLayouts() {
  const ok = layoutsFor()
  if (!ok.length) return
  for (let i = 0; i < 3; i++) contentLayouts[i] = ok[Math.min(i, ok.length - 1)].id
}

/** 进第 3 步：封面默认取原笔记首图，内页自动排版式 */
function prefillStep3() {
  const src = (props.source.images || []).filter(Boolean)
  coverImage.value = src[0] || ''
  assignLayouts()
}

watch(step, (v) => { if (v === 3) prefillStep3() })
watch(structure, () => { if (step.value === 3 && autoLayout.value) assignLayouts() })

function onAutoToggle() {
  if (autoLayout.value) assignLayouts()
}

/** 封面点选：选中再点取消 */
function pickCover(url: string) {
  coverImage.value = coverImage.value === url ? '' : url
}

/** 按当前（可能已编辑）大纲逐页 AI 生图：1 封面 + 3 内页，3:4 */
async function generateImages() {
  if (!outline.value.pages.length) {
    toast('大纲没有分页，无法生图', 'error')
    return
  }
  generating.value = true
  errMsg.value = ''
  try {
    const r = await apiAiImageFromOutline({
      profile_id: profileId.value,
      pages: outline.value.pages,
    })
    generatedImages.value = r.images || []
    if (!generatedImages.value.length) toast('未返回图片，请重试', 'error')
  } catch (e) {
    errMsg.value = (e as Error).message
  } finally {
    generating.value = false
  }
}

function removeGenerated(i: number) {
  generatedImages.value.splice(i, 1)
}

function apply() {
  // 优先用 AI 生成的封面+内页；未生图则回退到点选的原笔记封面
  const imgs = generatedImages.value.length
    ? [...generatedImages.value]
    : (coverImage.value ? [coverImage.value] : [])
  emit('apply', {
    title: outline.value.title.slice(0, 20),
    body: outline.value.body,
    tags: outline.value.tags,
    images: imgs,
  })
  emit('close')
  toast(imgs.length ? `已填入发布页（${imgs.length} 张图）` : '已填入发布页，可在发布页补图')
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
            <h2>一键生成图文</h2>
            <p>{{ source.title || '未命名原笔记' }} · 依据账号档案重写标题 / 正文 / 标签 / 配图</p>
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
              <a href="#" @click.prevent="toSettings">去设置里新建 / 编辑 →</a>
            </div>
            <div v-if="!profiles.length" class="empty">
              还没有账号档案。档案提供行业、卖点、口吻、避雷词，是生成不跑偏的前提。
              <button class="btn primary sm" @click="toSettings">去创建档案</button>
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
            <div class="grp-head"><b>内容结构</b><span class="hint">决定页数与叙事骨架，和视频工具「选模板」同一个位置</span></div>
            <div class="struct-grid">
              <button
                v-for="s in STRUCTURES"
                :key="s.id"
                class="pick-card struct"
                :class="{ on: structure === s.id }"
                @click="structure = s.id"
              >
                <b>{{ s.name }}</b>
                <em>{{ s.desc }}</em>
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

          <div v-if="errMsg" class="wz-err">{{ errMsg }}</div>
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
            <label class="fld">
              <span>话题标签 <em class="cnt">空格分隔，3–8 个</em></span>
              <input v-model="outline.tags" class="wz-input" />
            </label>
          </div>

          <div class="grp">
            <div class="grp-head"><b>分页规划</b><span class="hint">封面 1 张 + 内页 3 张（3:4，1080×1440）</span></div>
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

          <div v-if="errMsg" class="wz-err">{{ errMsg }}</div>
        </section>

        <!-- ③ 配图：封面真实图 + 内页模板版式 -->
        <section v-else>
          <div class="grp-head lead">
            <b>封面与内页排版</b>
            <span class="hint">封面用真实素材，内页走自建模板、自动错开版式</span>
          </div>

          <div class="warn">
            平台强制 AI 标识、严查模板化同质化与虚假实拍：封面不套通用模板，内页 3 张用不同版式。
          </div>

          <!-- AI 生图：按当前大纲逐页生成 3:4 图 -->
          <div class="grp gen-grp">
            <div class="grp-head">
              <b>AI 生图（封面 + 内页）</b>
              <span class="hint">基于改好的大纲逐页生成 3:4 图；图内不含文字，文字在发布页/后期叠加</span>
            </div>
            <button class="btn primary" :disabled="generating || !outline.pages.length" @click="generateImages">
              {{ generating ? '正在生图（约 10–30 秒）…' : (generatedImages.length ? '重新生图' : '开始 AI 生图') }}
            </button>
            <div v-if="generatedImages.length" class="gen-grid">
              <div v-for="(img, i) in generatedImages" :key="i" class="gen-cell">
                <span class="gen-tag">{{ i === 0 ? '封面' : '内页 ' + i }}</span>
                <img :src="coverProxy(img)" alt="" />
                <button class="gen-x" @click="removeGenerated(i)">×</button>
              </div>
            </div>
          </div>

          <!-- 封面：真实素材（备选） -->
          <div class="cover-slot">
            <div class="cv-head">
              <b>封面 3:4</b>
              <span class="real-tag">真实素材</span>
              <span class="hint">不套通用模板，避免模板脸</span>
            </div>
            <div class="cv-box">
              <div class="cv-thumb">
                <img v-if="coverImage" :src="coverProxy(coverImage)" alt="" />
                <div v-else class="slot-empty">
                  请从下方原笔记图点选<br />或上传门店 / 产品实拍
                </div>
              </div>
              <div v-if="outline.pages[0]" class="cv-meta">
                <p class="sc-h">{{ outline.pages[0].heading }}</p>
                <p class="sc-c">{{ outline.pages[0].copy }}</p>
              </div>
            </div>
          </div>

          <!-- 内页：模板版式 -->
          <div class="grp ip">
            <div class="grp-head">
              <b>内页模板（3 张）</b>
              <label class="auto-line">
                <input type="checkbox" v-model="autoLayout" @change="onAutoToggle" />
                自动错开版式
              </label>
            </div>
            <div class="ip-grid">
              <div v-for="i in 3" :key="i" class="ip-slot">
                <div class="ip-head"><b>内页 {{ i }}</b></div>
                <div class="ip-preview" :class="contentLayouts[i - 1]">
                  <span class="pv-line w1" /><span class="pv-line w2" /><span class="pv-line w3" />
                </div>
                <select v-model="contentLayouts[i - 1]" class="wz-input layout-select">
                  <option v-for="l in layoutsFor()" :key="l.id" :value="l.id">{{ l.name }} · {{ l.desc }}</option>
                </select>
                <p v-if="outline.pages[i]" class="sc-h">{{ outline.pages[i].heading }}</p>
                <p v-if="outline.pages[i]" class="sc-c">{{ outline.pages[i].copy }}</p>
              </div>
            </div>
          </div>

          <!-- 原笔记图：封面选图 -->
          <div v-if="source.images.length" class="grp">
            <div class="grp-head"><b>原笔记图片（点选封面）</b><span class="hint">再点一次取消</span></div>
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
          <button v-if="step === 3" class="btn primary" @click="apply">填入发布页</button>
        </div>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.wizard-mask {
  position: fixed; inset: 0; z-index: 900;
  background: rgba(20, 16, 12, .38);
  display: flex; align-items: stretch; justify-content: center;
}
.wizard {
  width: 100%; max-width: 960px; background: #fff;
  display: flex; flex-direction: column;
  box-shadow: 0 12px 40px rgba(0, 0, 0, .18);
}
.wz-head {
  display: flex; align-items: flex-start; justify-content: space-between;
  padding: 20px 26px 10px;
}
.wz-title { display: flex; gap: 12px; align-items: flex-start; }
.ai-dot {
  width: 34px; height: 34px; border-radius: 10px; flex-shrink: 0;
  background: #fff1e6; color: #b05a1f; font-weight: 800; font-size: 14px;
  display: flex; align-items: center; justify-content: center;
}
.wz-title h2 { margin: 0; font-size: 18px; color: #1e1e1e; }
.wz-title p { margin: 4px 0 0; font-size: 12px; color: #8c8c8c; }
.wz-close {
  border: 0; background: transparent; font-size: 22px; color: #999; cursor: pointer; line-height: 1;
}
.wz-steps { display: flex; align-items: center; gap: 10px; padding: 0 26px 12px; }
.stp {
  display: flex; align-items: center; gap: 7px; font-size: 12.5px; color: #999;
}
.stp i {
  width: 20px; height: 20px; border-radius: 50%; font-style: normal;
  display: flex; align-items: center; justify-content: center;
  background: #f0f0f2; color: #999; font-size: 11px; font-weight: 700;
}
.stp.cur { color: #b05a1f; font-weight: 700; }
.stp.cur i { background: #b05a1f; color: #fff; }
.stp.done i { background: #eaf7ef; color: #1a7f4c; }
.line { flex: 1; height: 1px; background: #eee; }
.wz-body { flex: 1; overflow-y: auto; padding: 6px 26px 18px; }
.grp {
  border: 1px solid #ececec; border-radius: 14px; padding: 16px; margin-bottom: 14px;
}
.grp-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-bottom: 12px; }
.grp-head b { font-size: 14px; color: #222; }
.grp-head a { font-size: 12px; color: #b05a1f; text-decoration: none; }
.hint { font-size: 11.5px; color: #9a9a9a; }
.grp-head.lead { border: 0; padding: 4px 2px 10px; margin: 0; }
.prof-pick { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.pick-card {
  text-align: left; border: 1px solid #ececec; background: #fafafa; border-radius: 10px;
  padding: 12px; cursor: pointer; display: flex; flex-direction: column; gap: 5px;
}
.pick-card:hover { border-color: #ffd0d9; background: #fff; }
.pick-card.on { border-color: #b05a1f; background: #fff; box-shadow: 0 2px 8px rgba(176, 90, 31, .1); }
.pick-card b { font-size: 13.5px; color: #222; }
.pick-card .badge {
  align-self: flex-start; font-size: 11px; color: #b45409; background: #fdf2e2;
  padding: 1px 8px; border-radius: 999px;
}
.pick-card em { font-style: normal; font-size: 11.5px; color: #777; line-height: 1.5; }
.struct-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.wc-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; max-width: 560px; }
.wc-grid .wc { align-items: center; text-align: center; }
.struct em { margin-top: 2px; }
.wz-input {
  width: 100%; box-sizing: border-box; border: 1px solid #e5e5e5; border-radius: 8px;
  padding: 8px 10px; font-size: 13px; background: #fff; font-family: inherit;
}
.wz-input:focus { outline: none; border-color: #b05a1f; }
textarea.wz-input { resize: vertical; line-height: 1.7; }
.fld { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.fld > span { font-size: 12px; color: #666; display: flex; justify-content: space-between; }
.cnt { font-style: normal; color: #9a9a9a; font-size: 11px; }
.cnt.over { color: #c0392b; font-weight: 700; }
.empty { font-size: 12.5px; color: #888; line-height: 1.7; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
.src-note { margin-top: 12px; border-top: 1px dashed #eee; padding-top: 10px; }
.src-note span { font-size: 11.5px; color: #b05a1f; font-weight: 700; }
.src-note p { margin: 4px 0 0; font-size: 12px; color: #777; line-height: 1.7; }
.page-list { display: flex; flex-direction: column; gap: 10px; }
.page-item { display: flex; gap: 12px; align-items: flex-start; }
.pg-tag {
  flex-shrink: 0; width: 56px; font-size: 11.5px; font-weight: 700; color: #b05a1f;
  background: #fff1e6; border-radius: 8px; padding: 6px 0; text-align: center;
}
.pg-fields { flex: 1; display: flex; flex-direction: column; gap: 8px; }
.warn {
  background: #fff7e6; border: 1px solid #ffe2b8; color: #b45409;
  border-radius: 10px; padding: 10px 12px; font-size: 12px; line-height: 1.7; margin-bottom: 14px;
}

/* 封面真实素材位 */
.cover-slot {
  border: 1px solid #f3d9c2; border-radius: 14px; padding: 14px; margin-bottom: 14px;
  background: linear-gradient(180deg, #fffaf4, #fff);
}
.cv-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.cv-head b { font-size: 14px; color: #222; }
.real-tag {
  font-size: 11px; font-weight: 700; color: #b05a1f; background: #fff1e6;
  padding: 2px 9px; border-radius: 999px;
}
.cv-box { display: flex; gap: 16px; align-items: flex-start; }
.cv-thumb {
  width: 150px; flex-shrink: 0; aspect-ratio: 3 / 4; border: 1px dashed #e0c4a8;
  border-radius: 10px; overflow: hidden; background: #faf7f3;
  display: flex; align-items: center; justify-content: center;
}
.cv-thumb img { width: 100%; height: 100%; object-fit: cover; }
.cv-meta { flex: 1; min-width: 0; }

/* 内页模板 */
.auto-line { font-size: 12px; color: #666; display: flex; align-items: center; gap: 5px; }
.ip-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.ip-slot { display: flex; flex-direction: column; gap: 8px; }
.ip-head b { font-size: 12.5px; color: #333; }
.ip-preview {
  aspect-ratio: 3 / 4; border: 1px solid #eee; border-radius: 8px; background: #fafafb;
  padding: 12px; display: flex; flex-direction: column; gap: 7px; justify-content: center;
}
.pv-line { height: 6px; border-radius: 3px; background: #e3d5c6; display: block; }
.pv-line.w1 { width: 55%; }
.pv-line.w2 { width: 85%; }
.pv-line.w3 { width: 70%; }
/* 不同版式的抽象预览差异 */
.ip-preview.num .w1 { width: 40%; height: 14px; background: #d9b48c; }
.ip-preview.compare .pv-line { width: 46%; }
.ip-preview.split { justify-content: flex-end; }
.ip-preview.quote .pv-line { width: 75%; margin-left: auto; margin-right: auto; }
.layout-select { font-size: 12px; padding: 6px 8px; }
.slot-empty { font-size: 12px; color: #a8a8a8; text-align: center; line-height: 1.7; }
.sc-h { margin: 0; font-size: 12.5px; font-weight: 700; color: #333; }
.sc-c { margin: 3px 0 0; font-size: 11.5px; color: #888; line-height: 1.6; }
.src-imgs { display: flex; gap: 8px; flex-wrap: wrap; }
.thumb {
  width: 64px; height: 84px; padding: 0; border: 2px solid transparent; border-radius: 8px;
  overflow: hidden; cursor: pointer; background: #f4f4f6;
}
.thumb img { width: 100%; height: 100%; object-fit: cover; }
.thumb.on { border-color: #b05a1f; }
.wz-err { font-size: 12px; color: #c0392b; background: #fdf0ef; border-radius: 8px; padding: 8px 12px; }
.wz-foot {
  border-top: 1px solid #eee; padding: 12px 26px;
  display: flex; align-items: center; justify-content: space-between; gap: 12px; background: #fff;
}
.foot-note { font-size: 11.5px; color: #999; }
.foot-btns { display: flex; gap: 8px; }

/* AI 生图结果 */
.gen-grp .btn { margin-bottom: 4px; }
.gen-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }
.gen-cell { position: relative; min-width: 0; }
.gen-cell img {
  width: 100%; aspect-ratio: 3 / 4; object-fit: cover; display: block;
  border-radius: 8px; border: 1px solid #eee;
}
.gen-tag {
  display: inline-block; font-size: 11px; font-weight: 700; color: #b05a1f;
  background: #fff1e6; padding: 2px 8px; border-radius: 999px; margin-bottom: 6px;
}
.gen-x {
  position: absolute; top: 26px; right: 6px; width: 22px; height: 22px; border: 0;
  border-radius: 999px; background: rgba(20, 16, 12, .55); color: #fff;
  cursor: pointer; font-size: 14px; line-height: 1;
}
</style>
