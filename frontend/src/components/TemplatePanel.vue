<script setup lang="ts">
// TemplatePanel：内容模板管理（第 4 Tab「档案」→ 子页「内容模板」）
// 两类模板：文字模板（内容结构，给 AI 写作要求）/ 图片模板（视觉版式，选骨架+调皮肤，实时 3:4 预览）
// 数据来自后端 content_templates 接口并落盘；本组件负责加载与增删改，默认值由后端在首次启动时回落。
import { computed, onMounted, reactive, ref } from 'vue'
import { toast } from '../store'
import {
  apiContentTemplates, apiDeleteTemplate, apiResetTemplateGroup,
  apiSaveImageTemplate, apiSaveTextTemplate,
} from '../api'
import type { ImageTemplate, TextTemplate } from '../api'

type FontStyle = 'serif' | 'sans' | 'round'

// ---------- 骨架（与 AiWizard 的 LAYOUTS 对齐） ----------
const SKELETONS = [
  { id: 'num', name: '大数字标题', desc: '顶部大序号 + 小标题' },
  { id: 'list', name: '清单条目', desc: '三条短句竖排' },
  { id: 'card', name: '卡片叠层', desc: '圆角卡片 + 重点加粗' },
  { id: 'compare', name: '左右对比', desc: '两栏参数对照' },
  { id: 'split', name: '上图下文', desc: '图片居顶 + 文字块' },
  { id: 'quote', name: '金句大字', desc: '居中大字 + 留白' },
]

// 皮肤预设色板（编辑器选色用；默认模板清单由后端提供）
const BG_PRESETS = ['#f7f2ec', '#efe6da', '#e7dccb', '#2f3a35', '#3a2e26']
const PRIMARY_PRESETS = ['#e22a43', '#2f7a55', '#a8743a', '#5a4636', '#2c3e50', '#f0e6d6']
const FONT_FAMILY: Record<FontStyle, string> = {
  serif: '"Songti SC","Noto Serif SC",serif',
  sans: '"PingFang SC","Noto Sans SC",sans-serif',
  round: '"Yuanti SC","PingFang SC",sans-serif',
}
function isLight(hex: string): boolean {
  const c = (hex || '').replace('#', '')
  if (c.length !== 6) return true
  const r = parseInt(c.slice(0, 2), 16), g = parseInt(c.slice(2, 4), 16), b = parseInt(c.slice(4, 6), 16)
  return (r * 299 + g * 587 + b * 114) / 1000 > 150
}

// 顶级分段：文字模板 / 图片模板
const view = ref<'text' | 'image'>('text')
const loading = ref(false)

// ---------- 数据（来自后端，首次启动后端回落默认） ----------
const textList = ref<TextTemplate[]>([])
const imageList = ref<ImageTemplate[]>([])

async function loadAll() {
  loading.value = true
  try {
    const d = await apiContentTemplates()
    textList.value = d.text
    imageList.value = d.image
  } catch (e) {
    toast(e instanceof Error ? e.message : '加载模板失败', 'error')
  } finally {
    loading.value = false
  }
}
onMounted(loadAll)

// ---------- 文字模板 ----------
const textOpen = ref(false)
const textForm = reactive<TextTemplate>({ id: '', name: '', desc: '', prompt: '', scene: '' })
function newText() {
  Object.assign(textForm, { id: '', name: '', desc: '', prompt: '', scene: '' })
  textOpen.value = true
}
function editText(t: TextTemplate) {
  Object.assign(textForm, t)
  textOpen.value = true
}
async function saveText() {
  if (!textForm.name.trim()) { toast('模板名称必填', 'error'); return }
  if (!textForm.prompt.trim()) { toast('请填写给 AI 的写作要求', 'error'); return }
  try {
    const { item } = await apiSaveTextTemplate({ ...textForm })
    const i = textList.value.findIndex(t => t.id === item.id)
    if (i >= 0) textList.value[i] = item
    else textList.value.unshift(item)
    textOpen.value = false
    toast('文字模板已保存')
  } catch (e) {
    toast(e instanceof Error ? e.message : '保存失败', 'error')
  }
}
async function removeText(id: string) {
  try {
    await apiDeleteTemplate('text', id)
    textList.value = textList.value.filter(t => t.id !== id)
    toast('已删除')
  } catch (e) {
    toast(e instanceof Error ? e.message : '删除失败', 'error')
  }
}
async function resetText() {
  try {
    const d = await apiResetTemplateGroup('text')
    textList.value = d.text
    toast('已恢复默认文字模板')
  } catch (e) {
    toast(e instanceof Error ? e.message : '恢复失败', 'error')
  }
}

