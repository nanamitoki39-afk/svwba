"""Bilingual captions for confirmed card-voice playback, not inferred actions."""
from dataclasses import dataclass
from pathlib import Path
import csv
import json
import queue
import time
import tkinter as tk

HERE = Path(__file__).resolve().parent
TRANSLATIONS = HERE / "gamedata/card_voice_subtitles.json"
EVENTS = HERE / "gamedata/card_voice_events.json"
SETTINGS = HERE / "logs/card-subtitle-window.json"
TRANSPARENT = "#010203"


DEFAULT_OPTIONS = dict(
    zh_family="微软雅黑", zh_size=22, zh_color="#ffffff",
    zh_bold=True, zh_italic=False,
    ja_family="Yu Gothic UI", ja_size=15, ja_color="#e1e5eb",
    ja_bold=False, ja_italic=False,
    stroke_color="#080a0d", stroke_width=1,
    x=None, y=None, enabled=True, locked=False,
)


def _font_tuple(style, prefix, fallback_family, fallback_size):
    """从样式字典拼 Tk 字体元组，缺字段回退默认。"""
    family = style.get(prefix + "_family") or fallback_family
    try:
        size = int(style.get(prefix + "_size") or fallback_size)
    except (TypeError, ValueError):
        size = fallback_size
    weight = "bold" if style.get(prefix + "_bold") else "normal"
    slant = "italic" if style.get(prefix + "_italic") else "roman"
    return (family, max(6, min(96, size)), weight, slant)


class OutlinedText(tk.Canvas):
    """Native DPI-aware text; no low-resolution bitmap is stretched onscreen.

    样式（字体 / 字色 / 描边颜色和粗细）全部从 style 字典读，
    改字体或颜色后必须重绘 —— 旧版只在 text 变化时重绘，
    结果「在设置里换字体完全没反应」。
    """
    def __init__(self, master, style, prefix, wraplength,
                 fallback_family="微软雅黑", fallback_size=20):
        super().__init__(master, bg=TRANSPARENT, highlightthickness=0, bd=0)
        self.style = style
        self.prefix = prefix
        self.fallback_family = fallback_family
        self.fallback_size = fallback_size
        self.wraplength, self.caption_text = wraplength, ""

    @property
    def caption_font(self):
        return _font_tuple(self.style, self.prefix,
                           self.fallback_family, self.fallback_size)

    @property
    def caption_fill(self):
        return self.style.get(self.prefix + "_color") or "#ffffff"

    def apply(self, style):
        """样式变更：换字体/颜色后必须重绘。"""
        self.style = style
        self._draw()

    def cget(self, key):
        if key == "font":
            return self.caption_font
        if key == "text":
            return self.caption_text
        return super().cget(key)

    def configure(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)
        changed = "text" in kwargs
        if changed:
            self.caption_text = kwargs.pop("text")
        result = super().configure(**kwargs)
        if changed:
            self._draw()
        return result

    config = configure

    def _draw(self):
        self.delete("all")
        if not self.caption_text:
            self.caption_image = None
            super().configure(width=1, height=1)
            return
        from PIL import ImageTk
        from subtitle_bitmap import render
        try:
            stroke = max(0, min(6, int(self.style.get("stroke_width", 1) or 0)))
        except (ValueError, TypeError):
            stroke = 1
        image = render(self.caption_text, self.caption_font, self.caption_fill,
                       self.style.get("stroke_color") or "#080a0d", stroke,
                       self.wraplength, self.winfo_fpixels("1p"))
        self.caption_bitmap = image
        self.caption_image = ImageTk.PhotoImage(image, master=self)
        super().configure(width=image.width, height=image.height)
        self.create_image(0, 0, image=self.caption_image, anchor="nw")


def wwise_id(name):
    value = 2166136261
    for byte in str(name).lower().encode("utf8"):
        value = ((value * 16777619) & 0xffffffff) ^ byte
    return value


def marker_key(value):
    value = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    return value.rsplit(".", 1)[0].lower()


@dataclass(frozen=True)
class Caption:
    zh: str
    ja: str


