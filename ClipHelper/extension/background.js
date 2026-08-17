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

// ── 店名代发 ─────────────────────────────────────────────
// content script 在页面上下文 fetch 127.0.0.1 会被 Chrome Private Network Access
// 预检拦截；扩展后台持有 host_permissions(["http://127.0.0.1/*"])，可正常请求，
// 故由这里代发店名到本机 ClipHelper 的 HTTP 服务。

async function postShop(shop, url) {
  const payload = JSON.stringify({ shop: shop, url: url || "" });
  const ports = Array.from({ length: 10 }, (_, i) => 8765 + i);
  for (const port of ports) {
    try {
      const r = await fetch("http://127.0.0.1:" + port + "/api/shop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload,
      });
      if (r.ok) return { ok: true, port };
    } catch (_) {
      /* try next port */
    }
  }
  return { ok: false };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.type !== "shop" || !msg.shop) {
    return;
  }
  postShop(String(msg.shop), msg.url || "").then(sendResponse);
  return true; // 异步 sendResponse
});
