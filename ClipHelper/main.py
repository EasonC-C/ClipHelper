import webview
import threading
import time
import json
import os
import sys
import re
import http.server
import ctypes
import shutil
import subprocess
from ctypes import wintypes

import pyperclip
from parser import parse_douyin_text


# ── 常量 ───────────────────────────────────────────────

# Win32 常用标志/消息
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5
SW_MINIMIZE = 6
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
VK_V = 0x56
VK_CONTROL = 0x11
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
KEYEVENTF_KEYUP = 0x0002   # 模拟 Alt 键抬起：解锁前台锁定，供激活扩展窗口用
VK_MENU = 0x12             # Alt 键

# WinEvent 钩子事件常量：事件级拦截 Chrome 恢复/激活专用窗口。
# 背景：专用窗口虽然 --start-minimized 最小化，但 Chrome 收到新导航命令会
# 主动恢复并激活窗口（弹屏抢焦点），0.05s 轮询压回不够快，用户仍能看到闪现；
# 在窗口激活/显示事件触发瞬间就 ShowWindow(SW_MINIMIZE) 压回最小化，
# 比轮询早得多，屏幕几乎不出现（任务栏按钮保留，窗口不消失）。
EVENT_SYSTEM_FOREGROUND = 0x0003   # 窗口被激活/置前台
EVENT_OBJECT_CREATE = 0x8000       # 顶层窗口创建
EVENT_OBJECT_SHOW = 0x8002         # 窗口可见性变为可见
WINEVENT_OUTOFCONTEXT = 0x0000     # 回调在本进程消息循环中收到事件

# 压回最小化时间窗（秒）：复制商品链接后的这段时间内，专用浏览器被
# Chrome 自动恢复/激活（弹屏抢焦点）→ 立即压回最小化（任务栏按钮保留）；
# 时间窗外（用户主动点任务栏/Alt+Tab 查看）不拦，正常显示。
# 长度要覆盖冷启动（Chrome 起来+开页可能十几秒），保证整个粘贴流程不被打断。
MINIMIZE_WINDOW = 15.0
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ALREADY_EXISTS = 183
ERROR_INVALID_PARAMETER = 87

# 各阶段提示文案（stage → 下一步粘贴提示）
STAGE_MSGS = {
    1: "就绪 · 粘贴链接",
    2: "再粘贴标题",
    3: "再粘贴店名",
    4: "已全部粘贴",
}


# ── 前端 HTML/CSS/JS ──────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width,initial-scale=1.0" />
<title>ClipHelper</title>
<style>
:root{
  --bg:#F6F5F3;
  --surface:#FFFFFF;
  --accent:#4F46E5;
  --accent-soft:rgba(79,70,229,0.08);
  --accent-glow:rgba(79,70,229,0.18);
  --text:#1A1A2E;
  --text2:#8B8982;
  --text3:#B8B5AE;
  --border:#E4E2DC;
  --success:#059669;
  --success-soft:rgba(5,150,105,0.10);
  --warn:#D97706;
  --radius:12px;
}
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%}
body{
  font-family:"Inter",-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
  background:var(--bg);color:var(--text);
  -webkit-font-smoothing:antialiased;
  display:flex;flex-direction:column;
  overflow:hidden;user-select:none;
}

/* ── 自定义标题栏（frameless 无边框窗口） ── */
.titlebar{
  height:34px;flex-shrink:0;             /* 拖动由 JS 实现（WebView2 不支持 app-region） */
  display:flex;align-items:center;justify-content:space-between;
  padding-left:12px;
  background:var(--surface);
  border-bottom:1px solid var(--border);
}
.titlebar-title{font-size:12px;font-weight:600;color:var(--text2);letter-spacing:.3px}
.titlebar-actions{display:flex;-webkit-app-region:no-drag;height:100%}
.tbtn{
  width:38px;height:100%;border:none;background:none;cursor:pointer;
  font-size:14px;line-height:1;color:var(--text2);
  display:flex;align-items:center;justify-content:center;
  transition:background .2s,color .2s;flex-shrink:0;
}
.tbtn:hover{background:var(--accent-soft);color:var(--text)}
.tbtn.active{color:var(--accent)}
.tbtn.close:hover{background:#f5222d;color:#fff}

/* ── 内容容器（替代原 body padding） ── */
.content{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;padding:14px 18px 14px}

/* ── 状态条 ── */
.status{display:flex;align-items:center;gap:8px;margin-bottom:14px;flex-shrink:0}
.dot-wrap{width:20px;height:20px;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.dot{width:7px;height:7px;border-radius:50%;background:var(--text3);
      transition:background .4s,transform .3s;flex-shrink:0;position:relative}
.dot.active{background:var(--accent);transform:scale(1.15)}
.dot.active::after{content:'';position:absolute;width:20px;height:20px;
                    border-radius:50%;border:2px solid var(--accent-soft);
                    animation:ripple 2s ease-in-out infinite;
                    top:-6.5px;left:-6.5px}
.dot.warn{background:var(--warn);transform:scale(1.15)}
@keyframes ripple{
  0%,100%{opacity:.6;transform:scale(1)}
  50%{opacity:0;transform:scale(1.5)}
}
.status-text{font-size:13px;color:var(--text2);letter-spacing:.01em;transition:color .3s}
.status-text.active{color:var(--text);font-weight:450}
.status-actions{margin-left:auto;display:flex;align-items:center;gap:8px;flex-shrink:0}

/* ── 总开关 ── */
.switch{position:relative;width:30px;height:17px;cursor:pointer;flex-shrink:0}
.switch input{opacity:0;width:0;height:0}
.switch .slider{position:absolute;inset:0;background:var(--border);border-radius:17px;transition:background .3s}
.switch .slider::before{content:'';position:absolute;width:13px;height:13px;left:2px;top:2px;
                        border-radius:50%;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.25);
                        transition:transform .25s}
.switch input:checked + .slider{background:var(--accent)}
.switch input:checked + .slider::before{transform:translateX(13px)}

/* ── 内容区：高度随内容自动适配；超过上限(MAX_H)时内部滚动 ── */
.scroll{flex:1 1 auto;min-height:0;overflow-y:auto;padding-right:2px;
        width:100%;max-width:900px;margin:0 auto;scrollbar-width:thin}

/* ── 卡片（签名元素：左侧竖线） ── */
.card{
  position:relative;
  background:var(--surface);
  border-radius:var(--radius);
  padding:14px 16px 14px 18px;
  margin-bottom:10px;
  border:1px solid var(--border);
  box-shadow:0 1px 3px rgba(0,0,0,.03),0 1px 2px rgba(0,0,0,.02);
  transition:border-color .35s,box-shadow .35s;
}
.card::before{
  content:'';position:absolute;left:-1px;top:10px;bottom:10px;
  width:3px;border-radius:2px;
  background:var(--border);
  transition:background .4s,box-shadow .4s;
}
.card.active{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent-soft),0 2px 6px rgba(0,0,0,.04)}
.card.active::before{background:var(--accent);box-shadow:0 0 6px var(--accent-glow)}
.card-label{font-size:10px;font-weight:600;color:var(--text3);
            letter-spacing:.5px;text-transform:uppercase;margin-bottom:5px}
.card-body{font-size:13px;line-height:1.55;word-break:break-all;min-height:20px;color:var(--text)}
.card-body.mono{font-family:"SF Mono","JetBrains Mono","Consolas",monospace;
                font-size:12px;letter-spacing:.15px}
.card-body .ph{color:var(--text3);font-style:normal}
.tag{display:inline-block;background:var(--accent-soft);color:var(--accent);
     font-size:10px;padding:1px 7px;border-radius:4px;margin-right:5px;
     vertical-align:middle;font-weight:600;letter-spacing:.3px}

/* ── 步骤指示 ── */
.steps{display:flex;align-items:center;gap:10px;padding-top:12px;
       border-top:1px solid var(--border);flex-shrink:0}
