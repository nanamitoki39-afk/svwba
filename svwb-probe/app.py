#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
控制台 —— 统一启动「悬浮层 / 战绩面板 / 悬浮窗 / 开战判定」，并分开设置。

窗口风格（与悬浮层、战绩面板一致）：
    · 无边框（overrideredirect）
    · 抠色透明（只把 CHROMA 键色挖掉，底板与文字保留）
    · 半透明 alpha
    · 可拖动、可锁定（锁定后点击穿透）
    · 位置记忆 + **钳制在屏幕内**（防止拖出屏幕后\"消失\"）

设置分级：
    「胜率悬浮层」的设置 → 只改 overlay.py 的显示参数
    「战绩面板」的设置   → 只改 panel_ui.py
    各自独立，互不影响

窗口关闭按钮：右上角 ✕（会先停掉子进程再退出）
"""
import ctypes
import ctypes.wintypes
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
try:
    import ui_dpi
except Exception:
    class _NoDpi:
        SCALE = 1.0
    ui_dpi = _NoDpi()
import tkinter.font as tkfont

import cleanup
import meta_client

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
GAME = os.path.join(PKG, "dsh-shadowverse-huanjing")
TOOLS = os.path.join(GAME, "tools")
LIVE = os.path.join(GAME, "shots", "live")
LOGS = os.path.join(HERE, "logs")
for _p in (LIVE, LOGS, os.path.join(HERE, "hero-portraits")):
    os.makedirs(_p, exist_ok=True)

OVERLAY_SET = os.path.join(LIVE, "overlay-settings.json")
PANEL_SET = os.path.join(LOGS, "panel-settings.json")
LEADER_SET = os.path.join(LOGS, "leader-settings.json")
DEFAULT_LEADER = {"w": 320, "h": 160, "locked": False,
                  "alpha": 0.90, "font_size": 9}
CONSOLE_POS = os.path.join(LOGS, "console-pos.json")
CONSOLE_SET = os.path.join(LOGS, "console-settings.json")

# 控制台自己的设置：关闭行为 / 不透明度 / 字号 / 面板大小
# running：上次关闭时各子进程是否在运行（首次没有这个键 → 全部关闭）
SNAPSHOT_FILES = (
    os.path.join(LOGS, "deck_live.json"),
    os.path.join(LOGS, "deck_live.json.tmp"),
)
SNAPSHOT_MAX_AGE_SEC = 3600


def cleanup_snapshots(force=False):
    """清理超过一小时的临时读取快照；force 用于真正退出时删除当前快照。"""
    now = time.time()
    removed = 0
    for path in SNAPSHOT_FILES:
        try:
            if not os.path.isfile(path):
                continue
            if force or now - os.path.getmtime(path) >= SNAPSHOT_MAX_AGE_SEC:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    return removed


PY = sys.executable

CHROMA = "#010203"
BG = "#12161d"
BG2 = "#171c25"
BORDER = "#3a3a44"
FG = "#efe9dc"
GOLD = "#c9a86a"
GOLD2 = "#8d7a52"
DIM = "#9aa7a0"
RULE = "#2a3038"
GREEN = "#8fbf8a"
RED = "#c07b6b"
ALPHA = 0.92


def _dark_title(win):
    """Windows 10/11：把系统标题栏改成暗色，和窗口背景一致。

    窗口还没映射时 winfo_id() 拿到的不是最终 HWND，所以延迟到显示后再设置。
    """
    def _apply():
        try:
            import ctypes
            hwnd = (ctypes.windll.user32.GetParent(win.winfo_id())
                    or win.winfo_id())
            v = ctypes.c_int(1)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE
                try:
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
                except Exception:
                    pass
        except Exception:
            pass
    try:
        win.after(150, _apply)
    except Exception:
        pass

# ── 设置窗口控件配色 ───────────────────────────────────────────
# 设置窗里的滑块 / 按钮 / 开关在深色底上太暗会看不清：
# 统一比底板亮一档，滑块用金色系（tk.Scale 的 bg 就是滑块本体）。
# 只调颜色，不动布局与尺寸。
TROUGH = "#5a6d8f"          # 滑槽：比底板明显亮一档
SELECT = "#4a5f85"          # 勾选框 / 单选框选中底色
BTN = "#39465e"             # 普通按钮底
BTN_HI = "#4d5d7d"          # 普通按钮悬停底
GOLD_HI = "#e6c88f"         # 金色按钮悬停 / 高亮金

WIN_W, WIN_H = 340, 400          # 逻辑像素

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
HWND_TOPMOST = -1
SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010

DEFAULT_OVERLAY = {"mode": "static", "alpha": 0.90, "text_alpha": 1.0,
                   "scale": 1.0, "font_size": 16,
                   "titleColor": "#c9a86a", "interval": 0.6, "settle": 0.25,
                   "locked": True, "level": 3, "forceTopmost": True}
DEFAULT_PANEL = {"show_time": True, "alpha": 0.88, "text_alpha": 1.0, "locked": False,
                 "scale": 1.0, "font_size": 9, "hero_display": "both",
                 "force_topmost": True}
DEFAULT_JUDGE = {"delay": 0.2, "static": True, "sims": 20000}
DEFAULT_CONSOLE = {"closeAction": "minimize", "consoleAlpha": 1.0,
                   "font_size": 9, "scale": 1.0, "tab": "hj"}


def load_json(p, default):
    try:
        with open(p, encoding="utf-8") as fh:
            raw = json.load(fh) or {}
        out = dict(default)
        out.update(raw)
        return out
    except Exception:
        return dict(default)


def save_json(p, d):
    """原子写：先写临时文件再 replace，避免别的进程读到半截 JSON"""
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


# ══════════════════════════════════════════════════════════════
# 托盘图标（纯 ctypes + Shell_NotifyIconW，不依赖 pystray/win32gui）
# ══════════════════════════════════════════════════════════════
WM_APP = 0x8000
WM_TRAY = WM_APP + 21
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_NULL = 0x0000
NIM_ADD, NIM_DELETE = 0, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
IDI_APPLICATION = 32512
MF_STRING = 0x0
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
PM_REMOVE = 0x1
SW_MINIMIZE, SW_RESTORE = 6, 9

_LRESULT = ctypes.c_ssize_t
_WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.wintypes.HWND,
                              ctypes.wintypes.UINT, ctypes.wintypes.WPARAM,
                              ctypes.wintypes.LPARAM)



_ABOVE_CACHE = {"at": 0.0, "val": True}


def _above_game():
    """
    控制台是否应该置顶：**只在游戏窗口位于前台时**才置顶。

    用户要求：面板只显示在游戏上面，不要显示在所有窗口最上面。
    1 秒缓存：面板大小/字号拖动时会频繁调用，每次枚举窗口会卡。
    """
    import time as _t
    now = _t.time()
    if now - _ABOVE_CACHE["at"] < 1.0:
        return _ABOVE_CACHE["val"]
    try:
        import game_win as _GW
        val = bool(_GW.game_is_foreground())
    except Exception:
        val = True
    _ABOVE_CACHE["at"] = now
    _ABOVE_CACHE["val"] = val
    return val



def _apply_zorder(root):
    """
    更新本窗口的 Z 序：**贴着游戏，但不进置顶层**。

    用户要求：
      · 面板显示在游戏上面
      · 但如果有别的窗口（不占满屏幕）盖到它，那个窗口要能遮挡面板

    置顶层做不到第二点，所以改成把窗口插到「游戏正上方」的普通层位置。
    """
    try:
        import game_win as _GW
        _GW.sync_zorder(_GW.hwnd_of(root))
    except Exception:
        pass


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.wintypes.DWORD),
                ("Data2", ctypes.wintypes.WORD),
                ("Data3", ctypes.wintypes.WORD),
                ("Data4", ctypes.c_byte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    """64 位 Windows 的 NOTIFYICONDATAW（cbSize = sizeof 本结构 = 976）"""
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("hWnd", ctypes.wintypes.HWND),
        ("uID", ctypes.wintypes.UINT),
        ("uFlags", ctypes.wintypes.UINT),
        ("uCallbackMessage", ctypes.wintypes.UINT),
        ("hIcon", ctypes.wintypes.HICON),
        ("szTip", ctypes.wintypes.WCHAR * 128),
        ("dwState", ctypes.wintypes.DWORD),
        ("dwStateMask", ctypes.wintypes.DWORD),
        ("szInfo", ctypes.wintypes.WCHAR * 256),
        ("uVersion", ctypes.wintypes.UINT),          # union uTimeout/uVersion
        ("szInfoTitle", ctypes.wintypes.WCHAR * 64),
        ("dwInfoFlags", ctypes.wintypes.DWORD),
        ("guidItem", _GUID),
        ("hBalloonIcon", ctypes.wintypes.HICON),
    ]


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.wintypes.HINSTANCE),
        ("hIcon", ctypes.wintypes.HICON),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.wintypes.HBRUSH),
        ("lpszMenuName", ctypes.wintypes.LPCWSTR),
        ("lpszClassName", ctypes.wintypes.LPCWSTR),
    ]


class TrayIcon:
    """
    通知区域图标。

    · 双击 → 显示控制台
    · 右键 → 弹出「显示控制台 / 退出」菜单
    消息通过一个**隐藏的顶层窗口**接收（不用 Tk 的窗口 proc），
    由 root.after 轮询 PeekMessageW，不阻塞 Tk 主循环。
    """

    _CLASS = "DshHuanjingConsoleTray"

    def __init__(self, app):
        self.app = app
        self.hwnd = None
        self.ok = False
        self._nid = None
        self._wndproc = None
        self._hicon = None
        try:
            self._create_window()
            self._add_icon()
        except Exception as e:
            print(f"（托盘图标创建失败：{type(e).__name__}: {e}）")
            self.ok = False
        if self.ok:
            try:
                app.root.after(120, self._poll)
            except Exception:
                pass

    # ---------- Win32 ----------
    def _create_window(self):
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        # 64 位下必须显式声明 restype，否则句柄会被截成 32 位 int
        k.GetModuleHandleW.restype = ctypes.wintypes.HINSTANCE
        k.GetModuleHandleW.argtypes = [ctypes.wintypes.LPCWSTR]
        hinst = k.GetModuleHandleW(None)
        self._wndproc = _WNDPROC(self._on_message)
        wc = _WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._wndproc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinst
        wc.hIcon = None
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = self._CLASS
        u.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        u.RegisterClassW.restype = ctypes.wintypes.WORD
        u.RegisterClassW(ctypes.byref(wc))
        u.CreateWindowExW.argtypes = [
            ctypes.wintypes.DWORD, ctypes.wintypes.LPCWSTR,
            ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.wintypes.HWND, ctypes.wintypes.HMENU,
            ctypes.wintypes.HINSTANCE, ctypes.c_void_p]
        u.CreateWindowExW.restype = ctypes.wintypes.HWND
        u.DefWindowProcW.restype = ctypes.c_ssize_t
        u.DefWindowProcW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT,
                                     ctypes.wintypes.WPARAM,
                                     ctypes.wintypes.LPARAM]
        # 普通隐藏顶层窗口（不用 HWND_MESSAGE，否则 TrackPopupMenu 的
        # 前台窗口/菜单关闭行为不稳定）
        self.hwnd = u.CreateWindowExW(
            0x00000080,  # WS_EX_TOOLWINDOW
            self._CLASS, "dsh-console-tray",
            0x80000000,  # WS_POPUP（不显示）
            0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW 失败（err=%s）"
                          % ctypes.windll.kernel32.GetLastError())

    def _add_icon(self):
        u = ctypes.windll.user32
        sh = ctypes.windll.shell32
        u.LoadIconW.restype = ctypes.wintypes.HICON
        u.LoadIconW.argtypes = [ctypes.wintypes.HINSTANCE, ctypes.c_void_p]
        # 先用插件自己的图标（assets/icon.ico），拿不到才退回系统默认
        try:
            import ui_icon
            self._hicon = ui_icon.tray_icon_handle()
        except Exception:
            self._hicon = None
        if not self._hicon:
            self._hicon = u.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._hicon
        nid.szTip = "影之诗助手（双击显示，右键菜单）"
        sh.Shell_NotifyIconW.argtypes = [ctypes.wintypes.DWORD,
                                         ctypes.POINTER(NOTIFYICONDATAW)]
        sh.Shell_NotifyIconW.restype = ctypes.wintypes.BOOL
        ret = sh.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        self._nid = nid
        self.ok = bool(ret)
        if not ret:
            print("（托盘图标添加失败：Shell_NotifyIconW 返回 0；"
                  "✕ 将退化为最小化到任务栏）")
        else:
            print("托盘图标已创建（双击显示，右键菜单：显示控制台 / 退出）")

    def _on_message(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                ev = int(lparam) & 0xFFFF
                if ev == WM_LBUTTONDBLCLK:
                    self.app.restore_from_tray()
                elif ev == WM_RBUTTONUP:
                    self._show_menu()
        except Exception as e:
            print(f"（托盘消息处理失败：{e}）")
        try:
            return ctypes.windll.user32.DefWindowProcW(
                hwnd, msg, wparam, lparam)
        except Exception:
            return 0

    def _show_menu(self):
        u = ctypes.windll.user32
        u.CreatePopupMenu.restype = ctypes.wintypes.HMENU
        hmenu = u.CreatePopupMenu()
        if not hmenu:
            return
        u.AppendMenuW.argtypes = [ctypes.wintypes.HMENU, ctypes.wintypes.UINT,
                                  ctypes.c_size_t, ctypes.wintypes.LPCWSTR]
        u.AppendMenuW(hmenu, MF_STRING, 1, "显示控制台")
        u.AppendMenuW(hmenu, MF_STRING, 2, "退出")
        pt = ctypes.wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        u.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
        u.SetForegroundWindow(self.hwnd)
        u.TrackPopupMenu.restype = ctypes.c_int
        u.TrackPopupMenu.argtypes = [
            ctypes.wintypes.HMENU, ctypes.wintypes.UINT,
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.wintypes.HWND, ctypes.c_void_p]
        cmd = u.TrackPopupMenu(
            hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
            pt.x, pt.y, 0, self.hwnd, None)
        u.DestroyMenu.argtypes = [ctypes.wintypes.HMENU]
        u.DestroyMenu(hmenu)
        u.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        if cmd == 1:
            self.app.restore_from_tray()
        elif cmd == 2:
            self.app.quit_from_tray()

    def _poll(self):
        """在 Tk 主循环里轮询托盘消息（PeekMessage 不阻塞）"""
        try:
            u = ctypes.windll.user32
            msg = ctypes.wintypes.MSG()
            while u.PeekMessageW(ctypes.byref(msg), self.hwnd, 0, 0, PM_REMOVE):
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
        except Exception as e:
            print(f"（托盘消息轮询失败：{e}）")
        try:
            self.app.root.after(150, self._poll)
        except Exception:
            pass

    def destroy(self):
        """退出时必须 NIM_DELETE，否则通知区域会留下死图标"""
        try:
            if self.ok and self._nid is not None:
                ctypes.windll.shell32.Shell_NotifyIconW(
                    NIM_DELETE, ctypes.byref(self._nid))
                print("托盘图标已移除")
        except Exception as e:
            print(f"（移除托盘图标失败：{e}）")
        self.ok = False
        try:
            if self.hwnd:
                ctypes.windll.user32.DestroyWindow(self.hwnd)
        except Exception:
            pass
        self.hwnd = None


class SettingsDialog:
    """设置窗 —— 这个是**普通窗口**（有标题栏），因为要方便点选和关闭"""

    def __init__(self, master, title, width=400, height=430):
        self.w = tk.Toplevel(master)
        self.w.title(title)
        self.w.configure(bg=BG)
        _dark_title(self.w)
        try:
            self.w.attributes("-topmost", True)   # 插件页面都在游戏上面
        except Exception:
            pass
        try:
            self.w.attributes("-alpha", 0.88)
        except Exception:
            pass
        # 位置/尺寸钳制在屏幕内：小屏或高 DPI 下不会把设置窗丢到屏幕外
        sw, sh = self.w.winfo_screenwidth(), self.w.winfo_screenheight()
        width = min(int(width), max(260, sw - 24))
        height = min(int(height), max(220, sh - 80))
        x = min(200, max(0, sw - width - 24))
        y = min(150, max(0, sh - height - 80))
        self.w.geometry(f"{width}x{height}+{x}+{y}")
        self.w.transient(master)
        self.w.resizable(False, False)
        # 控制台本身是 topmost，设置窗必须也置顶，否则会藏在控制台后面
        try:
            self.w.attributes("-topmost", True)
            self.w.lift()
        except Exception:
            pass
        self.f_t = tkfont.Font(family="Microsoft YaHei UI",
                               size=10, weight="bold")
        self.f_s = tkfont.Font(family="Microsoft YaHei UI", size=9)
        tk.Label(self.w, text=title, bg=BG, fg=GOLD, font=self.f_t).pack(pady=(10, 4))
        tk.Frame(self.w, bg=RULE, height=1).pack(fill="x", padx=16)
        self.body = tk.Frame(self.w, bg=BG)
        self.body.pack(fill="both", expand=True, padx=16, pady=6)
        self.vars = {}

    def section(self, title):
        # 分组标题用亮金（原 GOLD2 太暗，和深色底几乎分不开）
        tk.Label(self.body, text=title, bg=BG, fg=GOLD, font=self.f_s,
                 anchor="w").pack(fill="x", pady=(8, 1))
        tk.Frame(self.body, bg=RULE, height=1).pack(fill="x")

    def slider(self, label, key, lo, hi, step, init, fmt="{:.2f}", on_change=None):
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=BG, fg=FG, font=self.f_s, width=13,
                 anchor="w").pack(side="left")
        v = tk.DoubleVar(value=float(init))
        self.vars[key] = v
        # 数值标签由压暗金改成亮金，深色底上更清楚
        val = tk.Label(row, text=fmt.format(float(init)), bg=BG, fg=GOLD,
                       font=self.f_s, width=6, anchor="e")
        val.pack(side="right")

        def on_move(_=None):
            val.config(text=fmt.format(v.get()))
            if on_change is not None:
                try:
                    on_change(v.get())
                except Exception:
                    pass
        # ⚠️ tk.Scale 的 bg = **滑块本体**；troughcolor = 那条细滑槽。
        # 原配色 bg=BG(#12161d) / troughcolor=BG2(#171c25) 几乎全黑，
        # 用户反馈"看不出来能拖"。现在滑槽亮一档、滑块改金色系。
        tk.Scale(row, from_=lo, to=hi, resolution=step, orient="horizontal",
                 variable=v, showvalue=False, bg=GOLD, fg="#241a0c",
                 troughcolor=TROUGH, activebackground=GOLD_HI,
                 highlightthickness=0, bd=0, sliderrelief="flat",
                 command=on_move
                 ).pack(side="left", fill="x", expand=True, padx=6)

    def check(self, label, key, init):
        v = tk.BooleanVar(value=bool(init))
        self.vars[key] = v
        tk.Checkbutton(self.body, text=label, variable=v, bg=BG, fg=FG,
                       selectcolor=SELECT, activebackground=BG2,
                       activeforeground="#ffffff",
                       font=self.f_s, highlightthickness=0).pack(anchor="w")
        return v

    def entry(self, label, key, init, width=30):
        """单行文本输入（服务器地址等）。"""
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=BG, fg=FG, font=self.f_s, width=13,
                 anchor="w").pack(side="left")
        var = tk.StringVar(value=str(init or ""))
        self.vars[key] = var
        tk.Entry(row, textvariable=var, bg=BG2, fg=FG, font=self.f_s,
                 insertbackground=FG, relief="flat", width=width,
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=GOLD).pack(side="left", padx=6)
        return var

    def radio_row(self, label, key, init, options):
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, bg=BG, fg=FG, font=self.f_s, width=13,
                 anchor="w").pack(side="left")
        v = tk.StringVar(value=init)
        self.vars[key] = v
        for val, lab in options:
            # 选项文字原来用 DIM，和底色太接近；改成正文色 FG
            tk.Radiobutton(row, text=lab, variable=v, value=val, bg=BG, fg=FG,
                           selectcolor=SELECT, activebackground=BG2,
                           activeforeground="#ffffff", font=self.f_s,
                           highlightthickness=0).pack(side="left")
        return v

    def buttons(self, on_apply, label="应用并重启"):
        tk.Frame(self.w, bg=RULE, height=1).pack(fill="x", padx=16, pady=(6, 0))
        bar = tk.Frame(self.w, bg=BG)
        bar.pack(fill="x", padx=16, pady=8)
        tk.Button(bar, text=label, command=on_apply, bg=GOLD, fg="#1a1209",
                  activebackground=GOLD_HI, activeforeground="#000000",
                  relief="flat", bd=0, padx=12, pady=5, font=self.f_s,
                  cursor="hand2").pack(side="right")
        tk.Button(bar, text="取消", command=self.w.destroy, bg=BTN, fg=FG,
                  activebackground=BTN_HI, activeforeground="#ffffff",
                  relief="flat", bd=0, padx=12, pady=5, font=self.f_s,
                  cursor="hand2").pack(side="right", padx=6)
        self._fit()

    def _fit(self):
        """
        内容比固定尺寸高/宽时自动长高加宽。

        为什么要做：设置项是后加的，固定 geometry 在高 DPI / 小屏上会把
        底部「应用 / 取消」挤出窗口（用户反馈"按钮被挤掉"）。
        这里按控件真实请求尺寸兜底，并重新钳制位置。
        """
        try:
            self.w.update_idletasks()
            sw, sh = self.w.winfo_screenwidth(), self.w.winfo_screenheight()
            w = min(max(self.w.winfo_width(), self.w.winfo_reqwidth()),
                    max(260, sw - 24))
            h = min(max(self.w.winfo_height(), self.w.winfo_reqheight()),
                    max(220, sh - 80))
            x = min(max(0, self.w.winfo_x()), max(0, sw - w - 24))
            y = min(max(0, self.w.winfo_y()), max(0, sh - h - 80))
            self.w.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass


def _console_show_request():
    folder = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "影之诗助手")
    return os.path.join(folder, "console-show.request")


def request_console_show():
    try:
        path = _console_show_request()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf8") as output:
            output.write(str(time.time()))
        return True
    except OSError:
        return False


class App:
    def __init__(self, root):
        self.root = root
        self.procs = {}
        self.labels = {}
        self._last_sweep = time.time()
        self._sweep_lock = threading.Lock()
        self._orphan_cleanup_done = threading.Event()
        self._start_generation = {}
        self.boot_at = time.time()   # 启动时刻：用于忽略启动阶段的假关闭请求
        self.locked = False
        self._drag = None
        self._alpha = ALPHA
        self._minimizing = False
        self.tray = None
        self.tray_ok = False
        # 关闭按钮行为：minimize（隐藏到托盘）| exit（退出程序）
        _cs = load_json(CONSOLE_SET, DEFAULT_CONSOLE)
        self.close_action = str(_cs.get("closeAction") or "minimize")
        if self.close_action not in ("minimize", "exit"):
            self.close_action = "minimize"
        # 控制台不透明度（0.4~1.0，默认 1.0）
        try:
            self.console_alpha = max(0.4, min(1.0, float(_cs.get("consoleAlpha", 1.0))))
        except (TypeError, ValueError):
            self.console_alpha = 1.0
        # 控制台字号（基准小字 9；标题大 1 号）
        try:
            self.console_font_size = max(8, min(14, int(_cs.get("font_size", 9))))
        except (TypeError, ValueError):
            self.console_font_size = 9
        # 控制台面板大小（整体缩放，0.7~2.0，和字号独立）
        try:
            self.console_scale = max(0.7, min(2.0, float(_cs.get("scale", 1.0))))
        except (TypeError, ValueError):
            self.console_scale = 1.0

        # ⚠️ 控制台**保留系统标题栏**。
        # 之前做成无边框（overrideredirect）后既不能拖动也不能关闭，
        # 用户完全失去对它的控制 —— 控制类窗口必须能移动和关闭。
        # 这里只做"半透明 + 深色主题"，不加抠色、不加无边框。
        root.title("影之诗助手")
        # 无边框 + 自绘标题行（用户要求标题那块不要系统标题栏）
        root.overrideredirect(True)
        root.attributes("-topmost", _above_game())
        try:
            root.attributes("-alpha", self.console_alpha)
        except Exception:
            pass
        root.configure(bg=BG)

        global WIN_W, WIN_H
        WIN_W, WIN_H = self._console_size(self.console_font_size,
                                          getattr(self, "console_scale", 1.0))
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x, y = self._load_pos(sw, sh)
        # 位置按逻辑像素存的，DPI 感知后换算成物理像素
        x, y = int(x * getattr(ui_dpi, "SCALE", 1.0)), int(y * getattr(ui_dpi, "SCALE", 1.0))
        self.pos = [x, y]
        root.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

        # 透明外层 + 深色底板
        self.frame = tk.Frame(root, bg=BG, highlightthickness=0, bd=0)
        self.frame.pack(fill="both", expand=True)
        self.pad = tk.Frame(self.frame, bg=BG)
        self.pad.pack(fill="both", expand=True, padx=6, pady=4)

        self.f_t = tkfont.Font(family="Microsoft YaHei UI",
                               size=self.console_font_size + 1, weight="bold")
        self.f_s = tkfont.Font(family="Microsoft YaHei UI",
                               size=self.console_font_size)

        # 标题行：标题 + 锁 + 关闭
        head = tk.Frame(self.pad, bg=BG)
        head.pack(fill="x", padx=8, pady=(8, 2))
        self.title = tk.Label(head, text="影  之  幻  境", bg=BG, fg=GOLD,
                              font=self.f_t, anchor="w", cursor="fleur")
        self.title.pack(side="left")
        # 自绘最小化按钮：无边框窗口没有系统标题栏，
        # 直接 iconify 无效，所以由 _minimize_to_taskbar 临时去掉无边框再最小化。
        self.min_btn = tk.Button(head, text="—", command=self._minimize_to_taskbar,
                                 bg=BG, fg=GOLD2, relief="flat", bd=0, padx=6,
                                 font=self.f_s, activebackground=BG,
                                 cursor="hand2")
        self.min_btn.pack(side="right")

        # 自绘关闭按钮（无边框窗口没有系统 ✕）
        tk.Button(head, text="✕", command=self.on_close, bg=BG, fg=RED,
                  relief="flat", bd=0, padx=6, font=self.f_s,
                  activebackground=BG, cursor="hand2").pack(side="right")
        # 关闭按钮旁的「托盘」按钮：明确提供"隐藏到托盘"（✕ 的行为可配置）
        self.tray_btn = tk.Button(head, text="托盘", command=self._hide_or_fallback,
                                  bg=BG, fg=GOLD2, relief="flat", bd=0, padx=4,
                                  font=self.f_s, activebackground=BG,
                                  cursor="hand2")
        self.tray_btn.pack(side="right")
        # 标题行整体可拖动
        for _w in (head, self.title):
            _w.bind("<Button-1>", self._on_press)
            _w.bind("<B1-Motion>", self._on_drag)
            _w.bind("<ButtonRelease-1>", self._on_release)
        self.lock_btn = tk.Button(head, text="🔓", command=self._toggle_lock,
                                  bg=BG, fg=GOLD2, relief="flat", bd=0, padx=4,
                                  font=self.f_s, activebackground=BG,
                                  cursor="hand2")
        self.lock_btn.pack(side="right")

        vrow = tk.Frame(self.pad, bg=BG)
        vrow.pack()
        tk.Label(vrow, text="v1.1.4  控制台", bg=BG, fg=GOLD2,
                 font=self.f_s).pack(side="left")
        tk.Button(vrow, text="⚙ 设置", command=self.open_console_settings,
                  bg=BG, fg=GOLD, relief="flat", bd=0, padx=6,
                  font=self.f_s, activebackground=BG,
                  cursor="hand2").pack(side="left", padx=(8, 0))
        # 「设置」右边的「重启」：重启**整个控制台**（用户 2026-10-29 要求）
        tk.Button(vrow, text="重启", command=self.restart_console,
                  bg=BG, fg=GOLD2, relief="flat", bd=0, padx=6,
                  font=self.f_s, activebackground=BG,
                  cursor="hand2").pack(side="left", padx=(6, 0))
        self.close_lbl = tk.Label(vrow, text="", bg=BG, fg=GOLD2,
                                  font=self.f_s)
        self.close_lbl.pack(side="left", padx=(8, 0))
        self._refresh_close_label()
        tk.Frame(self.pad, bg=RULE, height=1).pack(fill="x", padx=14, pady=6)

        # ── 模块页签：影之幻境 / 影之诗（2026-10-21）──
        # 用户要求：插件从"幻境专用"扩成"影之诗"通用，控制台分两个模块；
        # 胜率 / 战绩 / 悬浮窗都归影之幻境，影之诗放记牌器。
        self.tabs = {}
        tabrow = tk.Frame(self.pad, bg=BG)
        tabrow.pack(fill="x", padx=12, pady=(2, 2))
        for _key, _label in (("hj", "影之幻境"), ("sv", "影之诗")):
            _b = tk.Button(tabrow, text=_label,
                           command=lambda k=_key: self.switch_tab(k),
                           bg=BG, fg=GOLD2, relief="flat", bd=0, padx=10,
                           pady=3, font=self.f_s, cursor="hand2",
                           activebackground=BG2, activeforeground=GOLD)
            _b.pack(side="left", padx=(0, 4))
            self.tabs[_key] = _b
        self.page_hj = tk.Frame(self.pad, bg=BG)
        self.page_sv = tk.Frame(self.pad, bg=BG)
        self._build_sv_page()
        self.switch_tab(str(_cs.get("tab") or "hj"), save=False)

        # 两个独立分组（只属于影之幻境）
        self._group("胜率悬浮层", "overlay", self.open_overlay_settings)
        self._group("战绩面板", "panel", self.open_panel_settings)
        self._group("悬浮窗", "leader", self.open_leader_settings)
        self._meta_group()

        tk.Frame(self.page_hj, bg=RULE, height=1).pack(fill="x", padx=14, pady=8)
        row = tk.Frame(self.page_hj, bg=BG)
        row.pack(fill="x", padx=12)
        for text, cmd, color in (("全部启动", self.start_all, GOLD),
                                 ("重启全部", self.restart_all, GOLD2),
                                 ("强制重启", self.restart_hard, RED),
                                 ("退出全部", self.stop_all, RED)):
            tk.Button(row, text=text, command=cmd, bg=BG2, fg=color,
                      activebackground=BG2, relief="flat", bd=0, padx=6, pady=6,
                      font=self.f_s, cursor="hand2").pack(side="left", fill="x",
                                                          expand=True, padx=2)

        tk.Frame(self.page_hj, bg=RULE, height=1).pack(fill="x", padx=14, pady=6)
        tk.Label(self.page_hj, text="面板右上角 🔓/🔒 可单独锁定\n"
                                    "锁定后点击穿透，不挡游戏操作\n"
                                    "重启/退出不结束外部判定器\n"
                                    "截图超过 2 小时自动清理",
                 bg=BG, fg=GOLD2, font=self.f_s, justify="left").pack(
            anchor="w", padx=16)

        # 拖动：**递归绑定到所有子控件**，这样点哪儿都能拖
        def _bind_drag(w):
            w.bind("<Button-1>", self._on_press)
            w.bind("<B1-Motion>", self._on_drag)
            w.bind("<ButtonRelease-1>", self._on_release)
            for c in w.winfo_children():
                _bind_drag(c)
        _bind_drag(self.frame)

        self._apply_style()
        # 从任务栏恢复时重新应用样式（之前忘了绑定，导致白框）
        root.bind("<Map>", self._on_map)
        root.update_idletasks()
        self.pos = [x, y]
        # 内容可能比基准尺寸高（大字号 / 小屏）：按控件实际请求尺寸兜底，
        # 避免底部按钮/说明被 packer 挤掉。
        self._fit_console_size()
        # 托盘图标（失败也不影响主流程，✕ 会退化为最小化到任务栏）
        self.tray = TrayIcon(self)
        self.tray_ok = bool(self.tray.ok)
        if not self.tray_ok:
            print("（托盘不可用：✕ 将退化为最小化到任务栏）")
        self._tick()
        # Show a responsive console before CIM enumeration / old image cleanup.
        threading.Thread(target=self._startup_housekeeping, daemon=True,
                         name="console-startup-cleanup").start()
        self._new_session()
        self.root.after_idle(self._restore_after_startup)
        import subtitle_source
        subtitle_source.watch_live(self.root)

    def _startup_housekeeping(self):
        try:
            self._kill_orphans()
        finally:
            self._orphan_cleanup_done.set()
        self._sweep_background()

    def _restore_after_startup(self):
        # Only the Huanjing windows are orphan-cleanup targets. The card
        # tracker and pet can start immediately without waiting for PowerShell.
        if self.tab == "hj" and not self._orphan_cleanup_done.is_set():
            self.root.after(50, self._restore_after_startup)
            return
        self._restore_running()

    def _sweep_background(self):
        if not self._sweep_lock.acquire(False):
            return
        def work():
            try:
                n, b = cleanup.sweep()
                if n:
                    print(f"截图清理：删除 {n} 个超期截图，释放 {b/1048576:.1f} MB")
            except Exception as exc:
                print("截图清理失败：", exc)
            finally:
                self._sweep_lock.release()
        threading.Thread(target=work, daemon=True, name="screenshot-cleanup").start()

    # ---------------- 位置 ----------------
    def _load_pos(self, sw, sh):
        x = y = None
        try:
            with open(CONSOLE_POS, encoding="utf-8") as fh:
                d = json.load(fh) or {}
            x, y = int(d["x"]), int(d["y"])
        except Exception:
            pass
        if x is None or y is None:
            x, y = 80, 80
        # 钳制：拖出屏幕后会被拉回来（之前面板就是跑出屏幕导致"看不到"）
        x = max(0, min(max(0, sw - WIN_W), x))
        y = max(0, min(max(0, sh - WIN_H), y))
        return x, y

    def _save_pos(self):
        # 原子写：控制台位置文件也可能被别的实例/托盘读，别让它读到半截
        try:
            tmp = "%s.tmp-%d" % (CONSOLE_POS, os.getpid())
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"x": self.pos[0], "y": self.pos[1]}, fh)
            os.replace(tmp, CONSOLE_POS)
        except Exception:
            pass

    def _on_press(self, e):
        if self.locked:
            return
        self._drag = (e.x_root, e.y_root, self.pos[0], self.pos[1])

    def _on_drag(self, e):
        if self.locked or not self._drag:
            return
        x0, y0, px, py = self._drag
        nx = px + (e.x_root - x0)
        ny = py + (e.y_root - y0)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        nx = max(-(WIN_W - 60), min(sw - 60, nx))
        ny = max(0, min(sh - 60, ny))
        self.pos = [nx, ny]
        self.root.geometry(f"+{nx}+{ny}")

    def _on_release(self, _e):
        if self.locked:
            return
        self._drag = None
        self._save_pos()

    def _toggle_lock(self, _e=None):
        self.locked = not self.locked
        self._apply_style()
        self.title.config(fg=GOLD2 if self.locked else GOLD)
        self.lock_btn.config(text="🔒" if self.locked else "🔓")
        print("控制台：" + ("已锁定（点击穿透）" if self.locked else "已解锁（可拖动）"))
        return "break"

    def _minimize_to_taskbar(self):
        """
        最小化到**任务栏**（这个按钮 ≠ 隐藏到托盘）。

        ⚠️ Tk 的 iconify() 对 overrideredirect(True) 窗口直接报错：
            can't iconify ".": override-redirect flag is set
        所以用 Win32 ShowWindow(SW_MINIMIZE)。但无边框窗口带
        WS_EX_TOOLWINDOW，任务栏不会给它按钮；第一次先去掉 TOOLWINDOW、
        加 WS_EX_APPWINDOW，并用 hide/show 让任务栏重新评估（只改 exstyle
        任务栏不认）。全程保持 overrideredirect(True)，不重建窗口。
        """
        u = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW = 0x00040000
        try:
            self._minimizing = True
            hwnd = self._hwnd()
            ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if not (ex & WS_EX_APPWINDOW):
                u.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                 (ex & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW)
                u.ShowWindow(hwnd, 0)      # SW_HIDE
                u.ShowWindow(hwnd, 5)      # SW_SHOW → 任务栏重新评估
                self.root.update_idletasks()
                hwnd = self._hwnd()
            u.ShowWindow(hwnd, SW_MINIMIZE)
            self._minimizing = False
            print("控制台已最小化到任务栏（点任务栏图标还原）")
        except Exception as e:
            self._minimizing = False
            print(f"（最小化失败：{e}）")

    def _on_map(self, evt=None):
        """
        从任务栏恢复时重新应用样式（无边框窗口恢复后可能丢置顶/主色）。
        只在"确实是本窗口、且状态为 normal"时处理，避免启动阶段误触发。
        """
        try:
            if evt is not None and evt.widget is not self.root:
                return
            if self.root.state() != "normal":
                return
            self.root.after(30, self._restore_borderless)
        except Exception:
            pass

    def _restore_borderless(self):
        """恢复后重新确保：无边框 + 深色底 + 置顶 + 原位置"""
        try:
            hwnd = self._hwnd()
            # 还在最小化状态：别急着装回无边框，否则任务栏按钮会消失/还原失败
            if self._minimizing or ctypes.windll.user32.IsIconic(hwnd):
                self.root.after(150, self._restore_borderless)
                return
            self.root.overrideredirect(True)
            self.root.configure(bg=BG)
            self.root.attributes("-topmost", False)
            _apply_zorder(self.root)
            self.root.geometry(f"+{self.pos[0]}+{self.pos[1]}")
            self._apply_style()
        except Exception:
            pass

    # ---------------- 托盘 / 关闭 ----------------
    def _refresh_close_label(self):
        try:
            txt = "关闭→托盘" if self.close_action == "minimize" else "关闭→退出"
            self.close_lbl.config(text=txt)
        except Exception:
            pass

    def _hide_or_fallback(self):
        """标题栏「托盘」按钮：隐藏到托盘；托盘不可用则退化为任务栏最小化"""
        if self.tray_ok:
            self.hide_to_tray()
        else:
            print("托盘不可用，退化为最小化到任务栏")
            self._minimize_to_taskbar()

    def hide_to_tray(self):
        """隐藏主窗口，程序与判定器继续运行（托盘双击恢复）"""
        try:
            self.root.withdraw()
            print("控制台已隐藏到托盘（双击托盘图标恢复）")
        except Exception as e:
            print(f"（隐藏到托盘失败：{e}）")

    def restore_from_tray(self):
        """托盘双击 / 菜单「显示控制台」"""
        try:
            self.root.deiconify()
            self.root.update_idletasks()
            self.root.overrideredirect(True)
            self.root.geometry(f"+{self.pos[0]}+{self.pos[1]}")
            self.root.attributes("-topmost", _above_game())
            self.root.lift()
            self._apply_style()
            try:
                self.root.focus_force()
            except Exception:
                pass
            print("控制台已从托盘恢复")
        except Exception as e:
            print(f"（从托盘恢复失败：{e}）")

    def quit_from_tray(self):
        """托盘菜单「退出」：真正退出并清理"""
        self.quit_app()

    def quit_app(self):
        """退出：记录运行状态 → 移除托盘图标 → 停子进程 → 清历史 → 关窗口"""
        self._save_running_state()
        try:
            self.tray.destroy()
        except Exception:
            pass
        def _shutdown():
            try:
                self.stop_all()
            finally:
                cleanup_snapshots(force=True)
        try:
            threading.Thread(target=_shutdown, daemon=True).start()
        except Exception:
            _shutdown()
        try:
            self.root.destroy()
        except Exception:
            pass

    def _hwnd(self):
        """
        取**真正可见的顶层窗口**句柄。

        Tk 的 winfo_id() 返回的是内部客户区子窗口，样式要设在它的父窗口上
        （class 为 TkTopLevel 的包装窗口）才生效。实测：
            winfo_id()      = 0x4508ca   ← 改了没用
            GetParent(...)  = 0x240aac   ← 这个才是屏幕上的窗口
        """
        import ctypes
        h = ctypes.windll.user32.GetParent(self.root.winfo_id())
        return h or self.root.winfo_id()

    @staticmethod
    def _console_size(font_size, scale=1.0):
        """
        控制台窗口尺寸 = 字号决定的基准尺寸 × 面板大小系数。

        字号决定"能不能装下文字"，面板大小决定"整体多大"（用户要求分开调）。
        """
        extra = max(0, int(font_size) - 9)
        try:
            sc = float(scale)
        except Exception:
            sc = 1.0
        sc = max(0.7, min(2.0, sc))
        d = getattr(ui_dpi, "SCALE", 1.0)
        return (int((340 + extra * 14) * sc * d),
                int((400 + extra * 22) * sc * d))

    def _fit_console_size(self):
        """
        让控制台窗口至少装得下全部控件（并重新钳制在屏幕内）。

        为什么要单独兜底：`_console_size()` 是按字号/缩放算的**基准**值，
        字号 14 或高 DPI 时控件的真实请求高度会超过它，packer 会把底部
        按钮/说明压扁（用户看到"按钮被挤掉"）。这里在布局完成后取实际
        请求尺寸，和基准值取最大。
        """
        global WIN_W, WIN_H
        try:
            self.root.update_idletasks()
            reqw = max(self.frame.winfo_reqwidth(),
                       self.pad.winfo_reqwidth())
            reqh = max(self.frame.winfo_reqheight(),
                       self.pad.winfo_reqheight())
        except Exception:
            return
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        WIN_W = min(max(int(WIN_W), int(reqw)), max(260, sw - 10))
        WIN_H = min(max(int(WIN_H), int(reqh)), max(240, sh - 40))
        _pos = getattr(self, "pos", None) or [0, 0]
        x = max(0, min(max(0, sw - WIN_W), _pos[0]))
        y = max(0, min(max(0, sh - WIN_H), _pos[1]))
        self.pos = [x, y]
        try:
            self.root.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")
        except Exception:
            pass

    def _apply_console_fonts(self, font_size, scale=None):
        """控制台字号 / 面板大小热生效：改 Font 对象 + 调整窗口尺寸（不重建控件）"""
        global WIN_W, WIN_H
        fs = max(8, min(14, int(font_size)))
        self.console_font_size = fs
        if scale is None:
            scale = getattr(self, "console_scale", 1.0)
        try:
            self.console_scale = max(0.7, min(2.0, float(scale)))
        except Exception:
            self.console_scale = 1.0
        try:
            self.f_t.configure(size=fs + 1)
            self.f_s.configure(size=fs)
        except Exception:
            pass
        WIN_W, WIN_H = self._console_size(fs, self.console_scale)
        try:
            self._fit_console_size()
        except Exception:
            pass
        self._apply_style()

    def _apply_style(self):
        """
        ⚠️ 这里**只**用 Tk 自带属性，不手动设 WS_EX_LAYERED。

        踩过的坑：手动给窗口加 WS_EX_LAYERED，但 Tk 的 -alpha 是在加样式
        之前设置的，分层窗口没有有效的 alpha 值，结果整块渲染成空白
        （用户在截图里看到的就是一片白）。
        交给 Tk 自己管 -alpha / -topmost 就没问题。
        """
        try:
            # ⚠️ 属性名必须是**带引号的字符串** "-topmost"。
            # 之前写成 attributes(-topmost, True)：-topmost 是"对变量 topmost
            # 取负"的表达式，运行时 NameError 被 except 吞掉，
            # 于是"强制置顶"每 2 秒调用一次、每次都静默失败。
            self.root.attributes("-topmost", _above_game())
        except Exception:
            pass

    # ---------------- 进程 ----------------
    def _kill_orphans(self):
        """
        启动时清理**上次遗留**的子进程。

        为什么需要：控制台被强杀（任务管理器/崩溃）时来不及 _kill，
        overlay / panel_ui / leader_panel 会变成孤儿继续跑（判定器不在此列）。
        反复重启几次就会出现"三个胜率窗口""两个战绩面板"这种事。
        这里按命令行特征扫一遍，杀掉不属于当前进程树的那几个。
        """
        me = os.getpid()
        pat = r"overlay\.py|panel_ui\.py|leader_panel\.py"
        ps = ("Get-CimInstance Win32_Process -Filter "
              "\"Name='python.exe' or Name='pythonw.exe'\" | "
              "Where-Object { $_.CommandLine -match '%s' -and $_.ProcessId -ne %d } | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
              "-ErrorAction SilentlyContinue }" % (pat, me))
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                 "-Command", ps],
                capture_output=True, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            print("已清理遗留的子进程")
        except Exception as e:
            print(f"（清理遗留进程失败：{e}）")

    # ---------------- 模块页签（影之幻境 / 影之诗） ----------------
    def switch_tab(self, key, save=True):
        key = key if key in ("hj", "sv") else "hj"
        # 模块互斥：切到哪个模块就停掉另一个模块的功能（用户 2026-10-29 要求）
        try:
            self._stop_other_module(key)
        except Exception:
            pass
        self.tab = key
        try:
            for k, b in self.tabs.items():
                b.config(fg=GOLD if k == key else GOLD2,
                         bg=BG2 if k == key else BG)
            if key == "hj":
                self.page_sv.pack_forget()
                self.page_hj.pack(fill="both", expand=True)
            else:
                self.page_hj.pack_forget()
                self.page_sv.pack(fill="both", expand=True)
            if save:
                cs = load_json(CONSOLE_SET, DEFAULT_CONSOLE)
                cs["tab"] = key
                save_json(CONSOLE_SET, cs)
            self._fit_console_size()
        except Exception:
            pass

    def _build_sv_page(self):
        """影之诗模块：记牌器（窗口独立，页内只放入口与说明）。"""
        p = self.page_sv
        box = tk.Frame(p, bg=BG2)
        box.pack(fill="x", padx=12, pady=4)
        left = tk.Frame(box, bg=BG2)
        left.pack(side="left", fill="x", expand=True, padx=8, pady=6)
        tk.Label(left, text="记牌器", bg=BG2, fg=FG, font=self.f_s,
                 anchor="w").pack(fill="x")
        self.sv_status = tk.Label(left, text="未在对战中", bg=BG2, fg=DIM,
                                  font=self.f_s, anchor="w")
        self.sv_status.pack(fill="x")
        right = tk.Frame(box, bg=BG2)
        right.pack(side="right", padx=8, pady=6)
        tk.Button(right, text="打开", command=self.open_deck_tracker, bg=BG,
                  fg=GOLD, relief="flat", bd=0, padx=10, pady=4,
                  font=self.f_s, cursor="hand2").pack(side="right")
        tk.Button(right, text="设置", command=self.open_tracker_settings, bg=BG,
                  fg=GOLD2, relief="flat", bd=0, padx=8, pady=4,
                  font=self.f_s, cursor="hand2").pack(side="right", padx=4)
        tk.Frame(p, bg=RULE, height=1).pack(fill="x", padx=14, pady=6)

        # AI 陪玩（语音助手）：设置和记牌器同页，但独立入口
        box2 = tk.Frame(p, bg=BG2)
        box2.pack(fill="x", padx=12, pady=4)
        left2 = tk.Frame(box2, bg=BG2)
        left2.pack(side="left", fill="x", expand=True, padx=8, pady=6)
        tk.Label(left2, text="AI 陪玩（语音助手）", bg=BG2, fg=FG,
                 font=self.f_s, anchor="w").pack(fill="x")
        self.ai_status = tk.Label(left2, text="未启用", bg=BG2, fg=DIM,
                                  font=self.f_s, anchor="w")
        self.ai_status.pack(fill="x")
        right2 = tk.Frame(box2, bg=BG2)
        right2.pack(side="right", padx=8, pady=6)
        tk.Button(right2, text="AI 设置", command=self.open_ai_settings,
                  bg=BG, fg=GOLD, relief="flat", bd=0, padx=10, pady=4,
                  font=self.f_s, cursor="hand2").pack(side="right")
        tk.Button(right2, text="启动宠物", command=self.open_companion,
                  bg=BG, fg=GOLD2, relief="flat", bd=0, padx=8, pady=4,
                  font=self.f_s, cursor="hand2").pack(side="right",
                                                     padx=(0, 4))

        tk.Frame(p, bg=RULE, height=1).pack(fill="x", padx=14, pady=6)

        # 卡组（导入卡组码 / 查看已导入 / 切换当前）—— 2026-10-29
        box3 = tk.Frame(p, bg=BG2)
        box3.pack(fill="x", padx=12, pady=4)
        left3 = tk.Frame(box3, bg=BG2)
        left3.pack(side="left", fill="x", expand=True, padx=8, pady=6)
        tk.Label(left3, text="卡组", bg=BG2, fg=FG, font=self.f_s,
                 anchor="w").pack(fill="x")
        self.deck_status = tk.Label(left3, text="", bg=BG2, fg=DIM,
                                    font=self.f_s, anchor="w")
        self.deck_status.pack(fill="x")
        right3 = tk.Frame(box3, bg=BG2)
        right3.pack(side="right", padx=8, pady=6)
        tk.Button(right3, text="导入卡组码", command=self.open_deck_import,
                  bg=BG, fg=GOLD, relief="flat", bd=0, padx=10, pady=4,
                  font=self.f_s, cursor="hand2").pack(side="right")
        tk.Button(right3, text="查看已导入", command=self.open_deck_list,
                  bg=BG, fg=GOLD2, relief="flat", bd=0, padx=8, pady=4,
                  font=self.f_s, cursor="hand2").pack(side="right",
                                                      padx=(0, 4))
        self._refresh_deck_status()
        import subtitle_source
        subtitle_source.build_panel(p, self.f_s, bg=BG2, fg=FG, dim=DIM,
                                    gold=GOLD, green=GREEN)

    # ---------------- 卡组（导入卡组码 / 查看 / 切换当前） ----------------
    def _toplevel(self, title, geometry=None, alpha=0.9):
        """统一弹出的功能窗口：半透明（用户 2026-10-29：所有功能窗口都要透明）。"""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=BG)
        _dark_title(win)
        try:
            win.attributes("-topmost", True)   # 插件页面都在游戏上面
        except Exception:
            pass
        try:
            win.attributes("-alpha", alpha)
        except Exception:
            pass
        if geometry:
            win.geometry(geometry)
        win.transient(self.root)
        return win

    def _decks_path(self):
        return os.path.join(HERE, "gamedata", "user_decks.json")

    def _refresh_deck_status(self):
        try:
            with open(self._decks_path(), encoding="utf-8") as fh:
                d = json.load(fh)
            decks = d.get("decks") or []
            active = d.get("active") or ""
        except Exception:
            decks, active = [], ""
        if not decks:
            txt = "还没有导入卡组（点「导入卡组码」）"
        else:
            # 只显示当前卡组（用户 2026-10-30：不要列全部已导入卡组）
            cur = active or (decks[0].get("name") or "?")
            txt = "当前卡组：%s" % cur
        try:
            self.deck_status.config(text=txt)
        except Exception:
            pass

    def _save_active_deck(self, name):
        p = self._decks_path()
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            return
        d["active"] = name
        d["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        self._refresh_deck_status()

    def _delete_deck(self, name):
        """删除一套卡组；删的是当前卡组时，active 切到剩下的第一套。"""
        p = self._decks_path()
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            return
        decks = [x for x in (d.get("decks") or [])
                 if (x.get("name") or "") != name]
        d["decks"] = decks
        if (d.get("active") or "") == name:
            d["active"] = (decks[0].get("name") or "") if decks else ""
        d["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        self._refresh_deck_status()

    def _rename_deck(self, old, new):
        """重命名卡组；如果改的是当前卡组，active 跟着改。"""
        p = self._decks_path()
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            return
        decks = d.get("decks") or []
        hit = False
        for x in decks:
            if (x.get("name") or "") == old:
                x.setdefault("stats_id", "name:" + old)
                x["name"] = new
                hit = True
        if not hit:
            return
        if (d.get("active") or "") == old:
            d["active"] = new
        d["decks"] = decks
        d["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        self._refresh_deck_status()

    def open_deck_import(self):
        """导入卡组码：贴码 → 解析预览 → 保存到 user_decks.json。"""
        import deckcode
        win = self._toplevel("导入卡组码", "560x720")
        tk.Label(win, text="把游戏里生成的卡组码粘贴到下面（整段 #…# 也行）：",
                 bg=BG, fg=FG, font=self.f_s, anchor="w",
                 wraplength=520).pack(fill="x", padx=12, pady=(10, 4))
        txt = tk.Text(win, height=7, bg=BG2, fg=FG, insertbackground=FG,
                      font=self.f_s, wrap="char")
        txt.pack(fill="x", padx=12, pady=4)
        tk.Label(win, text="提示：粘贴后按 Ctrl+Enter 直接导入（也可以点下面的「解析并保存」）。",
                 bg=BG, fg=GOLD2, font=self.f_s, anchor="w",
                 wraplength=520).pack(fill="x", padx=12, pady=(0, 2))
        tk.Label(win, text="解析结果：", bg=BG, fg=GOLD2, font=self.f_s,
                 anchor="w").pack(fill="x", padx=12, pady=(6, 2))
        out = tk.Text(win, height=18, bg=BG2, fg=FG, font=self.f_s,
                      wrap="char", state="disabled")
        out.pack(fill="both", expand=True, padx=12, pady=4)
        state = {"entries": None, "name": ""}

        def _show(text):
            out.config(state="normal")
            out.delete("1.0", "end")
            out.insert("end", text)
            out.config(state="disabled")

        def _parse():
            text = txt.get("1.0", "end")
            try:
                entries = deckcode.decode(text)
            except Exception as e:
                state["entries"] = None
                _show("解析失败：%s\n" % e)
                return
            name = deckcode.parse_name(text)
            state["entries"] = entries
            state["name"] = name
            try:
                import deck_reader
                meta = deck_reader.load_meta()
            except Exception:
                meta = {}
            total = sum(n for _, n in entries)
            lines = ["卡组：%s" % name, "%d 种 / %d 张" % (len(entries), total), ""]
            for cid, n in entries:
                nm = (meta.get(str(cid)) or {}).get("name") or ("#%d" % cid)
                lines.append("%s  ×%d" % (nm, n))
            _show("\n".join(lines))

        def _save():
            _parse()
            if not state["entries"]:
                return
            name = state["name"] or "导入的卡组"
            cards = []
            for cid, n in state["entries"]:
                cards.extend([cid] * n)
            try:
                p = self._decks_path()
                try:
                    with open(p, encoding="utf-8") as fh:
                        d = json.load(fh)
                except Exception:
                    d = {"decks": []}
                decks = [x for x in (d.get("decks") or [])
                         if x.get("name") != name]
                import uuid
                previous = next((x for x in d.get("decks", []) if x.get("name") == name), None)
                stats_id = ((previous.get("stats_id") or "name:" + name) if previous
                            else uuid.uuid4().hex)
                decks.insert(0, {"name": name, "cards": cards, "stats_id": stats_id})
                os.makedirs(os.path.dirname(p), exist_ok=True)
                tmp = p + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"decks": decks, "active": d.get("active") or "",
                               "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                              fh, ensure_ascii=False, indent=1)
                os.replace(tmp, p)
            except Exception as e:
                out.config(state="normal")
                out.insert("end", "\n保存失败：%s\n" % e)
                out.config(state="disabled")
                return
            self._refresh_deck_status()
            out.config(state="normal")
            out.insert("end", "\n已保存到卡组列表（%s）。\n" % name)
            out.config(state="disabled")

        bar = tk.Frame(win, bg=BG)
        # 按钮固定在窗口底部（place 不占 pack 空间，不会被文本框挤出可见区域）
        bar.place(relx=0, rely=1.0, anchor="sw", relwidth=1.0)
        tk.Button(bar, text="解析", command=_parse, bg=BG2, fg=FG,
                  relief="flat", bd=0, padx=14, pady=5,
                  font=self.f_s, cursor="hand2").pack(side="left")
        tk.Button(bar, text="解析并保存", command=_save, bg=BG2, fg=GOLD,
                  relief="flat", bd=0, padx=14, pady=5,
                  font=self.f_s, cursor="hand2").pack(side="left", padx=6)
        tk.Button(bar, text="关闭", command=win.destroy, bg=BG2, fg=DIM,
                  relief="flat", bd=0, padx=14, pady=5,
                  font=self.f_s, cursor="hand2").pack(side="right")
        # 导入键：粘贴卡组码后 Ctrl+Enter 直接解析并保存
        txt.bind("<Control-Return>",
                 lambda e: (_save(), "break")[1])
        txt.bind("<Control-KP_Enter>",
                 lambda e: (_save(), "break")[1])
        win.after(50, lambda: txt.focus_set())

    def open_deck_list(self):
        """查看已导入卡组，并可切换「当前使用的卡组」。"""
        import deck_reader
        try:
            with open(self._decks_path(), encoding="utf-8") as fh:
                d = json.load(fh)
            decks = d.get("decks") or []
            active = d.get("active") or ""
        except Exception:
            decks, active = [], ""
        try:
            meta = deck_reader.load_meta()
        except Exception:
            meta = {}

        win = self._toplevel("已导入的卡组", "760x680")
        canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
        scroll = tk.Scrollbar(win, command=canvas.yview)
        inner = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(8, 0),
                    pady=8)
        scroll.pack(side="right", fill="y")
        win.bind("<MouseWheel>", lambda e: canvas.yview_scroll(
            int(-e.delta / 120), "units"))

        def _pick(name):
            self._save_active_deck(name)
            win.destroy()
            self.open_deck_list()

        def _rename(name):
            from tkinter import simpledialog
            new = simpledialog.askstring("重命名卡组", "新的卡组名：",
                                         initialvalue=name, parent=win)
            if not new:
                return
            new = new.strip()
            if not new or new == name:
                return
            self._rename_deck(name, new)
            win.destroy()
            self.open_deck_list()

        def _delete(name):
            from tkinter import messagebox
            if not messagebox.askyesno("删除卡组",
                                       "确定删除「%s」？" % name,
                                       parent=win):
                return
            self._delete_deck(name)
            win.destroy()
            self.open_deck_list()

        def _show_code(name, text, copied=False):
            """卡组码窗口：进来就已经复制好了。"""
            w = self._toplevel("卡组码 · %s" % name, "620x400")
            tip = ("✅ 已复制到剪贴板，直接在游戏里【卡牌】→【新牌组】→"
                   "【使用牌组码】粘贴" if copied else
                   "复制下面的整段，在游戏里【卡牌】→【新牌组】→"
                   "【使用牌组码】粘贴")
            tk.Label(w, text=tip, bg=BG, fg=(GREEN if copied else DIM),
                     font=self.f_s, anchor="w", justify="left",
                     wraplength=570).pack(fill="x", padx=12, pady=(8, 4))
            box = tk.Text(w, bg=BG2, fg=FG, font=("Consolas", 9),
                          wrap="word", relief="flat", bd=0, padx=8, pady=8,
                          insertbackground=FG, height=13)
            box.pack(fill="both", expand=True, padx=12, pady=4)
            box.insert("1.0", text)
            box.config(state="disabled")

            def _again():
                from tkinter import messagebox
                if _copy_text(text):
                    messagebox.showinfo("已复制", "卡组码已复制到剪贴板",
                                        parent=w)
                else:
                    messagebox.showerror("复制失败",
                                         "自动复制失败了，请手动全选文本框里的内容复制",
                                         parent=w)

            bar = tk.Frame(w, bg=BG)
            bar.pack(fill="x", padx=12, pady=(0, 10))
            tk.Button(bar, text="再复制一次", command=_again, bg=GOLD,
                      fg="#1a1209", relief="flat", bd=0, padx=14, pady=5,
                      font=self.f_s, cursor="hand2").pack(side="right")
            tk.Button(bar, text="关闭", command=w.destroy, bg=BG2, fg=DIM,
                      relief="flat", bd=0, padx=14, pady=5,
                      font=self.f_s, cursor="hand2").pack(
                side="right", padx=6)

        def _build_code(name, cards):
            """生成卡组码文本（导出和复制共用）。"""
            import deckcode
            try:
                meta = deck_reader.load_meta()
            except Exception:
                meta = {}
            from collections import Counter as _Counter
            cls = None
            for _code, _cname in deckcode.CLASSES.items():
                if _cname in name:
                    cls = _code
                    break
            if cls is None:
                cls_cnt = _Counter()
                for cid in cards:
                    info = meta.get(str(cid)) or {}
                    k = info.get("class")
                    if isinstance(k, int) and 1 <= k <= 7:
                        cls_cnt[k] += 1
                cls = cls_cnt.most_common(1)[0][0] if cls_cnt else 3
            mode = 1
            entries = [(cid, n) for cid, n in _Counter(cards).items()]
            line = deckcode.encode(entries, "%d.%d" % (mode, cls))
            return chr(10).join([
                "#%s#" % name,
                "#%s#" % deckcode.MODES.get(mode, "指定系列"),
                "#%s#" % deckcode.CLASSES.get(cls, "巫师"),
                line,
                "#在游戏中点击【卡牌】-【新牌组】-【使用牌组码】进行粘贴 #",
            ])

        def _copy_text(text):
            """复制到剪贴板。先用 Tk，失败再直接用 Win32（不依赖窗口焦点）。"""
            try:
                win.clipboard_clear()
                win.clipboard_append(text)
                win.update()
                if win.clipboard_get() == text:
                    return True
            except Exception:
                pass
            try:
                import ctypes
                from ctypes import wintypes
                u = ctypes.windll.user32
                k = ctypes.windll.kernel32
                k.GlobalAlloc.restype = ctypes.c_void_p
                k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
                k.GlobalLock.restype = ctypes.c_void_p
                k.GlobalLock.argtypes = [ctypes.c_void_p]
                k.GlobalUnlock.argtypes = [ctypes.c_void_p]
                k.GlobalFree.argtypes = [ctypes.c_void_p]
                u.SetClipboardData.restype = ctypes.c_void_p
                u.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
                data = text.encode("utf-16-le") + bytes(2)
                h = k.GlobalAlloc(0x0002 | 0x0040, len(data))
                if not h:
                    return False
                p = k.GlobalLock(h)
                if not p:
                    k.GlobalFree(h)
                    return False
                ctypes.memmove(p, data, len(data))
                k.GlobalUnlock(h)
                if not u.OpenClipboard(None):
                    return False
                try:
                    u.EmptyClipboard()
                    return bool(u.SetClipboardData(13, h))
                finally:
                    u.CloseClipboard()
            except Exception:
                return False

        def _export(name, cards):
            """导出卡组码：直接复制到剪贴板，并打开窗口方便核对。"""
            from tkinter import messagebox
            try:
                text = _build_code(name, cards)
            except Exception as e:
                messagebox.showerror("导出失败", str(e), parent=win)
                return
            copied = _copy_text(text)
            _show_code(name, text, copied)

        from collections import Counter
        if not decks:
            tk.Label(inner,
                     text="还没有导入卡组。" + chr(10) * 2 +
                          "点「导入卡组码」粘贴游戏里生成的卡组码即可。",
                     bg=BG, fg=DIM, font=self.f_s, justify="left",
                     anchor="w").pack(fill="x", padx=8, pady=10)
        import battle_stats
        from tkinter import messagebox
        stat_labels = {}

        def refresh_stats():
            totals = battle_stats.deck_counts()
            for key, label in stat_labels.items():
                counts = totals.get(key, {})
                wins, losses = counts.get("wins", 0), counts.get("losses", 0)
                total = wins + losses
                rate = "%.1f%%" % (wins * 100 / total) if total else "—"
                label.config(text=f"使用胜率 {rate}    {total} 场 · {wins} 胜 · {losses} 负")

        def reset_stats(key, name):
            if messagebox.askyesno("清零卡组战绩", f"清零「{name}」的使用战绩？", parent=win):
                battle_stats.reset_deck(key)
                refresh_stats()

        for x in decks:
            name = x.get("name") or "?"
            cards = x.get("cards") or []
            row = tk.Frame(inner, bg=BG2)
            row.pack(fill="x", padx=8, pady=4)
            top = tk.Frame(row, bg=BG2)
            top.pack(fill="x", padx=8, pady=(6, 2))
            tk.Label(top, text="【%s】%d 张" % (name, len(cards)),
                     bg=BG2, fg=FG, font=self.f_s, anchor="w").pack(
                side="left")
            tk.Button(top, text="重命名", command=lambda n=name: _rename(n),
                      bg=BG, fg=GOLD2, relief="flat", bd=0, padx=8,
                      pady=3, font=self.f_s, cursor="hand2").pack(
                side="right", padx=(0, 4))
            tk.Button(top, text="导出码",
                      command=lambda n=name, c=list(cards): _export(n, c),
                      bg=BG, fg=GOLD, relief="flat", bd=0, padx=8,
                      pady=3, font=self.f_s, cursor="hand2").pack(
                side="right", padx=(0, 4))
            tk.Button(top, text="删除", command=lambda n=name: _delete(n),
                      bg=BG, fg=RED, relief="flat", bd=0, padx=8,
                      pady=3, font=self.f_s, cursor="hand2").pack(
                side="right", padx=(0, 4))
            if name == active:
                tk.Label(top, text="当前使用", bg=BG2, fg=GREEN,
                         font=self.f_s).pack(side="right")
            else:
                tk.Button(top, text="设为当前", command=lambda n=name: _pick(n),
                          bg=BG, fg=GOLD, relief="flat", bd=0, padx=10,
                          pady=3, font=self.f_s, cursor="hand2").pack(
                    side="right")
            stats_row = tk.Frame(row, bg=BG2)
            stats_row.pack(fill="x", padx=8, pady=(0, 4))
            key = battle_stats.deck_key(x)
            label = tk.Label(stats_row, bg=BG2, fg=GOLD, font=self.f_s, anchor="w")
            label.pack(side="left")
            stat_labels[key] = label
            tk.Button(stats_row, text="清零战绩", command=lambda k=key, n=name: reset_stats(k, n),
                      bg=BG, fg=DIM, relief="flat", font=self.f_s).pack(side="right")
            detail = "  ".join(
                "%s×%d" % ((meta.get(str(cid)) or {}).get("name")
                           or ("#%d" % cid), n)
                for cid, n in Counter(cards).items())
            tk.Label(row, text=detail, bg=BG2, fg=DIM, font=self.f_s,
                     wraplength=500, justify="left", anchor="w").pack(
                fill="x", padx=8, pady=(0, 6))
        refresh_stats()
        bar = tk.Frame(win, bg=BG)
        bar.pack(side="bottom", fill="x", padx=12, pady=(0, 10))
        tk.Button(bar, text="刷新战绩", command=refresh_stats, bg=BG2, fg=FG,
                  relief="flat", font=self.f_s).pack(side="left")
        tk.Button(bar, text="关闭", command=win.destroy, bg=BG2, fg=DIM,
                  relief="flat", bd=0, padx=14, pady=5,
                  font=self.f_s, cursor="hand2").pack(side="right")

    def open_tracker_settings(self):
        import deck_tracker as tracker
        cfg = tracker.window_settings()
        dlg = SettingsDialog(self.root, "记牌器设置", 440, 430)
        dlg.slider("窗口宽度", "width", 260, 900, 10, cfg["width"], "{:.0f}")
        dlg.slider("窗口高度", "height", 200, 1080, 10, cfg["height"], "{:.0f}")
        dlg.check("按内容自动调整高度", "auto", cfg["auto"])
        dlg.slider("字号", "font", 7, 20, 1, cfg["font"], "{:.0f}")
        dlg.check("锁定记牌器（鼠标穿透，取消可解锁）", "locked", cfg["locked"])
        dlg.check("显示敌方公开牌", "show_public", cfg["show_public"])
        dlg.check("显示自己已打出牌", "show_played", cfg["show_played"])
        def apply():
            options = {k: int(dlg.vars[k].get()) for k in ("width", "height", "font")}
            options["auto"] = bool(dlg.vars["auto"].get())
            for key in ("show_public", "show_played"):
                options[key] = bool(dlg.vars[key].get())
            options["locked"] = bool(dlg.vars["locked"].get())
            save_json(tracker.WINDOW_SETTINGS, options)
            if tracker._OPEN[0] is not None:
                tracker._OPEN[0].apply_window_settings(options)
            dlg.w.destroy()
        dlg.buttons(apply, label="应用")

    def open_deck_tracker(self):
        try:
            import deck_tracker
            try:
                self._ensure_frida()
            except Exception as exc:
                print("记牌器：实时读取启动异常：%s" % exc)
            deck_tracker.open(self.root)
        except Exception as exc:
            import traceback
            detail = traceback.format_exc()
            print("记牌器窗口打开失败：%s" % exc)
            print(detail)
            with open(os.path.join(LOGS, "console-app.log"), "a", encoding="utf8") as output:
                output.write("记牌器窗口打开失败：%s\n%s\n" % (exc, detail))
            from tkinter import messagebox
            messagebox.showerror("记牌器打不开", "记牌器窗口创建失败：%s\n\n完整信息已写入 logs/console-app.log" % exc, parent=self.root)


    def open_ai_settings(self):
        try:
            import ai_settings
            ai_settings.open_settings(self.root)
        except Exception as e:
            print("AI 设置打开失败：%s" % e)

    def open_companion(self):
        try:
            import subprocess
            log = open(os.path.join(HERE, "logs", "companion.log"), "a",
                       encoding="utf-8")
            self.procs["companion"] = subprocess.Popen(
                [sys.executable, "-u",
                 os.path.join(HERE, "run_companion.py")],
                stdout=log, stderr=subprocess.STDOUT, cwd=HERE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            print("AI 陪玩已启动")
        except Exception as e:
            print("AI 陪玩启动失败：%s" % e)

    def _ensure_frida(self):
        """Check and recover readers off the Tk event loop."""
        from reader_supervisor import ReaderSupervisor
        if not hasattr(self, "_reader_supervisor"):
            self._reader_supervisor = ReaderSupervisor(HERE, self.procs)
        self._reader_supervisor.ensure()


    def _group(self, label, key, settings_cmd):
        box = tk.Frame(self.page_hj, bg=BG2)
        box.pack(fill="x", padx=12, pady=4)
        left = tk.Frame(box, bg=BG2)
        left.pack(side="left", fill="x", expand=True, padx=8, pady=6)
        tk.Label(left, text=label, bg=BG2, fg=FG, font=self.f_s,
                 anchor="w").pack(fill="x")
        lbl = tk.Label(left, text="状态：—", bg=BG2, fg=DIM, font=self.f_s,
                       anchor="w")
        lbl.pack(fill="x")
        self.labels[key] = lbl
        right = tk.Frame(box, bg=BG2)
        right.pack(side="right", padx=8, pady=6)
        tk.Button(right, text="设置", command=settings_cmd, bg=BG, fg=GOLD,
                  relief="flat", bd=0, padx=10, pady=4, font=self.f_s,
                  cursor="hand2").pack(side="right")
        tk.Button(right, text="重启", command=lambda: self.restart_one(key),
                  bg=BG, fg=DIM, relief="flat", bd=0, padx=8, pady=4,
                  font=self.f_s, cursor="hand2").pack(side="right", padx=4)

    # ---------------- 阵容统计（服务器门控） ----------------
    def _meta_group(self):
        st = meta_client.state()
        box = tk.Frame(self.page_hj, bg=BG2)
        box.pack(fill="x", padx=12, pady=4)
        left = tk.Frame(box, bg=BG2)
        left.pack(side="left", fill="x", expand=True, padx=8, pady=6)
        tk.Label(left, text="阵容统计", bg=BG2, fg=FG, font=self.f_s,
                 anchor="w").pack(fill="x")
        self.meta_status = tk.Label(left, text=st["reason"],
                 bg=BG2, fg=DIM, font=self.f_s, anchor="w")
        self.meta_status.pack(fill="x")
        right = tk.Frame(box, bg=BG2)
        right.pack(side="right", padx=8, pady=6)
        self.meta_open = tk.Button(right, text="打开", command=self.open_meta_page, bg=BG, fg=GOLD,
                  relief="flat", bd=0, padx=10, pady=4, font=self.f_s,
                  cursor="hand2", state="normal" if st["available"] else "disabled")
        self.meta_open.pack(side="right")
        tk.Button(right, text="设置", command=self.open_meta_settings, bg=BG,
                  fg=DIM, relief="flat", bd=0, padx=8, pady=4, font=self.f_s,
                  cursor="hand2").pack(side="right", padx=4)

    def _rebuild_meta_entry(self):
        st = meta_client.state()
        self.meta_status.configure(text=st["reason"])
        self.meta_open.configure(state="normal" if st["available"] else "disabled")

    def open_meta_page(self):
        try:
            import meta_page
        except Exception as e:
            print("统计页模块加载失败：%s" % e)
            return
        w = meta_page.open(self.root)
        if w is None:
            print("统计页不可用：未连接服务器")

    def open_meta_settings(self):
        """统计服务器：地址 + 启用。保存时探测一次（未连接就撤掉入口）。"""
        cfg = meta_client.load_settings()
        dlg = SettingsDialog(self.root, "阵容统计 · 服务器", 470, 330)
        dlg.section("统计服务器")
        dlg.check("启用统计（连接后收集数据）", "meta_on",
                  bool(cfg.get("enabled")))
        dlg.entry("服务器地址", "meta_server", cfg.get("server") or "", width=34)
        tk.Label(dlg.body,
                 text="未连接服务器时：不收集数据，也不显示统计页。\n"
                      "地址示例：https://your-server.example.com",
                 bg=BG, fg=DIM, font=dlg.f_s, justify="left", anchor="w",
                 wraplength=430).pack(fill="x", pady=(4, 0))

        def apply():
            cfg["enabled"] = bool(dlg.vars["meta_on"].get())
            cfg["server"] = (dlg.vars["meta_server"].get() or "").strip()
            ok = False
            if cfg["enabled"] and cfg["server"]:
                meta_client.save_settings(cfg)
                ok = meta_client.probe(cfg["server"])       # 最多等 ~5 秒
            else:
                cfg["connected"] = False
                cfg["connected_at"] = 0.0
                meta_client.save_settings(cfg)
            dlg.w.destroy()
            print("统计服务器：" + ("已连接" if ok else "未连接"))
            self._rebuild_meta_entry()

        dlg.buttons(apply, label="测试并保存")

    @staticmethod
    def _find_pid(pattern):
        """按命令行特征找一个已在运行的 python 子进程（找不到返回 0）"""
        ps = ("Get-CimInstance Win32_Process -Filter "
              "\"Name='python.exe' or Name='pythonw.exe'\" | "
              "Where-Object { $_.CommandLine -match '%s' -and $_.ProcessId -ne %d } | "
              "Select-Object -First 1 -ExpandProperty ProcessId" % (pattern, os.getpid()))
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                 "-Command", ps],
                capture_output=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                text=True).stdout.strip()
            return int(out.splitlines()[0]) if out else 0
        except Exception:
            return 0

    def _spawn(self, key, args, cwd):
        # 所有子进程都由控制台管理；重复启动前先结束旧的
        self._kill(key)
        os.makedirs(LOGS, exist_ok=True)
        out = open(os.path.join(LOGS, f"{key}-out.txt"), "a", encoding="utf-8",
                   errors="replace")
        err = open(os.path.join(LOGS, f"{key}-err.txt"), "a", encoding="utf-8",
                   errors="replace")
        try:
            self.procs[key] = subprocess.Popen(
                [PY, "-u"] + args, cwd=cwd, stdout=out, stderr=err,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            print(f"启动 {key} 失败: {e}")

    def _kill(self, key):
        """
        结束本控制台管理的一个子进程（不区分外部/自启，统一结束）。

        ⚠️ frida_live 不能 taskkill：agent 挂在游戏进程里，强杀会让游戏崩。
        先写停止标记让它自己 unload，等不到才强杀兜底。
        """
        self._start_generation[key] = self._start_generation.get(key, 0) + 1
        p = self.procs.pop(key, None)
        if key == "frida":
            self._stop_frida(p)
            return
        if p is None:
            return
        if p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def _stop_frida(self, p=None):
        """请求读取器卸载；等待在后台线程，不阻塞 Tk/UI 或游戏关闭。"""
        stop = os.path.join(LOGS, "_frida.stop")
        try:
            os.makedirs(LOGS, exist_ok=True)
            with open(stop, "w", encoding="ascii"):
                pass
        except Exception:
            pass

        def _finish():
            if p is not None and p.poll() is None:
                try:
                    p.wait(timeout=3)
                except Exception:
                    return
            try:
                os.remove(stop)
            except Exception:
                pass
        try:
            threading.Thread(target=_finish, daemon=True).start()
        except Exception:
            pass

    def _cmd_for(self, key):
        if key == "overlay":
            return [os.path.join(TOOLS, "overlay.py")], GAME
        if key == "panel":
            return [os.path.join(HERE, "panel_ui.py")], HERE
        if key == "leader":
            # 主战者效果面板：宽高/锁定从 leader-settings.json 读
            ld = load_json(LEADER_SET, DEFAULT_LEADER)
            try:
                lw = max(200, int(ld.get("w", 320)))
                lh = max(90, int(ld.get("h", 160)))
            except (TypeError, ValueError):
                lw, lh = 320, 160
            args = [os.path.join(HERE, "leader_panel.py"),
                    "--w", str(lw), "--h", str(lh)]
            # 用户要求：**不跟随战绩面板**，固定放右下角并默认锁定。
            # 跟随模式已从设置里删掉。
            try:
                # 面板的 --x/--y 是**逻辑像素**（和战绩面板同一套坐标）。
                # 之前传的是物理像素，被 Tk 再乘一次 1.75 → 跑到屏幕外 (3416,2142)。
                import ctypes as _ct
                _u = _ct.windll.user32
                _sw, _sh = _u.GetSystemMetrics(0), _u.GetSystemMetrics(1)
                _dpi = 1.0
                try:
                    _dpi = _u.GetDpiForSystem() / 96.0
                except Exception:
                    _dpi = 1.0
                _lw_s = int(_sw / _dpi)
                _lh_s = int(_sh / _dpi)
                _px = max(0, _lw_s - lw - 20)      # 右边留 20
                _py = max(0, _lh_s - lh - 40)      # 下边留 40
                # 上次拖动过就接着用那个位置（悬浮窗不锁定时可拖）
                try:
                    _sx, _sy = int(ld.get("x")), int(ld.get("y"))
                    _px = max(0, min(_lw_s - 60, _sx))
                    _py = max(0, min(_lh_s - 60, _sy))
                except Exception:
                    pass
                args += ["--x", str(_px), "--y", str(_py)]
            except Exception:
                pass
            if bool(ld.get("locked", False)):
                args.append("--locked")
            return (args, HERE)
        delay = str(DEFAULT_JUDGE.get("delay", 0.2))
        return ([os.path.join(HERE, "watch_mem.py"),
                 "--timeout", "10800", "--delay", delay], HERE)

    def start_one(self, key, pause=0.0):
        if key in ("overlay", "panel", "leader") and not self._orphan_cleanup_done.is_set():
            generation = self._start_generation.get(key, 0)
            def retry():
                if generation == self._start_generation.get(key, 0) and self.tab == "hj":
                    self.start_one(key, pause)
            self.root.after(100, retry)
            return
        if key == "frida":
            self._ensure_frida()
            return
        # 所有子进程都由控制台自己启动（不再接管外部判定器）
        cur = self.procs.get(key)
        if cur is not None and cur.poll() is None:
            self._update_status()
            return
        args, cwd = self._cmd_for(key)
        if pause:
            generation = self._start_generation.get(key, 0)
            tab = self.tab
            def delayed():
                if self.tab == tab and generation == self._start_generation.get(key, 0):
                    self.start_one(key)
            self.root.after(round(pause * 1000), delayed)
            return
        self._spawn(key, args, cwd)
        self._update_status()

    def restart_one(self, key):
        self._kill(key)
        self.start_one(key)

    def _restore_running(self):
        """
        按**上次关闭时**的运行状态恢复子进程；第一次（没有记录）全部关闭。

        用户 2026-10-21：开启插件时不要自动启动所有东西，按上次状态来。
        影之幻境（overlay/panel/leader/judge）和影之诗（deck）分开记。
        """
        cs = load_json(CONSOLE_SET, DEFAULT_CONSOLE)
        st = cs.get("running")
        if not isinstance(st, dict) or not st:
            print("首次启动：所有插件默认关闭（在对应模块里各自启动）")
            self._update_status()
            return
        # 模块互斥：只恢复上次使用的那个模块（用户 2026-10-29 要求）
        tab = str(cs.get("tab") or "hj")
        order = (("overlay", 0.0), ("panel", 1.0), ("leader", 0.3), ("judge", 0.5))
        if tab == "hj":
            delay = 0.0
            for key, pause in order:
                if st.get(key):
                    delay += pause
                    self.start_one(key, pause=delay)
        elif st.get("deck"):
            self.open_deck_tracker()
        # AI 陪玩（宠物）：上次开着就一起恢复（属于影之诗模块）
        if st.get("companion") and tab == "sv":
            self.open_companion()
        # 实时读取（frida_live）：不恢复的话记牌器没有手牌数据
        if st.get("frida") and tab == "sv":
            self.start_one("frida")
        names = [k for k, _p in order if st.get(k)]
        if st.get("deck"):
            names.append("deck（记牌器）")
        if st.get("companion") and tab == "sv":
            names.append("AI 陪玩")
        if st.get("frida") and tab == "sv":
            names.append("实时读取")
        print("已恢复上次运行状态：" + ("、".join(names) if names else "（无）"))
        self._update_status()

    def _save_running_state(self):
        """退出前记下各子进程是否在跑，下次启动按这个恢复。"""
        try:
            st = {}
            for key in ("overlay", "panel", "leader", "judge"):
                p = self.procs.get(key)
                st[key] = bool(p is not None and p.poll() is None)
            try:
                import deck_tracker
                st["deck"] = deck_tracker._OPEN[0] is not None
            except Exception:
                st["deck"] = False
            try:
                p = self.procs.get("companion")
                st["companion"] = bool(p is not None and p.poll() is None)
            except Exception:
                st["companion"] = False
            try:
                p = self.procs.get("frida")
                st["frida"] = bool(p is not None and p.poll() is None)
            except Exception:
                st["frida"] = False
            cs = load_json(CONSOLE_SET, DEFAULT_CONSOLE)
            cs["running"] = st
            save_json(CONSOLE_SET, cs)
            print("已保存运行状态：%s" % st)
        except Exception as e:
            print("保存运行状态失败：%s" % e)

    def start_all(self):
        self.start_one("overlay")
        self.start_one("panel", pause=1.0)
        self.start_one("leader", pause=0.3)
        self.start_one("judge", pause=0.5)

    def restart_all(self):
        """
        「重启全部」：只重启三个显示窗口，判定器保持运行（由控制台管理）。

        判定器（watch_mem.py）是长跑进程，杀掉会中断正在进行的对局解析；
        这里只在它已经退出时重新拉起。需要连判定器一起重启请用「强制重启」。
        """
        for key in ("overlay", "panel", "leader"):
            self._kill(key)
        jp = self.procs.get("judge")
        if jp is None or jp.poll() is not None:
            self.procs.pop("judge", None)
            self.start_one("judge")
        else:
            self._update_status()
        self.start_one("overlay")
        self.start_one("panel", pause=1.0)
        self.start_one("leader", pause=0.3)
        print("已重启 3 个显示窗口（判定器保持运行，未重启）")

    # ---------------- 强制重启 / 模块互斥 / 自动识别（2026-10-29） ----------------
    def _force_kill_proc(self, key):
        """强制结束（包括外部接管的判定器）；frida 仍走优雅停。"""
        if key == "frida":
            self._kill("frida")
            return
        p = self.procs.pop(key, None)
        if p is None:
            return
        pid = getattr(p, "pid", None)
        if pid:
            try:
                subprocess.call(["taskkill", "/F", "/PID", str(pid)],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
            except Exception:
                pass
        try:
            p.kill()
        except Exception:
            pass

    def _stop_other_module(self, keep):
        """模块互斥：保留 keep（hj/sv），停掉另一个模块的所有功能。"""
        if keep == "hj":
            # 停影之诗侧：实时读取 + 宠物 + 记牌器窗口
            self._force_kill_proc("frida")
            self._force_kill_proc("companion")
            try:
                import deck_tracker
                if deck_tracker._OPEN[0] is not None:
                    deck_tracker._OPEN[0].close()
            except Exception:
                pass
        else:
            # 停影之幻境侧：悬浮层/战绩/悬浮窗/判定器
            for key in ("overlay", "panel", "leader", "judge"):
                self._force_kill_proc(key)
        self._update_status()

    def restart_hard(self):
        """强制重启：所有功能进程（含判定器/实时读取/宠物）全停，再按上次状态重开。"""
        cs = load_json(CONSOLE_SET, DEFAULT_CONSOLE)
        st = cs.get("running") or {}
        tab = str(cs.get("tab") or "hj")
        for key in ("overlay", "panel", "leader", "judge", "frida",
                    "companion"):
            self._force_kill_proc(key)
        try:
            import deck_tracker
            if deck_tracker._OPEN[0] is not None:
                deck_tracker._OPEN[0].close()
        except Exception:
            pass
        if tab == "hj":
            keys = [("overlay", 0.0), ("panel", 1.0), ("leader", 0.3),
                    ("judge", 0.5)]
            if not st:
                for key, pause in keys:
                    self.start_one(key, pause=pause)
            else:
                for key, pause in keys:
                    if st.get(key):
                        self.start_one(key, pause=pause)
        else:
            if st.get("deck") or not st:
                self.open_deck_tracker()
        self._update_status()
        print("已强制重启（含判定器 / 实时读取 / 宠物）")

    def _auto_detect_tab(self):
        """自动识别当前游戏界面：幻境对局 → 影之幻境；影之诗对局 → 影之诗。"""
        try:
            import grab as GRAB
            import phase as PH
            img = GRAB.grab_screen()
            ph = PH.phase_of(img) if img is not None else "other"
        except Exception:
            ph = "other"
        try:
            p = os.path.join(HERE, "logs", "deck_live.json")
            sv_live = (os.path.exists(p)
                       and (time.time() - os.path.getmtime(p)) < 8)
        except Exception:
            sv_live = False
        want = None
        if ph in ("battle", "operate"):
            want = "hj"
        elif sv_live:
            want = "sv"
        cur = getattr(self, "tab", "hj")
        if want and want != cur:
            self.switch_tab(want)
            print("自动识别：切换到「%s」" % ("影之幻境" if want == "hj" else "影之诗"))

    def restart_console(self):
        """重启整个控制台：先把新进程排上（延时等旧进程释放锁），再后台停子进程并退出。

        停子进程（尤其 frida 要等 unload）不能放在主线程——否则窗口会卡成
        Windows 的「未响应」。
        """
        try:
            self._save_running_state()
        except Exception:
            pass
        try:
            proc = subprocess.Popen(
                [PY, os.path.join(HERE, "app.py"), "--delay", "6"],
                cwd=HERE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            print("正在重启控制台（新进程 PID %s）" % proc.pid)
        except Exception as e:
            print("重启控制台失败：%s" % e)
            return
        # 主线程立刻返回，窗口保持响应；子进程（含 frida 优雅停）在后台处理
        def _finish():
            try:
                self.stop_all()
            except Exception:
                pass
            os._exit(0)

        try:
            threading.Thread(target=_finish, daemon=True).start()
        except Exception:
            _finish()

    def stop_all(self):
        for key in ("overlay", "panel", "leader"):
            self._kill(key)
        # frida_live 挂在游戏进程里，必须优雅停（强杀会让游戏崩）
        self._kill("frida")
        self._force_kill_proc("companion")
        # 判定器：控制台退出/全停时一起结束（所有进程都归控制台管理）
        self._force_kill_proc("judge")
        try:
            if threading.current_thread() is threading.main_thread():
                self._update_status()
        except Exception:
            pass

    def _update_status(self):
        for key, lbl in self.labels.items():
            p = self.procs.get(key)
            alive = bool(p and p.poll() is None)
            txt = "状态：" + ("运行中" if alive else "已停止")
            lbl.config(text=txt, fg=GREEN if alive else RED)

    def _check_show_request(self):
        path = _console_show_request()
        try:
            if os.path.isfile(path):
                os.remove(path)
                self.restore_from_tray()
        except OSError:
            pass

    def _tick(self):
        self._check_show_request()
        self._update_status()
        self._apply_style()
        # AI 陪玩状态：已启用/未启用（读 companion 配置，设置改完自动刷新）
        try:
            import companion as _comp
            ac = _comp.load_config()
            if ac.get("enabled") and (ac.get("api_key") or "").strip():
                prov = str(ac.get("provider") or "").strip().split("（")[0].strip()
                model = str(ac.get("model") or "").strip()
                label = " / ".join(x for x in (prov, model) if x)
                self.ai_status.config(
                    text=("运行中 · " if self.procs.get("companion") is not None and self.procs["companion"].poll() is None
                          else "已配置 · 陪玩已关闭 · ") + (label or "未选择模型"), fg=GREEN)
            else:
                self.ai_status.config(text="未启用", fg=DIM)
        except Exception:
            pass
        # 自动识别：幻境「准备界面」和「对局界面」特征不同，暂不启用
        # （用户 2026-10-29：幻境模式没开，先不做；以后用实测样本验证过再打开这里）
        # now_tab = time.time()
        # if now_tab - getattr(self, "_last_tab_detect", 0) > 5.0:
        #     self._last_tab_detect = now_tab
        #     self._auto_detect_tab()
        # 从最小化恢复后，如果 <Map> 没触发，这里兜底装回无边框/置顶
        try:
            if (not self._minimizing and self.root.state() == "normal"
                    and not ctypes.windll.user32.IsIconic(self._hwnd())
                    and not self.root.overrideredirect()):
                self._restore_borderless()
        except Exception:
            pass
        now = time.time()
        if now - getattr(self, "_last_frida_watchdog", 0) >= 5:
            self._last_frida_watchdog = now
            try:
                cs = load_json(CONSOLE_SET, DEFAULT_CONSOLE)
                if cs.get("running", {}).get("frida") and str(cs.get("tab") or "hj") == "sv":
                    self._ensure_frida()
            except Exception:
                pass
        if getattr(self, "_last_snapshot_cleanup", 0) == 0:
            self._last_snapshot_cleanup = now
            cleanup_snapshots()
        elif now - self._last_snapshot_cleanup >= 600:
            self._last_snapshot_cleanup = now
            cleanup_snapshots()
        if now - self._last_sweep > 600:
            self._last_sweep = now
            self._sweep_background()
        self.root.after(2000, self._tick)

    # ---------------- 设置 ----------------
    def open_overlay_settings(self):
        ov = load_json(OVERLAY_SET, DEFAULT_OVERLAY)
        dlg = SettingsDialog(self.root, "胜率悬浮层 · 设置", 420, 410)

        def preview_ov(key, v):
            try:
                ov[key] = round(float(v), 2)
                save_json(OVERLAY_SET, ov)
            except Exception:
                pass

        def preview_ov_bool(key, v):
            try:
                ov[key] = bool(v)
                save_json(OVERLAY_SET, ov)
            except Exception:
                pass

        dlg.section("悬浮层")
        dlg.slider("背景不透明度", "ov_alpha", 0.3, 1.0, 0.02, ov["alpha"],
                   "{:.2f}", on_change=lambda v: preview_ov("alpha", v))
        dlg.slider("文字不透明度", "ov_text_alpha", 0.2, 1.0, 0.02,
                   ov.get("text_alpha", 1.0), "{:.2f}",
                   on_change=lambda v: preview_ov("text_alpha", v))
        dlg.slider("缩放", "ov_scale", 0.6, 2.2, 0.05, ov["scale"],
                   "{:.2f}", on_change=lambda v: preview_ov("scale", v))
        dlg.slider("字号", "ov_font", 9, 24, 1, ov.get("font_size", 16),
                   "{:.0f}",
                   on_change=lambda v: preview_ov("font_size", int(round(v))))

        colors = [("#c9a86a", "香槟金"), ("#ffb6c1", "淡粉"),
                  ("#8fd3ff", "淡蓝"), ("#ffffff", "白")]
        cur_color = str(ov.get("titleColor") or "#c9a86a")
        if cur_color not in [c for c, _ in colors]:
            colors.append((cur_color, "当前"))
        dlg.radio_row("标题颜色", "ov_color", cur_color, colors)
        dlg.radio_row("胜率模式", "ov_mode",
                      str(ov.get("mode", "static")),
                      (("dynamic", "动态（持续更新）"), ("static", "静态（开打锁定）")))
        dlg.check("强制置顶（压在游戏之上）", "ov_top",
                  ov.get("forceTopmost", True))
        # 默认锁定 = 点击穿透，不挡游戏操作；取消后按住文字即可拖动
        dlg.check("锁定悬浮层（点击穿透）", "ov_locked",
                  bool(ov.get("locked", True)))
        def apply():
            ov["alpha"] = round(dlg.vars["ov_alpha"].get(), 2)
            ov["text_alpha"] = round(dlg.vars["ov_text_alpha"].get(), 2)
            ov["scale"] = round(dlg.vars["ov_scale"].get(), 2)
            ov["font_size"] = int(round(dlg.vars["ov_font"].get()))
            ov["titleColor"] = dlg.vars["ov_color"].get()
            ov["mode"] = dlg.vars["ov_mode"].get()
            ov["forceTopmost"] = bool(dlg.vars["ov_top"].get())
            ov["locked"] = bool(dlg.vars["ov_locked"].get())
            save_json(OVERLAY_SET, ov)
            dlg.w.destroy()
            print("悬浮层设置已保存（热生效，无需重启）")

        dlg.buttons(apply, label="应用")

    def open_panel_settings(self):
        pn = load_json(PANEL_SET, DEFAULT_PANEL)
        dlg = SettingsDialog(self.root, "战绩面板 · 设置", 420, 390)

        def preview(key, v):
            # 实时预览：写文件（面板每 500ms 轮询读取并立即应用），
            # 但不重启面板（重启会闪、也会打断拖动）。
            try:
                pn[key] = round(float(v), 2)
                save_json(PANEL_SET, pn)
            except Exception:
                pass

        def preview_int(key, v):
            try:
                pn[key] = int(round(float(v)))
                save_json(PANEL_SET, pn)
            except Exception:
                pass

        def preview_bool(key, v):
            try:
                pn[key] = bool(v)
                save_json(PANEL_SET, pn)
            except Exception:
                pass

        dlg.section("战绩面板")
        dlg.slider("背景不透明度", "pn_alpha", 0.0, 1.0, 0.05, pn["alpha"],
                   "{:.2f}", on_change=lambda v: preview("alpha", v))
        dlg.slider("文字不透明度", "pn_text_alpha", 0.5, 1.0, 0.05,
                   pn.get("text_alpha", 1.0), "{:.2f}",
                   on_change=lambda v: preview("text_alpha", v))
        dlg.slider("尺寸", "pn_scale", 0.6, 2.0, 0.05,
                   pn.get("scale", 1.0), "{:.2f}",
                   on_change=lambda v: preview("scale", v))
        dlg.slider("字号", "pn_font", 7, 16, 1, pn.get("font_size", 9),
                   "{:.0f}", on_change=lambda v: preview_int("font_size", v))
        # 「主战者显示」已删除：panel_ui.py 只读取 hero_display、从未使用，
        # 属于无效设置（用户要求"不要有无效项"）。
        # 显示时间 / 强制置顶 / 锁定 由 panel_ui 每 500ms 热读，应用即生效。
        dlg.check("显示时间", "pn_time", bool(pn.get("show_time", True)))
        dlg.check("强制置顶（压在游戏之上）", "pn_top",
                  pn.get("force_topmost", True))
        lock_var = dlg.check("锁定（点击穿透）", "pn_locked",
                             pn.get("locked", False))
        lock_var.trace_add("write",
                           lambda *_: preview_bool("locked", lock_var.get()))

        def apply():
            pn["alpha"] = round(dlg.vars["pn_alpha"].get(), 2)
            pn["text_alpha"] = round(dlg.vars["pn_text_alpha"].get(), 2)
            pn["scale"] = round(dlg.vars["pn_scale"].get(), 2)
            pn["font_size"] = int(round(dlg.vars["pn_font"].get()))
            pn["show_time"] = bool(dlg.vars["pn_time"].get())
            pn["force_topmost"] = bool(dlg.vars["pn_top"].get())
            pn["locked"] = bool(dlg.vars["pn_locked"].get())
            save_json(PANEL_SET, pn)
            dlg.w.destroy()
            print("战绩面板设置已保存（热生效，无需重启）")

        dlg.buttons(apply, label="应用")

    def open_leader_settings(self):
        """悬浮窗 · 设置（不透明度/字号热生效，宽高重启生效）"""
        st = load_json(LEADER_SET, DEFAULT_LEADER)
        st.pop("follow", None)   # 跟随战绩面板的旧字段：已废弃，不再写回
        dlg = SettingsDialog(self.root, "悬浮窗 · 设置", 440, 310)
        init_w = int(st.get("w", 320))
        init_h = int(st.get("h", 160))

        def preview(key, v):
            # 写文件即可：leader_panel.py 每轮热读 alpha / font_size
            try:
                st[key] = round(float(v), 2) if key == "alpha" else int(round(float(v)))
                save_json(LEADER_SET, st)
            except Exception:
                pass

        dlg.section("悬浮窗面板")
        dlg.slider("不透明度", "ld_alpha", 0.3, 1.0, 0.02,
                   st.get("alpha", 0.90), "{:.2f}",
                   on_change=lambda v: preview("alpha", v))
        dlg.slider("字号", "ld_font", 7, 14, 1, st.get("font_size", 9),
                   "{:.0f}", on_change=lambda v: preview("font_size", v))
        dlg.slider("宽度", "ld_w", 200, 800, 20, init_w, "{:.0f}")
        dlg.slider("高度", "ld_h", 90, 600, 20, init_h, "{:.0f}")

        def apply():
            st["w"] = int(dlg.vars["ld_w"].get())
            st["h"] = int(dlg.vars["ld_h"].get())
            st["alpha"] = round(float(dlg.vars["ld_alpha"].get()), 2)
            st["font_size"] = int(round(float(dlg.vars["ld_font"].get())))
            save_json(LEADER_SET, st)
            dlg.w.destroy()
            if (st["w"], st["h"]) != (init_w, init_h):
                self.restart_one("leader")
                print("悬浮窗设置已保存（宽度/高度变化，已重启面板）")
            else:
                print("悬浮窗设置已保存（不透明度/字号已热生效）")

        dlg.buttons(apply, label="应用")

    def open_console_settings(self):
        """控制台自己的设置：不透明度 + 关闭按钮行为（console-settings.json）"""
        cs = load_json(CONSOLE_SET, DEFAULT_CONSOLE)
        ov = load_json(OVERLAY_SET, DEFAULT_OVERLAY)
        dlg = SettingsDialog(self.root, "控制台 · 设置", 480, 440)

        def live_alpha(v):
            try:
                a = max(0.4, min(1.0, float(v)))
                self.root.attributes("-alpha", a)
                self.console_alpha = a
                cs["consoleAlpha"] = round(a, 2)
                save_json(CONSOLE_SET, cs)
            except Exception:
                pass

        def live_font(v):
            try:
                fs = max(8, min(14, int(round(float(v)))))
                self._font_job = getattr(self, "_font_job", None)
                if self._font_job is not None:
                    try:
                        self.root.after_cancel(self._font_job)
                    except Exception:
                        pass

                def _apply():
                    try:
                        self._apply_console_fonts(fs)
                        cs["font_size"] = fs
                        save_json(CONSOLE_SET, cs)
                    except Exception:
                        pass

                self._font_job = self.root.after(250, _apply)
            except Exception:
                pass

        def live_scale(v):
            """面板大小：整体缩放窗口（和字号独立）；拖动时防抖，停下再应用。"""
            try:
                sc = max(0.7, min(2.0, float(v)))
                self._scale_job = getattr(self, "_scale_job", None)
                if self._scale_job is not None:
                    try:
                        self.root.after_cancel(self._scale_job)
                    except Exception:
                        pass

                def _apply():
                    try:
                        self._apply_console_fonts(self.console_font_size, sc)
                        cs["scale"] = round(sc, 2)
                        save_json(CONSOLE_SET, cs)
                    except Exception:
                        pass

                self._scale_job = self.root.after(250, _apply)
            except Exception:
                pass

        dlg.section("外观")
        dlg.slider("不透明度", "console_alpha", 0.4, 1.0, 0.05,
                   self.console_alpha, "{:.2f}", on_change=live_alpha)
        dlg.slider("字号", "console_font", 8, 14, 1,
                   self.console_font_size, "{:.0f}", on_change=live_font)
        dlg.slider("面板大小", "console_scale", 0.7, 2.0, 0.05,
                   getattr(self, "console_scale", 1.0), "{:.2f}",
                   on_change=live_scale)

        dlg.section("AI 陪玩")

        def _open_ai(d):
            try:
                import ai_settings
                ai_settings.open_settings(d.w)
            except Exception:
                pass

        tk.Button(dlg.body, text="打开 AI 设置（API Key / 语音）",
                  command=lambda: _open_ai(dlg), bg=BG2, fg=GOLD,
                  relief="flat", bd=0, padx=10, pady=3, font=dlg.f_s,
                  activebackground=BG, cursor="hand2").pack(anchor="w",
                                                           pady=(2, 0))
        tk.Label(dlg.body, text="没填 API Key 时陪玩不启用、不联网",
                 bg=BG, fg=GOLD2, font=dlg.f_s, anchor="w").pack(fill="x")

        dlg.section("直播设定")
        live_var = dlg.check("直播模式（不透明背景）", "console_live",
                             bool(ov.get("liveMode", False)))

        def live_mode(*_a):
            try:
                ov["liveMode"] = bool(live_var.get())
                save_json(OVERLAY_SET, ov)
                print("直播模式：" + ("开启（OBS 可窗口采集）"
                                      if ov["liveMode"] else "关闭（透明）"))
            except Exception:
                pass

        live_var.trace_add("write", live_mode)
        tk.Label(dlg.body,
                 text="开启后，影之诗和影之幻境共用直播模式。OBS 可分别采集记牌器、宠物和气泡、幻境面板。",
                 bg=BG, fg=DIM, font=dlg.f_s, justify="left", anchor="w",
                 wraplength=430).pack(fill="x", pady=(2, 0))

        dlg.section("显卡（Live2D 宠物 / 字幕）")
        _gpus, _gpu_idx = [], -1
        try:
            import gpu_select
            _gpus = gpu_select.list_gpus()
            # 当前选择按「显卡序号」记录（-1 = 自动），换机器时序号对不上就回自动
            try:
                _gpu_idx = int(cs.get("gpu_index", -1))
            except (TypeError, ValueError):
                _gpu_idx = -1
            if not (-1 <= _gpu_idx < len(_gpus)):
                _gpu_idx = -1
            _opts = [("-1", "自动（系统决定）")]
            for _i, _g in enumerate(_gpus):
                _kind = ("独显" if _g["kind"] == "discrete" else
                         "核显" if _g["kind"] == "integrated" else "显卡")
                _vram = (" · %d GB" % round(_g["vram_mb"] / 1024)) if _g["vram_mb"] else ""
                _opts.append((str(_i), "%s（%s%s）" % (_g["name"], _kind, _vram)))
            _row = tk.Frame(dlg.body, bg=BG)
            _row.pack(fill="x", pady=(2, 0))
            _gpu_var = tk.StringVar(value=str(_gpu_idx))
            dlg.vars["gpu_index"] = _gpu_var
            for _val, _lab in _opts:
                tk.Radiobutton(_row, text=_lab, variable=_gpu_var, value=_val,
                               bg=BG, fg=FG, selectcolor=SELECT,
                               activebackground=BG2, activeforeground="#ffffff",
                               font=dlg.f_s, anchor="w", justify="left",
                               highlightthickness=0).pack(anchor="w")
            tk.Label(dlg.body,
                     text="选独显 / 核显会写进 Windows「图形设置」，重启助手生效。\n"
                          "Windows 只区分「高性能 / 省电」两档，所以选哪块卡等价于选那一档。\n"
                          "Live2D 走 OpenGL；若没效果，可在 NVIDIA 控制面板 → "
                          "管理 3D 设置 → 程序设置里把 pythonw.exe 单独指定为高性能。",
                     bg=BG, fg=DIM, font=dlg.f_s, justify="left", anchor="w",
                     wraplength=430).pack(fill="x", pady=(2, 0))
        except Exception as _gpu_err:
            tk.Label(dlg.body, text="显卡设置不可用：%s" % _gpu_err,
                     bg=BG, fg=DIM, font=dlg.f_s, anchor="w",
                     wraplength=430).pack(fill="x")

        dlg.section("关闭按钮行为")
        mode_var = dlg.radio_row("点 ✕ 时", "closeAction",
                                 str(cs.get("closeAction") or "minimize"),
                                 (("minimize", "隐藏到托盘"),
                                  ("exit", "退出程序")))

        def live_close_action(*_a):
            v = mode_var.get()
            if v not in ("minimize", "exit"):
                return
            self.close_action = v
            cs["closeAction"] = v
            save_json(CONSOLE_SET, cs)
            self._refresh_close_label()
            print("控制台关闭行为已切换：" +
                  ("隐藏到托盘" if v == "minimize" else "退出程序"))

        mode_var.trace_add("write", live_close_action)
        tk.Label(dlg.body,
                 text="— 最小化：进任务栏（程序继续运行）\n"
                      "✕ 关闭：按上面的选择执行",
                 bg=BG, fg=DIM, font=dlg.f_s, justify="left", anchor="w",
                 wraplength=420).pack(fill="x", pady=(8, 0))

        def apply():
            cs["closeAction"] = dlg.vars["closeAction"].get()
            cs["consoleAlpha"] = round(float(dlg.vars["console_alpha"].get()), 2)
            cs["font_size"] = int(round(dlg.vars["console_font"].get()))
            cs["scale"] = round(float(dlg.vars["console_scale"].get()), 2)
            ov["liveMode"] = bool(dlg.vars["console_live"].get())
            save_json(CONSOLE_SET, cs)
            save_json(OVERLAY_SET, ov)
            # 显卡偏好（写 HKCU 的 UserGpuPreferences，重启助手生效）
            try:
                if "gpu_index" in dlg.vars:
                    import gpu_select
                    _idx = int(dlg.vars["gpu_index"].get())
                    # 选的是一块具体显卡：独显 → 高性能，核显 → 省电
                    if 0 <= _idx < len(_gpus):
                        _mode = (gpu_select.HIGH_PERFORMANCE
                                 if _gpus[_idx]["kind"] == "discrete"
                                 else gpu_select.POWER_SAVING)
                    else:
                        _mode = gpu_select.AUTO
                    _ok, _msg = gpu_select.set_preference(
                        gpu_select.current_exe(), _mode)
                    if _ok:
                        cs["gpu_index"] = _idx
                        cs["gpu_mode"] = _mode
                        save_json(CONSOLE_SET, cs)
                    print("显卡：" + _msg)
            except Exception as _e:
                print("显卡设置失败：%s" % _e)
            self.close_action = cs["closeAction"]
            self.console_alpha = cs["consoleAlpha"]
            self._apply_console_fonts(cs["font_size"], cs["scale"])
            self._refresh_close_label()
            self._preview_console_alpha(self.console_alpha)
            dlg.w.destroy()
            print("控制台关闭行为：" +
                  ("隐藏到托盘" if self.close_action == "minimize" else "退出程序"))

        dlg.buttons(apply, label="应用")

    def _preview_console_alpha(self, v):
        """拖动控制台不透明度滑块时实时生效（应用时再持久化）"""
        try:
            a = max(0.4, min(1.0, float(v)))
            self.root.attributes("-alpha", a)
        except Exception:
            pass

    def _clear_history(self):
        """清空**往次战绩**（用户要求：每次启动从零开始记）"""
        import glob
        removed = 0
        # 2026-10-18：世界缓存也要清 —— 不清的话，新一局判定器可能顺着
        # 上一局的旧地址读，把上一局的光环/阵容当成这一局的（用户实测）。
        for pat in ("history.json", "内存判定-*.txt",
                    "_world_cache.json", "_leader_effects.json"):
            for f in glob.glob(os.path.join(LOGS, pat)):
                try:
                    os.remove(f)
                    removed += 1
                except Exception:
                    pass
        if removed:
            print(f"已清空往次战绩：{removed} 个文件")
        # 每次控制台启动都清理过期快照；读取日志保留。
        n = cleanup_snapshots()
        if n:
            print(f"已清理过期读取快照：{n} 个文件")

    def _new_session(self):
        """
        控制台启动时开一个**全新会话**（清空战绩）。

        为什么启动时也做一次：正常退出会清，但进程被强杀 / 崩溃时不会，
        下次启动就会看到上一次的旧战绩（用户反馈"关不掉的旧记录"）。
        这里两处都清，保证"重启后从零开始"。
        """
        try:
            import history as HIS
            sess = HIS.new_session()
            print(f"新会话：{sess}（往次战绩已清空）")
        except Exception as e:
            print(f"（新建会话失败：{e}）")

    def on_close(self):
        """
        ✕ 的行为由设置决定：
          minimize → 隐藏到托盘（程序、判定器继续跑）
          exit     → 真正退出（移除托盘图标 + 停子进程 + 清历史）
        托盘不可用时 minimize 退化为「最小化到任务栏」，绝不悄悄退出。

        ⚠️ 启动后 8 秒内的关闭请求一律忽略：
           实测窗口刚建好时会收到一个**假的 WM_CLOSE**（无边框 + 托盘初始化期间），
           结果控制台一启动就把自己收进托盘，用户以为程序没起来。
        """
        import time as _t
        if _t.time() - getattr(self, "boot_at", 0) < 8.0:
            print("（忽略启动阶段的关闭请求）")
            return
        if self.close_action == "minimize":
            if self.tray_ok:
                self.hide_to_tray()
                return
            print("（托盘图标不可用：✕ 退化为最小化到任务栏）")
            self._minimize_to_taskbar()
            return
        self.quit_app()


def _ensure_std_streams():
    """
    pythonw.exe 没有控制台：sys.stdout / sys.stderr 是 None，任何 print 都会抛异常。
    这里把输出重定向到 logs/console-app.log（用户要求启动时不要黑框）。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
            except (OSError, ValueError):
                pass
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        os.makedirs(LOGS, exist_ok=True)
        fh = open(os.path.join(LOGS, "console-app.log"), "a",
                  encoding="utf-8", buffering=1)
    except Exception:
        return
    if sys.stdout is None:
        sys.stdout = fh
    if sys.stderr is None:
        sys.stderr = fh


def main():
    _ensure_std_streams()
    # 重启控制台时带 --delay N：等旧进程退出、释放单实例锁后再启动
    if "--delay" in sys.argv:
        try:
            _i = sys.argv.index("--delay")
            time.sleep(max(0.0, float(sys.argv[_i + 1])))
        except Exception:
            time.sleep(2.0)
    try:
        import singleton
        if not singleton.acquire("SvwbConsole"):
            request_console_show()
            print("控制台已经在运行，正在显示现有窗口")
            return 0
    except Exception:
        pass
    try:
        import parent_guard
        parent_guard.write_pid()
    except Exception:
        pass
    try:
        import ui_dpi
        ui_dpi.enable()
    except Exception:
        pass
    root = tk.Tk()
    try:
        import ui_icon
        ui_icon.set_window_icon(root)
    except Exception:
        pass
    app = App(root)
    def startup_ready():
        elapsed = time.perf_counter() - globals().get("_launch_started", time.perf_counter())
        print("控制台已就绪：%.3f 秒" % max(0, elapsed), flush=True)
    root.after_idle(startup_ready)
    root.mainloop()
    try:
        import atexit, parent_guard
        parent_guard.clear_pid()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