// ---------- 图片模板状态 ----------
const imgOpen = ref(false)
const imgForm = reactive<ImageTemplate>({
  id: '', name: '', skeleton: 'num',
  skin: { bg: '#f7f2ec', primary: '#e22a43', font: 'sans', radius: 8, align: 'left', number: true, divider: false },
})
function blankSkin() {
  return { bg: '#f7f2ec', primary: '#e22a43', font: 'sans', radius: 8, align: 'left', number: true, divider: false }
}
function newImage() {
  Object.assign(imgForm, { id: '', name: '', skeleton: 'num', skin: blankSkin() })
  imgOpen.value = true
}
function editImage(t: ImageTemplate) {
  Object.assign(imgForm, { id: t.id, name: t.name, skeleton: t.skeleton, skin: { ...t.skin } })
  imgOpen.value = true
}
async function saveImage() {
  if (!imgForm.name.trim()) { toast('模板名称必填', 'error'); return }
  try {
    const { item } = await apiSaveImageTemplate({ ...imgForm, skin: { ...imgForm.skin } })
    const i = imageList.value.findIndex(t => t.id === item.id)
    if (i >= 0) imageList.value[i] = item
    else imageList.value.unshift(item)
    imgOpen.value = false
    toast('图片模板已保存')
  } catch (e) {
    toast(e instanceof Error ? e.message : '保存失败', 'error')
  }
}
async function removeImage(id: string) {
  try {
    await apiDeleteTemplate('image', id)
    imageList.value = imageList.value.filter(t => t.id !== id)
    toast('已删除')
  } catch (e) {
    toast(e instanceof Error ? e.message : '删除失败', 'error')
  }
}
async function resetImage() {
  try {
    const d = await apiResetTemplateGroup('image')
    imageList.value = d.image
    toast('已恢复默认图片模板')
  } catch (e) {
    toast(e instanceof Error ? e.message : '恢复失败', 'error')
  }
}

const skeletonName = (id: string) => SKELETONS.find(s => s.id === id)?.name || id

// 实时预览：把皮肤参数应用到 3:4 容器
const previewStyle = computed(() => ({
  background: imgForm.skin.bg,
  fontFamily: FONT_FAMILY[imgForm.skin.font],
  borderRadius: imgForm.skin.radius + 'px',
  textAlign: imgForm.skin.align,
  '--pv-primary': imgForm.skin.primary,
  '--pv-ink': isLight(imgForm.skin.bg) ? 'rgba(28,20,16,.78)' : 'rgba(240,230,214,.85)',
}))
// 列表卡片里的迷你预览（只读）
function miniStyle(t: ImageTemplate) {
  return {
    background: t.skin.bg, fontFamily: FONT_FAMILY[t.skin.font],
    borderRadius: t.skin.radius + 'px', textAlign: t.skin.align,
    '--pv-primary': t.skin.primary,
    '--pv-ink': isLight(t.skin.bg) ? 'rgba(28,20,16,.78)' : 'rgba(240,230,214,.85)',
  }
}
</script>