.step{display:flex;align-items:center;gap:6px;flex-shrink:0}
.step-dot{
  width:18px;height:18px;border-radius:50%;
  background:var(--border);color:var(--text3);
  font-size:10px;font-weight:700;font-family:"SF Mono","JetBrains Mono",monospace;
  display:flex;align-items:center;justify-content:center;
  transition:background .35s,color .35s,box-shadow .35s;
}
.step-name{font-size:11px;color:var(--text3);transition:color .3s}
.step.active .step-dot{background:var(--accent);color:#fff;box-shadow:0 0 6px var(--accent-glow)}
.step.active .step-name{color:var(--text);font-weight:500}
.step.done .step-dot{background:var(--success);color:#fff}
.step.done .step-name{color:var(--text2)}
.step-line{flex:1;height:1px;background:var(--border);min-width:24px}

/* ── Toast ── */
.toast{
  position:fixed;bottom:82px;left:50%;
  transform:translateX(-50%) translateY(12px);
  background:var(--text);color:#fff;font-size:12px;font-weight:500;
  padding:7px 18px;border-radius:20px;opacity:0;
  transition:opacity .25s,transform .3s cubic-bezier(.34,1.56,.64,1);
  pointer-events:none;white-space:nowrap;z-index:99;
  box-shadow:0 4px 12px rgba(0,0,0,.12);
}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
</style>
</head>
<body>

<div class="titlebar">
  <span class="titlebar-title">ClipHelper</span>
  <div class="titlebar-actions">
    <button class="tbtn" id="pinBtn" onclick="togglePin()" title="置顶/取消置顶">📌</button>
    <button class="tbtn" id="extBtn" onclick="openExtensions()" title="打开扩展管理页（首次安装插件）">⚙</button>
    <button class="tbtn" onclick="minimizeWindow()" title="最小化">─</button>
    <button class="tbtn close" onclick="closeWindow()" title="关闭">✕</button>
  </div>
</div>

<div class="content">

<div class="status">
  <div class="dot-wrap"><div class="dot" id="dot"></div></div>
  <span class="status-text" id="statusText">等待复制分享文字...</span>
  <div class="status-actions">
    <label class="switch" id="enabledSwitch" title="总开关：启用/暂停应用">
      <input type="checkbox" id="enabledBox" checked />
      <span class="slider"></span>
    </label>
  </div>
</div>

<div class="scroll">
  <div class="card" id="urlCard">
    <div class="card-label">分享链接</div>
    <div class="card-body mono" id="urlBody">
      <span class="ph">暂无</span>
    </div>
  </div>
  <div class="card" id="titleCard">
    <div class="card-label">商品标题</div>
    <div class="card-body" id="titleBody">
      <span class="ph">暂无</span>
    </div>
  </div>
  <div class="card" id="shopCard">
    <div class="card-label">店铺</div>
    <div class="card-body" id="shopBody">
      <span class="ph">暂无</span>
    </div>
  </div>
</div>

<div class="steps" id="steps">
  <div class="step" id="step1">
    <span class="step-dot">1</span>
    <span class="step-name">链接</span>
  </div>
  <div class="step-line"></div>
  <div class="step" id="step2">
    <span class="step-dot">2</span>
    <span class="step-name">标题</span>
  </div>
  <div class="step-line"></div>
  <div class="step" id="step3">
    <span class="step-dot">3</span>
    <span class="step-name">店名</span>
  </div>
</div>

</div><!-- /content -->

<div class="toast" id="toast"></div>

<script>
var dot=document.getElementById('dot');
var st=document.getElementById('statusText');
var uc=document.getElementById('urlCard');
var tc=document.getElementById('titleCard');
var sc=document.getElementById('shopCard');
var ub=document.getElementById('urlBody');
var tb=document.getElementById('titleBody');
var sb=document.getElementById('shopBody');
var toast=document.getElementById('toast');
var pinBtn=document.getElementById('pinBtn');
var enabledBox=document.getElementById('enabledBox');
var step1=document.getElementById('step1');
var step2=document.getElementById('step2');
var step3=document.getElementById('step3');
var timer=null;

function setHeight(winH){
  if(winH===setHeight.last)return;   // 模式没变就不动窗口
  setHeight.last=winH;
  try{pywebview.api.resize_window(winH);}catch(e){}
}

function showToast(m){
  toast.textContent=m;toast.classList.add('show');
  clearTimeout(timer);timer=setTimeout(function(){toast.classList.remove('show')},1800);
}

function togglePin(){
  try{pywebview.api.toggle_pin().then(function(pinned){
    pinBtn.classList.toggle('active',pinned);
    showToast(pinned?'已置顶':'取消置顶');
  });}catch(e){}
}

function toggleEnabled(){
  try{pywebview.api.toggle_enabled().then(function(en){
    enabledBox.checked=en;
    showToast(en?'已启用 · 恢复拦截':'已暂停 · 粘贴正常');
  });}catch(e){}
}

function openExtensions(){
  try{pywebview.api.open_extensions().then(function(){
    showToast('已打开扩展管理页');
  });}catch(e){}
}

function minimizeWindow(){
  try{pywebview.api.minimize_window();}catch(e){}
}

function closeWindow(){
  try{pywebview.api.close_window();}catch(e){}
}

// 自定义标题栏拖动：WebView2 不支持 -webkit-app-region，用 JS 手动移动窗口
(function(){
  var tb=document.querySelector('.titlebar');
  if(!tb)return;
  var sx=0,sy=0,dragging=false;
  tb.addEventListener('mousedown',function(e){
    if(e.button!==0)return;
    if(e.target.closest('.titlebar-actions'))return;   // 按钮区不触发拖动
    dragging=true;sx=e.screenX;sy=e.screenY;
    e.preventDefault();
  });
  window.addEventListener('mousemove',function(e){
    if(!dragging)return;
    var dx=e.screenX-sx,dy=e.screenY-sy;
    if(dx||dy){
      sx=e.screenX;sy=e.screenY;
      try{pywebview.api.move_window(dx,dy);}catch(err){}
    }
  });
  window.addEventListener('mouseup',function(){dragging=false;});
  window.addEventListener('blur',function(){dragging=false;});
})();

function updateUI(s){
  dot.className='dot'+(s.status_dot==='ok'?' active':s.status_dot==='warn'?' warn':'');
  st.textContent=s.status_text;
  st.classList.toggle('active',s.status_dot==='ok');
  pinBtn.classList.toggle('active',s.pinned);

  // 步骤指示：0=无 1=链接 2=标题 3=店名 4=完成
  step1.className='step'+(s.stage>=1?' active':'')+(s.stage>=2?' done':'');
  step2.className='step'+(s.stage>=2?' active':'')+(s.stage>=3?' done':'');
  step3.className='step'+(s.stage>=3?' active':'')+(s.stage>=4?' done':'');

  if(s.has_data){
    uc.classList.add('active');tc.classList.add('active');
    ub.innerHTML='<span class="tag">URL</span>'+s.url;
    tb.innerHTML=s.title;
    if(s.shop_ready){
      sc.classList.add('active');
      sb.innerHTML=s.shop;
    }else{
      sc.classList.remove('active');
      sb.innerHTML='<span class="ph">'+(s.shop_failed?'暂无':'获取中…')+'</span>';
    }
  }else{
    uc.classList.remove('active');tc.classList.remove('active');
    sc.classList.remove('active');
    ub.innerHTML='<span class="ph">暂无</span>';
    tb.innerHTML='<span class="ph">暂无</span>';
    sb.innerHTML='<span class="ph">暂无</span>';
  }
  setHeight(s.has_data?410:380);
}

function poll(){
  try{pywebview.api.get_state().then(function(s){
    updateUI(s);
  });}catch(e){}
  setTimeout(poll,400);
}
document.addEventListener('DOMContentLoaded',poll);
enabledBox.addEventListener('change',toggleEnabled);
</script>
</body>
</html>"""


# ── 缓存 ───────────────────────────────────────────────

class DouyinCache:
    def __init__(self):
        self.lock = threading.Lock()
        self.has_data = False
        self.url = ""
        self.title = ""
        self.written = ""
        self.stage = 0            # 0=无数据 1=链接就绪 2=标题就绪 3=完成
        self.task_id = 0          # 任务版本号：每次新解析 +1，作废旧粘贴线程
        self.busy = False         # 粘贴推进进行中（防双击/并发双推进）
        self.pending_advance = False  # 推进在途时又按了 Ctrl+V：待补推一轮
        self.status_dot = "idle"  # idle / ok / warn
        self.status_text = "等待复制分享文字..."
        self.pinned = False
        self.enabled = True         # 总开关：关闭时完全不拦截 Ctrl+V / 不监听剪贴板
        self.skip_once = True         # 启动/重新启用后跳过剪贴板首轮，避免旧内容误触发
        self.last_seq = None        # 剪贴板序列号：内容未变不重复触发；重新复制（内容相同）必然触发
        self.shop = ""            # 店名（浏览器扩展回传）
        self.shop_ready = False   # 店名是否已收到
        self.shop_failed = False  # 店名获取失败（超时）
        self.shop_cold = False    # 本次任务冷启动专用浏览器（给更长的店名等待时间）
        self.shop_port = None     # 店名接收服务实际端口

        # 右键粘贴识别状态（WH_MOUSE_LL 钩子 + UIA 菜单枚举）
        self.rclick = None        # 最近一次右键坐标 (x, y)
        self.rclick_stage = 0     # 右键时的 stage（stage 变化后不再判定，防误判）
        self.rclick_seq = 0       # 右键序号：每次右键 +1，作废旧识别线程（防串扰）
        self.rclick_clicks = []   # 右键后收集的左键点击 [(x, y), ...]
        self.rclick_rect = None   # UIA 找到的『粘贴』菜单项矩形 (l, t, r, b)
        self.rclick_rect_until = 0.0  # 矩形有效截止时间（过期防陈旧误判）
        self.rclick_rect_enabled = False  # 『粘贴』菜单项是否可用（灰置无效不判定）
        self.rclick_judged = False    # 本轮是否已判定（防重复推进）


class Api:
    """暴露给 JS 的 API"""
    def __init__(self, cache):
        self._cache = cache
        self._hwnd = None

    def set_hwnd(self, hwnd):
        self._hwnd = hwnd

    def _get_hwnd(self):
        if self._hwnd is None:
            self._hwnd = ctypes.windll.user32.FindWindowW(None, "ClipHelper")
        return self._hwnd

    def get_state(self):
        with self._cache.lock:
            return {
                "has_data": self._cache.has_data,
                "url": self._cache.url,
                "title": self._cache.title,
                "stage": self._cache.stage,
                "status_dot": self._cache.status_dot,
                "status_text": self._cache.status_text,
                "pinned": self._cache.pinned,
                "enabled": self._cache.enabled,
                "shop": self._cache.shop,
                "shop_ready": self._cache.shop_ready,
                "shop_failed": self._cache.shop_failed,
            }

    def toggle_pin(self):
        with self._cache.lock:
            self._cache.pinned = not self._cache.pinned
            pinned = self._cache.pinned
        hwnd = self._get_hwnd()
        if hwnd:
            flag = wintypes.HWND(HWND_TOPMOST if pinned else HWND_NOTOPMOST)
            ctypes.windll.user32.SetWindowPos(
                hwnd, flag, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        return pinned

    def toggle_enabled(self):
        """总开关：关闭后不再拦截 Ctrl+V、不再监听剪贴板"""
        with self._cache.lock:
            self._cache.enabled = not self._cache.enabled
            en = self._cache.enabled
            self._cache.busy = False   # 中止进行中的推进
            if en:
                # 跳过剪贴板首轮，避免把暂停期间的旧内容误当新任务
                self._cache.skip_once = True
                if self._cache.has_data:
                    self._cache.status_text = STAGE_MSGS.get(
                        self._cache.stage, "等待复制分享文字...")
                    self._cache.status_dot = "ok"
                else:
                    self._cache.status_dot = "idle"
                    self._cache.status_text = "等待复制分享文字..."
            else:
                self._cache.status_dot = "idle"
                self._cache.status_text = "已暂停 · 正常粘贴"
        return en

    def open_extensions(self):
        """打开专用浏览器的扩展管理页（chrome://extensions），用于首次安装 ClipHelper 扩展。
        先等预预热线程结束（最多 6 秒），避免预预热与本次启动两个 chrome 进程
        同时抢同一 profile 产生竞态（弹无标题黑窗口）；然后正常焦点模式打开。"""
        _PREWARM_EVENT.wait(6)
        _open_browser("chrome://extensions", focus=True)
        return True

    def minimize_window(self):
        """自定义标题栏「最小化」按钮"""
        hwnd = self._get_hwnd()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, SW_MINIMIZE)
        return True

    def close_window(self):
        """自定义标题栏「关闭」按钮"""
        hwnd = self._get_hwnd()
        if hwnd:
            ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return True

    def move_window(self, dx, dy):
        """标题栏拖动：按相对位移移动窗口（WebView2 不支持 app-region，用 JS 手动移动）"""
        hwnd = self._get_hwnd()
        if not hwnd:
            return False
        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        user32.SetWindowPos(
            hwnd, None, rect.left + dx, rect.top + dy, 0, 0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        return True

    def resize_window(self, win_h):
        """固定尺寸：按模式切换窗口高度（win_h 为窗口总高），宽度固定 310。
        窗口保持左上角不动、向下变化，且不抢焦点。"""
        hwnd = self._get_hwnd()
        if not hwnd:
            return False
        ctypes.windll.user32.SetWindowPos(
            hwnd, None, 0, 0, 310, max(int(win_h), 1),
            SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
        return True


# ── 工具函数 ───────────────────────────────────────────

def _init_win32():
    """一次性声明全部 Win32 API 签名。
    ctypes 默认按 int 处理参数/返回值，64 位句柄会被截断，
    必须在任何调用前集中声明 argtypes/restype（进程内仅此一处）。"""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.CallNextHookEx.argtypes = [
        wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD  # 无参：剪贴板序列号（每次写入 +1）
    user32.GetMessageW.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND,
        wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = wintypes.LPARAM
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]

    # EnumWindows：枚举所有顶层窗口（隐藏/显示专用浏览器窗口用）
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]

    # SetWinEventHook：事件级拦截专用浏览器窗口显示（Chrome 恢复窗口瞬间即藏，
    # 比 0.05s 轮询早得多）。out-of-context 回调在本进程消息循环中分发。
    user32.SetWinEventHook.restype = wintypes.HANDLE
    user32.SetWinEventHook.argtypes = [
        wintypes.UINT, wintypes.UINT, wintypes.HMODULE,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
    user32.UnhookWinEvent.restype = wintypes.BOOL
    user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]

    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [
        wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def _base_dir():
    """程序所在目录：打包后为 exe 所在目录，开发时为脚本目录"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _profile_dir():
    """专用浏览器数据目录：固定在 %LOCALAPPDATA%\\ClipHelper\\browser_profile。
    与 exe 位置无关：exe 放哪都用同一套浏览器（扩展/登录态只装一次）。"""
    base = os.environ.get("LOCALAPPDATA") or _base_dir()
    return os.path.join(base, "ClipHelper", "browser_profile")


def _log(msg):
    """调试日志：追加写入 %LOCALAPPDATA%\\ClipHelper\\browser_open.log。
    ClipHelper 目录不存在时自动创建；用于定位浏览器启动相关问题
    （黑窗口/启动失败等）；写入失败静默。"""
    try:
        base = os.environ.get("LOCALAPPDATA") or _base_dir()
        d = os.path.join(base, "ClipHelper")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "browser_open.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _load_config():
    path = os.path.join(_base_dir(), "config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"poll_interval": 0.1}


# 剪贴板全局写锁：推进线程/右键预写/钩子回调/店名回传等多线程会并发写剪贴板，
# Windows 剪贴板同一时刻只允许一个打开者，并发 copy 会互相失败/覆盖。
_CLIP_LOCK = threading.Lock()


def _write_clipboard(text, attempts=6, interval=0.1):
    """写入剪贴板，失败时快速重试（目标程序粘贴后可能短暂占用剪贴板）。
    后台线程调用，阻塞拿锁；持锁期间最多重试 attempts 次。"""
    with _CLIP_LOCK:
        for _ in range(attempts):
            try:
                pyperclip.copy(text)
                return True
            except Exception:
                time.sleep(interval)
    return False


def _write_now(text):
    """钩子回调内单次快速写剪贴板：超时拿锁（钩子回调绝不能长时间阻塞，
    否则卡住全局输入），拿不到锁或写入失败即返回 False。"""
    if not _CLIP_LOCK.acquire(timeout=0.05):
        return False
    try:
        pyperclip.copy(text)
        return True
    except Exception:
        return False
    finally:
        _CLIP_LOCK.release()


# ── 店名接收端（浏览器扩展回传） ─────────────────────────

SHOP_PORT_START = 8765
SHOP_PORT_RANGE = 10
SHOP_TIMEOUT = 8   # 热启动：等待店名最长秒数（专用浏览器已在运行，回传快）
SHOP_COLD_TIMEOUT = 30  # 冷启动：专用浏览器不在运行，要等 Chrome 起来+加载扩展+开页回传，给足时间


def _start_shop_server(cache):
    """本地 HTTP 小服务：监听 127.0.0.1，接收扩展 POST 的店名。
    返回实际端口；端口全部被占用返回 None。"""
    class Handler(http.server.BaseHTTPRequestHandler):
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            # Chrome Private Network Access：https 页面请求 http://127.0.0.1 时
            # 必须声明允许私网访问，否则预检被拦，店名永远到不了程序
            self.send_header("Access-Control-Allow-Private-Network", "true")

        def _json(self, obj):
            data = json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            # 预留：扩展查询类接口（目前仅店名 POST 回传，无 GET 需求）
            self.send_response(404)
            self._cors()
            self.end_headers()

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length <= 0 or length > 65536:
                    raise ValueError("bad length")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                shop = (body.get("shop") or "").strip()
                if not shop:
                    raise ValueError("no shop")
                with cache.lock:
                    cache.shop = shop
                    cache.shop_ready = True
                self._json({"ok": True})
            except Exception:
                self.send_response(400)
                self._cors()
                self.end_headers()
                self.wfile.write(b'{"ok":false}')

        def log_message(self, *args):
            pass

    class NoReuseServer(http.server.ThreadingHTTPServer):
        # Windows 默认 SO_REUSEADDR 会让同端口重复 bind 而不报错，
        # 必须禁用，否则端口被占用时店名会被路由到错误的程序
        allow_reuse_address = False

    for port in range(SHOP_PORT_START, SHOP_PORT_START + SHOP_PORT_RANGE):
        try:
            server = NoReuseServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    else:
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return port


_BROWSER_CLASSES = (
    "Chrome_WidgetWin_1", "Chrome_WidgetWin_0",
    "ApplicationFrameWindow", "MozillaWindowClass",
    "IEFrame", "360se6_Frame",
)

# 本程序启动过的专用浏览器主进程 PID 集合：
# 隐藏/显示窗口时只动这些进程的窗口，绝不碰用户日常浏览器。
_CH_CPIDS = set()
_CH_CPIDS_LOCK = threading.Lock()

# 预预热完成标记（成功或失败都会置位）：点⚙ 打开扩展页前等待它，
# 避免预预热与冷启动两个 chrome 进程同时抢同一 profile 产生竞态黑窗口。
_PREWARM_EVENT = threading.Event()


def _register_browser_pid(pid):
    """记录一个专用浏览器主进程 PID（隐藏/显示窗口时按它定位窗口）。"""
    if not pid:
        return
    with _CH_CPIDS_LOCK:
        _CH_CPIDS.add(pid)


# 进程扫描结果缓存：_dedicated_browser_pids 每次要起 powershell 查命令行，
# 结果缓存几秒复用，避免隐藏线程频繁拉起 powershell 影响性能。
_PIDSCAN_LOCK = threading.Lock()
_PIDSCAN_AT = 0.0
_PIDSCAN_VAL = set()

# 隐藏线程最近一次刷新出的专用浏览器 PID 集合（含遗留实例）：
# 供 WinEvent hook 回调快速判断窗口归属——回调里绝不能起 powershell 扫描
# （会在钩子线程里阻塞），所以由 _refresh_pids_loop 线程定时刷新。
_HIDE_PIDS = set()

# 最近一次商品链接打开时间：hook 据此判断"是否在压回最小化时间窗内"，
# 窗内专用浏览器被自动激活 → 压回最小化；窗外 → 用户主动查看，不拦。
_LAST_OPEN_AT = 0.0

# 主动放行截止时间：点 ⚙ 扩展页等"用户要看专用窗口"的场景置为 now+几秒，
# 期间 hook 不压回最小化，让窗口正常显示激活。
_SKIP_RESTORE_UNTIL = 0.0

# WinEvent hook 全局引用：句柄与回调指针必须存活到进程退出，否则被 GC 后
# 钩子静默失效（这是低层钩子最常见的坑）。
_HOOK_KEEP = []

# 消息循环线程登记（线程 id）：退出时向它们投 WM_QUIT，让 GetMessageW 返回 0，
# 各循环在 finally 里由钩子线程自己 UnhookWindowsHookEx 干净卸载。
# 低级钩子必须由安装它的线程卸载：进程被强杀时钩子残留 → 系统桌面输入卡顿。
_MSG_THREADS = []


def _dedicated_browser_pids():
    """扫描进程命令行，返回所有带专用 profile（browser_profile）的进程 PID 集合。
    覆盖"本会话之前就存在的遗留专用浏览器实例"——它们不在 _CH_CPIDS 里，
    隐藏窗口/判断运行状态时会漏掉（Win10 上"藏不掉"就是这类实例的窗口）。
    结果缓存 3 秒；查询失败返回空集，退回原来的逻辑。"""
    global _PIDSCAN_AT, _PIDSCAN_VAL
    now = time.time()
    with _PIDSCAN_LOCK:
        if now - _PIDSCAN_AT < 3.0:
            return set(_PIDSCAN_VAL)
    try:
        prof = _profile_dir().lower()
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_Process | Where-Object "
             "{$_.CommandLine -match 'browser_profile'} | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
        pids = set()
        if out:
            rows = json.loads(out)
            if isinstance(rows, dict):
                rows = [rows]
            for r in rows:
                if prof in (r.get("CommandLine") or "").lower():
                    pids.add(int(r["ProcessId"]))
    except Exception:
        pids = set()
    with _PIDSCAN_LOCK:
        _PIDSCAN_AT = now
        _PIDSCAN_VAL = set(pids)
    return pids


def _reset_pidscan():
    """强制下次进程扫描重新查询（清掉 3 秒缓存）。
    Popen 后 Chrome launcher 还没派生真浏览器进程，立即扫描只会得到空集
    并被缓存 3 秒——窗口在这 3 秒里弹出来没人藏。所以要延迟到
    真进程出现后再重置，让隐藏线程下一次刷新就能扫到真进程。"""
    global _PIDSCAN_AT
    _PIDSCAN_AT = 0.0


def _pid_alive(pid):
    """进程是否存活：OpenProcess 成功（含"拒绝访问"= 进程在但没权限）
    都视为存活；只有 ERROR_INVALID_PARAMETER（进程不存在）判定为死。"""
    if not pid:
        return False
    kernel32 = ctypes.windll.kernel32
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        kernel32.CloseHandle(h)
        return True
    return kernel32.GetLastError() != ERROR_INVALID_PARAMETER


def _prewarm_browser():
    """程序启动后后台预启动专用浏览器（无窗口常驻）：
    首个链接打开时实例已热，省去冷启动等待。
    用 about:blank 建一个最小化窗口常驻（任务栏有按钮）：
    之后商品链接 URL 都转发进这个窗口开标签，不新建可见窗口。
    失败静默，下次复制再冷启动。
    CREATE_NO_WINDOW 保证即使误启动到控制台程序也不弹 cmd 窗口。"""
    browser = _find_browser()
    if not browser:
        _log("预预热：未找到浏览器")
        _PREWARM_EVENT.set()
        return
    time.sleep(0.5)  # 等程序主窗口初始化完成，避免抢资源
    si = subprocess.STARTUPINFO()
    si.dwFlags = subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = SW_SHOWNOACTIVATE
    try:
        p = subprocess.Popen([
            browser,
            "--user-data-dir=" + _profile_dir(),
            "--no-first-run", "--no-default-browser-check",
            "--start-minimized", "about:blank",
        ], startupinfo=si, creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
        _register_browser_pid(p.pid)
        _log("预预热：已启动 %s pid=%d" % (browser, p.pid))
        # 启动弹屏兜底：--start-minimized 可能失效，监控线程把专用 Chrome
        # 窗口压回任务栏（不弹屏、不抢焦点），15 秒后自动退出。
        threading.Thread(target=_keep_prewarm_minimized, daemon=True).start()
        # 最小化启动常驻：商品链接 URL 都转发进这个窗口开标签。
        # 延迟 0.5s 重置扫描缓存：launcher 派生真进程后再扫，hook 归属判断
        # 能尽快识别专用进程。
        threading.Timer(0.5, _reset_pidscan).start()
        time.sleep(1.5)  # 等 chrome 完成初始化并锁定 profile，避免与点⚙ 的启动竞态
    except Exception as e:
        _log("预预热：启动失败 %r" % (e,))
    finally:
        _PREWARM_EVENT.set()


def _is_browser_window(hwnd):
    """判断窗口是否为常见浏览器主窗口。
    类名命中后还需确认进程名，排除 ClipHelper 自己的 WebView2 窗口
    （类名同为 Chrome_WidgetWin_1，进程为 msedgewebview2.exe）。"""
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, buf, 64)
    if buf.value not in _BROWSER_CLASSES:
        return False
    # 进程名确认
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return True   # 取不到进程信息时按类名兜底
    kernel32 = ctypes.windll.kernel32
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return True
    try:
        size = wintypes.DWORD(260)
        path = ctypes.create_unicode_buffer(260)
        if kernel32.QueryFullProcessImageNameW(h, 0, path, ctypes.byref(size)):
            name = path.value.lower()
            if "msedgewebview2" in name:
                return False   # ClipHelper 自己的 WebView2 窗口，不是浏览器
            for kw in ("chrome", "msedge", "firefox", "iexplore",
                       "360se", "360chrome", "opera", "brave"):
                if kw in name:
                    return True
        # 进程名不在白名单（Electron 应用等）一律不算浏览器，避免误伤
        return False
    finally:
        kernel32.CloseHandle(h)


_BROWSER_PATH = None       # 查找到的浏览器路径缓存
_BROWSER_SEARCHED = False  # 是否已查找过（含未找到的 None）


def _find_browser():
    """定位可用的 Chromium 浏览器：优先 Chrome，其次 Edge。
    结果进程内缓存：程序运行期间只查找一次，之后直接复用。"""
    global _BROWSER_PATH, _BROWSER_SEARCHED
    if _BROWSER_SEARCHED:
        return _BROWSER_PATH

    def _from_app_paths(name):
        try:
            import winreg
            with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s" % name) as k:
                p, _ = winreg.QueryValueEx(k, None)
                return p if p and os.path.isfile(p) else None
        except Exception:
            return None

    def _first(cands):
        for p in cands:
            if os.path.isfile(p):
                return p
        return None

    # Chrome：常见安装路径 > App Paths / PATH
    p = _first((
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     "Google", "Chrome", "Application", "chrome.exe"),
    ))
    if p:
        _BROWSER_PATH = p
        _BROWSER_SEARCHED = True
        return p
    p = _from_app_paths("chrome.exe") or shutil.which("chrome")
    if p and p.lower().endswith(".exe"):
        _BROWSER_PATH = p
        _BROWSER_SEARCHED = True
        return p

    # Edge（通常装在 x86 目录）
    p = _first((
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     "Microsoft", "Edge", "Application", "msedge.exe"),
    ))
    if p:
        _BROWSER_PATH = p
        _BROWSER_SEARCHED = True
        return p
    p = _from_app_paths("msedge.exe")
    _BROWSER_PATH = p
    _BROWSER_SEARCHED = True
    return p


def _refresh_pids_loop():
    """后台线程：定时刷新专用浏览器 PID 集合（_HIDE_PIDS = 见过的 + 扫描到的）。
    方案改为"窗口显示但不抢焦点"后不再隐藏窗口，本线程只负责让 WinEvent hook
    能快速判断"某个前台窗口是不是专用浏览器"——hook 回调里不能起 powershell
    扫描（会阻塞钩子线程），所以由这里每 1 秒兜底刷新（_dedicated_browser_pids
    自带 3 秒缓存，实际约每 3 秒才真正扫一次系统进程）。"""
    while True:
        time.sleep(1.0)
        # 清理已退出的 launcher PID（Chrome 转发 URL 后 launcher 即退出），
        # 避免 _CH_CPIDS 每次复制 +1 越积越多
        with _CH_CPIDS_LOCK:
            dead = [p for p in _CH_CPIDS if not _pid_alive(p)]
            for p in dead:
                _CH_CPIDS.discard(p)
        _HIDE_PIDS.clear()
        _HIDE_PIDS.update(set(_CH_CPIDS) | _dedicated_browser_pids())


def _keep_prewarm_minimized(seconds=8.0):
    """预预热拉起的专用 Chrome 弹屏兜底：启动初期 Chrome 可能忽略
    --start-minimized 直接弹到前台（launcher 转发丢参数/恢复会话等）。
    本线程每 0.5 秒主动枚举顶层窗口，把属于专用浏览器的窗口压回最小化
    （任务栏按钮保留、不弹屏不抢焦点），补 WinEvent 钩子在冷启动期的
    竞态缺口（pid 扫描晚于窗口弹出）。
    - 覆盖时间窗：Chrome 冷启动弹窗出现在 1~4 秒内，8 秒足够且更安全；
    - 用户点⚙ 打开扩展页（focus=True 置了放行期 _SKIP_RESTORE_UNTIL）→
      说明冷启动阶段已结束、用户正在主动查看专用窗口 → 本线程直接退出，
      绝不再压用户正在看的窗口（否则扩展页 5 秒放行期一过就被压回，
      「点扩展不弹出」的回归根因）；
    - 到期自动退出；与事件钩子正交、重复 SW_MINIMIZE 无害。"""
    deadline = time.time() + seconds
    user32 = ctypes.windll.user32
    target = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum_cb(hwnd, lparam):
        # 枚举瞬间正好进入放行期（用户点了扩展页）→ 不压，交给外层判断退出
        if time.time() < _SKIP_RESTORE_UNTIL:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in target and _is_browser_window(hwnd):
            user32.ShowWindow(hwnd, SW_MINIMIZE)
        return True

    while time.time() < deadline:
        if time.time() < _SKIP_RESTORE_UNTIL:
            # 用户主动打开了专用浏览器的窗口（扩展页）→ 使命完成，退出
            return
        with _CH_CPIDS_LOCK:
            target = set(_CH_CPIDS) | _dedicated_browser_pids()
        user32.EnumWindows(_enum_cb, 0)
        time.sleep(0.5)


def _browser_win_event_hook():
    """不弹屏（最小化到任务栏）：专用浏览器在粘贴时间窗内被 Chrome 自动
    恢复/激活（弹屏抢焦点）→ 立即 ShowWindow(SW_MINIMIZE) 压回最小化。
    任务栏按钮保留、窗口不消失——它仍是"活着的应用"，用户随时可点开查看；
    时间窗外（用户主动点任务栏/Alt+Tab）→ 不拦，正常显示。
    回调只做快速判断（pid 命中 _HIDE_PIDS + 浏览器窗口），不扫描进程。"""
    def _run():
        _MSG_THREADS.append(threading.get_ident())
        user32 = ctypes.windll.user32

        @ctypes.WINFUNCTYPE(
            None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
            wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
        def proc(h_event, event, hwnd, id_obj, id_child, thread_id, evt_time):
            if not hwnd or id_obj != 0 or id_child != 0:
                return
            # 只关心专用浏览器窗口被激活/显示的瞬间
            if event != EVENT_SYSTEM_FOREGROUND and event != EVENT_OBJECT_SHOW:
                return
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in _HIDE_PIDS and pid.value not in _CH_CPIDS:
                return
            if not _is_browser_window(hwnd):
                return
            now = time.time()
            # 主动放行期（点 ⚙ 扩展页，用户要看专用窗口）→ 不拦
            if now < _SKIP_RESTORE_UNTIL:
                return
            # 粘贴时间窗内 → 压回最小化；窗外（用户主动查看）→ 不拦
            if now - _LAST_OPEN_AT <= MINIMIZE_WINDOW:
                user32.ShowWindow(hwnd, SW_MINIMIZE)

        # 回调指针必须留全局引用，否则被 GC 后钩子静默失效
        _HOOK_KEEP.append(proc)
        h = user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND, EVENT_OBJECT_SHOW, None,
            proc, 0, 0, WINEVENT_OUTOFCONTEXT)
        if not h:
            _log("winhook：SetWinEventHook 失败")
            return
        _HOOK_KEEP.append(h)
        # out-of-context 模式：事件经本线程消息循环分发到回调。
        # GetMessageW 阻塞直到有消息，钩子事件到达时自动唤醒。
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    threading.Thread(target=_run, daemon=True).start()


def _activate_extension_window(seconds=5.0):
    """点扩展后把新窗口激活到前台：专用浏览器已是后台实例（预预热的最小化
    窗口），Chrome 用 --new-window 建的新窗口被 Windows 判定为"后台启动"，
    自己抢焦点失败 → 窗口可见但不在最前面。本线程轮询找到新开的可见
    （未最小化）专用浏览器窗口并 SetForegroundWindow 激活，模拟正常 Chrome
    单击弹出。
    - 只激活"可见且未最小化"的窗口：预预热常驻的 about:blank 窗口是
      最小化的，绝不会被误激活；
    - 5 秒内没等到窗口（冷启动慢）→ 静默退出，不打扰；
    - 与放行期正交：放行期内 hook/监控已不压回，激活后扩展页保持在前台。"""
    deadline = time.time() + seconds
    user32 = ctypes.windll.user32
    target = set()
    done = [False]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum_cb(hwnd, lparam):
        if done[0]:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in target and _is_browser_window(hwnd):
            if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_SHOWNORMAL)
                # 解锁前台锁定后激活（ClipHelper 是前台进程，通常一次就成功）
                user32.keybd_event(VK_MENU, 0, 0, 0)
                user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
                user32.SetForegroundWindow(hwnd)
                done[0] = True
                return False
        return True

    while time.time() < deadline and not done[0]:
        with _CH_CPIDS_LOCK:
            target = set(_CH_CPIDS) | _dedicated_browser_pids()
        if target:
            user32.EnumWindows(_enum_cb, 0)
        if not done[0]:
            time.sleep(0.3)


