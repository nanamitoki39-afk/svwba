#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗 · 记牌器窗口（2026-10-21）

Firestone 式：列出你的卡组，标出还没抽到的牌。
数据来自 `deck_reader.snapshot()`；未校准/未对战时显示「未在对战中」。
只读；不读对手手牌/牌库。
"""
import os
import sys
import time

try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import tkinter as tk

BG = "#12161d"
BG2 = "#171c25"
FG = "#efe9dc"
GOLD = "#c9a86a"
GOLD2 = "#8d7a52"
DIM = "#9aa7a0"
RULE = "#2a3038"
RED = "#c07b6b"
GREEN = "#6fbf73"

POLL_SEC = 0.15
_OPEN = [None]


WINDOW_SETTINGS = os.path.join(HERE, "logs", "tracker-window.json")


def window_settings():
    import json
    import io
    defaults = dict(width=360, height=460, font=9, auto=True, locked=False, show_public=True, show_played=True)
    try:
        with io.open(WINDOW_SETTINGS, encoding="utf-8") as fh:
            data = json.load(fh)
        for key, low, high in (("width", 260, 900), ("height", 200, 1080), ("font", 7, 20)):
            defaults[key] = max(low, min(high, int(data.get(key, defaults[key]))))
        defaults["locked"] = data.get("locked") is True
        defaults["auto"] = data.get("auto", True) is True
        for key in ("show_public", "show_played"):
            defaults[key] = data.get(key, True) is True
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return defaults


def _font(size, bold=False):
    import tkinter.font as tkfont
    return tkfont.Font(family="Microsoft YaHei UI", size=size,
                       weight="bold" if bold else "normal")


def _card_name(cid):
    """卡名查询：先查影之诗卡表（8 位 id），再退回幻境卡库，最后 #id。"""
    try:
        import deck_reader
        v = deck_reader.card_name(cid)
        if v:
            return v
    except Exception:
        pass
    try:
        import meta_stats
        names = meta_stats.load_cards_db()
        return names.get(int(cid)) or ("#%d" % cid)
    except Exception:
        return "#%d" % cid


