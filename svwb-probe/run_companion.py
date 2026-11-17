#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""陪玩独立气泡启动器：气泡窗口 + AI 陪玩 + （可选）Frida 常驻读取。"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import singleton
if not singleton.acquire("SvwbPet"):
    print("宠物已经在运行")
    raise SystemExit(0)

# 宠物只允许从控制台启动：控制台没开就拒绝，避免“插件关了模型还在”
try:
    import parent_guard
    if not parent_guard.running():
        print("请先从控制台启动 AI 陪玩")
        raise SystemExit(0)
except SystemExit:
    raise
except Exception:
    pass


def _missing_companion_deps():
    """宠物渲染 / AI 陪玩依赖；新机器没装时给出明确提示，而不是静默透明窗。"""
    import contextlib
    import importlib
    import io
    need = (("pygame", "pygame"),
            ("OpenGL", "PyOpenGL"),
            ("live2d", "live2d-py"))
    missing = []
    for mod, pkg in need:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                module = importlib.import_module(mod)
            # 旧版 live2d-py 0.4.x 的顶层没有 Model，宠物会渲染失败。
            if mod == "live2d" and not hasattr(module, "Model"):
                missing.append(pkg)
        except Exception:
            missing.append(pkg)
    return missing


_missing = _missing_companion_deps()
if _missing:
    _msg = ("AI 陪玩缺少运行库：%s\n\n"
            "请双击助手目录里的「修复环境.bat」自动安装，\n"
            "装好后再从控制台点「启动宠物」。" % "、".join(_missing))
    try:
        import tkinter as tk
        from tkinter import messagebox
        _r = tk.Tk()
        _r.withdraw()
        messagebox.showerror("AI 陪玩无法启动", _msg)
        _r.destroy()
    except Exception:
        pass
    try:
        os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
        with open(os.path.join(HERE, "logs", "companion-err.log"), "a",
                  encoding="utf-8") as _fh:
            _fh.write("%s 缺少依赖：%s\n"
                      % (time.strftime("%Y-%m-%d %H:%M:%S"),
                         "、".join(_missing)))
    except Exception:
        pass
    print(_msg, flush=True)
    raise SystemExit(1)

import pet_view
import companion

AUTO_FRIDA = "--no-frida" not in sys.argv

w = pet_view.PetView()
_requests_missing = False
try:
    import requests as _requests  # noqa: F401
except Exception:
    _requests_missing = True
    try:
        w.show("AI 文字依赖 requests 缺失，请双击「修复环境.bat」", 12000)
    except Exception:
        pass
c = companion.Companion(on_text=w.show, on_state=w.show)
if _requests_missing:
    c._dep_warned = True


# Use the same asynchronous supervisor as the console. Never block the pet's
# first frame on process discovery or create an independent duplicate reader.
_reader_supervisor = None
if AUTO_FRIDA:
    from reader_supervisor import ReaderSupervisor
    _reader_supervisor = ReaderSupervisor(HERE, {})
    w.root.after(0, _reader_supervisor.ensure)


def tick():
    # 控制台退出后自动收尸，别留孤儿宠物窗口
    # ⚠️ 用 running() 而不是 orphaned()：控制台正常退出会删 _app.pid，
    #    orphaned() 那时返回 False，宠物会一直留着（"插件关了模型没关"）。
    try:
        import parent_guard
        if not parent_guard.running():
            try:
                w.close()
            except Exception:
                pass
            os._exit(0)
    except Exception:
        pass
    try:
        c.tick()
    except Exception:
        pass
    try:
        w.root.after(250, tick)
    except Exception:
        pass


# Closing the pet immediately disables the AI, before renderer shutdown.
_close_pet = w.close
def close_pet():
    c.close()
    _close_pet()
w.close = close_pet
w.root.protocol("WM_DELETE_WINDOW", close_pet)
w.root.after(250, tick)
try:
    w.run()
finally:
    c.close()
