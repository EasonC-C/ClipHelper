// ClipHelper · 店名提取 v1.2.0
// 在抖音商品落地页提取店铺名，经 background 回传到本机 ClipHelper
// （v1.2.0：不再在页面里直接 fetch 127.0.0.1，避免 Chrome Private Network Access 预检拦截）
// 右下角红点=已注入；顶部横幅=提取/发送状态（肉眼可见，不依赖控制台）
(() => {
  const HREF = location.href;
  window.__shopHelperLoaded = true;
  console.log("[ClipHelper] 脚本已注入", HREF);

  // 创建并挂载一个页面元素
  const add = (css, title) => {
    const el = document.createElement("div");
    el.style.cssText = css;
    if (title) el.title = title;
    (document.body || document.documentElement).appendChild(el);
    return el;
  };
  // 右下角红点：肉眼确认扩展已注入
  try {
    add("position:fixed;right:6px;bottom:6px;width:10px;height:10px;border-radius:50%;" +
        "background:#f5222d;z-index:2147483647", "ClipHelper 已注入");
  } catch (e) {}
  // 顶部横幅：显示提取/发送结果
  function banner(text, color) {
    try {
      const b = add("position:fixed;top:0;left:0;right:0;z-index:2147483646;" +
        "background:" + (color || "#059669") + ";color:#fff;font:600 13px/1.5 sans-serif;" +
        "padding:6px 12px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.2)");
      b.textContent = text;
      setTimeout(() => b.remove(), 5000);
    } catch (e) {}
  }

  // 店名关键词；过滤"打开抖音商城"等按钮文案
  const KWS = ["旗舰店", "官方店", "专卖店", "自营店", "企业店", "专营店", "小店", "商城"];
  const BAD = /^(打开|进入|查看|前往|去|逛逛|立即|马上|点击|点开|移步|访问|关注|收藏)|^抖音商城$/;
  const findShop = text => {
    if (!text) return null;
    for (const kw of KWS) {
      const m = text.match(new RegExp("[\\u4e00-\\u9fa5A-Za-z0-9]{1,40}?" + kw));
      if (m && !BAD.test(m[0])) return m[0];
    }
    return null;
  };

  // 店名元素选择器（抖店实测结构 shop-component__...__title-area__name）
  const NAME_SEL = "[class$='__name'], [class*='title-area'] [class*='name'], [class*='basic-info'] [class*='name']";

  // 结构化匹配（最优先）：shop-component 区块内的店名元素
  function findShopComponent() {
    const wrap = document.querySelector("[class*='shop-component']");
    if (!wrap) return null;
    const nameEl = wrap.querySelector(NAME_SEL);
    if (nameEl) {
      const t = (nameEl.textContent || "").trim();
      if (t && t.length < 60 && !BAD.test(t)) return t;
    }
    return findShop((wrap.innerText || "").trim());  // 容器内关键词兜底
  }

  // 通过"进店"类锚点反查：按钮附近（父级容器）文本里通常有店名
  function findShopByAnchor() {
    const re = /进入店铺|进店逛逛|进店|查看店铺|进入小店|店铺主页/;
    for (const el of document.querySelectorAll("button, a, div, span")) {
      const t = (el.textContent || "").trim();
      if (!t || t.length > 40 || !re.test(t)) continue;
      let p = el;
      for (let i = 0; i < 4 && p.parentElement; i++) {
        p = p.parentElement;
        const s = findShop((p.innerText || "").trim());
        if (s) return s;
      }
    }
    return null;
  }

  // 提取店名：结构化组件 > class 选择器 > 进店锚点 > 全文兜底
  let lastSource = "";  // 本次命中来源，用于诊断
  function extract() {
    lastSource = "";
    let shop = findShopComponent();
    if (shop) { lastSource = "组件"; return shop; }
    const sels = [".shop-name", ".store-name", ".shopName", ".storeName",
      "[class*='shopName']", "[class*='shop-name']", "[class*='store-name']",
      "[class*='shop_name']", "[class*='sellerName']", "[class*='seller-name']",
      ".shop-info", ".store-info", "[class*='shopInfo']", "[class*='shop-card']"];
    for (const sel of sels) {
      for (const el of document.querySelectorAll(sel)) {
        shop = findShop((el.textContent || "").trim());
        if (shop) { lastSource = "class"; return shop; }
      }
    }
    shop = findShopByAnchor();
    if (shop) { lastSource = "锚点"; return shop; }
    lastSource = "全文";
    return findShop(document.body ? document.body.innerText : "");
  }

  // 经 background 代发回本地程序（background 带 host_permissions，不受 PNA 预检限制）
  function send(shop, source) {
    if (!/(douyin|jinritemai)/i.test(HREF)) return;  // 只在抖音系页面回传
    try {
      chrome.runtime.sendMessage(
        { type: "shop", shop: shop, url: HREF },
        (resp) => {
          if (chrome.runtime.lastError) {
            console.log("[ClipHelper] 发送失败:", chrome.runtime.lastError.message);
            banner("店名发送失败：" + chrome.runtime.lastError.message, "#f5222d");
            return;
          }
          if (resp && resp.ok) {
            console.log("[ClipHelper] 已发送店名:", shop, "来源:", source, "端口:", resp.port);
            banner("已发送店名「" + shop + "」(" + source + "·端口" + resp.port + ")", "#059669");
          } else {
            console.log("[ClipHelper] 发送失败：ClipHelper 未运行？");
            banner("店名发送失败：ClipHelper 未运行？", "#f5222d");
          }
        }
      );
    } catch (e) {
      console.log("[ClipHelper] 发送异常:", e);
      banner("店名发送失败：" + (e && e.message ? e.message : e), "#f5222d");
    }
  }

  // 页面异步渲染：MutationObserver + 快速重试，最长 30 秒，成功后停止
  let sent = false;
  function attempt() {
    if (sent) return;
    const shop = extract();
    if (shop) {
      console.log("[ClipHelper] 命中店名:", shop, "来源:", lastSource);
      send(shop, lastSource);
      sent = true;
    }
  }
  attempt();  // document_start：meta 已存在时立即命中，不等任何事件
  const obs = new MutationObserver(attempt);
  try { obs.observe(document.documentElement, { childList: true, subtree: true, characterData: true }); } catch (e) {}
  const iv = setInterval(attempt, 500);  // 兜底重试，meta 未命中时更快补位
  setTimeout(() => {
    clearInterval(iv);
    obs.disconnect();
    if (!sent) banner("30 秒内未提取到店名", "#D97706");
  }, 30000);
})();