def _browser_running():
    """专用浏览器是否已在运行。
    1) 锁文件（SingletonLock/Socket/Cookie）存在即视为运行中；
    2) 锁文件不可靠（Chrome 版本/冷启动窗口/文件系统差异）时，扫描带专用
       profile 的真进程兜底——launcher 转发 URL 后即退出，真 PID 从不进
       _CH_CPIDS，只能靠命令行扫描找到；扫到即登记并视为运行中；
    3) 最后按"本程序见过的专用浏览器进程是否存活"兜底。
    检测不精确只会把热启动误判成冷启动（店名等更久，无害），不会漏判冷启动。"""
    try:
        d = _profile_dir()
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            if os.path.exists(os.path.join(d, name)):
                return True
    except Exception:
        pass
    extra = _dedicated_browser_pids()  # 自带 3 秒缓存，后台线程调用可接受
    if extra:
        for pid in extra:
            _register_browser_pid(pid)
        return True
    with _CH_CPIDS_LOCK:
        pids = list(_CH_CPIDS)
    return any(_pid_alive(p) for p in pids)


def _open_browser(url, focus=False):
    """打开商品链接（或扩展管理页）：固定独立 profile 的 Chromium 实例（Chrome/Edge）。
    所有链接都进同一个专用浏览器（与用户日常浏览器完全隔离）：
    - 第一次复制 → 冷启动专用浏览器进程；
    - 之后只要它没被关掉 → 新链接自动在其中开新标签页（浏览器自身行为）；
    - 专用窗口被关闭后 → 下一次复制重新冷启动。
    每次复制都会真实打开页面：复制相同文案重新开页，扩展重新注入回传店名
    （页签堆积由扩展 background.js 的 MAX_TABS 上限自动清理）。
    focus=False（商品链接）：--start-minimized 最小化打开，不弹屏不抢焦点，
    任务栏按钮保留（应用存在）；Chrome 收到新导航会自行恢复/激活窗口
    （弹屏），hook 在粘贴时间窗内立即压回最小化，粘贴三连不被打断；
    focus=True（扩展管理页）：--new-window 强制新开可见窗口，正常激活，
    5 秒内 hook 不压回最小化（用户要看扩展页）。"""
    global _LAST_OPEN_AT, _SKIP_RESTORE_UNTIL
    browser = _find_browser()
    if not browser:
        _log("open：未找到浏览器 url=%s" % url)
        # 找不到 Chrome/Edge：仅对普通网址退回系统默认浏览器；
        # chrome:// 这类内部协议没有关联程序，强行打开可能弹奇怪窗口，静默失败
        if url.startswith(("http://", "https://")):
            try:
                os.startfile(url)
            except Exception:
                pass
        return

    profile = _profile_dir()
    si = subprocess.STARTUPINFO()
    si.dwFlags = subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = SW_SHOWNORMAL if focus else SW_SHOWNOACTIVATE
    args = [
        browser,
        "--user-data-dir=" + profile,
        "--no-first-run", "--no-default-browser-check",
    ]
    if focus:
        # 扩展管理页：强制开新窗口。否则 URL 会转发进已运行实例的
        # 现有窗口（商品窗口），用户看不到扩展页。
        args.append("--new-window")
    else:
        # 商品链接：--start-minimized 最小化打开，屏幕上看不到弹屏；
        # 任务栏按钮保留（应用存在），用户随时可点开查看。
        args.append("--start-minimized")
    args.append(url)
    try:
        p = subprocess.Popen(
            args, startupinfo=si,
            creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
        _register_browser_pid(p.pid)
        _log("open：%s pid=%d focus=%s" % (url, p.pid, focus))
    except Exception as e:
        _log("open：启动失败 %r url=%s" % (e, url))
        if url.startswith(("http://", "https://")):
            try:
                os.startfile(url)
            except Exception:
                pass
        return

    if focus:
        # 扩展管理页：正常显示激活；5 秒内 hook 不压回最小化（用户要看扩展页）。
        _SKIP_RESTORE_UNTIL = time.time() + 5.0
        # 后台激活新窗口到前台：Chrome 已运行（后台实例）时新窗口自己不抢焦点，
        # 由本线程找到新开的可见窗口 SetForegroundWindow，模拟正常 Chrome 单击弹出。
        threading.Thread(target=_activate_extension_window, daemon=True).start()
    else:
        # 商品链接：最小化打开不弹屏。已运行实例会自行转发 URL 并退出，
        # Chrome 激活已有窗口时，hook 在粘贴时间窗内立即压回最小化。
        # 延迟 0.5s 重置扫描缓存：launcher 派生真进程后再扫，hook 归属判断
        # 能尽快识别专用进程。
        _LAST_OPEN_AT = time.time()
        threading.Timer(0.5, _reset_pidscan).start()


def _wait_shop_then_advance(cache, my_task):
    """粘贴完标题后店名未就绪：后台等待店名回传。
    店名到达 → 写入剪贴板并推进到第3步；超时（热启动8秒/冷启动30秒）→ 结束流程
    （只粘贴两次），状态显示"获取失败"，不重试。"""
    with cache.lock:
        cold = cache.shop_cold
    deadline = time.time() + (SHOP_COLD_TIMEOUT if cold else SHOP_TIMEOUT)
    while time.time() < deadline:
        with cache.lock:
            if my_task != cache.task_id or not cache.has_data or not cache.enabled:
                cache.busy = False
                return
            if cache.shop_ready:
                break
        time.sleep(0.3)
    else:
        # 超时：只完成链接+标题两次粘贴
        with cache.lock:
            cache.busy = False
            if my_task != cache.task_id or not cache.has_data:
                return
            cache.shop_failed = True
            cache.stage = 4
            cache.status_dot = "warn"
            cache.status_text = "获取失败"
        return

    # 店名已到达：写入剪贴板，等待第3次粘贴
    with cache.lock:
        shop = cache.shop
    ok = _write_clipboard(shop)
    with cache.lock:
        cache.busy = False
        if my_task != cache.task_id or not cache.has_data:
            return
        if ok:
            cache.stage = 3
            cache.written = shop
            cache.status_dot = "ok"
            cache.status_text = "再粘贴店名"
        else:
            cache.status_dot = "warn"
            cache.status_text = "剪贴板切换失败"


def _on_key_down(cache, force=False):
    """Ctrl+V 按下瞬间：把本次要粘贴的内容写进剪贴板，放行后目标程序粘贴它。
    右键粘贴共用本函数：右键按下时同样先把内容写进剪贴板，
    这样菜单弹出后用户点『粘贴』，目标程序读到的是本段内容。

    force=True（右键场景）：总是写入当前阶段内容，绕过 written 短路。
    右键按下到用户点菜单『粘贴』间隔数百毫秒，期间剪贴板可能被其他程序改写，
    若 written==content 短路不写，就会粘出残留内容（与当前阶段对不上）。
    写同样的内容无害，但能保证粘出的一定是本段内容。"""
    with cache.lock:
        if not cache.enabled or not cache.has_data:
            return
        if cache.stage == 1:
            content = cache.url
        elif cache.stage == 2:
            content = cache.title
        elif cache.stage == 3:
            if not cache.shop_ready:
                return  # 店名未就绪时不写入，避免覆盖
            content = cache.shop
        else:
            return
        if not force and cache.written == content:
            return  # 剪贴板已是本段内容（推进已预写），避免重复写入
    ok = _write_now(content)
    if ok:
        # 标记为本程序刚写入：剪贴板轮询据此跳过，避免把链接/标题/店名误当成新任务
        with cache.lock:
            cache.written = content
    _log("keydown force=%d stage=%d ok=%d head=%s" %
         (force, cache.stage if ok else -1, ok, content[:16]))


def _advance_after_paste(cache, delay):
    """一次粘贴完成（KeyUp）后：写入下一段内容并推进状态。
    快速连按时（上一轮推进尚未结束）由 KeyUp 标记补推，本函数循环补推，不丢状态。"""
    while True:
        # 原子占用 busy：防双击/连按导致的双重推进
        with cache.lock:
            my_task = cache.task_id
            if not cache.has_data or not cache.enabled or cache.busy:
                return
            cache.busy = True
            cache.pending_advance = False
            stage = cache.stage
            wait_shop = False
            if stage == 1:
                content = cache.title
                next_stage = 2
                done_msg = STAGE_MSGS[2]
            elif stage == 2:
                if cache.shop_ready:
                    content = cache.shop
                    next_stage = 3
                    done_msg = STAGE_MSGS[3]
                else:
                    content = None      # 店名未就绪：交给等待线程
                    next_stage = 2
                    # 冷启动要等浏览器起来，提示更明确，避免用户以为卡住
                    done_msg = ("正在启动浏览器获取店名…" if cache.shop_cold
                                else "正在获取店名…")
                    wait_shop = True
            elif stage == 3:
                content = None        # 店名已在剪贴板，无需再写
                next_stage = 4
                done_msg = STAGE_MSGS[4]
            else:
                cache.busy = False
                return

        # 延时：等目标程序读完本次粘贴的内容（链接/标题/店名）
        time.sleep(delay)

        with cache.lock:
            # 延时期间可能复制了新商品或清空了状态，任务过期则放弃本次写入
            if my_task != cache.task_id or not cache.has_data:
                cache.busy = False
                return
        ok = True
        if content is not None:
            ok = _write_clipboard(content)

        with cache.lock:
            if my_task != cache.task_id or not cache.has_data:
                cache.busy = False
                return  # 写入期间已切换新任务/清空，不再推进旧状态
            if not ok:
                cache.busy = False
                cache.status_dot = "warn"
                cache.status_text = "剪贴板切换失败"
                return
            cache.stage = next_stage
            if content is not None:
                cache.written = content
            cache.status_text = done_msg
            _log("advance %d -> %d wait_shop=%d" % (stage, next_stage, wait_shop))
            if wait_shop:
                cache.busy = False  # 释放 busy，交给店名等待线程接管
                threading.Thread(
                    target=_wait_shop_then_advance, args=(cache, my_task), daemon=True).start()
                return
            cache.busy = False
            if cache.pending_advance:
                continue  # 推进期间用户又按了 Ctrl+V：补推一轮
            return


# ── 全局键盘监听（Ctrl+V） ────────────────────────────

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


def _keyboard_loop(cache):
    _MSG_THREADS.append(threading.get_ident())
    user32 = ctypes.windll.user32
    ctrl_pressed = [False]

    HOOKPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    def _proc(nCode, wParam, lParam):
        try:
            if nCode >= 0:
                kbd = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if wParam == WM_KEYDOWN and kbd.vkCode == VK_V:
                    if user32.GetAsyncKeyState(VK_CONTROL) & 0x8000:
                        ctrl_pressed[0] = True
                        _on_key_down(cache)
                    else:
                        # Ctrl 未按下但标志残留（上次 Ctrl+V 的 keyup 丢失，如 Alt+Tab）：
                        # 清除标志，避免下次单独按 V 的 keyup 误触发一次推进。
                        ctrl_pressed[0] = False
                elif wParam == WM_KEYUP and kbd.vkCode == VK_V and ctrl_pressed[0]:
                    ctrl_pressed[0] = False
                    with cache.lock:
                        if cache.busy:
                            cache.pending_advance = True  # 推进在途：标记补推，不丢状态
                        else:
                            threading.Thread(
                                target=_advance_after_paste, args=(cache, 0.05), daemon=True).start()
        except Exception:
            pass  # 低级钩子回调异常会导致系统输入阻塞，务必兜底
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    callback = HOOKPROC(_proc)
    hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, callback, None, 0)
    if not hook:
        with cache.lock:
            cache.status_dot = "warn"
            cache.status_text = "键盘监听失败"

    msg = wintypes.MSG()
    try:
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        if hook:
            user32.UnhookWindowsHookEx(hook)


