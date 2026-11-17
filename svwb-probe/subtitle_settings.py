"""卡牌字幕样式设置：字体（含导入）、颜色、字号、描边、粗斜体。

设计要点：
1. 字体列表 = 系统已装字体 + 用户导入的字体（导入后立刻可用，重启后自动重新加载）
2. 中文/日文分开设置（日文用中日双语字体时，中文和假名的字形差异很大）
3. 实时预览，点「应用」写入 logs/card-subtitle-window.json 并让字幕窗立即生效
4. 字体导入走 AddFontResourceEx(FR_PRIVATE)：不需要管理员权限，只对本进程有效
"""
import ctypes
import json
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser, filedialog, messagebox

HERE = Path(__file__).resolve().parent
SETTINGS = HERE / "logs/card-subtitle-window.json"
IMPORTED_FONTS = HERE / "gamedata/imported_fonts.json"

# 与 app.py 一致的深色配色（不 import app，避免循环依赖）
BG = "#171c25"
BG2 = "#1f2632"
FG = "#efe9dc"
DIM = "#9aa7a0"
GOLD = "#c9a86a"
GOLD2 = "#b8945a"
GOLD_HI = "#e0c082"
RULE = "#2c3542"
BTN = "#2a3341"
BTN_HI = "#38445a"
BORDER = "#39424f"
SELECT = "#3a4456"
TROUGH = "#2a3240"

# 推荐字体（中文）。只有系统里存在的才会出现在下拉框里。
RECOMMEND_ZH = [
    ("微软雅黑", "常规 · 默认，最清晰"),
    ("华文行楷", "华丽 · 行书，洒脱"),
    ("华文楷体", "雅致 · 楷书"),
    ("华文彩云", "华丽 · 装饰字"),
    ("华文琥珀", "厚重 · 装饰字"),
    ("华文新魏", "方正 · 魏碑"),
    ("方正舒体", "毛笔 · 行书"),
    ("隶书", "古风 · 隶书"),
    ("幼圆", "圆润 · 可爱"),
    ("楷体", "清秀 · 楷书"),
    ("宋体", "经典 · 衬线"),
    ("仿宋", "文雅 · 仿宋"),
    ("黑体", "粗黑 · 醒目"),
    ("等线", "现代 · 无衬线"),
    ("思源黑体", "开源 · 无衬线"),
    ("思源宋体", "开源 · 衬线"),
    ("霞鹜文楷", "开源 · 楷体，很耐看"),
    ("站酷快乐体", "活泼 · 手写"),
]
RECOMMEND_JA = [
    ("Yu Gothic UI", "Gothic · 默认"),
    ("Meiryo", "Gothic · 圆润"),
    ("MS Gothic", "Gothic · 标准"),
    ("Yu Mincho", "明朝 · 雅致"),
    ("MS Mincho", "明朝 · 经典"),
    ("Noto Sans JP", "开源 · 无衬线"),
    ("Noto Serif JP", "开源 · 衬线"),
    ("小塚ゴシック Pro", "Kozuka Gothic"),
    ("小塚明朝 Pro", "Kozuka Mincho"),
]

DEFAULT_STYLE = {
    "zh_family": "微软雅黑",
    "zh_size": 22,
    "zh_color": "#ffffff",
    "zh_bold": True,
    "zh_italic": False,
    "ja_family": "Yu Gothic UI",
    "ja_size": 15,
    "ja_color": "#e1e5eb",
    "ja_bold": False,
    "ja_italic": False,
    "stroke_color": "#080a0d",
    "stroke_width": 1,
    "locked": False,
}

FR_PRIVATE = 0x10


