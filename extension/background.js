// 采集窗口分组：把采集窗的任务标签收进原生标签组「采集窗口」。
//
// 为什么按 URL 识别而不是标题：标题是页面的东西，Python 侧两份 title 守护
// （采集调试窗口 / 账号运营窗口）会互相覆盖，实测开始采集后标题变成
// 「普通人 (@5546627342) · 运营窗口」，按标题前缀判定直接失配，标签进不了组，
// 原组内标签一关组就解散。URL 由导航决定、守护脚本改不了，是可靠判据。
//
// 触发口径（用户拍板）：**只在开始采集这一刻检查一次**——有组就复用、没组就建。
// 不做通用守护：不监听切标签、不在安装/启动时扫全量、不按定时器巡检。
// 「开始采集」在浏览器侧唯一可见的信号，就是采集窗的任务标签开始导航（onUpdated
// 的 loading + url），所以只挂这一个入口；其余时刻一律不主动检查。
//
// 生命周期：组由 Chrome 自己维护——组内标签全部关掉后组自动消失，不需要回收逻辑。

const GROUP_TITLE = '采集窗口';
const GROUP_COLOR = 'blue'; // Chrome 8 色之一：grey/blue/red/yellow/green/pink/purple/cyan
const XHS_HOST = 'xiaohongshu.com';

function isCollectTab(tab) {
  return !!tab && typeof tab.url === 'string' && tab.url.includes(XHS_HOST);
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

// 一次检查：把窗口里的采集标签补齐进组，缺组就建组；已对齐则什么都不做。
async function checkWindow(windowId) {
  const tabs = (await chrome.tabs.query({ windowId })).filter(isCollectTab);
  if (!tabs.length) return;
  const sameWindow = (await chrome.tabGroups.query({ title: GROUP_TITLE }))
    .find((g) => g.windowId === windowId);
  const inside = sameWindow ? tabs.filter((t) => t.groupId === sameWindow.id) : [];
  if (sameWindow && inside.length === tabs.length) return; // 已对齐，不动
  await ensureGroup(tabs.map((t) => t.id), windowId);
}

// 采集启动 = 采集窗标签开始一次新导航。只在这里检查，别处不做通用守护。
chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (info.status !== 'loading' || !info.url) return; // 只要新导航，忽略标题/图标刷新
  if (!isCollectTab(tab)) return;
  checkWindow(tab.windowId).catch((e) => {
    console.warn('[collect-group] check failed:', e && e.message);
  });
});
