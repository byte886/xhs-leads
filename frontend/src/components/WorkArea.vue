<script setup lang="ts">
import { ref } from 'vue'
import { appState } from '../store'
import type { Note, Author } from '../types'
import { apiAuthors } from '../api'
import CardGrid from './CardGrid.vue'

const emit = defineEmits<{
  (e: 'menu', payload: { note: Note; x: number; y: number }): void
  (e: 'clone', note: Note): void
  (e: 'clients', note: Note): void
}>()

const filterText = ref('')
// 采集页内子切换（v1.5）：笔记 / 作者榜（P2）
const view = ref<'notes' | 'authors'>('notes')
const authors = ref<Author[]>([])
const authorsLoaded = ref(false)

function fmtCount(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`
  return String(n)
}

function fmtPub(ts: number | null): string {
  if (ts == null) return '—'
  const d = new Date(ts > 1e12 ? ts : ts * 1000)
  if (Number.isNaN(d.getTime())) return '—'
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

async function showAuthors() {
  view.value = 'authors'
  if (!authorsLoaded.value) {
    try {
      const res = await apiAuthors()
      authors.value = res.authors || []
      authorsLoaded.value = true
    } catch {
      authors.value = []
    }
  }
}

function showNotes() {
  view.value = 'notes'
}

/** 点作者：切回笔记视图并按昵称过滤，只看该作者的笔记 */
function pickAuthor(a: Author) {
  view.value = 'notes'
  filterText.value = a.nickname || a.user_id
}
</script>

<template>
  <div>
    <!-- 待输入 -->
    <div v-if="appState.stage === 'input'" class="empty">
      <div class="big">待输入</div>
      <p>在左侧填写关键词并设置参数，点击「开始采集」</p>
      <p class="sub">（视频示例："比如说关键词输入一个减肥"）</p>
    </div>

    <!-- 采集作品 -->
    <div v-else-if="appState.stage === 'search'">
      <div class="info-bar">
        <div class="info-item">
          <span class="k">共获取</span><span class="v">{{ appState.total || '—' }}</span>
        </div>
        <div class="info-item">
          <span class="k">当前任务</span><span class="v small">{{ appState.taskLabel || '—' }}</span>
        </div>
        <div class="info-item">
          <span class="k">状态</span>
          <span class="status-pill" :class="{ running: appState.status === '采集中', done: appState.status === '展示中' }">{{ appState.status }}</span>
        </div>
        <div class="info-item">
          <span class="k">已完成</span><span class="v">{{ appState.notes.length }}</span>
          <span class="v small">{{ appState.elapsed }}s</span>
        </div>
      </div>
      <CardGrid :notes="appState.notes" @menu="emit('menu', $event)" />
      <div v-if="appState.searchNote" style="margin-top:12px;font-size:12px;color:var(--ink-2);background:#FFF7E6;border-radius:8px;padding:8px 12px">
        {{ appState.searchNote }}
      </div>
    </div>

    <!-- 选择内容（一页式：评分徽标卡片 + 筛选 + 勾选 + 单条克隆；P2 作者榜在采集页内子切换） -->
    <div v-else-if="appState.stage === 'pick'">
      <div class="info-bar">
        <div class="info-item">
          <span class="k">评分模型</span><span class="v small">互动密度 × 内容结构 × 爆款潜力（卡片右上角分数）</span>
        </div>
        <div class="info-item">
          <span class="k">操作提示</span><span class="v small">点击卡片勾选；卡片「一键克隆」进创作页，发布手动确认</span>
        </div>
      </div>
      <div style="display:flex;gap:10px;align-items:center;margin-bottom:10px">
        <div class="view-switch">
          <button class="vs-btn" :class="{ on: view === 'notes' }" @click="showNotes">笔记 {{ appState.notes.length }}</button>
          <button class="vs-btn" :class="{ on: view === 'authors' }" @click="showAuthors">作者榜</button>
        </div>
        <input v-model="filterText" placeholder="请选标题,作者或UID" style="flex:1;min-width:0" />
      </div>

      <!-- 作者榜（P2）：从当前采集集按作者聚合，低粉爆款多 × 均分高 = 值得模仿 -->
      <div v-if="view === 'authors'" class="author-grid">
        <div v-if="!authors.length" class="empty" style="padding:40px 0">
          <div class="big">暂无作者</div>
          <p>先采集笔记，作者榜会按博主聚合展示（低粉爆款越多的博主越值得模仿）</p>
        </div>
        <div v-for="a in authors" :key="a.user_id" class="author-card" @click="pickAuthor(a)">
          <div class="au-top">
            <img v-if="a.avatar" :src="a.avatar" alt="" class="au-avatar" referrerpolicy="no-referrer" />
            <div v-else class="au-avatar au-ph">{{ (a.nickname || '博')[0] }}</div>
            <div class="au-name">
              <div class="au-nick">{{ a.nickname || '未知作者' }}</div>
              <div class="au-sub">{{ fmtCount(a.fans_count) }} 粉丝</div>
            </div>
            <span v-if="a.hot_count > 0" class="au-hot">🔥{{ a.hot_count }} 低粉爆款</span>
          </div>
          <div class="au-stats">
            <div class="au-stat"><span class="k">收录</span><b>{{ a.note_count }}</b><span class="unit">篇</span></div>
            <div class="au-stat"><span class="k">均分</span><b>{{ a.avg_score ?? '—' }}</b></div>
            <div class="au-stat"><span class="k">最高</span><b>{{ a.max_score ?? '—' }}</b></div>
            <div class="au-stat"><span class="k">总赞</span><b>{{ fmtCount(a.total_liked) }}</b></div>
            <div class="au-stat"><span class="k">最新</span><b class="small">{{ fmtPub(a.last_publish) }}</b></div>
          </div>
          <div class="au-foot">点击查看该作者全部笔记 →</div>
        </div>
      </div>

      <!-- 笔记视图 -->
      <CardGrid v-else :notes="appState.notes" selectable :filter-text="filterText" @menu="emit('menu', $event)" @clone="emit('clone', $event)" @clients="emit('clients', $event)" />
    </div>
  </div>
</template>

<style scoped>
.view-switch {
  display: inline-flex;
  background: var(--bg-2, #f2f3f5);
  border-radius: 9px;
  padding: 3px;
  gap: 3px;
}
.vs-btn {
  border: 0;
  background: transparent;
  color: var(--ink-2, #888);
  font-size: 13px;
  padding: 6px 14px;
  border-radius: 7px;
  cursor: pointer;
  transition: all 0.15s;
}
.vs-btn.on {
  background: #fff;
  color: var(--ink-1, #111);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.1);
  font-weight: 600;
}
.author-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
}
.author-card {
  background: var(--bg-card, #fff);
  border: 1px solid var(--line, #ececec);
  border-radius: 14px;
  padding: 14px;
  cursor: pointer;
  transition: box-shadow 0.15s, border-color 0.15s;
}
.author-card:hover {
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.08);
  border-color: #e0b36b;
}
.au-top {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.au-avatar {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  object-fit: cover;
  flex-shrink: 0;
}
.au-ph {
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #ff6a3d, #ffb03d);
  color: #fff;
  font-weight: 700;
  font-size: 18px;
}
.au-nick {
  font-weight: 600;
  font-size: 15px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.au-sub {
  font-size: 12px;
  color: var(--ink-2, #888);
  margin-top: 2px;
}
.au-hot {
  margin-left: auto;
  flex-shrink: 0;
  background: #fff1e6;
  color: #d24a2d;
  font-size: 12px;
  font-weight: 600;
  padding: 4px 8px;
  border-radius: 8px;
}
.au-stats {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 6px;
  background: var(--bg-2, #f7f7f8);
  border-radius: 10px;
  padding: 10px 8px;
}
.au-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}
.au-stat .k {
  font-size: 11px;
  color: var(--ink-2, #999);
}
.au-stat b {
  font-size: 15px;
}
.au-stat b.small {
  font-size: 12px;
}
.au-stat .unit {
  font-size: 11px;
  color: var(--ink-2, #999);
}
.au-foot {
  margin-top: 10px;
  font-size: 12px;
  color: #b08a4d;
  text-align: right;
}
</style>