class Catalog:
    def __init__(self, path=TRANSLATIONS, events=EVENTS):
        self.path, self.events_path = Path(path), Path(events)
        self.by_file, self.by_event, self.allowed = {}, {}, set()
        self.stamp = None
        self.reload()

    @property
    def ready(self):
        return bool(self.by_file or self.by_event)

    def reload(self):
        try:
            stamp = (self.path.stat().st_mtime_ns, self.events_path.stat().st_mtime_ns)
            if stamp == self.stamp:
                return
            events = json.loads(self.events_path.read_bytes())
            allowed = {wwise_id(name) for name, row in events.items()
                       if set(row.get("actions", ())) & {"play", "attack", "attack_evolved", "evolve", "destroy", "destroy_evolved", "destroy_token", "skill", "skill_evolved", "act"}}
            if self.path.suffix.lower() == ".csv":
                with self.path.open(encoding="utf-8-sig", newline="") as file:
                    rows = list(csv.DictReader(file))
            else:
                data = json.loads(self.path.read_text(encoding="utf-8-sig"))
                rows = data.get("entries", [])
            by_file, by_event = {}, {}
            for row in rows:
                zh, ja = str(row.get("zh") or "").strip(), str(row.get("ja") or "").strip()
                if not zh or not ja:
                    continue
                event = str(row.get("event") or "").strip()
                event_id = wwise_id(event) if event else int(row.get("event_id") or 0)
                if event_id not in allowed:
                    continue
                caption = Caption(zh, ja)
                marker = marker_key(row.get("file"))
                if marker:
                    key = (event_id, marker)
                    # Conflicting translations are not guessed.
                    by_file[key] = caption if key not in by_file or by_file[key] == caption else None
                elif row.get("single_line") is True or str(row.get("single_line")).lower() == "true":
                    by_event[event_id] = caption if event_id not in by_event or by_event[event_id] == caption else None
            self.by_file = {key: value for key, value in by_file.items() if value is not None}
            self.by_event = {key: value for key, value in by_event.items() if value is not None}
            self.allowed, self.stamp = allowed, stamp
        except (OSError, ValueError, TypeError, KeyError):
            # Never leave old translations active after a broken replacement.
            self.by_file, self.by_event, self.allowed = {}, {}, set()

    def match(self, voice):
        if not voice or voice.get("actual") is not True:
            return None
        event_id = int(voice.get("event_id") or 0)
        if event_id not in self.allowed or not voice.get("playing_id"):
            return None
        marker = marker_key(voice.get("file"))
        return self.by_file.get((event_id, marker)) or self.by_event.get(event_id)