<template>
  <div class="tpl-page">
    <!-- 二级分段：文字模板 / 图片模板 -->
    <div class="tpl-subnav">
      <button :class="{ on: view === 'text' }" @click="view = 'text'">文字模板</button>
      <button :class="{ on: view === 'image' }" @click="view = 'image'">图片模板</button>
    </div>

    <!-- ============ 文字模板 ============ -->
    <div v-show="view === 'text'">
      <div class="tpl-toolbar">
        <span class="tpl-count">{{ textList.length }} 个文字模板</span>
        <div class="tpl-actions">
          <button class="btn sm" @click="resetText">恢复默认</button>
          <button class="btn primary sm" @click="newText">＋ 新建文字模板</button>
        </div>
      </div>

      <div v-if="loading" class="tpl-skel">
        <div v-for="n in 4" :key="n" class="skel-card"></div>
      </div>
      <div v-else-if="!textList.length" class="tpl-empty">
        <p>还没有文字模板，点右上角「新建」加一个，或恢复默认。</p>
      </div>
      <div v-else class="text-grid">
        <div v-for="t in textList" :key="t.id" class="text-card">
          <div class="tc-top">
            <b>{{ t.name }}</b>
            <span v-if="t.scene" class="tc-tag">{{ t.scene }}</span>
          </div>
          <p class="tc-desc">{{ t.desc }}</p>
          <p class="tc-prompt">{{ t.prompt }}</p>
          <div class="tc-foot">
            <a href="#" @click.prevent="editText(t)">编辑</a>
            <a href="#" class="danger-link" @click.prevent="removeText(t.id)">删除</a>
          </div>
        </div>
      </div>
    </div>

    <!-- ============ 图片模板 ============ -->
    <div v-show="view === 'image'">
      <div class="tpl-toolbar">
        <span class="tpl-count">{{ imageList.length }} 个图片模板</span>
        <div class="tpl-actions">
          <button class="btn sm" @click="resetImage">恢复默认</button>
          <button class="btn primary sm" @click="newImage">＋ 新建图片模板</button>
        </div>
      </div>

      <div v-if="loading" class="tpl-skel skel-6">
        <div v-for="n in 6" :key="n" class="skel-card"></div>
      </div>
      <div v-else-if="!imageList.length" class="tpl-empty">
        <p>还没有图片模板，点右上角「新建」加一个，或恢复默认。</p>
      </div>
      <div v-else class="img-grid">
        <div v-for="t in imageList" :key="t.id" class="img-card">
          <div class="mini" :style="miniStyle(t)">
            <b class="mini-title">示例标题</b>
            <span class="mini-copy">版式文字</span>
          </div>
          <div class="ic-meta">
            <b>{{ t.name }}</b>
            <span>{{ skeletonName(t.skeleton) }}</span>
          </div>
          <div class="ic-foot">
            <a href="#" @click.prevent="editImage(t)">编辑</a>
            <a href="#" class="danger-link" @click.prevent="removeImage(t.id)">删除</a>
          </div>
        </div>
      </div>
    </div>

    <!-- 文字模板编辑弹层 -->
    <div v-if="textOpen" class="tpl-mask" @click.self="textOpen = false">
      <div class="tpl-modal">
        <div class="tm-head">
          <b>{{ textForm.id ? '编辑文字模板' : '新建文字模板' }}</b>
          <button class="m-x" @click="textOpen = false">关闭</button>
        </div>
        <div class="tm-body">
          <label class="field"><span>模板名称 *</span><input v-model="textForm.name" placeholder="如：新手入门指南" /></label>
          <label class="field"><span>一句话说明</span><input v-model="textForm.desc" placeholder="选择卡片上显示的那句" /></label>
          <label class="field"><span>适用场景</span><input v-model="textForm.scene" placeholder="如：建立信任 / 新手（可选）" /></label>
          <label class="field">
            <span>给 AI 的写作要求 *</span>
            <textarea v-model="textForm.prompt" rows="6" placeholder="告诉 AI 按什么结构、什么语气写，这是真正生效的提示词"></textarea>
          </label>
        </div>
        <div class="tm-foot">
          <button class="btn sm" @click="textOpen = false">取消</button>
          <button class="btn primary sm" @click="saveText">保存</button>
        </div>
      </div>
    </div>

    <!-- 图片模板编辑弹层：左实时预览，右骨架+皮肤 -->
    <div v-if="imgOpen" class="tpl-mask" @click.self="imgOpen = false">
      <div class="img-modal">
        <div class="tm-head">
          <b>{{ imgForm.id ? '编辑图片模板' : '新建图片模板' }}</b>
          <button class="m-x" @click="imgOpen = false">关闭</button>
        </div>
        <div class="im-body">
          <!-- 左：3:4 实时预览 -->
          <div class="im-preview">
            <div class="pv" :style="previewStyle">
              <!-- num -->
              <div v-if="imgForm.skeleton === 'num'" class="pv-num">
                <span v-if="imgForm.skin.number" class="pv-big">01</span>
                <div class="pv-title">示例标题</div>
                <div class="pv-copy">排进图里的一句话说明</div>
              </div>
              <!-- list -->
              <div v-else-if="imgForm.skeleton === 'list'" class="pv-list">
                <div class="pv-title">示例标题</div>
                <p :class="{ lined: imgForm.skin.divider }">第一条短句</p>
                <p :class="{ lined: imgForm.skin.divider }">第二条短句</p>
                <p>第三条短句</p>
              </div>
              <!-- card -->
              <div v-else-if="imgForm.skeleton === 'card'" class="pv-cardwrap">
                <div class="pv-title">示例标题</div>
                <div class="pv-cardbox" :style="{ borderRadius: imgForm.skin.radius + 'px' }">
                  <b>重点文字</b><span>补充说明</span>
                </div>
              </div>
              <!-- compare -->
              <div v-else-if="imgForm.skeleton === 'compare'" class="pv-compare">
                <div class="pv-title">示例标题</div>
                <div class="pv-cols">
                  <div><b>A 款</b><p>参数一</p><p>参数二</p></div>
                  <div><b>B 款</b><p>参数一</p><p>参数二</p></div>
                </div>
              </div>
              <!-- split -->
              <div v-else-if="imgForm.skeleton === 'split'" class="pv-split">
                <div class="pv-photo"></div>
                <div class="pv-title">示例标题</div>
                <p>图片下方的文字说明</p>
              </div>
              <!-- quote -->
              <div v-else class="pv-quote">
                <div class="pv-bigquote">一句话金句</div>
              </div>
            </div>
          </div>

          <!-- 右：参数 -->
          <div class="im-form">
            <label class="field"><span>模板名称 *</span><input v-model="imgForm.name" placeholder="如：暖白避坑内页" /></label>

            <div class="im-label">① 选骨架</div>
            <div class="skel-grid">
              <button
                v-for="s in SKELETONS" :key="s.id"
                class="skel-btn" :class="{ on: imgForm.skeleton === s.id }"
                :title="s.desc" @click="imgForm.skeleton = s.id"
              >{{ s.name }}</button>
            </div>

            <div class="im-label">② 调皮肤</div>

            <div class="skin-row">
              <span class="skin-k">背景色</span>
              <div class="swatches">
                <button
                  v-for="c in BG_PRESETS" :key="c" class="sw" :class="{ on: imgForm.skin.bg === c }"
                  :style="{ background: c }" @click="imgForm.skin.bg = c"
                ></button>
                <input type="color" v-model="imgForm.skin.bg" class="color-pick" title="自定义背景色" />
              </div>
            </div>

            <div class="skin-row">
              <span class="skin-k">主色</span>
              <div class="swatches">
                <button
                  v-for="c in PRIMARY_PRESETS" :key="c" class="sw" :class="{ on: imgForm.skin.primary === c }"
                  :style="{ background: c }" @click="imgForm.skin.primary = c"
                ></button>
                <input type="color" v-model="imgForm.skin.primary" class="color-pick" title="自定义主色" />
              </div>
            </div>

            <div class="skin-row">
              <span class="skin-k">字体</span>
              <div class="seg3">
                <button :class="{ on: imgForm.skin.font === 'serif' }" @click="imgForm.skin.font = 'serif'">衬线</button>
                <button :class="{ on: imgForm.skin.font === 'sans' }" @click="imgForm.skin.font = 'sans'">黑体</button>
                <button :class="{ on: imgForm.skin.font === 'round' }" @click="imgForm.skin.font = 'round'">圆体</button>
              </div>
            </div>

            <div class="skin-row">
              <span class="skin-k">圆角</span>
              <input type="range" min="0" max="28" v-model.number="imgForm.skin.radius" class="range" />
              <span class="num-val">{{ imgForm.skin.radius }}px</span>
            </div>

            <div class="skin-row">
              <span class="skin-k">对齐</span>
              <div class="seg3">
                <button :class="{ on: imgForm.skin.align === 'left' }" @click="imgForm.skin.align = 'left'">左对齐</button>
                <button :class="{ on: imgForm.skin.align === 'center' }" @click="imgForm.skin.align = 'center'">居中</button>
              </div>
            </div>

            <div class="skin-row">
              <span class="skin-k">装饰</span>
              <div class="checks">
                <label class="chk"><input type="checkbox" v-model="imgForm.skin.number" /> 显示序号</label>
                <label class="chk"><input type="checkbox" v-model="imgForm.skin.divider" /> 分隔线</label>
              </div>
            </div>
          </div>
        </div>
        <div class="tm-foot">
          <button class="btn sm" @click="imgOpen = false">取消</button>
          <button class="btn primary sm" @click="saveImage">保存模板</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tpl-page { min-width: 0; }

