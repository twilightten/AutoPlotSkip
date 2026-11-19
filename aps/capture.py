# -*- coding: utf-8 -*-
"""窗口定位 + 屏幕截图（纯 ctypes，GDI BitBlt，不依赖第三方库）。

BetterGI 用 GameCapture（Windows.Graphics.Capture）抓画面，这里用最朴素的
GDI 方案：PrintWindow 在游戏（Unity/DX）上经常是黑屏，而 BitBlt 从屏幕 DC
拷贝对窗口化游戏稳定可靠。所以：

  1. 用 EnumWindows 找到游戏窗口；
  2. 拿到客户区的屏幕矩形（ClientToScreen）；
  3. 优先 BitBlt 屏幕 DC；
  4. 窗口被别的窗口压住时，屏幕 DC 上取到的是**遮挡者**的画面（实测直接把
     浏览器窗口当成剧情画面），所以再退到窗口 DC / PrintWindow。

抓图用 CreateDIBSection 直接映射内存，避免 GetDIBits 的二次拷贝。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from dataclasses import dataclass

import numpy as np

from . import config

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.AttachThreadInput.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.BringWindowToTop.restype = wt.BOOL
user32.SetFocus.argtypes = [wt.HWND]
user32.SetFocus.restype = wt.HWND
user32.GetForegroundWindow.restype = wt.HWND
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL
user32.IsIconic.argtypes = [wt.HWND]
user32.IsIconic.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL
kernel32.GetCurrentThreadId.restype = wt.DWORD

SRCCOPY = 0x00CC0020
PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0
BI_RGB = 0
SW_RESTORE = 9

if ctypes.sizeof(ctypes.c_void_p) == 8:
    LONG_PTR = ctypes.c_longlong
else:
    LONG_PTR = ctypes.c_long

user32.GetWindowLongPtrW.restype = LONG_PTR
user32.GetWindowLongPtrW.argtypes = (wt.HWND, ctypes.c_int)
# 下面这批 argtypes/restype 必须显式声明：64 位下句柄是 64 位指针，
# 不声明的话 ctypes 默认按 int 处理，GetDC/CreateDIBSection 返回值会被截断，
# 再传回去就抛 "OverflowError: int too long to convert"。
for _fn, _res, _arg in (("GetDC", wt.HDC, (wt.HWND,)),
                        ("ReleaseDC", ctypes.c_int, (wt.HWND, wt.HDC)),
                        ("CreateCompatibleDC", wt.HDC, (wt.HDC,)),
                        ("SelectObject", ctypes.c_void_p, (wt.HDC, ctypes.c_void_p)),
                        ("DeleteDC", wt.BOOL, (wt.HDC,)),
                        ("DeleteObject", wt.BOOL, (ctypes.c_void_p,)),
                        ("BitBlt", wt.BOOL, (wt.HDC, ctypes.c_int, ctypes.c_int,
                                             ctypes.c_int, ctypes.c_int,
                                             wt.HDC, ctypes.c_int, ctypes.c_int,
                                             wt.DWORD)),
                        ("PrintWindow", wt.BOOL, (wt.HWND, wt.HDC, wt.UINT))):
    if hasattr(user32, _fn):
        _f = getattr(user32, _fn)
    else:
        _f = getattr(gdi32, _fn)
    _f.restype = _res
    _f.argtypes = _arg
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = (
    wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p),
    wt.HANDLE, wt.DWORD)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", wt.DWORD * 3)]


@dataclass
class GameWindow:
    hwnd: int
    title: str
    left: int          # 客户区左上角（屏幕坐标）
    top: int
    width: int
    height: int

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.left + self.width, self.top + self.height)

    @property
    def foreground(self) -> bool:
        """游戏窗口当前是不是前台窗口。"""
        return user32.GetForegroundWindow() == self.hwnd

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        """把画面内像素坐标换算成屏幕绝对坐标。"""
        return (int(round(self.left + x)), int(round(self.top + y)))


def _rects(hwnd: int):
    """返回 (客户区屏幕原点x, y, 宽, 高)。"""
    cr = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(cr))
    origin = wt.POINT(cr.left, cr.top)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return origin.x, origin.y, cr.right - cr.left, cr.bottom - cr.top


def _title_of(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _match_game_title(hwnd: int) -> str | None:
    """判断某个顶层窗口是不是游戏窗口。

    两个判据，都不允许"标题里带 Star Rail 就算"——
    实测桌面上开着「Star Rail截图器 — DeepSeek Harness」这种网页也会带上
    Star Rail 字样，用 `in` 匹配会直接把浏览器当成游戏窗口抓，然后疯狂点它。
    """
    if not user32.IsWindowVisible(hwnd):
        return None
    title = _title_of(hwnd)
    if not title:
        return None
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    name = cls.value
    if name in config.WINDOW_CLASSES:            # Unity 窗口：标题含游戏名即可
        return title if any(t in title for t in config.WINDOW_TITLES) else None
    # 非 Unity 类只能靠标题**完全等于**游戏名（启动器/无边框窗口的兜底）
    return title if title.strip() in config.WINDOW_TITLES else None


ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _enum(cb) -> None:
    user32.EnumWindows(ENUMPROC(cb), 0)


def find_game_window(*, allow_any: bool = False) -> GameWindow | None:
    """找游戏窗口。allow_any=True 时退化为"最大的可见窗口"，方便调试。"""
    found: list[tuple[int, str]] = []

    def _cb(hwnd, _lp):
        t = _match_game_title(hwnd)
        if t:
            found.append((hwnd, t))
        return True

    _enum(_cb)
    cands = found
    if not cands and allow_any:
        def _cb2(hwnd, _lp):
            if not user32.IsWindowVisible(hwnd):
                return True
            t = _title_of(hwnd)
            if not t.strip():
                return True
            _, _, w, h = _rects(hwnd)
            if w >= 960 and h >= 540:
                cands.append((hwnd, t))
            return True

        _enum(_cb2)

    best = None
    best_area = 0
    for hwnd, title in cands:
        l, t, w, h = _rects(hwnd)
        if w < 320 or h < 240:
            continue
        if w * h > best_area:
            best_area = w * h
            best = GameWindow(hwnd, title, l, t, w, h)
    return best


def _make_dib(hdc_ref: int, width: int, height: int):
    """创建 32bit 顶向下的 DIB section，返回 (hdc_mem, hbmp, bits_ptr, np_view)。"""
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = width
    bi.bmiHeader.biHeight = -height        # 负高度 = 自上而下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = BI_RGB

    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(hdc_ref, ctypes.byref(bi), DIB_RGB_COLORS,
                                  ctypes.byref(bits), None, 0)
    if not hbmp or not bits:
        return None
    hdc_mem = gdi32.CreateCompatibleDC(hdc_ref)
    gdi32.SelectObject(hdc_mem, hbmp)
    view = np.frombuffer((ctypes.c_ubyte * (width * height * 4)).from_address(bits.value),
                         dtype=np.uint8).reshape(height, width, 4)
    return hdc_mem, hbmp, bits, view


def _free_dib(hdc_mem, hbmp) -> None:
    if hdc_mem:
        gdi32.DeleteDC(hdc_mem)
    if hbmp:
        gdi32.DeleteObject(hbmp)


def _capture_screen(w: GameWindow) -> np.ndarray | None:
    """从前台屏幕抓 —— 只要窗口没被遮挡，这就是最真实的画面。"""
    hdc_screen = user32.GetDC(0)
    if not hdc_screen:
        return None
    made = _make_dib(hdc_screen, w.width, w.height)
    try:
        if made is None:
            return None
        hdc_mem, hbmp, _bits, view = made
        ok = gdi32.BitBlt(hdc_mem, 0, 0, w.width, w.height,
                          hdc_screen, w.left, w.top, SRCCOPY)
        if not ok:
            return None
        return view[:, :, :3].copy()                  # 内存里已是 BGR，和 cv2.imread 一致
    finally:
        if made:
            _free_dib(made[0], made[1])
        user32.ReleaseDC(0, hdc_screen)


def _capture_client_dc(w: GameWindow) -> np.ndarray | None:
    """窗口被挡住时的兜底：从窗口**客户区** DC 上 BitBlt。

    这里必须用 `GetDC(hwnd)` 而不是 `GetWindowDC(hwnd)`：
    GetWindowDC 的原点在**窗口左上角**（含边框标题栏），而程序全程用客户区
    坐标（`use_client` 的宽高、点击坐标也是客户区）。用 GetWindowDC 抓出来
    的图会整体偏移一个边框的宽度/高度，实测 Tk 窗口上偏 (8, 31) 像素，
    识别直接崩掉：顶部图标被推出 ROI，底部 ▼ 只剩一点点还能匹配上。

    以前的回归没发现这个问题，是因为那时窗口一直在前台、走的是屏幕 DC 分支。
    """
    hwnd_dc = user32.GetDC(w.hwnd)
    if not hwnd_dc:
        return None
    made = _make_dib(hwnd_dc, w.width, w.height)
    try:
        if made is None:
            return None
        hdc_mem, hbmp, _bits, view = made
        ok = gdi32.BitBlt(hdc_mem, 0, 0, w.width, w.height, hwnd_dc, 0, 0, SRCCOPY)
        if not ok:
            return None
        return view[:, :, :3].copy()
    finally:
        if made:
            _free_dib(made[0], made[1])
        user32.ReleaseDC(w.hwnd, hwnd_dc)


def _capture_window_dc(w: GameWindow) -> np.ndarray | None:
    """兼容旧名字（老代码/工具里有引用），等价于客户区 DC。"""
    return _capture_client_dc(w)


def _capture_printwindow(w: GameWindow) -> np.ndarray | None:
    hwnd_dc = user32.GetDC(w.hwnd)
    if not hwnd_dc:
        return None
    made = _make_dib(hwnd_dc, w.width, w.height)
    try:
        if made is None:
            return None
        hdc_mem, hbmp, _bits, view = made
        if not user32.PrintWindow(w.hwnd, hdc_mem, PW_RENDERFULLCONTENT):
            return None
        return view[:, :, :3].copy()
    finally:
        if made:
            _free_dib(made[0], made[1])
        user32.ReleaseDC(w.hwnd, hwnd_dc)


def _is_foreground(hwnd: int) -> bool:
    """窗口是不是当前前台窗口。"""
    return bool(hwnd) and user32.GetForegroundWindow() == hwnd


def grab(w: GameWindow) -> np.ndarray | None:
    """抓一帧 BGR 图像（和 cv2.imread 的通道顺序一致）；黑屏/失败返回 None。

    **遮挡问题**：`BitBlt` 从屏幕 DC 上取像素，窗口被别的窗口压住时，
    抓到的是压在上面那个窗口的内容 —— 识别当然全错。所以按窗口是否在前台
    换顺序试三种来源：屏幕 DC → 窗口 DC → PrintWindow。
    """
    if _is_foreground(w.hwnd):
        order = (_capture_screen, _capture_client_dc, _capture_printwindow)
    else:
        # 被遮挡时屏幕 DC 上是遮挡者的画面，改从窗口自己的客户区 DC 取。
        # 实测（2026-10-24）客户区 DC 即使窗口被压住、失去前台也能拿到正确画面。
        order = (_capture_client_dc, _capture_printwindow, _capture_screen)
    for fn in order:
        try:
            img = fn(w)
        except OSError:
            img = None
        if img is not None and float(img.mean()) > config.MIN_FRAME_MEAN:
            return img
    return None


class ScreenGrabber:
    """带缓存的截图器：窗口丢失/移动后自动刷新矩形。"""

    def __init__(self, *, allow_any: bool = False):
        self.allow_any = allow_any
        self.window: GameWindow | None = None
        self._last_find = 0.0
        self.last_error = ""

    def ensure_window(self, *, force: bool = False) -> GameWindow | None:
        now = time.time()
        if not force and self.window is not None:
            if user32.IsWindow(self.window.hwnd) and user32.IsWindowVisible(self.window.hwnd):
                l, t, w, h = _rects(self.window.hwnd)
                if w > 200 and h > 200:
                    self.window.left, self.window.top = l, t
                    self.window.width, self.window.height = w, h
                    return self.window
            self.window = None
        if not force and now - self._last_find < 2.0:
            return self.window
        self._last_find = now
        self.window = find_game_window(allow_any=self.allow_any)
        if self.window is None:
            self.last_error = "未找到游戏窗口"
        return self.window

    def grab(self) -> np.ndarray | None:
        w = self.ensure_window()
        if w is None:
            return None
        img = grab(w)
        if img is None:
            self.last_error = "截图失败（窗口可能被遮挡或最小化）"
        return img


def activate_window(hwnd: int) -> bool:
    """把游戏窗口切到前台 —— **只在用户明确要求时调用一次**，绝不在循环里调。

    为什么单独做一层：`SetForegroundWindow` 有前台锁，随便调会失败（返回 0），
    所以这里用 `AttachThreadInput` 把自己的输入队列挂到当前前台线程上再切，
    切完立刻解挂。这样成功率接近 100%，而且只影响这一次。
    """
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True

    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    mine = kernel32.GetCurrentThreadId()
    attached = []
    for tid in (fg_tid, target_tid):
        if tid and tid != mine:
            if user32.AttachThreadInput(mine, tid, True):
                attached.append(tid)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        for tid in attached:
            user32.AttachThreadInput(mine, tid, False)
    time.sleep(0.05)
    return user32.GetForegroundWindow() == hwnd
