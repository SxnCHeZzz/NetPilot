"""Center a popup above Windows' hidden notification icon overflow HWND.

Uses the same HWND classes as the known working Volty integration. The Windows
shell may hide its popup on focus loss, so the last real geometry is cached.
All native rectangles are physical px; Qt layout uses device-independent px.
"""
from __future__ import annotations
import ctypes
from dataclasses import dataclass


SHELL_OVERFLOW_CLASSES = ('NotifyIconOverflowWindow', 'TopLevelWindowForOverflowXamlIsland')


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self): return self.right - self.left
    @property
    def height(self): return self.bottom - self.top


def find_overflow_rect() -> Rect | None:
    if not hasattr(ctypes, 'windll'):
        return None
    try:
        u = ctypes.windll.user32
        class NativeRect(ctypes.Structure):
            _fields_ = [(a, ctypes.c_long) for a in ('left', 'top', 'right', 'bottom')]
        cb_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        result = []
        def visit(hwnd, unused):
            name = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, name, len(name))
            if name.value in SHELL_OVERFLOW_CLASSES:
                rect = NativeRect()
                if u.GetWindowRect(hwnd, ctypes.byref(rect)):
                    bounds = Rect(rect.left, rect.top, rect.right, rect.bottom)
                    if 80 <= bounds.width <= 800 and 40 <= bounds.height <= 800:
                        result.append((bool(u.IsWindowVisible(hwnd)), bounds))
            return True
        callback = cb_type(visit)
        u.EnumWindows(callback, 0)
        if result:
            result.sort(key=lambda x: x[0], reverse=True)
            return result[0][1]
    except (OSError, AttributeError, ValueError):
        pass
    return None


def centered(overflow: Rect, window_width: int, window_height: int, available: Rect, gap: int = 0):
    """Return clamped (x,y) with horizontal centers exactly aligned."""
    x = round((overflow.left + overflow.right - window_width) / 2)
    y = overflow.top - window_height - gap
    if y < available.top:
        y = overflow.bottom + gap
    x = max(available.left, min(x, available.right - window_width))
    y = max(available.top, min(y, available.bottom - window_height))
    return x, y


def physical_to_qt(native: Rect, screen_rect: Rect, ratio: float) -> Rect:
    """Map native physical coordinates to Qt logical coordinates on one screen.

    Screen rectangle is logical. In common Windows layouts, screen logical and
    physical origins differ on multi-monitor mixed-DPI systems. The caller may
    choose tray.geometry() instead when the mapping can't be established.
    """
    ratio = max(0.5, float(ratio))
    return Rect(round(screen_rect.left + (native.left - screen_rect.left * ratio) / ratio),
                round(screen_rect.top + (native.top - screen_rect.top * ratio) / ratio),
                round(screen_rect.left + (native.right - screen_rect.left * ratio) / ratio),
                round(screen_rect.top + (native.bottom - screen_rect.top * ratio) / ratio))