/* 二级分段（文字/图片），与档案子页 segmented 同款 */
.tpl-subnav {
  display: inline-flex; gap: 2px;
  background: var(--panel-2); border: 1px solid var(--line);
  border-radius: var(--r-ctl); padding: 3px; margin-bottom: 16px;
}
.tpl-subnav button {
  border: 0; background: transparent; cursor: pointer;
  font-size: 13px; font-weight: 600; color: var(--ink-3);
  padding: 6px 18px; border-radius: 4px;
  transition: background .15s, color .15s, box-shadow .15s;
}
.tpl-subnav button:hover { color: var(--ink); }
.tpl-subnav button:focus-visible { outline: 2px solid var(--ring); outline-offset: 1px; }
.tpl-subnav button.on { background: var(--panel); color: var(--ink); box-shadow: 0 1px 3px rgba(43,30,24,.12); }

.tpl-skel { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.tpl-skel.skel-6 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.skel-card {
  height: 150px; border-radius: var(--r-card);
  background: linear-gradient(100deg, var(--panel-2) 40%, rgba(255,255,255,.7) 50%, var(--panel-2) 60%);
  background-size: 200% 100%; animation: tpl-shimmer 1.3s ease-in-out infinite;
}
.tpl-skel.skel-6 .skel-card { height: 230px; }
@keyframes tpl-shimmer { from { background-position: 120% 0; } to { background-position: -120% 0; } }
.tpl-empty { border: 1px dashed var(--line); border-radius: var(--r-card); padding: 40px 20px; text-align: center; }
.tpl-empty p { margin: 0; font-size: 13px; color: var(--ink-3); }
@media (prefers-reduced-motion: reduce) { .skel-card { animation: none; } }

.tpl-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
.tpl-count { font-size: 12px; color: var(--ink-3); font-variant-numeric: tabular-nums; }
.tpl-actions { display: flex; gap: 8px; }

/* 文字模板卡片 */
.text-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.text-card {
  border: 1px solid var(--line); border-radius: var(--r-card);
  padding: 14px 15px; background: var(--panel);
  display: flex; flex-direction: column;
}
.tc-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.tc-top b { font-size: 14px; color: var(--ink); }
.tc-tag {
  font-size: 11px; color: var(--ink-3); background: var(--panel-2);
  padding: 2px 9px; border-radius: var(--r-pill);
}
.tc-desc { margin: 8px 0 0; font-size: 12px; color: var(--ink-2); }
.tc-prompt {
  margin: 8px 0 0; font-size: 12px; line-height: 1.7; color: var(--ink-3);
  flex: 1; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden;
}
.tc-foot { display: flex; gap: 14px; margin-top: 12px; }
.tc-foot a { font-size: 12px; color: var(--ink-2); text-decoration: none; }
.tc-foot a:hover { color: var(--red); }
.danger-link:hover { color: var(--danger) !important; }

/* 图片模板卡片 */
.img-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.img-card {
  border: 1px solid var(--line); border-radius: var(--r-card);
  padding: 10px; background: var(--panel);
}
.mini {
  aspect-ratio: 3 / 4; border-radius: 6px; padding: 14px;
  display: flex; flex-direction: column; justify-content: center; gap: 6px;
  border: 1px solid var(--line); overflow: hidden;
}
.mini-title { font-size: 13px; color: var(--pv-primary); }
.mini-copy { font-size: 10px; color: var(--pv-ink); }
.ic-meta { display: flex; flex-direction: column; gap: 2px; margin-top: 9px; }
.ic-meta b { font-size: 13px; color: var(--ink); }
.ic-meta span { font-size: 11px; color: var(--ink-3); }
.ic-foot { display: flex; gap: 14px; margin-top: 8px; }
.ic-foot a { font-size: 12px; color: var(--ink-2); text-decoration: none; }
.ic-foot a:hover { color: var(--red); }

/* 弹层通用 */
.tpl-mask {
  position: fixed; inset: 0; background: rgba(28,20,16,.42);
  display: flex; align-items: center; justify-content: center; z-index: 60;
}
.tpl-modal {
  width: 560px; max-width: calc(100vw - 48px);
  background: var(--panel); border-radius: 10px;
  display: flex; flex-direction: column; max-height: 88vh; overflow: hidden;
}
.img-modal {
  width: 880px; max-width: calc(100vw - 40px);
  background: var(--panel); border-radius: 10px;
  display: flex; flex-direction: column; max-height: 92vh; overflow: hidden;
}
.tm-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 15px 18px; border-bottom: 1px solid var(--line);
}
.tm-head b { font-size: 15px; color: var(--ink); }
.m-x { border: 0; background: transparent; cursor: pointer; font-size: 13px; color: var(--ink-3); }
.m-x:hover { color: var(--ink); }
.tm-body { padding: 16px 18px; overflow-y: auto; display: flex; flex-direction: column; gap: 13px; }
.tm-foot {
  display: flex; justify-content: flex-end; gap: 8px;
  padding: 13px 18px; border-top: 1px solid var(--line);
}
.field { display: flex; flex-direction: column; gap: 6px; }
.field span { font-size: 12px; font-weight: 600; color: var(--ink-2); }
.field input, .field textarea {
  font-size: 13px; padding: 8px 10px; resize: none;
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--r-ctl);
}
.field input:focus, .field textarea:focus { border-color: var(--red); outline: 2px solid var(--ring); }

