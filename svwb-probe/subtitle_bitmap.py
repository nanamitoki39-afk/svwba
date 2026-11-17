"""Render caption glyphs at native screen DPI before compositing the overlay."""
import ctypes as C
from ctypes import wintypes as W


class BitmapInfo(C.Structure):
    _fields_ = [("size", W.DWORD), ("width", W.LONG), ("height", W.LONG),
                ("planes", W.WORD), ("bits", W.WORD), ("compression", W.DWORD),
                ("image_size", W.DWORD), ("xppm", W.LONG), ("yppm", W.LONG),
                ("used", W.DWORD), ("important", W.DWORD), ("colors", W.DWORD * 3)]


def render(text, font, color, stroke_color, stroke, wrap, pixels_per_point):
    from PIL import Image
    g, u = C.WinDLL("gdi32", use_last_error=True), C.WinDLL("user32", use_last_error=True)
    g.CreateCompatibleDC.argtypes = (W.HDC,)
    g.CreateCompatibleDC.restype = W.HDC
    g.CreateFontW.argtypes = (C.c_int,) * 5 + (W.DWORD,) * 8 + (W.LPCWSTR,)
    g.CreateFontW.restype = W.HFONT
    g.SelectObject.argtypes = (W.HDC, W.HGDIOBJ)
    g.SelectObject.restype = W.HGDIOBJ
    g.DeleteObject.argtypes = (W.HGDIOBJ,)
    g.DeleteDC.argtypes = (W.HDC,)
    g.CreateDIBSection.argtypes = (W.HDC, C.POINTER(BitmapInfo), W.UINT,
                                  C.POINTER(C.c_void_p), W.HANDLE, W.DWORD)
    g.CreateDIBSection.restype = W.HBITMAP
    g.SetBkMode.argtypes = (W.HDC, C.c_int)
    g.SetTextColor.argtypes = (W.HDC, W.DWORD)
    u.DrawTextW.argtypes = (W.HDC, W.LPCWSTR, C.c_int, C.POINTER(W.RECT), W.UINT)
    dc = g.CreateCompatibleDC(None)
    face, points, weight, slant = font
    hf = g.CreateFontW(-round(points * pixels_per_point), 0, 0, 0,
        700 if weight == "bold" else 400, int(slant == "italic"),
        0, 0, 1, 0, 0, 4, 0, face)
    if not dc or not hf:
        if hf: g.DeleteObject(hf)
        if dc: g.DeleteDC(dc)
        raise OSError("Unable to create subtitle font")
    old_font, old_bitmap, bitmap = g.SelectObject(dc, hf), None, None
    try:
        flags = 0x1 | 0x10 | 0x800  # CENTER, WORDBREAK, NOPREFIX
        rect = W.RECT(0, 0, max(40, int(wrap)), 0)
        if not u.DrawTextW(dc, text, len(text.encode("utf-16-le")) // 2, C.byref(rect), flags | 0x400):
            raise OSError("Unable to measure subtitle text")
        pad = stroke + 3
        width, height = max(1, rect.right) + 2*pad, max(1, rect.bottom) + 2*pad
        info = BitmapInfo()
        info.size, info.width, info.height, info.planes, info.bits = 40, width, -height, 1, 32
        bits = C.c_void_p()
        bitmap = g.CreateDIBSection(dc, C.byref(info), 0, C.byref(bits), None, 0)
        if not bitmap or not bits.value:
            raise OSError("Unable to create subtitle bitmap")
        old_bitmap = g.SelectObject(dc, bitmap)
        # Non-black color key keeps the antialiased edge colors intact.
        pixel = bytes((3, 2, 1, 255))
        C.memmove(bits, pixel * (width*height), width*height*4)
        g.SetBkMode(dc, 1)
        def draw(fill, dx=0, dy=0):
            r, green, b = (int(fill[i:i+2], 16) for i in (1, 3, 5))
            g.SetTextColor(dc, r | green << 8 | b << 16)
            box = W.RECT(pad+dx, pad+dy, width-pad+dx, height-pad+dy)
            u.DrawTextW(dc, text, len(text.encode("utf-16-le")) // 2, C.byref(box), flags)
        if stroke:
            for dx, dy in ((-stroke, 0), (stroke, 0), (0, -stroke), (0, stroke),
                           (-stroke, -stroke), (-stroke, stroke), (stroke, -stroke), (stroke, stroke)):
                draw(stroke_color, dx, dy)
        draw(color)
        image = Image.frombytes("RGB", (width, height), C.string_at(bits, width*height*4), "raw", "BGRX")
        from PIL import ImageChops
        delta = ImageChops.difference(image, Image.new("RGB", image.size, (1, 2, 3)))
        r, green, b = delta.split()
        mask = ImageChops.lighter(ImageChops.lighter(r, green), b).point(lambda value: 255 if value else 0)
        image = image.convert("RGBA")
        image.putalpha(mask)
        return image
    finally:
        if old_bitmap: g.SelectObject(dc, old_bitmap)
        g.SelectObject(dc, old_font)
        if bitmap: g.DeleteObject(bitmap)
        g.DeleteObject(hf)
        g.DeleteDC(dc)