def default_position(screen_width, screen_height, width, height):
    return max(0, (screen_width - width) // 2), max(0, round(screen_height * .75 - height / 2))


class SubtitleWindow:
    def __init__(self, master, catalog=None, settings=SETTINGS, start_worker=True):
        self.master, self.settings = master, Path(settings)
        self.catalog = catalog or Catalog()
        self.options = dict(DEFAULT_OPTIONS)
        try:
            saved = json.loads(self.settings.read_bytes())
            # 旧版字段兼容：zh_font / ja_font
            if "zh_font" in saved and "zh_size" not in saved:
                saved["zh_size"] = saved["zh_font"]
            if "ja_font" in saved and "ja_size" not in saved:
                saved["ja_size"] = saved["ja_font"]
            self.options.update({key: saved[key] for key in self.options
                                 if key in saved})
        except (OSError, ValueError, TypeError):
            pass
        self.root = tk.Toplevel(master)
        self.root.withdraw()
        self.root.title("影之诗 · 卡牌语音字幕")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT)
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.root.attributes("-alpha", 1)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.body = tk.Frame(self.root, bg=TRANSPARENT, padx=8, pady=3)
        self.body.pack(fill="both", expand=True)
        wrap = min(1000, round(master.winfo_screenwidth() * .78))
        # 中文字体族缺省按系统可用字体挑一个，避免整窗空白
        import tkinter.font as tkfont
        families = set(tkfont.families(master))
        if not self.options.get("zh_family") or self.options["zh_family"] not in families:
            for cand in ("微软雅黑", "Microsoft YaHei UI", "Microsoft YaHei", "黑体"):
                if cand in families:
                    self.options["zh_family"] = cand
                    break
        if not self.options.get("ja_family") or self.options["ja_family"] not in families:
            for cand in ("Yu Gothic UI", "Meiryo", "MS Gothic", "Microsoft YaHei UI"):
                if cand in families:
                    self.options["ja_family"] = cand
                    break
        self.zh = OutlinedText(self.body, self.options, "zh", wrap,
                               "Microsoft YaHei UI", 22)
        self.ja = OutlinedText(self.body, self.options, "ja", wrap,
                               "Yu Gothic UI", 15)
        self.zh.pack(anchor="center")
        self.ja.pack(anchor="center", pady=(3, 0))
        for widget in (self.root, self.body, self.zh, self.ja):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)
        from subtitle_overlay import Overlay
        self.overlay = Overlay(self._native_drag_start, self._native_drag_end, self.hide)
        self.queue = queue.Queue(maxsize=64)
        self.worker = None
        self.start_worker = start_worker
        self.last_token, self.timer, self.drag = None, None, None
        self.place_id = None
        self.minimum_visible_until = 0.0
        self.closed, self.last_reload = False, 0
        import stream_settings
        stream_settings.watch(self.root, self._stream_mode)
        self.poll_id = master.after(50, self._poll)

    def apply_options(self, options=None):
        """样式变更后立即生效（字体 / 字色 / 字号 / 粗斜体 / 描边）。

        旧版没有这个方法，改完设置只有重启字幕窗才生效；
        而且 OutlinedText 只在 text 变化时重绘，换字体等于没换。
        """
        if options:
            self.options.update(options)
        if not self.options.get("enabled", True):
            self.hide()
            if self.worker is not None:
                self.worker.stop()
            self._discard_queue()
        self.last_reload = 0
        if self.options.get("locked") and self.drag:
            self.drag = None
            self.timer = self.master.after(3000, self.hide)
        try:
            self.zh.apply(self.options)
            self.ja.apply(self.options)
            self._place()
            # Windows may apply a pending size Configure after the first move.
            self.place_id = self.root.after_idle(self._place)
            if self.overlay.visible:
                self._show_native()
        except Exception:
            pass

    def _stream_mode(self, enabled):
        """Keep the native per-pixel-alpha caption transparent in both modes.

        The hidden Tk host only measures text; OBS should capture the native
        caption window, whose stable title and APPWINDOW style stay unchanged.
        """
        previous = getattr(self, "_stream_enabled", False)
        self._stream_enabled = bool(enabled)
        if enabled and not previous:
            self.apply_options({"enabled": True})
            self.settings.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.options, ensure_ascii=False), encoding="utf8")
            temporary.replace(self.settings)
        self.overlay.set_capture_mode(enabled)
        if not self.overlay.visible:
            self.hide()
        for widget in (self.root, self.body, self.zh, self.ja):
            widget.configure(bg=TRANSPARENT)
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.root.withdraw()
        if getattr(self, "overlay", None) and self.overlay.visible:
            self._show_native()

    def show(self, caption, token, duration=5):
        if self.closed or not self.options.get("enabled"):
            return
        if self.timer:
            self.master.after_cancel(self.timer)
        self.zh.config(text=caption.zh)
        self.ja.config(text=caption.ja)
        self._place()
        self.last_token = token
        self.minimum_visible_until = time.monotonic() + 1.2
        self._show_native()
        self.root.update_idletasks()
        # Bounded diagnostics: one current display record, never append audio.
        try:
            target = self.settings.parent / "voice-window.json"
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(dict(at=time.time(), zh=caption.zh, ja=caption.ja,
                geometry=self.overlay.rectangle(), mapped=self.overlay.visible,
                screen=[self.master.winfo_screenwidth(), self.master.winfo_screenheight()]), ensure_ascii=False), encoding="utf8")
            tmp.replace(target)
        except OSError:
            pass
        self.timer = self.master.after(round(max(1.2, min(float(duration), 40)) * 1000), self.hide)

    def _live_canvas_size(self):
        sw, sh = self.master.winfo_screenwidth(), self.master.winfo_screenheight()
        points = self.zh.caption_font[1] + self.ja.caption_font[1]
        width = min(sw, self.zh.wraplength + 34)
        height = min(sh, max(320, round(sh * .30),
                             round(points * self.zh.winfo_fpixels("1p") * 5 + 32)))
        return width, height

    def _show_native(self, blank=False):
        from PIL import Image
        live = bool(getattr(self, "_stream_enabled", False))
        if blank:
            width, height = self._live_canvas_size()
            image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        else:
            zh, ja = self.zh.caption_bitmap, self.ja.caption_bitmap
            text_width, text_height = max(zh.width, ja.width), zh.height+ja.height+3
            group = Image.new("RGBA", (text_width, text_height), (0, 0, 0, 0))
            group.alpha_composite(zh, ((text_width-zh.width)//2, 0))
            group.alpha_composite(ja, ((text_width-ja.width)//2, zh.height+3))
            width, height = self._live_canvas_size() if live else (text_width+16, text_height+6)
            if live:
                ratio = min(1, (width-16)/group.width, (height-12)/group.height)
                if ratio < 1:
                    group = group.resize((max(1, round(group.width*ratio)),
                                          max(1, round(group.height*ratio))), Image.Resampling.LANCZOS)
            image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            image.alpha_composite(group, ((width-group.width)//2, (height-group.height)//2))
        self._place(width, height)
        self.overlay.show(image, *self.placement, locked=blank or bool(self.options.get("locked")))
        if blank:
            self.overlay.visible = False
        self.root.withdraw()

    def _raise_above_game(self):
        self.overlay.raise_window()

    def _native_drag_start(self):
        self.drag = True
        if self.timer:
            self.master.after_cancel(self.timer)
            self.timer = None

    def _native_drag_end(self, x, y):
        self.drag = None
        self.options.update(x=x, y=y)
        self.root.geometry("%+d%+d" % (x, y))
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.options), encoding="utf8")
        temporary.replace(self.settings)
        self.timer = self.master.after(3000, self.hide)

    def _place(self, width=None, height=None):
        if self.closed:
            return
        self.root.update_idletasks()
        if width is None or height is None:
            width, height = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        sw, sh = self.master.winfo_screenwidth(), self.master.winfo_screenheight()
        default_x, default_y = default_position(sw, sh, width, height)
        x = self.options.get("x")
        y = self.options.get("y")
        x, y = default_x if x is None else int(x), default_y if y is None else int(y)
        if x < 0 or y < 0 or x + width > sw or y + height > sh:
            x, y = default_x, default_y
        x, y = max(0, min(sw - width, x)), max(0, min(sh - height, y))
        self.placement = (x, y)
        self.root.geometry("%dx%d%+d%+d" % (width, height, x, y))

    def hide(self):
        if self.timer:
            self.master.after_cancel(self.timer)
            self.timer = None
        if not self.closed:
            if getattr(self, "_stream_enabled", False):
                self._show_native(blank=True)
            else:
                self.overlay.hide()
            self.root.withdraw()

    def _discard_queue(self):
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                return

    def _poll(self):
        if self.closed:
            return
        self.poll_id = self.master.after(50, self._poll)
        if not self.options.get("enabled", True):
            self._discard_queue()
            return
        if self.worker is not None and self.worker.cancel.is_set():
            # Wait without blocking Tk; never run two voice readers after a quick toggle.
            self._discard_queue()
            if self.worker.is_alive():
                return
            self.worker = None
            self.last_token = None
            self.last_reload = 0
        if time.monotonic() - self.last_reload > 1:
            self.last_reload = time.monotonic()
            self.catalog.reload()
            if self.start_worker and self.catalog.ready and self.worker is None:
                from voice_memory import VoiceWorker
                worker = VoiceWorker(self.queue, self.catalog)
                try:
                    worker.start()
                except RuntimeError as exc:
                    from voice_memory import report_status
                    report_status("error", error=repr(exc))
                else:
                    self.worker = worker
            if not self.catalog.ready:
                self.hide()
        while not self.queue.empty():
            voice = self.queue.get_nowait()
            if voice is None:
                if not self.drag:
                    remaining = self.minimum_visible_until - time.monotonic()
                    if remaining > 0:
                        if self.timer:
                            self.master.after_cancel(self.timer)
                        self.timer = self.master.after(max(1, round(remaining * 1000)), self.hide)
                    else:
                        self.hide()
                continue
            token = voice.get("token")
            if token == self.last_token:
                continue
            caption = self.catalog.match(voice)
            if caption:
                self.show(caption, token, voice.get("duration", 5))
            else:
                self.last_token = token
                self.hide()
        if self.overlay.visible:
            self._raise_above_game()

    def _drag_start(self, event):
        if self.options.get("locked"):
            return
        self.drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())
        if self.timer:
            self.master.after_cancel(self.timer)
            self.timer = None

    def _drag_move(self, event):
        if self.drag and not self.options.get("locked"):
            self.root.geometry("%+d%+d" % (event.x_root - self.drag[0], event.y_root - self.drag[1]))

    def _drag_end(self, event):
        if self.options.get("locked") or not self.drag:
            return
        self.drag = None
        self.options.update(x=self.root.winfo_x(), y=self.root.winfo_y())
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.options), encoding="utf8")
        temporary.replace(self.settings)
        self.timer = self.master.after(3000, self.hide)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.worker:
            self.worker.stop()
        for callback in (self.place_id, self.timer, self.poll_id):
            if callback is not None:
                try:
                    self.master.after_cancel(callback)
                except tk.TclError:
                    pass
        try:
            self.overlay.close()
        finally:
            try:
                self.root.destroy()
            except tk.TclError:
                pass