/* 图片弹层左右布局 */
.im-body { display: flex; gap: 20px; padding: 18px; overflow-y: auto; }
.im-preview { flex: 0 0 280px; }
.pv {
  aspect-ratio: 3 / 4; width: 280px; padding: 22px;
  border: 1px solid var(--line); overflow: hidden;
}
.pv-title { font-size: 18px; font-weight: 700; color: var(--pv-primary); line-height: 1.3; }
.pv-copy, .pv-list p, .pv-split p, .pv-cols p { font-size: 12px; color: var(--pv-ink); margin: 8px 0 0; line-height: 1.6; }
.pv-big { font-size: 40px; font-weight: 800; color: var(--pv-primary); display: block; margin-bottom: 8px; }
.pv-list p.lined { padding-bottom: 8px; border-bottom: 1px solid var(--pv-ink); }
.pv-cardbox {
  margin-top: 12px; padding: 14px; border: 1px solid var(--pv-primary);
  display: flex; flex-direction: column; gap: 4px;
}
.pv-cardbox b { font-size: 14px; color: var(--pv-primary); }
.pv-cardbox span { font-size: 11px; color: var(--pv-ink); }
.pv-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; text-align: left; }
.pv-cols b { font-size: 13px; color: var(--pv-primary); }
.pv-photo { height: 45%; background: rgba(128,110,95,.22); border-radius: 6px; margin-bottom: 12px; }
.pv-quote { height: 100%; display: flex; align-items: center; justify-content: center; }
.pv-bigquote { font-size: 26px; font-weight: 700; color: var(--pv-primary); line-height: 1.5; }

