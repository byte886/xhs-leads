const PIN_HOSTS = ["127.0.0.1:8791", "www.xiaohongshu.com"];

function hostOf(url) {
  try { return new URL(url || "").host; } catch (e) { return ""; }
}

function maybePin(tab) {
  try {
    if (tab && PIN_HOSTS.includes(hostOf(tab.url)) && !tab.pinned) {
      chrome.tabs.update(tab.id, { pinned: true });
      console.log("pin ->", tab.id, hostOf(tab.url));
    }
  } catch (e) {}
}

// 取消固定兜底：标签条右键「取消固定」后自动重新固定
chrome.tabs.onUpdated.addListener((id, info, tab) => {
  if (info.pinned === false && tab && PIN_HOSTS.includes(hostOf(tab.url))) {
    chrome.tabs.update(id, { pinned: true });
    console.log("re-pin ->", id);
  }
  maybePin(tab);
});

// 回收「+」等产生的空白标签：about:blank 停留 2s 未导航即关闭。
// 后台任务页（new_page）会立即 goto 白名单 URL，不受影响；
// 用户主动导航到其他真实站点则保留（不干预正常浏览）。
chrome.tabs.onCreated.addListener((t) => {
  maybePin(t);
  const u = t.pendingUrl || t.url || "";
  if (!PIN_HOSTS.some((h) => u.includes(h))) {
    setTimeout(async () => {
      try {
        const tab = await chrome.tabs.get(t.id);
        if (!tab || !tab.url) return;
        const h = hostOf(tab.url);
        if (h && !PIN_HOSTS.includes(h)) return;  // 已导航到真实站点 → 保留
        chrome.tabs.remove(t.id);                 // 仍空白 → 回收
        console.log("recycle blank tab ->", t.id);
      } catch (e) {}
    }, 2000);
  }
});

// SW 启动兜底：补 pin 已存在的白名单标签
chrome.tabs.query({}).then((tabs) => tabs.forEach((t) => maybePin(t)));
console.log("xhs-leads pin extension loaded");
