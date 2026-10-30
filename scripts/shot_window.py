# -*- coding: utf-8 -*-
"""截取指定窗口标题的界面，存成 PNG。

用纯 ctypes + PIL，不依赖 PowerShell 的 Add-Type（那个在本机被安全策略禁用）。

用法：
    python scripts/shot_window.py "微信多开管家" docs/主界面.png
"""
import ctypes
import os
import sys
from ctypes import wintypes

from PIL import ImageGrab

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

SW_RESTORE = 9
SW_SHOW = 5


def find_window(title_part: str):
    """按标题片段找第一个可见顶层窗口。"""
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        if title_part in buf.value:
            found.append((hwnd, buf.value))
            return False
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else (None, None)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/shot_window.py <标题片段> [输出png]")
        return 2
    title = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "shot.png"

    hwnd, real_title = find_window(title)
    if not hwnd:
        print("找不到标题含 %r 的窗口" % title)
        return 1
    print("窗口 = %r (hwnd=%s)" % (real_title, hwnd))

    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    import time
    time.sleep(1.2)

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    box = (rect.left, rect.top, rect.right, rect.bottom)
    print("区域 = %s" % (box,))

    img = ImageGrab.grab(bbox=box, all_screens=True)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    img.save(out)
    print("已保存 %s  (%d x %d, %.1f KB)"
          % (out, img.width, img.height, os.path.getsize(out) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