# ── 右键粘贴识别（WH_MOUSE_LL + UI Automation） ────────

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


def _hit(x, y, rect):
    """坐标是否落在矩形 (l, t, r, b) 内"""
    return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]


def _is_paste_item(name):
    """判断 UIA 控件名是否为普通『粘贴』菜单项。
    排除『粘贴为纯文本/粘贴并转到/粘贴并搜索/粘贴到/粘贴成快捷方式』等复合项：
    这些名称在"粘贴"后紧跟汉字，而普通粘贴项后面只跟快捷键/加速键/空白。
    兼容中文(粘贴)与英文(Paste)。"""
    n = (name or "").strip()
    if not n:
        return False
    if n.startswith("粘贴"):
        rest = n[2:]
        return not rest or not re.match(r"[\u4e00-\u9fa5]", rest[0])
    if n.startswith("Paste"):
        rest = n[5:]
        if "Special" in rest or "Plain" in rest or "&" in rest:
            return False
        return not rest or not re.match(r"[A-Za-z\u4e00-\u9fa5]", rest[0])
    return False


def _enum_popup_menus():
    """枚举当前可见的 #32768 弹出菜单窗口（原生 Win32 右键菜单，
    如记事本/资源管理器/Office）。这类菜单是独立顶层窗口，
    不在应用主窗口的 UIA 子树里，必须单独按类名找。"""
    user32 = ctypes.windll.user32
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        try:
            buf = ctypes.create_unicode_buffer(16)
            user32.GetClassNameW(hwnd, buf, 16)
            if buf.value == "#32768" and user32.IsWindowVisible(hwnd):
                out.append(hwnd)
        except Exception:
            pass
        return True

    user32.EnumWindows(cb, 0)
    return out