class DeckTracker:
    def __init__(self, master=None):
        self.root = tk.Toplevel(master) if master is not None else tk.Tk()
        self.root.title("影之诗 · 记牌器")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.configure(bg=BG)
        self.options = window_settings()
        from card_art import ArtManager
        self.card_art = ArtManager(self)
        self.root.geometry("{width}x{height}".format(**self.options))
        self.root.overrideredirect(True)
        self.root.resizable(False, False)
        self._drag = None
        self._fitting = False
        self._pending_fit = None
        self.f_t = _font(self.options["font"] + 1, True)
        self.f_s = _font(self.options["font"])
        self._after = None
        self._last = None
        self._pending_snapshot = None
        self._snapshot_busy = False
        self._snapshot_lock = threading.Lock()

        pad = tk.Frame(self.root, bg=BG)
        self.pad = pad
        pad.pack(fill="both", expand=True, padx=10, pady=8)

        head = tk.Frame(pad, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="记牌器", bg=BG, fg=GOLD, font=self.f_t,
                 anchor="w").pack(side="left")
        tk.Button(head, text="✕", command=self.close, bg=BG, fg=DIM,
                  relief="flat", bd=0, padx=6, font=self.f_s,
                  activebackground=BG, cursor="hand2").pack(side="right")
        # 自定义标题区也可拖动
        for _w in head.winfo_children():
            _w.bind("<Button-1>", self._drag_start)
            _w.bind("<B1-Motion>", self._drag_move)
        head.bind("<Button-1>", self._drag_start)
        head.bind("<B1-Motion>", self._drag_move)

        # 胜率行：标题下一栏、卡组数量上方（胜绿、负红；可刷新）
        self.wr = tk.Frame(pad, bg=BG)
        self.wr.pack(fill="x", pady=(2, 0))
        tk.Label(self.wr, text="胜率", bg=BG, fg=DIM,
                 font=self.f_s).pack(side="left")
        self.rate_label = tk.Label(self.wr, text="", bg=BG, fg=DIM,
                                   font=self.f_s, anchor="w")
        self.rate_label.pack(side="left", padx=(4, 8))
        self.win_label = tk.Label(self.wr, text="", bg=BG, fg=GREEN,
                                  font=self.f_s)
        self.win_label.pack(side="left")
        self.loss_label = tk.Label(self.wr, text="", bg=BG, fg=RED,
                                   font=self.f_s)
        self.loss_label.pack(side="left", padx=(6, 0))
        self.reset_btn = tk.Button(self.wr, text="刷新",
                                   command=self._reset_winrate,
                                   bg=BG, fg=GOLD2, relief="flat", bd=0,
                                   padx=6, pady=1, font=self.f_s,
                                   cursor="hand2")
        self.reset_btn.pack(side="right")
        self._refresh_winrate()
        # 对局结束检测：记录胜负用
        self._was_in_battle = False
        self._last_life = None

        self.status = tk.Frame(pad, bg=BG)
        self.status.pack(fill="x", pady=(2, 0))
        self.status_mark = tk.Label(self.status, text="", bg=BG, fg=RED,
                                    font=self.f_s, anchor="w")
        self.status_mark.pack(side="left")
        self.status_text = tk.Label(self.status, text="未在对战中", bg=BG,
                                    fg=DIM, font=self.f_s, anchor="w")
        self.status_text.pack(side="left")

        # 视图切换按钮（炉石风格后不再使用，保留对象避免旧引用报错）
        self.view = "remaining"
        self.view_btns = {}
        viewbar = tk.Frame(pad, bg=BG)
        for _k, _label in (("remaining", "还在卡组"),
                           ("gone", "已经没有"),
                           ("all", "完整卡组")):
            _b = tk.Button(viewbar, text=_label,
                           command=lambda k=_k: self.set_view(k),
                           bg=BG, fg=DIM, relief="flat", bd=0, padx=6,
                           pady=2, font=self.f_s, cursor="hand2")
            _b.pack(side="left", padx=(0, 4))
            self.view_btns[_k] = _b
        self._paint_view()

        # 筛选（炉石风格后不再使用，保留变量避免旧引用报错）
        filt = tk.Frame(pad, bg=BG)
        tk.Label(filt, text="稀有度", bg=BG, fg=GOLD2,
                 font=self.f_s).pack(side="left")
        self.rarity_var = tk.StringVar(value="全部")
        rm = tk.OptionMenu(filt, self.rarity_var, "全部", "铜", "银", "金", "传说",
                           command=lambda *_: self._refresh_filter())
        rm.config(bg=BG, fg=FG, activebackground=BG2, relief="flat", bd=0,
                  font=self.f_s, highlightthickness=0)
        rm["menu"].config(bg=BG, fg=FG, activebackground=BG2,
                          activeforeground=GOLD, font=self.f_s)
        rm.pack(side="left", padx=(2, 8))
        tk.Label(filt, text="费用", bg=BG, fg=GOLD2,
                 font=self.f_s).pack(side="left")
        self.cost_var = tk.StringVar(value="全部")
        cm = tk.OptionMenu(filt, self.cost_var, "全部",
                           *[str(i) for i in range(0, 11)],
                           command=lambda *_: self._refresh_filter())
        cm.config(bg=BG, fg=FG, activebackground=BG2, relief="flat", bd=0,
                  font=self.f_s, highlightthickness=0)
        cm["menu"].config(bg=BG, fg=FG, activebackground=BG2,
                          activeforeground=GOLD, font=self.f_s)
        cm.pack(side="left", padx=(2, 0))

        tk.Frame(pad, bg=RULE, height=1).pack(fill="x", pady=6)

        # 卡牌列表：Canvas + 内嵌 Frame，滚轮滚动（无滚动条，保持原视觉风格）
        self.listbox = tk.Frame(pad, bg=BG)
        self.listbox.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(self.listbox, bg=BG, bd=0,
                                highlightthickness=0, yscrollincrement=24)
        self.scrollbar = tk.Scrollbar(self.listbox, orient="vertical",
                                      command=self.canvas.yview,
                                      troughcolor=BG, bg=BG2,
                                      activebackground=GOLD2, width=10)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self._win = self.canvas.create_window((0, 0), window=self.inner,
                                              anchor="nw")
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.inner.bind("<Configure>", self._on_inner_configure)
        for _w in (self.canvas, self.inner):
            self._wheel_bind(_w)

        self.chat = tk.Label(pad, text="", bg=BG2, fg=GOLD, font=self.f_s,
                             anchor="w", justify="left", wraplength=270,
                             padx=8, pady=5)
        self.chat.pack(fill="x", pady=(6, 0))
        self.chat.pack_forget()
        # 陪玩只在宠物窗口（run_companion）里跑；记牌器不再创建第二个实例，
        # 否则同一句话会被两个 Companion 各说一遍。
        self.companion = None

        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        if sys.platform == "win32":
            self.root.bind("<Map>", self._hide_system_frame, add="+")
            self.root.update_idletasks()
            self._hide_system_frame()
        import stream_settings
        stream_settings.watch(self.root, self._stream_mode)
        import subtitle_source
        self.card_subtitles = subtitle_source.ensure_window(self.root)
        self._create_public_window()
        self.poll()

    # ---------------- 对手公开手牌（记牌器旁的独立列窗） ----------------
    def _create_public_window(self):
        try:
            w = tk.Toplevel(self.root)
        except Exception:
            return
        self._public_win = w
        self._public_sig = None
        w.title("对手公开手牌")
        w.overrideredirect(True)
        w.configure(bg=BG)
        w.resizable(False, False)
        try:
            w.attributes("-topmost", True)
        except Exception:
            pass
        pad = tk.Frame(w, bg=BG)
        pad.pack(fill="both", expand=True, padx=6, pady=6)
        self._public_pad = pad
        tk.Label(pad, text="对手公开手牌", bg=BG, fg=GOLD2,
                 font=self.f_s, anchor="w").pack(fill="x")
        self._public_body = tk.Frame(pad, bg=BG)
        self._public_body.pack(fill="both", expand=True)
        self.root.bind("<Configure>", self._sync_public_window, add="+")
        self._sync_public_window()

    def _sync_public_window(self, event=None):
        w = getattr(self, "_public_win", None)
        if w is None or not w.winfo_exists():
            return
        try:
            if not self.root.winfo_viewable():
                return
            self._public_pad.update_idletasks()
            # 宽度与记牌器的卡名显示栏一致；高度只跟公开牌数量走，不跟随主窗口。
            width = max(140, min(220, int(self.root.winfo_width() * 0.45)))
            height = max(48, self._public_pad.winfo_reqheight() + 4)
            x = self.root.winfo_x() + self.root.winfo_width() + 4
            y = self.root.winfo_y()
            if x + width + 8 > self.root.winfo_screenwidth():
                x = self.root.winfo_x() - width - 4
            w.geometry("%dx%d+%d+%d" % (width, height, x, y))
        except Exception:
            pass

    def _render_public(self, items, ready, show, name_of):
        w = getattr(self, "_public_win", None)
        if w is None or not w.winfo_exists():
            return
        if not show:
            try:
                w.withdraw()
            except Exception:
                pass
            return
        try:
            w.deiconify()
            w.attributes("-topmost", True)
        except Exception:
            pass
        sig = tuple((cid, qty) for cid, qty in items)
        if sig == getattr(self, "_public_sig", None):
            self._sync_public_window()
            return
        self._public_sig = sig
        for child in self._public_body.winfo_children():
            child.destroy()
        if not items:
            tk.Label(self._public_body,
                     text="暂无已确认的公开牌" if ready else "公开牌状态确认中",
                     bg=BG, fg=DIM, font=self.f_s, anchor="w").pack(fill="x")
        else:
            for cid, qty in items:
                label = name_of(cid)
                if qty > 1:
                    label = "%s ×%d" % (label, qty)
                tk.Label(self._public_body, text=label, bg=BG, fg=FG,
                         font=self.f_s, anchor="w").pack(fill="x", pady=1)
        self._sync_public_window()

    def _hide_public(self):
        w = getattr(self, "_public_win", None)
        if w is not None:
            try:
                w.withdraw()
            except Exception:
                pass

    def _stream_mode(self, enabled):
        import stream_settings
        self._streaming = enabled
        self.root.attributes("-alpha", 1.0 if enabled else 0.96)
        if enabled:
            stream_settings.capture_style(self.root)
            w = getattr(self, "_public_win", None)
            if w is not None and w.winfo_exists():
                try:
                    w.attributes("-topmost", True)
                    stream_settings.capture_style(w)
                except Exception:
                    pass
        self._apply_lock()
        # 悬停卡图 / 效果提示窗跟随直播模式（可录屏 / 透明）
        try:
            self.card_art.set_streaming(enabled)
        except Exception:
            pass

    def apply_window_settings(self, options):
        self.options = options
        self._stream_mode(getattr(self, "_streaming", False))
        self.f_s.configure(size=options["font"])
        self.f_t.configure(size=options["font"] + 1)
        self.card_art.hide()
        self._layout_sig = None
        if self._last:
            self.render(self._last)
        self._fit_height()

    def _apply_lock(self):
        from ctypes import wintypes
        hwnd = wintypes.HWND(int(self.root.frame(), 0))
        api = ctypes.windll.user32
        flags = api.GetWindowLongW(hwnd, -20)
        locked = self.options.get("locked", False)
        # Windows 的分层窗口设置 TRANSPARENT 后，鼠标可穿透到其他进程。
        if locked:
            api.SetWindowLongW(hwnd, -20, flags | 0x80000 | 0x20)
            opacity = 255 if getattr(self, "_streaming", False) else 245
            api.SetLayeredWindowAttributes(hwnd, 0, opacity, 2)
        elif flags & 0x20:
            api.SetWindowLongW(hwnd, -20, flags & ~0x20)

    def _hide_system_frame(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = wintypes.HWND(int(self.root.frame(), 0))
        if getattr(self, "_streaming", False):
            import stream_settings
            stream_settings.capture_style(self.root)
        self._apply_lock()
        style = user32.GetWindowLongW(hwnd, -16)
        frame_bits = 0x00C00000 | 0x00040000  # WS_CAPTION | WS_THICKFRAME
        if style & frame_bits:
            user32.SetWindowLongW(hwnd, -16, style & ~frame_bits)
            # Recalculate the frame without moving, activating or changing z-order.
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)

    # ---------------- 拖动 ----------------
    def _drag_start(self, ev):
        self._drag = (ev.x_root - self.root.winfo_x(),
                      ev.y_root - self.root.winfo_y())

    def _drag_move(self, ev):
        if not self._drag:
            return
        self.root.geometry("+%d+%d" % (ev.x_root - self._drag[0],
                                       ev.y_root - self._drag[1]))

    # ---------------- 视图切换 ----------------
    def _paint_view(self):
        for k, b in self.view_btns.items():
            b.config(fg=GOLD if k == self.view else DIM,
                     bg=BG2 if k == self.view else BG)

    def set_view(self, key):
        if key not in ("remaining", "gone", "all"):
            return
        self.view = key
        self._paint_view()
        self._sig = None
        try:
            self.render(self._last or {})
        except Exception:
            pass

    def _refresh_filter(self):
        self._sig = None
        try:
            self.render(self._last or {})
        except Exception:
            pass

    # ---------------- 轮询 ----------------
    def poll(self):
        self._after = None
        import deck_reader
        try:
            stamp = os.stat(deck_reader.LIVE_PATH).st_mtime_ns
        except OSError:
            stamp = None
        with self._snapshot_lock:
            ready = self._pending_snapshot
            self._pending_snapshot = None
            now = time.monotonic()
            changed = stamp != getattr(self, "_read_stamp", None)
            start = not self._snapshot_busy and (changed or now >= getattr(self, "_next_read", 0))
            if start:
                self._read_stamp = stamp
                # 文件变化下一次 Tk 回调即处理；兜底读取用于过期和配置更新。
                self._next_read = now + 1.0
                self._snapshot_busy = True
        # 后台线程只写入结果，所有 Tk 调用都由主线程完成。
        self._after = self.root.after(15, self.poll)
        if start:
            try:
                threading.Thread(target=self._read_snapshot_worker, daemon=True).start()
            except RuntimeError as exc:
                with self._snapshot_lock:
                    self._snapshot_busy = False
                self._next_read = time.monotonic() + .5
                print("记牌器快照线程暂不可用，将重试：", exc)
        if ready is not None:
            self._apply_snapshot(ready)

    def _apply_snapshot(self, snap):
        # 读取器在结算发生时落盘；窗口关闭、快照过期都不参与判胜负。
        import battle_stats
        try:
            stamp = os.stat(battle_stats.PATH).st_mtime_ns
        except OSError:
            stamp = None
        if stamp != getattr(self, "_stats_stamp", -1):
            self._refresh_winrate()
            self._stats_stamp = stamp
        content = dict(snap)
        content.pop("sample_at", None)
        deck_key = None
        if not snap.get("in_battle"):
            import deck_reader
            deck_key = deck_reader._deck_file_key()
        key = (content, deck_key)
        if key != getattr(self, "_rendered_snapshot", None):
            try:
                self.render(snap)
                self._rendered_snapshot = key
            except Exception:
                pass
        if self.companion is not None:
            try:
                self.companion.tick()
            except Exception:
                pass
    def _read_snapshot_worker(self):
        try:
            snap = self._snapshot()
        except Exception as e:
            snap = {"in_battle": False, "reason": str(e)[:80]}
        with self._snapshot_lock:
            self._pending_snapshot = snap
            self._snapshot_busy = False

    def _refresh_winrate(self, st=None):
        """刷新顶栏胜率：胜/负数字分色，胜率百分比。"""
        try:
            if st is None:
                import battle_stats
                st = battle_stats.stats()
            wins = int(st.get("wins") or 0)
            losses = int(st.get("losses") or 0)
            if wins + losses:
                self.rate_label.config(text="%.0f%%" % st.get("rate", 0))
                self.win_label.config(text="%d胜" % wins)
                self.loss_label.config(text="%d负" % losses)
            else:
                # 刷新后显示 0%（用户 2026-10-31）
                self.rate_label.config(text="0%")
                self.win_label.config(text="0胜")
                self.loss_label.config(text="0负")
        except Exception:
            pass

    def _reset_winrate(self):
        """清空胜率统计（刷新）。"""
        try:
            from tkinter import messagebox
            if not messagebox.askyesno("刷新胜率",
                                       "清空所有胜/负记录？", parent=self.root):
                return
        except Exception:
            pass
        try:
            import battle_stats
            self._refresh_winrate(battle_stats.reset())
        except Exception:
            pass

    def _snapshot(self):
        import deck_reader
        return deck_reader.snapshot()

    # ---------------- 陪玩 ----------------
    def _on_ai_text(self, text):
        def _u():
            try:
                self.chat.config(text=str(text))
                self.chat.pack(fill="x", pady=(6, 0))
            except Exception:
                pass
        try:
            self.root.after(0, _u)
        except Exception:
            pass

    def _on_ai_state(self, text):
        self._on_ai_text(text)

    def _open_ai(self):
        try:
            import companion
        except Exception:
            return
        cfg = companion.load_config()
        win = tk.Toplevel(self.root)
        win.title("AI 陪玩设置")
        win.configure(bg=BG)
        try:
            win.geometry("+%d+%d" % (self.root.winfo_x() + 40,
                                     self.root.winfo_y() + 60))
            win.attributes("-topmost", True)
        except Exception:
            pass
        pad = tk.Frame(win, bg=BG)
        pad.pack(fill="both", expand=True, padx=12, pady=10)
        vars_ = {}

        def row(label, key, show=""):
            f = tk.Frame(pad, bg=BG)
            f.pack(fill="x", pady=2)
            tk.Label(f, text=label, bg=BG, fg=GOLD2, font=self.f_s,
                     width=8, anchor="w").pack(side="left")
            v = tk.StringVar(value=str(cfg.get(key) or ""))
            tk.Entry(f, textvariable=v, bg=BG2, fg=FG, insertbackground=FG,
                     relief="flat", font=self.f_s, show=show).pack(
                side="left", fill="x", expand=True)
            vars_[key] = v

        row("API Key", "api_key", show="*")
        row("Base URL", "base_url")
        row("模型", "model")

        ttsv = tk.BooleanVar(value=bool(cfg.get("tts_enabled")))
        f1 = tk.Frame(pad, bg=BG)
        f1.pack(fill="x", pady=3)
        tk.Checkbutton(f1, text="语音（Edge TTS，云端）", variable=ttsv,
                       bg=BG, fg=FG, selectcolor=BG2, activebackground=BG,
                       font=self.f_s).pack(side="left")

        f2 = tk.Frame(pad, bg=BG)
        f2.pack(fill="x", pady=2)
        tk.Label(f2, text="音色", bg=BG, fg=GOLD2, font=self.f_s,
                 width=8, anchor="w").pack(side="left")
        voice_names = [n for _, n in companion.VOICES]
        voice_ids = [v for v, _ in companion.VOICES]
        cur = cfg.get("voice") or voice_ids[0]
        vv = tk.StringVar(value=voice_names[voice_ids.index(cur)]
                          if cur in voice_ids else voice_names[0])
        om = tk.OptionMenu(f2, vv, *voice_names)
        om.config(bg=BG2, fg=FG, relief="flat", bd=0, font=self.f_s,
                  highlightthickness=0)
        om["menu"].config(bg=BG, fg=FG, activebackground=BG2,
                          activeforeground=GOLD, font=self.f_s)
        om.pack(side="left", fill="x", expand=True)

        enabledv = tk.BooleanVar(value=bool(cfg.get("enabled")))
        f3 = tk.Frame(pad, bg=BG)
        f3.pack(fill="x", pady=3)
        tk.Checkbutton(f3, text="启用陪玩（对局中自动说话）", variable=enabledv,
                       bg=BG, fg=FG, selectcolor=BG2, activebackground=BG,
                       font=self.f_s).pack(side="left")

        def do_save():
            cfg["api_key"] = vars_["api_key"].get().strip()
            cfg["base_url"] = vars_["base_url"].get().strip() or cfg["base_url"]
            cfg["model"] = vars_["model"].get().strip() or cfg["model"]
            cfg["tts_enabled"] = bool(ttsv.get())
            cfg["enabled"] = bool(enabledv.get())
            idx = voice_names.index(vv.get()) if vv.get() in voice_names else 0
            cfg["voice"] = voice_ids[idx]
            companion.save_config(cfg)
            if self.companion is not None:
                self.companion.cfg = companion.load_config()
            win.destroy()

        def do_test():
            if self.companion is None:
                return
            self.companion.cfg = companion.load_config()
            self.companion.test_tts()

        bf = tk.Frame(pad, bg=BG)
        bf.pack(fill="x", pady=(6, 0))
        for txt, cmd in (("保存", do_save), ("测试语音", do_test),
                         ("关闭", win.destroy)):
            tk.Button(bf, text=txt, command=cmd, bg=BG2, fg=GOLD,
                      relief="flat", bd=0, padx=10, pady=3, font=self.f_s,
                      activebackground=BG, cursor="hand2").pack(
                side="left", padx=(0, 6))

    # ---------------- 渲染 ----------------
    def render(self, snap):
        self._last = snap
        if not snap.get("in_battle"):
            # 用户 2026-10-29：选好卡组后、还没进对局时也要显示卡组
            item = None
            try:
                import deck_reader
                decks = deck_reader._decks_from_file()
                active = deck_reader._active_name()
                item = next((x for x in decks if x.get("name") == active),
                            decks[0] if decks else None)
            except Exception:
                item = None
            if not item:
                self.status_mark.config(text="")
                self.status_text.config(text="未在对战中 · " +
                                        str(snap.get("reason") or "")[:40])
                self._hide_public()
                self._clear()
                self._layout_sig = None
                return
            from collections import Counter
            rows = [(cid, n, n, 0)
                    for cid, n in Counter(item["cards"]).items()]
            snap = {"in_battle": True, "deckCount": len(item["cards"]),
                    "hand": [], "played": [], "destroyed": [],
                    "rows": rows, "total": len(item["cards"]),
                    "overdraw": 0, "returned": False}
            self._pre_name = item["name"]
            self._pre_battle = True
        else:
            self._pre_battle = False
            self._pre_name = None
        try:
            import deck_reader
            meta = deck_reader.load_meta()
        except Exception:
            meta = {}

        def info(cid):
            try:
                direct = meta.get(str(cid))
                if direct and direct.get("name"):
                    return direct
                return deck_reader.card_meta(cid)
            except Exception:
                return meta.get(str(cid)) or {}

        def name_of(cid):
            return info(cid).get("name") or ("#%d" % cid)

        CLS_ORDER = {1: 0, 2: 1, 3: 2, 6: 3, 4: 4, 5: 5, 7: 6, 0: 7}
        CLS_COLOR = {1: "#2e8b57", 2: "#e8c547", 3: "#3b6fd4", 6: "#e8e8e8",
                     4: "#e8843c", 5: "#8b2f4a", 7: "#3fb5a8", 0: "#808080"}
        RAR_COLOR = {1: "#b87333", 2: "#c0c0c0", 3: "#e8c547", 4: "#c86bd8"}
        # 卡表 type：1=随从、2/3=护符、4=法术（2 原被错标成法术，见 2026-10-29）
        TYPE_TXT = {1: "随从", 2: "护符", 3: "护符", 4: "法术"}

        def sort_key(cid):
            m = info(cid)
            # 最后加 cid 作为 tie-breaker，保证排序稳定（否则同名卡顺序会抖、整窗闪烁）
            return (m.get("cost") or 0,
                    CLS_ORDER.get(m.get("class"), 9),
                    m.get("rarity") or 0, name_of(cid), cid)

        rows = snap.get("rows") or []
        hand_ready = snap.get("handReady", snap.get("mulliganDone", False))
        hand = list(snap.get("hand") or []) if hand_ready else []
        # 卡组段：还在牌库的卡，显示**牌库剩余数量**（抽上手就减数字，不直接删行）
        deck_items = [(r[0], int(r[2] or 0)) for r in rows if r[2] > 0]
        deck_items.sort(key=lambda x: sort_key(x[0]))
        # 手牌：相同卡名合并成一行 + 数量（用户 2026-10-29）
        hand_by_name = {}
        for c in hand:
            nm = name_of(c)
            if nm not in hand_by_name:
                hand_by_name[nm] = [c, 0]
            hand_by_name[nm][1] += 1
        hand_items = [(cid, cnt) for cid, cnt in hand_by_name.values()]
        hand_items.sort(key=lambda x: sort_key(x[0]))

        deck_heading = "牌库中"
        if getattr(self, "_pre_battle", False):
            self.status_mark.config(text="")
            self.status_text.config(
                text="当前卡组：%s" % self._pre_name)
        else:
            remaining = int(snap.get("deckCount") or snap.get("remaining") or 0)
            self.status_mark.config(text="")
            # 顶栏不再显示爆牌数量（用户 2026-10-30：爆牌区显示卡名）
            hand_status = "手牌 %d" % len(hand) if hand_ready else "等待换牌确认"
            self.status_text.config(text="牌库剩余 %d · %s" % (remaining, hand_status))

        show_played = bool(self.options.get("show_played", True) and hand_ready and not self._pre_battle)
        played_by_name = {}
        for raw_id, count in (snap.get("playedFromHand") or {}).items():
            cid = int(raw_id)
            label = name_of(cid)
            if label not in played_by_name:
                played_by_name[label] = [cid, 0]
            played_by_name[label][1] += count
        played_items = sorted(played_by_name.values(), key=lambda item: sort_key(item[0]))
        import opponent_public
        show_public = bool(self.options.get("show_public", True) and hand_ready and not self._pre_battle)
        public_ready = bool(show_public and snap.get("opponentPublicHandReady"))
        public_by_name = {}
        for cid, count in opponent_public.public_counts(snap).items() if public_ready else []:
            label = name_of(cid)
            if label not in public_by_name:
                public_by_name[label] = [cid, 0]
            public_by_name[label][1] += count
        public_items = sorted(public_by_name.values(), key=lambda item: sort_key(item[0]))
        # 对手公开手牌放在记牌器旁的独立列窗；不参与主窗口布局签名，
        # 公开牌变化不会让整个记牌器重建。
        self._render_public(public_items, public_ready, show_public, name_of)
        layout_sig = (show_played, tuple(cid for cid, _ in played_items), deck_heading,
                      tuple(cid for cid, _ in deck_items),
                      tuple(cid for cid, _ in hand_items),
                      tuple(sorted(snap.get("overdraw_cards") or [])),
                      bool(snap.get("mulliganDone", True)),
                      bool(deck_items), bool(hand_items and snap.get("mulliganDone", True)),
                      bool(snap.get("overdraw_cards")))
        qty_values = {("played", cid): n for cid, n in played_items}
        qty_values.update({("deck", cid): qty for cid, qty in deck_items})
        qty_values.update({("hand", cid): qty for cid, qty in hand_items})
        from collections import Counter
        qty_values.update({("overdraw", cid): cnt for cid, cnt in
                           Counter(snap.get("overdraw_cards") or []).items()})
        if layout_sig == getattr(self, "_layout_sig", None):
            # 抽牌通常只改变剩余张数；原位更新数量标签，保留卡名控件，避免整列表闪烁。
            for key, label in getattr(self, "_qty_labels", {}).items():
                qty = qty_values.get(key, 0)
                try:
                    label.config(text="" if qty == 1 else str(qty))
                except Exception:
                    pass
            self._sig = (layout_sig, tuple(sorted(qty_values.items())))
            return
        self._layout_sig = layout_sig
        self._qty_labels = {}
        self._sig = (layout_sig, tuple(sorted(qty_values.items())))
        try:
            _scroll_before = self.canvas.yview()
        except Exception:
            _scroll_before = None
        self._clear()
        self._row_count = 0
        self._section = "deck"

        def add_header(text):
            self._row_count = getattr(self, "_row_count", 0) + 1
            if text == "牌库中":
                self._section = "deck"
            elif text == "手牌中":
                self._section = "hand"
            elif text == "已打出":
                self._section = "played"
            elif text == "对手公开手牌":
                self._section = "opponent_public"
            elif text == "爆牌中":
                self._section = "overdraw"
            tk.Label(self.inner, text=text, bg=BG, fg=GOLD2, font=self.f_s,
                     anchor="w").pack(fill="x", pady=(4, 1))

        def add_row(cid, qty):
            self._row_count = getattr(self, "_row_count", 0) + 1
            m = info(cid)
            cost = m.get("cost") or 0
            cls = m.get("class")
            rar = m.get("rarity") or 0
            col = CLS_COLOR.get(cls, "#808080")
            from card_art import CardRow
            section = getattr(self, "_section", "deck")
            row = CardRow(self.card_art, self.inner, cid, qty,
                          dict(m, name=name_of(cid)), section, self.f_s, self.f_t,
                          col, RAR_COLOR.get(rar, BG2))
            self._qty_labels[(section, cid)] = row
            self._wheel_bind(row)

        add_header(deck_heading)
        for cid, qty in deck_items[:80]:
            add_row(cid, qty)
        # 换牌阶段不显示手牌（用户 2026-10-31：换牌结束才显示）
        # 有手牌就显示：不再看 mulliganDone/turn（这俩标志不可靠，
        # 曾导致「换牌结束后手牌一直不显示」）
        if hand_items:
            # 手牌按卡名合并后，一张一行 + 数量
            add_header("手牌中")
            for cid, qty in hand_items[:40]:
                add_row(cid, qty)
        if show_played:
            add_header("已打出")
            for cid, qty in played_items:
                add_row(cid, qty)
        # 爆牌区（手牌下方）：显示被爆掉的卡名，同名合并，1 张不显示数字
        from collections import Counter
        over_cards = [c for c in (snap.get("overdraw_cards") or []) if c]
        if over_cards:
            add_header("爆牌中")
            over_by = Counter(over_cards)
            for cid, cnt in sorted(over_by.items(),
                                   key=lambda x: sort_key(x[0])):
                add_row(cid, cnt)
        self._fit_height()
        if _scroll_before:
            try:
                self.canvas.yview_moveto(_scroll_before[0])
            except Exception:
                pass

    def _fit_height(self, n=None):
        """窗口高度 = 内容实际高度 + 固定区实际高度（不留多余空白）。"""
        try:
            self.root.update_idletasks()
            need = max(1, self.inner.winfo_reqheight())
            try:
                # pad 的请求高度包含标题/状态/分隔/边距，减去 canvas 自身的请求高度
                fixed = (self.pad.winfo_reqheight()
                         - self.canvas.winfo_reqheight())
            except Exception:
                fixed = 150
            fixed = max(120, min(260, fixed))
            h = need + fixed + 4
            sh = self.root.winfo_screenheight()
            h = max(200, min(h, int(sh * 0.88)))
            if not self.options["auto"]:
                h = min(self.options["height"], max(200, sh - 40))
            w = min(self.options["width"], self.root.winfo_screenwidth())
            if abs(self.root.winfo_height() - h) > 2 or self.root.winfo_width() != w:
                self.root.geometry("%dx%d" % (w, h))
            self.root.update_idletasks()
            overflow = self.inner.winfo_reqheight() > self.canvas.winfo_height() + 2
            if overflow and not self.scrollbar.winfo_manager():
                # 滚动条靠屏幕内侧：左侧窗口放右边，右侧窗口放左边。
                sw = self.root.winfo_screenwidth()
                side = "right" if self.root.winfo_x() < sw // 2 else "left"
                self.scrollbar.pack(side=side, fill="y", before=self.canvas)
            elif not overflow and self.scrollbar.winfo_manager():
                self.scrollbar.pack_forget()
        except Exception:
            pass

    def _clear(self):
        self.card_art.clear_rows()
        # 行在内嵌 frame 里（canvas 的滚动内容），清空它并回到顶部
        try:
            rows = self.inner.winfo_children()
        except Exception:
            rows = []
        for w in rows:
            w.destroy()
        # ⚠️ 不要在这里重置 self._sig：render() 先设 sig 再调 _clear，
        #    重置会导致每帧都判定"数据变了"→ 整窗重建 → 卡名闪烁。
        try:
            self.canvas.yview_moveto(0.0)
        except Exception:
            pass

    # ---------------- 滚轮滚动 ----------------
    def _on_canvas_configure(self, event):
        try:
            self.canvas.itemconfigure(self._win, width=event.width)
        except Exception:
            pass
        self._update_scrollregion()

    def _on_inner_configure(self, _event=None):
        self._update_scrollregion()

    def _update_scrollregion(self):
        try:
            box = self.canvas.bbox("all")
            if box is not None:
                self.canvas.configure(scrollregion=box)
        except Exception:
            pass

    def _wheel_bind(self, w):
        try:
            w.bind("<MouseWheel>", self._on_mousewheel)
            w.bind("<Button-4>", lambda _e: self.canvas.yview_scroll(-3, "units"))
            w.bind("<Button-5>", lambda _e: self.canvas.yview_scroll(3, "units"))
        except Exception:
            pass

    def _wheel_enter(self, _event=None):
        try:
            self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        except Exception:
            pass

    def _wheel_leave(self, _event=None):
        # 在列表内部从子控件移到另一个子控件也会触发 <Leave>，
        # 指针还在列表区域就别解绑，避免滚轮中途失效。
        if self._pointer_in_list():
            return
        try:
            self.root.unbind_all("<MouseWheel>")
        except Exception:
            pass

    def _pointer_in_list(self):
        try:
            w = self.root.winfo_containing(self.root.winfo_pointerx(),
                                           self.root.winfo_pointery())
        except Exception:
            return False
        while w is not None:
            if w is self.listbox:
                return True
            w = getattr(w, "master", None)
        return False

    def _on_mousewheel(self, event):
        try:
            delta = int(event.delta)
        except Exception:
            return
        if not delta:
            return
        # Windows：delta 为 120 的倍数；个别设备会给出更小的值，兜底滚一格
        step = delta // 120
        if step == 0:
            step = 1 if delta > 0 else -1
        try:
            self.canvas.yview_scroll(-1 * step, "units")
        except Exception:
            pass

    # ---------------- 关闭 ----------------
    def close(self):
        if getattr(self, "_closing", False):
            return
        self._closing = True
        # The voice reader owns a process handle and a background thread.
        # Destroying Tk alone does not stop either of them.
        subtitles = getattr(self, "card_subtitles", None)
        if subtitles is not None and getattr(subtitles, "_stream_enabled", False) is not True:
            try:
                subtitles.close()
            except Exception as exc:
                print("字幕关闭异常：", exc)
        try:
            self.card_art.close()
        except Exception as exc:
            print("卡图关闭异常：", exc)
        _OPEN[0] = None
        try:
            if getattr(self, "_public_win", None) is not None:
                self._public_win.destroy()
        except Exception:
            pass
        try:
            self.root.unbind_all("<MouseWheel>")
            self.root.unbind_all("<Button-4>")
            self.root.unbind_all("<Button-5>")
        except Exception:
            pass

        try:
            if self._after is not None:
                self.root.after_cancel(self._after)
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        import singleton
        singleton.release("SvwbDeckTracker")


def open(master=None):
    if _OPEN[0] is not None:
        try:
            _OPEN[0].root.lift()
            return _OPEN[0]
        except Exception:
            _OPEN[0] = None
    # 跨进程单实例：记牌器同时只能有一个
    try:
        import singleton
        if not singleton.acquire("SvwbDeckTracker"):
            print("记牌器已经在运行")
            return None
    except Exception:
        pass
    w = DeckTracker.__new__(DeckTracker)
    try:
        w.__init__(master)
    except Exception:
        for name in ("card_subtitles", "card_art"):
            component = getattr(w, name, None)
            if component is not None:
                if name == "card_subtitles" and getattr(component, "_stream_enabled", False) is True:
                    continue
                try:
                    component.close()
                except Exception:
                    pass
        if getattr(w, "root", None) is not None:
            try:
                w.root.destroy()
            except Exception:
                pass
        import singleton
        singleton.release("SvwbDeckTracker")
        raise
    _OPEN[0] = w
    return w
