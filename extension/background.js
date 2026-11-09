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

// ---------------------------------------------------------------------------
// 原子化建采集窗（R-6.86，用户口径：右边窗口一出现就已在目标位置、且已在
// 「采集窗口」组里，不要「先出现在左边再被挪走」的过渡）。
//
// 为什么必须由扩展来做：`--new-window` 由已运行的 Chrome 实例执行，它只把 URL
// 交给实例，命令行里的 --window-position/--window-size 被丢弃，窗口先落在实例
// 自己的默认位置（左上角），Python 侧只能事后 setWindowBounds 搬走——这就是用户
// 看到的「左半屏先闪一下」。chrome.windows.create 是唯一能在**创建时**给定
// left/top/width/height 的 API，且只有扩展能调。
//
// 调用方：APP 页（http://127.0.0.1:8791）经 externally_connectable 用
// chrome.runtime.sendMessage(<扩展ID>, {type: CREATE_WINDOW, ...}) 触发。
// 顺序刻意是「建空白窗 → 立刻分组命名 → 再导航」，这样窗口出现的那一帧就已经
// 在正确位置、带着组标题，小红书页面最后才加载。
const CREATE_WINDOW = 'xhsCreateCollectWindow';
const PING = 'xhsPing';

async function createCollectWindow(req) {
  const { url, left, top, width, height } = req || {};
  const win = await chrome.windows.create({
    url: 'about:blank',
    type: 'normal',
    width: Math.round(width),
    height: Math.round(height),
    focused: true,
  });
  const tabId = win.tabs && win.tabs[0] && win.tabs[0].id;
  if (!tabId) throw new Error('新窗口里没有拿到标签');
  // macOS 上 windows.create 的 left/top 会被忽略（实测创建后仍在 (0,25)）；而紧邻的
  // windows.update 虽然立刻生效，但窗口**首次显示**时 Chrome 还会套用它记住的位置，
  // 把刚才的 update 盖回去（实测 create→update 返回 960，1s 后又回到 0,25）。
  // 所以：立即校正一次，等窗口稳定后再校正一次。两次都在扩展 SW 内，间隔约 120ms，
  // 与 Python 侧跨进程 CDP 搬窗（约 700ms，用户能看见「先闪在左边」）不是一个量级。
  const L = Math.round(left);
  const T = Math.round(top);
  const W = Math.round(width);
  const H = Math.round(height);
  await chrome.windows.update(win.id, { left: L, top: T, width: W, height: H });
  await new Promise((r) => setTimeout(r, 120));
  await chrome.windows.update(win.id, { left: L, top: T, width: W, height: H });
  const gid = await chrome.tabs.group({
    tabIds: [tabId],
    createProperties: { windowId: win.id },
  });
  await chrome.tabGroups.update(gid, {
    title: GROUP_TITLE,
    color: GROUP_COLOR,
    collapsed: false,
  });
  // 组标题就位之后才导航：用户看到窗口的那一刻，位置、组、标题都已经对了，只剩页面在加载。
  if (typeof url === 'string' && url) {
    await chrome.tabs.update(tabId, { url });
  }
  return { ok: true, windowId: win.id, tabId, groupId: gid };
}

chrome.runtime.onMessageExternal.addListener((msg, sender, sendResponse) => {
  if (!msg) return undefined;
  // 连通性探测：调用方先用它确认扩展在线，再走建窗，避免扩展没加载时干等页面超时。
  // 必须异步回（同步 sendResponse 时外部消息通道实测会报
  // "The message port closed before a response was received"）。
  if (msg.type === PING) {
    setTimeout(() => sendResponse({ ok: true, id: chrome.runtime.id }), 0);
    return true;
  }
  if (msg.type !== CREATE_WINDOW) return undefined;
  createCollectWindow(msg)
    .then(sendResponse)
    .catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
  return true; // 异步响应：保持消息通道直到 sendResponse
});