def _collect_menu_items(auto, hwnd, out):
    """从窗口句柄收集菜单项候选 (name, rect, enabled)。
    网页/Chromium 类菜单渲染在应用窗口内部；
    #32768 原生菜单从弹出菜单窗口单独找。"""
    try:
        root = auto.ControlFromHandle(hwnd)
    except Exception:
        return
    seen = set()

    def add(c):
        try:
            r = c.BoundingRectangle
            if not r or r.right <= r.left or r.bottom <= r.top:
                return
            key = (c.Name, r.left, r.top)
            if key in seen:
                return
            seen.add(key)
            enabled = True
            try:
                enabled = bool(c.IsEnabled)
            except Exception:
                pass  # 读不到可用性时默认可用，避免误拦正常场景
            out.append((c.Name or "", (r.left, r.top, r.right, r.bottom), enabled))
        except Exception:
            pass

    def walk(c, depth):
        try:
            chs = c.GetChildren()
        except Exception:
            return
        for ch in chs:
            try:
                if ch.ControlTypeName == "MenuItemControl" or (ch.Name and ch.Name.strip()):
                    add(ch)
            except Exception:
                pass
            if depth > 0:
                walk(ch, depth - 1)

    walk(root, 8)


def _find_paste_rect():
    """UIA 查找当前右键菜单的『粘贴』菜单项，返回 (rect, enabled)；
    找不到返回 (None, False)。在后台线程调用（uiautomation 延迟导入）。
    两条查找路径互补：
    1) 前台窗口内枚举——网页/Chromium 类菜单渲染在应用窗口内部；
    2) 顶层 #32768 弹出菜单窗口——原生 Win32 菜单是独立顶层窗口。
    只认普通『粘贴』（排除纯文本/并转到/并搜索等复合项），避免误匹配。
    enabled=菜单项是否可用：灰置不可选时点击不会真的粘贴，
    不能据此判定"已粘贴"，否则程序推进到下一段、用户却什么都没粘上。"""
    try:
        import uiautomation as auto
    except Exception:
        return (None, False)

    cands = []
    fg = ctypes.windll.user32.GetForegroundWindow()
    if fg:
        _collect_menu_items(auto, fg, cands)
    for hwnd in _enum_popup_menus():
        _collect_menu_items(auto, hwnd, cands)

    paste = [(n, r, e) for (n, r, e) in cands if _is_paste_item(n)]
    if not paste:
        return (None, False)
    # 优先精确『粘贴/Paste』；其余取最上方一项（右键菜单的粘贴通常在上部）
    exact = [p for p in paste if p[0].strip() in ("粘贴", "Paste")]
    pool = exact or paste
    pool.sort(key=lambda p: (p[1][1], p[1][0]))
    _, rect, enabled = pool[0]
    return (rect, enabled)