.im-form { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.im-label { font-size: 12px; font-weight: 700; color: var(--ink); }
.skel-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
.skel-btn {
  border: 1px solid var(--line); background: var(--panel); cursor: pointer;
  font-size: 12px; color: var(--ink-2); padding: 8px 4px; border-radius: var(--r-ctl);
}
.skel-btn:hover { border-color: var(--red); }
.skel-btn.on { border-color: var(--red); background: var(--red-soft); color: var(--red); font-weight: 700; }

.skin-row { display: flex; align-items: center; gap: 10px; }
.skin-k { flex: 0 0 48px; font-size: 12px; color: var(--ink-2); }
.swatches { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.sw {
  width: 22px; height: 22px; border-radius: 4px; border: 1px solid var(--line);
  cursor: pointer; padding: 0;
}
.sw.on { box-shadow: 0 0 0 2px var(--panel), 0 0 0 4px var(--red); }
.color-pick { width: 26px; height: 26px; border: 1px solid var(--line); border-radius: 4px; padding: 0; background: none; cursor: pointer; }
.seg3 { display: inline-flex; gap: 2px; background: var(--panel-2); border-radius: var(--r-ctl); padding: 2px; }
.seg3 button {
  border: 0; background: transparent; cursor: pointer;
  font-size: 12px; color: var(--ink-3); padding: 5px 12px; border-radius: 4px;
}
.seg3 button.on { background: var(--panel); color: var(--ink); box-shadow: 0 1px 2px rgba(43,30,24,.12); }
.range { flex: 1; accent-color: var(--red); }
.num-val { font-size: 12px; color: var(--ink-3); min-width: 38px; font-variant-numeric: tabular-nums; }
.checks { display: flex; gap: 16px; }
.chk { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; color: var(--ink-2); cursor: pointer; }
.chk input { accent-color: var(--red); }

@media (max-width: 760px) {
  .im-body { flex-direction: column; }
  .im-preview { flex: none; align-self: center; }
  .img-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
