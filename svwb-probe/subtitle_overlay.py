"""Small per-pixel-alpha Windows caption window, independent of Tk painting."""
import ctypes as C
from ctypes import wintypes as W
import os

LRESULT = C.c_ssize_t
WNDPROC = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)


class WndClass(C.Structure):
    _fields_ = [("style", W.UINT), ("proc", WNDPROC), ("cls_extra", C.c_int),
                ("wnd_extra", C.c_int), ("instance", W.HINSTANCE),
                ("icon", W.HICON), ("cursor", W.HANDLE), ("background", W.HBRUSH),
                ("menu", W.LPCWSTR), ("name", W.LPCWSTR)]


class Blend(C.Structure):
    _fields_ = [("op", C.c_ubyte), ("flags", C.c_ubyte),
                ("alpha", C.c_ubyte), ("format", C.c_ubyte)]


class Overlay:
    def __init__(self, on_begin, on_end, on_close=None):
        self.u, self.g, self.k = C.WinDLL("user32", use_last_error=True), C.WinDLL("gdi32", use_last_error=True), C.WinDLL("kernel32", use_last_error=True)
        self.on_begin, self.on_end, self.on_close = on_begin, on_end, on_close
        self.visible, self.locked, self.hwnd = False, False, None
        self.capture_mode = False
        self.name = "SvwbCardVoiceSubtitles"
        self.k.GetModuleHandleW.argtypes = (W.LPCWSTR,)
        self.k.GetModuleHandleW.restype = W.HMODULE
        self.instance = self.k.GetModuleHandleW(None)
        self.u.DefWindowProcW.argtypes = (W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        self.u.DefWindowProcW.restype = LRESULT
        self.u.RegisterClassW.argtypes = (C.POINTER(WndClass),)
        self.u.CreateWindowExW.argtypes = (W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
            C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p)
        self.u.CreateWindowExW.restype = W.HWND
        self.u.SendMessageW.argtypes = (W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        self.u.SendMessageW.restype = LRESULT
        self.u.SetWindowPos.argtypes = (W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT)
        self.u.GetWindowRect.argtypes = (W.HWND, C.POINTER(W.RECT))
        self.u.ShowWindow.argtypes = (W.HWND, C.c_int)
        self.u.DestroyWindow.argtypes = (W.HWND,)
        self.u.UnregisterClassW.argtypes = (W.LPCWSTR, W.HINSTANCE)
        self.u.GetWindowLongW.argtypes = (W.HWND, C.c_int)
        self.u.SetWindowLongW.argtypes = (W.HWND, C.c_int, W.LONG)
        self.u.UpdateLayeredWindow.argtypes = (W.HWND, W.HDC, C.POINTER(W.POINT),
            C.POINTER(W.SIZE), W.HDC, C.POINTER(W.POINT), W.DWORD, C.POINTER(Blend), W.DWORD)
        self.g.CreateCompatibleDC.argtypes = (W.HDC,)
        self.g.CreateCompatibleDC.restype = W.HDC
        self.g.SelectObject.argtypes = (W.HDC, W.HGDIOBJ)
        self.g.SelectObject.restype = W.HGDIOBJ
        self.g.DeleteObject.argtypes = (W.HGDIOBJ,)
        self.g.DeleteDC.argtypes = (W.HDC,)
        self.proc = WNDPROC(self._message)
        wc = WndClass()
        wc.proc, wc.instance, wc.name = self.proc, self.instance, self.name
        if not self.u.RegisterClassW(C.byref(wc)):
            raise C.WinError(C.get_last_error())
        self.hwnd = self.u.CreateWindowExW(0x80000 | 0x40000 | 0x08000000,
            self.name, "影之诗 · 卡牌语音字幕", 0x80000000,
            0, 0, 1, 1, None, None, self.instance, None)
        if not self.hwnd:
            self.u.UnregisterClassW(self.name, self.instance)
            raise C.WinError(C.get_last_error())

    def _message(self, hwnd, msg, wp, lp):
        try:
            if msg == 0x10:
                if self.on_close:
                    self.on_close()
                else:
                    self.hide()
                return 0
            if msg == 0x201 and not self.locked:  # native move loop
                self.u.SendMessageW(hwnd, 0xA1, 2, 0)
                return 0
            if msg == 0x231:
                self.on_begin()
            elif msg == 0x232:
                rect = W.RECT()
                if self.u.GetWindowRect(hwnd, C.byref(rect)):
                    self.on_end(rect.left, rect.top)
            elif msg == 0x21:
                return 3  # MA_NOACTIVATE: keep game keyboard focus
        except Exception:
            pass
        return self.u.DefWindowProcW(hwnd, msg, wp, lp)

    def show(self, image, x, y, locked=False):
        from subtitle_bitmap import BitmapInfo
        width, height = image.size
        dc = self.g.CreateCompatibleDC(None)
        info = BitmapInfo()
        info.size, info.width, info.height, info.planes, info.bits = 40, width, -height, 1, 32
        bits = C.c_void_p()
        self.g.CreateDIBSection.argtypes = (W.HDC, C.POINTER(BitmapInfo), W.UINT,
            C.POINTER(C.c_void_p), W.HANDLE, W.DWORD)
        self.g.CreateDIBSection.restype = W.HBITMAP
        bitmap = self.g.CreateDIBSection(dc, C.byref(info), 0, C.byref(bits), None, 0)
        if not dc or not bitmap:
            if bitmap: self.g.DeleteObject(bitmap)
            if dc: self.g.DeleteDC(dc)
            raise C.WinError(C.get_last_error())
        old = self.g.SelectObject(dc, bitmap)
        try:
            from PIL import Image, ImageChops
            r, green, b, alpha = image.convert("RGBA").split()
            premultiplied = Image.merge("RGBA", (ImageChops.multiply(r, alpha),
                ImageChops.multiply(green, alpha), ImageChops.multiply(b, alpha), alpha))
            pixels = premultiplied.tobytes("raw", "BGRA")
            C.memmove(bits, pixels, len(pixels))
            pos, size, src, blend = W.POINT(x, y), W.SIZE(width, height), W.POINT(0, 0), Blend(0, 0, 255, 1)
            if not self.u.UpdateLayeredWindow(self.hwnd, None, C.byref(pos), C.byref(size), dc, C.byref(src), 0, C.byref(blend), 2):
                raise C.WinError(C.get_last_error())
            self.locked = locked
            flags = self.u.GetWindowLongW(self.hwnd, -20)
            flags = flags | 0x20 if locked else flags & ~0x20
            self.u.SetWindowLongW(self.hwnd, -20, flags)
            self.visible = True
            self.raise_window()
        finally:
            self.g.SelectObject(dc, old)
            self.g.DeleteObject(bitmap)
            self.g.DeleteDC(dc)

    def raise_window(self):
        if self.hwnd and self.visible:
            self.u.SetWindowPos(self.hwnd, W.HWND(-1), 0, 0, 0, 0, 0x53)

    def rectangle(self):
        rect = W.RECT()
        if not self.hwnd or not self.u.GetWindowRect(self.hwnd, C.byref(rect)):
            return None
        return rect.left, rect.top, rect.right-rect.left, rect.bottom-rect.top

    def set_capture_mode(self, enabled):
        self.capture_mode = bool(enabled)

    def hide(self):
        if self.hwnd and self.capture_mode:
            # Keep the same HWND available to OBS even between spoken lines.
            # Idle pixels are fully transparent and cannot intercept game clicks.
            from PIL import Image
            rect = self.rectangle()
            if rect and rect[2] > 1 and rect[3] > 1:
                x, y, width, height = rect
            else:
                width, height = 640, 100
                x = max(0, (self.u.GetSystemMetrics(0)-width)//2)
                y = max(0, round(self.u.GetSystemMetrics(1)*.75-height/2))
            self.show(Image.new("RGBA", (width, height), (0, 0, 0, 0)), x, y, locked=True)
            self.visible = False
            return
        if self.hwnd:
            self.u.ShowWindow(self.hwnd, 0)
        self.visible = False

    def close(self):
        if self.hwnd:
            self.u.DestroyWindow(self.hwnd)
            self.hwnd = None
            self.u.UnregisterClassW(self.name, self.instance)