def _rclick_dump(cache):
    """右键后后台线程：轮询 UIA 找『粘贴』矩形 →
    若用户在枚举完成前已点过左键（点在矩形内）则判定为右键粘贴。
    菜单可能延迟弹出（慢应用），或原生菜单需另找 #32768 窗口，
    故最多轮询 ~2 秒；一旦矩形就绪，之后用户点的左键由 _on_left_down 判定。
    uiautomation 导入较慢，绝不在鼠标钩子回调线程内调用。"""
    time.sleep(0.25)
    with cache.lock:
        seq = cache.rclick_seq
    deadline = time.time() + 2.0
    while True:
        rect, enabled = _find_paste_rect()
        with cache.lock:
            if seq != cache.rclick_seq:
                return  # 期间又发生了新右键：本轮作废，防旧线程用旧矩形误判
            if rect:
                cache.rclick_rect = rect
                cache.rclick_rect_enabled = enabled
                cache.rclick_rect_until = time.time() + 4.0
            clicks = list(cache.rclick_clicks)
            judged = cache.rclick_judged
        if rect:
            _log("rclick menu rect=%s enabled=%d clicks=%d" %
                 (rect, enabled, len(clicks)))
            if enabled and not judged:
                for (x, y) in clicks:
                    if _hit(x, y, rect):
                        _confirm_rclick_paste(cache)
                        return
            return  # 矩形已拿到：用户还没点的左键由 _on_left_down 判定
        if time.time() > deadline:
            _log("rclick menu 未找到 clicks=%d" % len(clicks))
            return
        time.sleep(0.25)