def load_imported(path=IMPORTED_FONTS):
    """已导入字体的文件路径列表。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return [str(p) for p in (data.get("fonts") or []) if p]
    except (OSError, ValueError, TypeError):
        return []


def save_imported(paths, path=IMPORTED_FONTS):
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(path).with_suffix(".tmp")
        tmp.write_text(json.dumps({"fonts": list(paths)}, ensure_ascii=False,
                                  indent=1), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        return False


def register_font_file(font_path):
    """把字体文件临时注册到本进程（不需要管理员）。返回成功与否。"""
    try:
        n = ctypes.windll.gdi32.AddFontResourceExW(str(font_path), FR_PRIVATE, 0)
        return n > 0
    except Exception:
        return False


def load_saved_fonts():
    """启动时重新加载之前导入过的字体（进程重启后注册失效）。"""
    ok = 0
    for p in load_imported():
        if Path(p).exists() and register_font_file(p):
            ok += 1
    return ok


def read_style(path=SETTINGS):
    """读现有设置，缺的字段补默认值。"""
    style = dict(DEFAULT_STYLE)
    try:
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
        for key in style:
            if key in saved:
                style[key] = saved[key]
    except (OSError, ValueError, TypeError):
        pass
    return style


def _family_list(root, recommend, imported_names):
    """系统已装字体 + 导入字体，按推荐顺序排前面。"""
    installed = set(tkfont.families(root))
    # 导入的字体在 tk 里可能名字不同，做一个宽松匹配
    ordered = []
    for name, note in recommend:
        if name in installed or name in imported_names:
            ordered.append((name, note))
    # 其余中文/日文可用的字体附在后面（避免 400 个字体全列出来）
    extra = []
    for name in sorted(installed):
        if name in {n for n, _ in ordered}:
            continue
        if name in imported_names:
            extra.append((name, "已导入"))
    ordered.extend(extra)
    return ordered


class StyleDialog:
    """字幕样式设置窗。"""

    def __init__(self, master, on_apply=None):
        self.master = master
        self.on_apply = on_apply
        self.style = read_style()
        self.imported = load_imported()
        self.imported_names = set()
        for p in self.imported:
            self.imported_names.add(Path(p).stem)

        self.w = tk.Toplevel(master)
        self.w.title("字幕样式")
        self.w.configure(bg=BG)
        try:
            self.w.attributes("-topmost", True)
            self.w.attributes("-alpha", 0.95)
        except Exception:
            pass
        sw, sh = self.w.winfo_screenwidth(), self.w.winfo_screenheight()
        width, height = min(680, sw - 30), min(700, sh - 60)
        x, y = max(0, (sw - width) // 2), max(0, (sh - height) // 3)
        self.w.geometry("%dx%d+%d+%d" % (width, height, x, y))
        self.w.transient(master)
        self.w.resizable(False, False)

        self.f_t = tkfont.Font(family="Microsoft YaHei UI", size=11, weight="bold")
        self.f_s = tkfont.Font(family="Microsoft YaHei UI", size=9)
        self.vars = {}

        tk.Label(self.w, text="卡牌字幕 · 样式", bg=BG, fg=GOLD,
                 font=self.f_t).pack(pady=(10, 2))
        tk.Frame(self.w, bg=RULE, height=1).pack(fill="x", padx=16)
        area = tk.Frame(self.w, bg=BG)
        area.pack(fill="both", expand=True, padx=16, pady=4)
        self.scroll = tk.Canvas(area, bg=BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(area, orient="vertical", command=self.scroll.yview)
        scrollbar.pack(side="right", fill="y")
        self.scroll.pack(side="left", fill="both", expand=True)
        self.scroll.configure(yscrollcommand=scrollbar.set)
        self.body = tk.Frame(self.scroll, bg=BG)
        item = self.scroll.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: self.scroll.configure(
            scrollregion=self.scroll.bbox("all")))
        self.scroll.bind("<Configure>", lambda e: self.scroll.itemconfigure(item, width=e.width))
        self.w.bind("<MouseWheel>", lambda e: self.scroll.yview_scroll(
            -int(e.delta / 120), "units"))

        # ---------- 预览 ----------
        self._build_preview()

        # ---------- 中文 ----------
        self._section("中文（主字幕）")
        self._font_row("中文字体", "zh_family", RECOMMEND_ZH, "zh")
        self._number_row("字号", "zh_size", 12, 64, self.style.get("zh_size", 22))
        self._color_row("字色", "zh_color", self.style.get("zh_color", "#ffffff"))
        self._toggle_row("加粗 / 斜体", "zh_bold", "zh_italic",
                         bool(self.style.get("zh_bold", True)),
                         bool(self.style.get("zh_italic", False)))

        # ---------- 日文 ----------
        self._section("日文（副字幕）")
        self._font_row("日文字体", "ja_family", RECOMMEND_JA, "ja")
        self._number_row("字号", "ja_size", 8, 48, self.style.get("ja_size", 15))
        self._color_row("字色", "ja_color", self.style.get("ja_color", "#e1e5eb"))
        self._toggle_row("加粗 / 斜体", "ja_bold", "ja_italic",
                         bool(self.style.get("ja_bold", False)),
                         bool(self.style.get("ja_italic", False)))

        # ---------- 描边 ----------
        self._section("描边（让字在任何背景上都看得清）")
        self._color_row("描边颜色", "stroke_color",
                        self.style.get("stroke_color", "#080a0d"))
        self._number_row("描边粗细", "stroke_width", 0, 6,
                         self.style.get("stroke_width", 1))

        # ---------- 字体导入 ----------
        self._section("字体导入")
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=4)
        tk.Label(row, text="支持 .ttf / .otf / .ttc；导入后立刻可用，"
                           "下次启动自动重新加载", bg=BG, fg=DIM,
                 font=self.f_s, anchor="w", justify="left").pack(side="left")
        tk.Button(row, text="导入字体…", command=self._import_font, bg=BTN,
                  fg=GOLD, activebackground=BTN_HI, activeforeground="#ffffff",
                  relief="flat", bd=0, padx=10, pady=3, font=self.f_s,
                  cursor="hand2").pack(side="right")
        self.import_label = tk.Label(self.body, text="", bg=BG, fg=DIM,
                                     font=self.f_s, anchor="w")
        self.import_label.pack(fill="x")
        self._refresh_import_label()

        self.vars["locked"] = tk.BooleanVar(value=bool(self.style.get("locked", False)))
        tk.Checkbutton(self.body, text="锁定字幕位置（禁止拖动）",
                       variable=self.vars["locked"], bg=BG, fg=FG,
                       selectcolor=SELECT, activebackground=BG2,
                       activeforeground=FG, font=self.f_s).pack(anchor="w", pady=5)

        # ---------- 底部按钮 ----------
        tk.Frame(self.w, bg=RULE, height=1).pack(fill="x", padx=16, pady=(4, 0))
        bar = tk.Frame(self.w, bg=BG)
        bar.pack(fill="x", padx=16, pady=8)
        tk.Button(bar, text="应用", command=self._apply, bg=GOLD, fg="#1a1209",
                  activebackground=GOLD_HI, activeforeground="#000000",
                  relief="flat", bd=0, padx=16, pady=5, font=self.f_s,
                  cursor="hand2").pack(side="right")
        tk.Button(bar, text="取消", command=self.w.destroy, bg=BTN, fg=FG,
                  activebackground=BTN_HI, activeforeground="#ffffff",
                  relief="flat", bd=0, padx=14, pady=5, font=self.f_s,
                  cursor="hand2").pack(side="right", padx=6)
        tk.Button(bar, text="重置字幕", command=self._reset, bg=BTN, fg=DIM,
                  activebackground=BTN_HI, activeforeground="#ffffff",
                  relief="flat", bd=0, padx=12, pady=5, font=self.f_s,
                  cursor="hand2").pack(side="left")

        self._update_preview()

    # ------------------------------------------------------------------ UI
    def _section(self, title):
        tk.Label(self.body, text=title, bg=BG, fg=GOLD2, font=self.f_s,
                 anchor="w").pack(fill="x", pady=(7, 1))
        tk.Frame(self.body, bg=RULE, height=1).pack(fill="x")

    def _build_preview(self):
        box = tk.Frame(self.body, bg=BG2, bd=0)
        box.pack(fill="x", pady=(6, 2))
        self.preview = tk.Canvas(box, bg="#2a2f3a", height=96,
                                 highlightthickness=0, bd=0)
        self.preview.pack(fill="x", padx=8, pady=8)
        self.preview.bind("<Configure>", lambda e: self._update_preview())

    def _font_row(self, label, key, recommend, prefix):
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=BG, fg=FG, font=self.f_s, width=10,
                 anchor="w").pack(side="left")
        families = _family_list(self.w, recommend, self.imported_names)
        names = [n for n, _ in families] or [self.style.get(key, "微软雅黑")]
        var = tk.StringVar(value=self.style.get(key) or names[0])
        self.vars[key] = var
        box = tk.OptionMenu(row, var, *names)
        box.configure(bg=BG2, fg=FG, activebackground=BTN_HI,
                      activeforeground="#ffffff", relief="flat", bd=0,
                      highlightthickness=1, highlightbackground=BORDER,
                      font=self.f_s, width=18)
        box["menu"].configure(bg=BG2, fg=FG, activebackground=GOLD,
                              activeforeground="#1a1209", font=self.f_s)
        box.pack(side="left", padx=4)
        var.trace_add("write", lambda *_: self._update_preview())

    def _number_row(self, label, key, lo, hi, init):
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=BG, fg=FG, font=self.f_s, width=10,
                 anchor="w").pack(side="left")
        var = tk.IntVar(value=int(init))
        self.vars[key] = var
        val = tk.Label(row, text=str(int(init)), bg=BG, fg=GOLD, width=4,
                       font=self.f_s, anchor="e")
        val.pack(side="right")
        var.trace_add("write", lambda *_: val.configure(text=str(var.get())))

        def on_move(v):
            var.set(int(float(v)))
            val.config(text=str(int(float(v))))
            self._update_preview()

        tk.Scale(row, from_=lo, to=hi, orient="horizontal", variable=var,
                 showvalue=False, bg=BG, fg=FG, troughcolor=TROUGH,
                 activebackground=GOLD_HI, highlightthickness=0, bd=0,
                 sliderrelief="flat", command=on_move,
                 length=280).pack(side="left", padx=6, fill="x", expand=True)

    def _color_row(self, label, key, init):
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=BG, fg=FG, font=self.f_s, width=10,
                 anchor="w").pack(side="left")
        var = tk.StringVar(value=init)
        self.vars[key] = var
        swatch = tk.Label(row, text="    ", bg=init, width=6, bd=0,
                          relief="flat")
        swatch.pack(side="left", padx=4)
        hexlab = tk.Label(row, text=init, bg=BG, fg=DIM, font=self.f_s,
                          width=10, anchor="w")
        hexlab.pack(side="left", padx=2)
        def sync_color(*_):
            swatch.configure(bg=var.get())
            hexlab.configure(text=var.get())
        var.trace_add("write", sync_color)

        def pick():
            rgb, hx = colorchooser.askcolor(color=var.get(),
                                            title="选择%s" % label,
                                            parent=self.w)
            if hx:
                var.set(hx)
                swatch.config(bg=hx)
                hexlab.config(text=hx)
                self._update_preview()

        tk.Button(row, text="选择颜色…", command=pick, bg=BTN, fg=GOLD,
                  activebackground=BTN_HI, activeforeground="#ffffff",
                  relief="flat", bd=0, padx=8, pady=2, font=self.f_s,
                  cursor="hand2").pack(side="left", padx=6)

    def _toggle_row(self, label, bold_key, italic_key, bold_init, italic_init):
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=BG, fg=FG, font=self.f_s, width=10,
                 anchor="w").pack(side="left")
        bv = tk.BooleanVar(value=bold_init)
        iv = tk.BooleanVar(value=italic_init)
        self.vars[bold_key] = bv
        self.vars[italic_key] = iv
        tk.Checkbutton(row, text="加粗", variable=bv, bg=BG, fg=FG,
                       selectcolor=SELECT, activebackground=BG2,
                       activeforeground="#ffffff", font=self.f_s,
                       highlightthickness=0,
                       command=self._update_preview).pack(side="left")
        tk.Checkbutton(row, text="斜体", variable=iv, bg=BG, fg=FG,
                       selectcolor=SELECT, activebackground=BG2,
                       activeforeground="#ffffff", font=self.f_s,
                       highlightthickness=0,
                       command=self._update_preview).pack(side="left")

    # ------------------------------------------------------------------ 预览
    def _style_now(self):
        s = {}
        for key, var in self.vars.items():
            try:
                s[key] = var.get()
            except Exception:
                pass
        return s

    def _font_tuple(self, style, prefix):
        family = style.get(prefix + "_family") or DEFAULT_STYLE[prefix + "_family"]
        size = int(style.get(prefix + "_size") or DEFAULT_STYLE[prefix + "_size"])
        weight = "bold" if style.get(prefix + "_bold") else "normal"
        slant = "italic" if style.get(prefix + "_italic") else "roman"
        return (family, max(6, min(96, size)), weight, slant)

    def _update_preview(self):
        try:
            style = self._style_now()
            c = self.preview
            c.delete("all")
            # 窗口刚创建时 winfo_width() 还是 1，直接用它会把字画到 0.
            # 像素处看不见；预览区宽度是固定的，这里用逻辑宽度兜底。
            w = c.winfo_width()
            if w < 100:
                w = 600
            stroke = int(style.get("stroke_width", 1) or 0)
            zh_font = self._font_tuple(style, "zh")
            ja_font = self._font_tuple(style, "ja")
            zh_color = style.get("zh_color") or "#ffffff"
            ja_color = style.get("ja_color") or "#e1e5eb"
            stroke_color = style.get("stroke_color") or "#080a0d"
            zh_text = "那颗心脏就由我收下"
            ja_text = "その心、いただきます"

            def draw(font, text, fill, y):
                # 注意：base 里不能放 fill，否则下面 fill=stroke_color 会
                # 和 **base 里的 fill 撞成 "got multiple values"（以前被
                # except 吞掉，表现就是预览永远空白）。
                base = dict(text=text, font=font, anchor="n", width=max(20, w - 24 - 2 * stroke), justify="center")
                if stroke > 0:
                    for dx, dy in ((-stroke, 0), (stroke, 0), (0, -stroke),
                                   (0, stroke), (-stroke, -stroke),
                                   (-stroke, stroke), (stroke, -stroke),
                                   (stroke, stroke)):
                        c.create_text(w / 2 + dx, y + dy,
                                      fill=stroke_color, **base)
                return c.create_text(w / 2, y, fill=fill, **base)

            # Tk 字号是点，实际像素高度还受 DPI、字体和换行影响。
            # 用画布真实边界依次排版，描边也留出间隔。
            zh = draw(zh_font, zh_text, zh_color, 8 + stroke)
            zh_box = c.bbox(zh)
            ja = draw(ja_font, ja_text, ja_color, zh_box[3] + 8 + 2 * stroke)
            ja_box = c.bbox(ja)
            self.preview_bounds = (zh_box, ja_box)
            height = max(74, ja_box[3] + stroke + 8)
            if int(float(c.cget("height"))) != height:
                c.configure(height=height)
        except Exception:
            pass

    # ------------------------------------------------------------------ 动作
    def _import_font(self):
        path = filedialog.askopenfilename(
            parent=self.w, title="选择字体文件",
            filetypes=[("字体文件", "*.ttf *.otf *.ttc"), ("所有文件", "*.*")])
        if not path:
            return
        if not register_font_file(path):
            messagebox.showerror("导入失败",
                                 "无法加载这个字体文件。\n"
                                 "可能不是有效的 ttf/otf/ttc，或者文件已损坏。",
                                 parent=self.w)
            return
        if path not in self.imported:
            self.imported.append(path)
            save_imported(self.imported)
        # 记下名字，刷新下拉框
        self.imported_names.add(Path(path).stem)
        name = Path(path).stem
        messagebox.showinfo(
            "导入成功",
            "字体「%s」已加载。\n\n如果下拉框里还没出现，请在字体名那一栏手动输入它，"
            "或者重新打开本窗口。\n\n（下次启动会自动重新加载）" % name,
            parent=self.w)
        self._refresh_import_label()

    def _refresh_import_label(self):
        if not self.imported:
            self.import_label.config(text="（还没有导入字体）")
        else:
            names = ", ".join(Path(p).stem for p in self.imported[-4:])
            more = "" if len(self.imported) <= 4 else " 等 %d 个" % len(self.imported)
            self.import_label.config(text="已导入：" + names + more)

    def _reset(self):
        for key, value in DEFAULT_STYLE.items():
            if key in self.vars:
                try:
                    self.vars[key].set(value)
                except Exception:
                    pass
        self._update_preview()
        # 重置立即生效，位置恢复为屏幕 75% 居中，保留字体导入文件。
        from card_subtitles import DEFAULT_OPTIONS
        self._save_options(dict(DEFAULT_OPTIONS), close=False)

    def _apply(self):
        self._save_options(self._style_now(), close=True)

    def _save_options(self, changes, close):
        # 普通应用保留位置；重置传入完整的初始字幕设置。
        out = {}
        try:
            saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
            out.update(saved)
        except (OSError, ValueError, TypeError):
            pass
        out.update(changes)
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            tmp = SETTINGS.with_suffix(".tmp")
            tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(SETTINGS)
        except OSError as e:
            messagebox.showerror("保存失败", str(e), parent=self.w)
            return
        if self.on_apply:
            try:
                self.on_apply(out)
            except Exception:
                pass
        if close:
            self.w.destroy()


def open_settings(master, on_apply=None):
    """打开字幕样式设置窗。"""
    return StyleDialog(master, on_apply)
