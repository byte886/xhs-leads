<script setup lang="ts">
/**
 * 设置 › 账号与登录：多账号管理（列表 / 备注 / 扫码登录 / 开窗收窗 / 新增删除）。
 *
 * 登录态不在这里展示：左窗不弹登录框，需要登录由采集/发布撞墙时在右侧调试窗开页；
 * 账号卡片显示 @小红书号 即已登录，没有就是未登录。
 *
 * 注：设置页左侧该分类当前置灰（on: false），本组件只在被打开时才会挂载并发请求。
 */
import { onMounted, ref } from 'vue'
import { toast } from '../../store'
import {
  apiMirrorOpen, apiMirrorClose,
  apiListAccounts, apiAddAccount, apiDeleteAccount, apiRenameAccount,
  apiRefreshIdentity, apiScanLogin,
} from '../../api'
import type { AccountItem } from '../../types'

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

onMounted(loadAccounts)
</script>

<template>
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
</template>

<style scoped>
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
</style>
