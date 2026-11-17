"""各窗口共用控制台的直播模式。"""
import ctypes
import json
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "dsh-shadowverse-huanjing/shots/live/overlay-settings.json"


def watch(root, change):
    previous = None
    timer = None
    closed = False
    def refresh():
        nonlocal previous, timer
        if closed:
            return
        try:
            enabled = bool(json.loads(FILE.read_text(encoding="utf-8-sig")).get("liveMode"))
        except FileNotFoundError:
            enabled = False
        except (ValueError, OSError):
            enabled = previous
        try:
            if enabled is not None and enabled != previous:
                change(enabled)
                previous = enabled
        finally:
            if not closed:
                timer = root.after(600, refresh)
    def stop(event=None):
        nonlocal closed, timer
        if event is not None and event.widget is not root:
            return
        closed = True
        if timer is not None:
            try:
                root.after_cancel(timer)
            except Exception:
                pass
            timer = None
    root.bind("<Destroy>", stop, add="+")
    timer = root.after_idle(refresh)
    return stop


def capture_style(window):
    from ctypes import wintypes
    u = ctypes.windll.user32
    hwnd = wintypes.HWND(int(window.frame(), 0))
    flags = u.GetWindowLongW(hwnd, -20)
    desired = (flags & ~(0x80000 | 0x80)) | 0x40000
    if flags != desired:
        u.SetWindowLongW(hwnd, -20, desired)
        u.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x37)
