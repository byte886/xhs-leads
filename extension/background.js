// 采集窗口分组：把标题为「XHS Leads · 采集调试窗口」的标签页收进原生标签组。
//
// 为什么靠标题识别：采集窗的标题由 Python 侧 init script 恒定守护
// （injections._COLLECT_DEBUG_TITLE_JS 拦截 document.title setter），是本进程里
// 唯一稳定、可跨导航存活的标识。扩展不读页面内容、不发网络请求。
//
// 生命周期：组由 Chrome 自己维护——组内标签全部关掉后组自动消失，不需要回收逻辑。
// 「存在的窗口必须有用」由 Python 侧保证（采集窗只留一个任务标签、关多余标签）。

const GROUP_TITLE = '采集窗口';
const GROUP_COLOR = 'blue'; // Chrome 8 色之一：grey/blue/red/yellow/green/pink/purple/cyan
const COLLECT_TITLE = 'XHS Leads · 采集调试窗口';

function isCollectTab(tab) {
  return !!tab && typeof tab.title === 'string' && tab.title.startsWith(COLLECT_TITLE);
}

async function ensureGroup(tabIds, windowId) {
  if (!tabIds.length) return;
  const existing = await chrome.tabGroups.query({ title: GROUP_TITLE });
  const sameWindow = existing.find((g) => g.windowId === windowId);
  const gid = await chrome.tabs.group({
    tabIds,
    ...(sameWindow ? { groupId: sameWindow.id } : { createProperties: { windowId } }),
  });
  await chrome.tabGroups.update(gid, {
    title: GROUP_TITLE,
    color: GROUP_COLOR,
    collapsed: false,
  });
}

// 单个标签被创建/更新（含标题变化、跨导航）时归组。
chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  if (!info.title && !info.status) return;
  if (!isCollectTab(tab)) return;
  try {
    await ensureGroup([tabId], tab.windowId);
  } catch (e) {
    console.warn('[collect-group] group failed:', e && e.message);
  }
});

// 扩展被安装/浏览器启动/扩展被唤醒时补扫已有标签（已分组的不重复建组）。
async function sweep() {
  try {
    const tabs = (await chrome.tabs.query({})).filter(isCollectTab);
    if (!tabs.length) return;
    const byWindow = new Map();
    for (const t of tabs) {
      if (!byWindow.has(t.windowId)) byWindow.set(t.windowId, []);
      byWindow.get(t.windowId).push(t.id);
    }
    for (const [windowId, ids] of byWindow) await ensureGroup(ids, windowId);
  } catch (e) {
    console.warn('[collect-group] sweep failed:', e && e.message);
  }
}

chrome.runtime.onInstalled.addListener(sweep);
chrome.runtime.onStartup.addListener(sweep);
sweep();