def _confirm_rclick_paste(cache):
    """判定『右键粘贴』成功：推进粘贴流程（与 Ctrl+V 的 KeyUp 推进一致）"""
    with cache.lock:
        if cache.rclick_judged:
            return
        cache.rclick_judged = True
        cache.rclick_rect = None
        cache.rclick_clicks = []
        _log("rclick判定 stage=%d" % cache.stage)
        if cache.busy:
            cache.pending_advance = True  # 推进在途：标记补推，不丢状态
            return
    # 右键点『粘贴』后目标程序读剪贴板可能稍慢（微信/抖音等常见），
    # 延时再写下一段，避免粘的瞬间剪贴板已被换成下一段内容。
    # 延时 0.3s：Ctrl+V 路径 KeyUp 后 0.05s 即推进且从未粘错，说明目标读得快；
    # 右键从左键按下算，真正粘贴在左键抬起（菜单选中）后，0.3s 已留足余量。
    threading.Thread(
        target=_advance_after_paste, args=(cache, 0.3), daemon=True).start()


def _on_right_down(cache, x, y):
    """右键按下：把本段内容写进剪贴板（菜单弹出时即就绪），并启动 UIA 识别。
    仅任务进行中生效，日常右键不受影响。"""
    with cache.lock:
        if (not cache.enabled or not cache.has_data or
                cache.stage not in (1, 2, 3)):
            return
    _on_key_down(cache, force=True)  # 写剪贴板：右键总是强制写本段内容（防粘出残留）
    with cache.lock:
        cache.rclick = (x, y)
        cache.rclick_stage = cache.stage
        cache.rclick_seq += 1
        cache.rclick_clicks = []
        cache.rclick_rect = None
        cache.rclick_rect_until = 0.0
        cache.rclick_rect_enabled = False
        cache.rclick_judged = False
    threading.Thread(target=_rclick_dump, args=(cache,), daemon=True).start()


