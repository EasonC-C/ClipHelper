// ClipHelper · 标签页上限清理
// 复制商品多了，专用浏览器会堆积标签页导致卡顿 / 占内存。
// 超过 MAX_TABS 条时自动关闭最早的标签页（保留当前正在看的活动页）。
const MAX_TABS = 3;

function trimTabs() {
  chrome.tabs.query({}, (tabs) => {
    if (!tabs || tabs.length <= MAX_TABS) return;
    // 按窗口分组：每个窗口独立清理，互不影响
    const byWindow = new Map();
    for (const tab of tabs) {
      if (!byWindow.has(tab.windowId)) byWindow.set(tab.windowId, []);
      byWindow.get(tab.windowId).push(tab);
    }
    for (const list of byWindow.values()) {
      // tab.id 单调递增：id 越小，打开的越早
      list.sort((a, b) => a.id - b.id);
      const excess = list.length - MAX_TABS;
      if (excess <= 0) continue;
      const activeTab = list.find((t) => t.active);
      const toClose = [];
      for (const tab of list) {
        if (toClose.length >= excess) break;
        if (tab === activeTab) continue; // 不关当前正在看的页
        toClose.push(tab.id);
      }
      if (toClose.length) chrome.tabs.remove(toClose);
    }
  });
}

chrome.tabs.onCreated.addListener(trimTabs);
chrome.runtime.onStartup.addListener(trimTabs);
