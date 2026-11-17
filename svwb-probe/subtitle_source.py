"""User-provided attribution and authorization label for caption translations."""
import json
from pathlib import Path
import tkinter as tk

SETTINGS = Path(__file__).resolve().parent / "logs/card-subtitle-window.json"

SOURCE = Path(__file__).resolve().parent / "gamedata/card_subtitle_source.json"


def load_source(path=SOURCE):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


_WINDOW = None


def _find_subtitle_window():
    """字幕窗挂在记牌器窗口里（deck_tracker._OPEN[0].card_subtitles）。"""
    if _WINDOW is not None and not _WINDOW.closed:
        return _WINDOW
    try:
        import deck_tracker
        w = deck_tracker._OPEN[0]
        return getattr(w, "card_subtitles", None) if w is not None else None
    except Exception:
        return None


def ensure_window(parent):
    global _WINDOW
    existing = _find_subtitle_window()
    if existing is not None:
        _WINDOW = existing
        return existing
    from card_subtitles import SubtitleWindow
    # Use the console's Tk root so closing a tracker cannot destroy the live layer.
    _WINDOW = SubtitleWindow(parent._root())
    return _WINDOW


def watch_live(root):
    import stream_settings
    def changed(enabled):
        if enabled:
            set_enabled(True)
            ensure_window(root)._stream_mode(True)
    stream_settings.watch(root, changed)


def open_style_settings(parent):
    """打开字幕样式设置；字幕窗存在时实时生效。"""
    try:
        import subtitle_settings
        subtitle_settings.load_saved_fonts()   # 先把之前导入的字体重新注册
        sub = _find_subtitle_window()
        on_apply = sub.apply_options if sub is not None else None
        subtitle_settings.open_settings(parent, on_apply=on_apply)
    except Exception as e:
        print("字幕样式设置打开失败：%s" % e)


def is_enabled():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8-sig")).get("enabled", True) is not False
    except (OSError, ValueError, TypeError, AttributeError):
        return True


def set_enabled(enabled):
    options = {}
    try:
        options = json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
        if not isinstance(options, dict):
            options = {}
    except (OSError, ValueError, TypeError):
        pass
    options["enabled"] = bool(enabled)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS.with_suffix(".tmp")
    temporary.write_text(json.dumps(options, ensure_ascii=False, indent=1), encoding="utf8")
    temporary.replace(SETTINGS)
    window = _find_subtitle_window()
    if window is not None:
        window.apply_options({"enabled": bool(enabled)})


def build_panel(parent, font, bg="#171c25", fg="#efe9dc", dim="#9aa7a0",
                gold="#c9a86a", green="#6fbf73", source=None):
    data = load_source() if source is None else source
    panel = tk.Frame(parent, bg=bg)
    panel.pack(fill="x", padx=12, pady=4)
    heading = tk.Frame(panel, bg=bg)
    heading.pack(fill="x", padx=8, pady=(7, 3))
    tk.Label(heading, text="卡牌双语字幕", bg=bg, fg=fg, font=font,
             anchor="w").pack(side="left")
    tk.Button(heading, text="字幕样式…", command=lambda: open_style_settings(parent),
              bg="#2a3341", fg=gold, activebackground="#38445a",
              activeforeground="#ffffff", relief="flat", bd=0,
              padx=8, pady=2, font=font, cursor="hand2").pack(side="right")
    toggle = tk.Button(heading, bg="#2a3341", fg=gold, relief="flat", bd=0,
                       padx=8, pady=2, font=font, cursor="hand2")
    toggle.pack(side="right", padx=(0, 6))
    timer = [None]
    def refresh():
        toggle.config(text="关闭字幕" if is_enabled() else "开启字幕")
        timer[0] = parent.after(1000, refresh)
    def toggle_caption():
        try:
            set_enabled(not is_enabled())
            toggle.config(text="关闭字幕" if is_enabled() else "开启字幕")
        except OSError as exc:
            from tkinter import messagebox
            messagebox.showerror("字幕设置未保存", str(exc), parent=parent)
    def cleanup(event):
        if event.widget is toggle and timer[0] is not None:
            parent.after_cancel(timer[0])
            timer[0] = None
    toggle.configure(command=toggle_caption)
    toggle.bind("<Destroy>", cleanup, add="+")
    refresh()
    authorized = data.get("authorization_status") == "authorized"
    tk.Label(heading, text="已授权" if authorized else "授权状态未设置",
             bg=bg, fg=green if authorized else dim, font=font).pack(side="right",
                                                                   padx=(0, 8))
    author = data.get("author")
    label = "台词翻译来源：B站 UP 主「%s」" % author if author else "台词翻译来源：待设置"
    tk.Label(panel, text=label, bg=bg, fg=gold, font=font,
             anchor="w", justify="left", wraplength=360).pack(fill="x", padx=8)
    tk.Label(panel, text="屏幕高度 75% 居中 · 中文在上，日文较小 · "
                         "字体/颜色/字号/描边可在「字幕样式」里改",
             bg=bg, fg=dim, font=font, anchor="w",
             justify="left", wraplength=360).pack(fill="x", padx=8, pady=(3, 2))
    return panel