def _on_left_down(cache, x, y):
    """左键按下：记录点击坐标；『粘贴』矩形已就绪且命中 → 判定右键粘贴。
    矩形未就绪时先入队，由 _rclick_dump 完成后复查。"""
    with cache.lock:
        if (not cache.enabled or not cache.has_data or
                cache.rclick is None or cache.rclick_judged):
            return
        cache.rclick_clicks.append((x, y))
        rect = cache.rclick_rect
        enabled = cache.rclick_rect_enabled
        until = cache.rclick_rect_until
        stage = cache.stage
    if rect and enabled and time.time() <= until and stage == cache.rclick_stage:
        if _hit(x, y, rect):
            _confirm_rclick_paste(cache)


def _mouse_loop(cache):
    """全局鼠标监听：识别『右键 → 点粘贴』操作。
    纯监听不拦截任何输入；回调内所有异常一律放行
    （低级钩子回调异常会导致系统输入阻塞，务必兜底）。"""
    _MSG_THREADS.append(threading.get_ident())
    user32 = ctypes.windll.user32

    HOOKPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    def _proc(nCode, wParam, lParam):
        try:
            if nCode >= 0:
                ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if wParam == WM_RBUTTONDOWN:
                    _on_right_down(cache, ms.x, ms.y)
                elif wParam == WM_LBUTTONDOWN:
                    _on_left_down(cache, ms.x, ms.y)
        except Exception:
            pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    callback = HOOKPROC(_proc)
    hook = user32.SetWindowsHookExW(WH_MOUSE_LL, callback, None, 0)
    if not hook:
        with cache.lock:
            cache.status_dot = "warn"
            cache.status_text = "鼠标监听失败"

    msg = wintypes.MSG()
    try:
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        if hook:
            user32.UnhookWindowsHookEx(hook)


# ── 剪贴板轮询 ─────────────────────────────────────────

def _clipboard_loop(cache):
    config = _load_config()
    interval = config.get("poll_interval", 0.1)
    user32 = ctypes.windll.user32  # restype 已在 _init_win32 声明（单例共享）

    while True:
        if not cache.enabled:
            time.sleep(interval)
            continue

        # 重新启用后的首轮：丢弃当前剪贴板旧内容，避免误触发。
        # 必须在序列号更新前消费：禁用期间剪贴板若有新内容，重新启用后仍要能触发
        skip = False
        with cache.lock:
            if cache.skip_once:
                cache.skip_once = False
                skip = True
        if skip:
            time.sleep(interval)
            continue

        # 剪贴板序列号：内容没变 → 序列号不变，轮询不会重复触发
        # （替代原"同 URL + 同阶段"去重，保护效果一致）；
        # 用户重新复制（哪怕内容完全一样）→ 序列号 +1，必然再次触发。
        # last_seq 只在成功 paste 后更新：粘贴失败下一轮重试，不丢事件。
        seq = user32.GetClipboardSequenceNumber()
        with cache.lock:
            changed = seq != cache.last_seq
        if not changed:
            # 内容没变：睡眠放锁外，避免空闲时长期占用 cache.lock
            # （否则粘贴推进线程/店名回传都要等这把锁，单次粘贴最多被拖 ~0.1s）
            time.sleep(interval)
            continue

        try:
            current_text = pyperclip.paste()
        except Exception:
            time.sleep(interval)
            continue

        with cache.lock:
            cache.last_seq = seq

        if current_text:
            with cache.lock:
                # 剪贴板里是程序刚写入的内容（written 或 本任务三段中的一段），跳过，避免误清空/误开新任务。
                # 比对 url/title/shop 是为了覆盖"写入剪贴板→更新 written"之间的瞬时窗口：
                # 轮询恰在该窗口采样到新内容时，written 尚未更新，但仍能靠内容归属识别出自写。
                # （真实用户复制的完整分享文案 ≠ 裸链接/标题/店名，不会误跳过）
                if (current_text == cache.written or
                        current_text == cache.url or
                        current_text == cache.title or
                        current_text == cache.shop):
                    time.sleep(interval)
                    continue
            result = parse_douyin_text(current_text)
            if result:
                url = result["urls"][0]
                title = result["title"]
                with cache.lock:
                    cache.task_id += 1   # 新任务：作废旧任务的粘贴线程
                    cache.has_data = True
                    cache.url = url
                    cache.title = title
                    cache.written = ""
                    cache.stage = 1
                    cache.shop = ""
                    cache.shop_ready = False
                    cache.shop_failed = False
                    cache.status_dot = "ok"
                    cache.status_text = STAGE_MSGS[1]
                # 预写链接到剪贴板：第一段粘贴（Ctrl+V 或右键）不再依赖"按下瞬间"那一次写入——
                # 解析后剪贴板里还是用户复制的完整分享文案（开头即【标题】），
                # 若首段写入失败/被抢，粘出的就是残留文案。预写后即使写入偶发失败也粘出链接。
                ok = _write_clipboard(url)
                with cache.lock:
                    if ok:
                        cache.written = url
                _log("parse task=%d stage=1 prewrite url ok=%d len=%d" %
                     (cache.task_id, ok, len(url)))
                # 后台打开商品链接：进入专用浏览器，等扩展回传店名。
                # 专用浏览器不在运行 → 冷启动要等 Chrome 起来+加载扩展+开页回传，
                # 记录冷启动标记，店名等待给足时间（否则第一次复制必然超时"获取失败"）。
                with cache.lock:
                    cache.shop_cold = not _browser_running()
                threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
            else:
                with cache.lock:
                    cache.has_data = False
                    cache.url = ""
                    cache.title = ""
                    cache.written = ""
                    cache.stage = 0
                    cache.status_dot = "idle"
                    cache.status_text = "等待复制分享文字..."

        time.sleep(interval)


# ── 入口 ───────────────────────────────────────────────

def _stop_msg_threads():
    """退出清理：向三个消息循环线程（WinEvent/键盘/鼠标低级钩子）投 WM_QUIT，
    让各自的 GetMessageW 返回 0 → 循环退出 → finally 里 UnhookWindowsHookEx
    由钩子线程自己卸载。低级钩子必须由安装它的线程卸载：进程被强杀时钩子
    残留会让系统继续向已释放的回调派发消息 → 桌面输入卡顿（退出卡顿根因）。"""
    user32 = ctypes.windll.user32
    for tid in list(_MSG_THREADS):
        user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
    # 给钩子线程一点时间走完 GetMessageW→卸载→退出（daemon 线程，进程退出即止）
    time.sleep(0.3)


def main():
    _init_win32()
    _browser_win_event_hook()
    _log("ClipHelper 启动 pid=%d" % os.getpid())

    # 防止多开：命名互斥锁
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, "DouyinProductHelper_SingleInstance")
    if mutex and kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        # 已有实例运行，激活窗口后退出
        hwnd = ctypes.windll.user32.FindWindowW(None, "ClipHelper")
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, SW_SHOWNORMAL)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        return

    cache = DouyinCache()

    shop_port = _start_shop_server(cache)
    if shop_port:
        cache.shop_port = shop_port
        cache.status_text = "店名端口 %d · 等待复制" % shop_port

    threading.Thread(target=_clipboard_loop, args=(cache,), daemon=True).start()
    threading.Thread(target=_keyboard_loop, args=(cache,), daemon=True).start()
    threading.Thread(target=_mouse_loop, args=(cache,), daemon=True).start()
    threading.Thread(target=_prewarm_browser, daemon=True).start()
    threading.Thread(target=_refresh_pids_loop, daemon=True).start()

    window = webview.create_window(
        "ClipHelper",
        html=HTML,
        js_api=Api(cache),
        width=310,
        height=380,
        min_size=(310, 380),
        frameless=True,   # 无边框：自定义标题栏（置顶/扩展/最小化/关闭）
    )
    api = window._js_api

    def _on_started():
        # 窗口已创建，从原生窗口拿 HWND
        try:
            native = window.native
            handle = getattr(native, "Handle", None)
            if handle is not None:
                api.set_hwnd(int(handle))
        except Exception:
            pass

    webview.start(_on_started, private_mode=True)

    # 窗口已关闭：先让三个消息循环线程（低级钩子）干净卸载，再退出进程，
    # 否则钩子残留会导致系统桌面输入卡顿。
    _stop_msg_threads()


if __name__ == "__main__":
    main()
