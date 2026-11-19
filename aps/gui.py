# -*- coding: utf-8 -*-
"""启动器 —— 参照 BetterGI 启动器的卡片式界面（用户 2026-10-25 指定重做）。

布局（自上而下）：

  ┌ 顶栏：图标 / 名称 / 副标题                    右侧运行状态 ┐
  ├ 封面图（左下角写着 AutoPlotSkip，图可换 assets/cover.png） ┤
  ├ 卡片 · AutoPlotSkip 截图器，启动！            [▶ 启动] [■ 停止] ┤
  ├ 卡片 · 日志浮层（跟随游戏窗口）                    [开关] ┤
  ├ 卡片 · 节奏：截图间隔 / 空转间隔 / 触发间隔倍率   三条滑块 ┤
  ├ 卡片 · 运行行为：最小化 / 切前台 / 仅前台点击 / 仅识别  四个开关 ┤
  └ 底栏：源码地址 + 打开 / 还原默认                        ┘

日志不再堆在启动器里：点「启动」后同步日志出现在**游戏画面左下角**的浮层上，
第一句固定是「已就绪，请尽量避免…」。说明类 / 状态类日志不再显示，
点击类日志不再带坐标（都在 aps/logutil.py 里统一处理）。

浮层的三条硬性要求见 overlay.py：不抢焦点、点击穿透、不被自己截图抓到。
"""
from __future__ import annotations

import base64
import os
import threading
import tkinter as tk
import webbrowser

import cv2
import numpy as np

from . import __app_name__, __version__, config, logutil, settings, winutil
from .capture import ScreenGrabber, activate_window, find_game_window
from .engine import Engine
from .overlay import LogOverlay
from .vision import Recognizer

# -- 配色（浅灰毛玻璃：底色像磨砂玻璃，卡片是压在玻璃上的半透白） -------------
BG = "#eef0f4"          # 窗口底色（毛玻璃的雾面浅灰）
BG2 = "#fbfcfd"         # 卡片（玻璃上那层更亮的白）
BG3 = "#e4e7ed"         # 次级按钮 / 图标底
FG = "#20222b"          # 主文字（深炭灰，浅底上才清楚）
DIM = "#6d7280"         # 次要文字
BORDER = "#d9dde5"
ACCENT = "#4f8dfd"      # 主色（沿用上面的蓝）
OK = "#25a45a"
WARN = "#c98a12"
ERR = "#d94848"

SIDE = "#e7eaf0"       # 侧栏（比窗口略深一点点，做出层次）
SIDEBAR_W = 168

FONT_UI = ("Microsoft YaHei UI", 9)
FONT_SUB = ("Microsoft YaHei UI", 9)
FONT_TITLE = ("Microsoft YaHei UI", 11, "bold")
FONT_BTN = ("Microsoft YaHei UI", 10, "bold")
FONT_MONO = ("Consolas", 9)

COVER_ASPECT = 1600 / 560.0        # 默认封面 2.857:1；换图后按实际比例自动适配
COVER_HEIGHT = 152

# 卡片标题与副标题之间要收掉的留白（px）。
# Tk 的 pack 不接受负 pad，所以这个值靠「把副标题的文字上沿垫高、
# 再让它整体上移」来实现，见 Card.title_row 里的 _TightLabel。
TITLE_GAP_TRIM = 4

PAD_X = 14                         # 窗口左右留白


# -- 小工具：圆角多边形 -----------------------------------------------------
def _round_points(x1: float, y1: float, x2: float, y2: float, r: float) -> list[float]:
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def round_rect(cv: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
               r: float, **kw) -> int:
    """在 canvas 上画一个圆角矩形（smooth 折线近似），返回 item id。

    圆角上限取短边的一半（真圆角/胶囊）；以前没夹，传大了会画成鼓包。
    """
    r = max(0.0, min(r, abs(x2 - x1) / 2.0, abs(y2 - y1) / 2.0))
    return cv.create_polygon(_round_points(x1, y1, x2, y2, r), smooth=True,
                             splinesteps=48, **kw)


def mix(color: str, base: str, k: float) -> str:
    """把 color 按 k 混进 base，用来做"浅一点的同色底"。"""
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(base[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] * k + b[i] * (1 - k)) for i in range(3))


# -- 自绘控件 ---------------------------------------------------------------
class IconTile(tk.Canvas):
    """卡片左边那块方形图标：同色底的圆角块 + 一个字符。"""

    def __init__(self, master, glyph: str, *, color: str = ACCENT,
                 size: int = 40, bg: str = BG2, radius: int = 10):
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0)
        round_rect(self, 1, 1, size - 1, size - 1, radius,
                   fill=mix(color, "#ffffff", 0.16), outline="")
        self.create_text(size / 2, size / 2, text=glyph, fill=color,
                         font=("Segoe UI Symbol", 13))


class Toggle(tk.Checkbutton):
    """原生勾选框（用户 m07441：「图二这种样式的还是太丑了，退回最开始的样式」）。

    自绘的细胶囊被否掉了，这里退回 tkinter 原生 Checkbutton：
    方框 + 勾 + 文字，跟系统其它复选框长得一样，用户一眼认得。
    两个细节：
      * `selectcolor` 设成卡片白，否则勾选后框里会出现一块突兀的灰；
      * `activebackground` 跟卡片同色，鼠标划过不闪。
    """

    def __init__(self, master, variable: tk.BooleanVar, command=None,
                 *, text: str = "", bg: str = BG2):
        super().__init__(master, text=text, variable=variable, command=command,
                         bg=bg, activebackground=bg, fg=DIM,
                         activeforeground=FG, font=FONT_UI, selectcolor=BG2,
                         bd=0, highlightthickness=0, takefocus=False,
                         cursor="hand2")

    # 旧代码在变量变化后调 sync() 重画；Checkbutton 自己跟着变量走，留空实现
    def sync(self) -> None:
        pass


class RoundSlider(tk.Canvas):
    """圆角滑轨 + 圆形滑块的滑条（自绘）。

    tk.Scale 的方槽、方滑块在浅色毛玻璃底上显得很硬，这里自己画：
    灰白色胶囊滑轨 + 蓝色已填充段 + 一个白底带阴影的圆钮。
    拖动/点击都会改 `variable`，并回调 `command(值)`。
    """

    H = 30          # 控件总高（垂直居中画滑轨）
    KNOB = 9        # 圆钮半径
    PAD = 10        # 左右留白，避免圆钮被裁

    def __init__(self, master, variable: tk.DoubleVar, lo: float, hi: float, *,
                 bg: str = BG2, resolution: float = 0.01, length: int = 380,
                 command=None):
        super().__init__(master, width=length, height=self.H, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable
        self.lo, self.hi = float(lo), float(hi)
        self.res = float(resolution) or 0.01
        self.command = command
        self._len = length
        self._drag = False
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_move)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    # -- 坐标换算 ------------------------------------------------------
    def _x_of(self, value: float) -> float:
        t = 0.0 if self.hi == self.lo else (value - self.lo) / (self.hi - self.lo)
        t = max(0.0, min(1.0, t))
        span = max(1, self.winfo_width() - self.PAD * 2)
        return self.PAD + span * t

    def _value_at(self, x: float) -> float:
        span = max(1, self.winfo_width() - self.PAD * 2)
        t = max(0.0, min(1.0, (float(x) - self.PAD) / span))
        raw = self.lo + (self.hi - self.lo) * t
        snapped = round(raw / self.res) * self.res
        return max(self.lo, min(self.hi, snapped))

    # -- 交互 ----------------------------------------------------------
    def _set(self, value: float) -> None:
        self.var.set(value)
        self._draw()
        if self.command is not None:
            self.command(value)

    def _on_press(self, event) -> None:
        self._drag = True
        self._set(self._value_at(event.x))

    def _on_move(self, event) -> None:
        if self._drag:
            self._set(self._value_at(event.x))

    def _on_release(self, _event) -> None:
        self._drag = False

    # -- 绘制 ----------------------------------------------------------
    def _draw(self) -> None:
        self.delete("all")
        w = self.winfo_width() or self._len
        cy = self.H / 2
        x0, x1 = self.PAD, w - self.PAD
        kx = self._x_of(float(self.var.get()))

        # 滑轨：浅灰胶囊
        round_rect(self, x0 - 3, cy - 3, x1 + 3, cy + 3, 3,
                   fill="#dfe3ea", outline="")
        # 已填充段：主色胶囊
        if kx > x0:
            round_rect(self, x0 - 3, cy - 3, kx, cy + 3, 3, fill=ACCENT, outline="")
        # 圆钮：一点阴影 + 白面
        r = self.KNOB
        self.create_oval(kx - r, cy - r + 1, kx + r, cy + r + 1,
                         fill="#c9cfda", outline="")
        self.create_oval(kx - r, cy - r, kx + r, cy + r,
                         fill="#ffffff", outline="#cfd5df")


class PillButton(tk.Canvas):
    """圆角按钮（Canvas 自绘，方便做圆角与 hover）。"""

    def __init__(self, master, text: str, command, *, width: int = 108,
                 height: int = 34, fill: str = ACCENT, fg: str = "#ffffff",
                 bg: str = BG2, font=FONT_BTN, tip: str = ""):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._text = text
        self._command = command
        self._fill = fill
        self._fg = fg
        self._font = font
        self._bw, self._bh = width, height
        self._enabled = True
        self._hover = False
        self._tip = tip
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self._draw()

    def _on_click(self, _evt=None) -> None:
        if self._enabled and self._command is not None:
            self._command()

    def _on_enter(self, _evt=None) -> None:
        self._hover = True
        self._draw()

    def _on_leave(self, _evt=None) -> None:
        self._hover = False
        self._draw()

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        self.configure(cursor="hand2" if self._enabled else "arrow")
        self._draw()

    def set_text(self, text: str) -> None:
        self._text = text
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        if not self._enabled:
            fill, fg = "#e6e9ef", "#a2a8b4"
        else:
            fill = mix("#ffffff", self._fill, 0.12) if self._hover else self._fill
            fg = self._fg
        # 圆角直接取高度的一半 —— 端头是正圆，比以前的 10px 方感小得多
        r = self._bh / 2.0 - 0.5
        if self._enabled:
            # 底下垫一层同色浅影，按钮才像浮在玻璃面上
            round_rect(self, 1, 2.5, self._bw - 1, self._bh,
                       r, fill=mix(self._fill, BG2, 0.22), outline="")
        round_rect(self, 1, 0.5, self._bw - 1, self._bh - 1, r,
                   fill=fill, outline="")
        self.create_text(self._bw / 2, self._bh / 2, text=self._text,
                         fill=fg, font=self._font)


class Card(tk.Frame):
    """一张毛玻璃卡片：圆角白面 + 四周几层极淡的投影 + 顶部高光细线。

    tk 的控件没法真的半透明，所以"毛玻璃"是用**投影 + 高光边**做出来的：
    底下几层越来越淡的灰给出悬浮感，顶上一条近白细线模拟玻璃边缘的反光，
    整体压在浅灰雾面底上，看起来就是一块磨砂玻璃。
    """

    RADIUS = 20
    SHADOW = 6          # 投影层数

    def __init__(self, master, *, pad: int = 13):
        super().__init__(master, bg=BG, bd=0, highlightthickness=0)
        # 底色画在 Canvas 上（tk 的 Frame 没有圆角），内容叠在上面
        self._bg = tk.Canvas(self, bg=BG, bd=0, highlightthickness=0)
        self._bg.place(x=0, y=0, relwidth=1, relheight=1)
        self.bind("<Configure>", self._redraw)
        self.body = tk.Frame(self, bg=BG2)
        self.body.pack(fill="x", padx=15, pady=pad)

    def _redraw(self, event=None) -> None:
        w = int(event.width if event is not None else self.winfo_width())
        h = int(event.height if event is not None else self.winfo_height())
        if w < 8 or h < 8:
            return
        cv = self._bg
        cv.delete("all")

        # 1) 投影：由外向内一层层加深，越靠下越明显（光从上方来）
        base = [int(BG[i:i + 2], 16) for i in (1, 3, 5)]
        for i in range(self.SHADOW, 0, -1):
            k = i / float(self.SHADOW)                 # 1 = 最外层最淡
            tone = "#%02x%02x%02x" % tuple(
                int(base[c] * (1 - 0.05 * k)) for c in range(3))
            pad_b = i * 0.9
            round_rect(cv, 2, 2 + pad_b * 0.4, w - 3, h - 3 + pad_b,
                       self.RADIUS + i * 0.6, fill=tone, outline="")

        # 2) 玻璃面：白里带一点蓝，近白但不纯白（纯白在浅灰底上会"发光"）
        round_rect(cv, 1, 1, w - 2, h - 3, self.RADIUS, fill=BG2, outline="")
        # 3) 边缘反光：顶部一条更亮的细线
        cv.create_line(self.RADIUS, 1.5, w - self.RADIUS, 1.5,
                       fill="#ffffff", width=1)

    def title_row(self, glyph: str, color: str, title: str, subtitle: str):
        """左边图标 + 标题/副标题，返回右侧可以放控件的容器。"""
        row = tk.Frame(self.body, bg=BG2)
        row.pack(fill="x")

        IconTile(row, glyph, color=color, size=34, bg=BG2, radius=12).pack(
            side="left", padx=(0, 11))

        text = tk.Frame(row, bg=BG2)
        text.pack(side="left", fill="x", expand=True)
        tk.Label(text, text=title, bg=BG2, fg=FG, font=FONT_TITLE,
                 anchor="w").pack(fill="x")
        if subtitle:
            # 两行文字要挨紧一点，但 Tk 的 pack 不接受负 padding
            # （pady=(-6,0) 会直接 TclError: must be positive screen distance），
            # 所以改用一个「高度减掉 TITLE_GAP_TRIM」的容器 + place 把副标题整体上移。
            # place 的子控件不会被父容器裁剪，副标题照样完整显示，只是贴上去几像素。
            line = tk.Frame(text, bg=BG2)
            line.pack(fill="x")
            sub = tk.Label(line, text=subtitle, bg=BG2, fg=DIM, font=FONT_SUB,
                           anchor="nw", justify="left", wraplength=600)
            sub.place(x=0, y=-TITLE_GAP_TRIM, relwidth=1.0)

            def _fit(_e=None, _sub=sub, _line=line) -> None:
                # 副标题换行导致高度变化时重算容器高度
                h = _sub.winfo_reqheight() - TITLE_GAP_TRIM
                _line.configure(height=max(1, h))

            sub.bind("<Configure>", _fit)
            text.after_idle(_fit)

        right = tk.Frame(row, bg=BG2)
        right.pack(side="right", padx=(12, 0))
        return right


# -- 封面 / 图标 / 窗口外观 ------------------------------------------------
def _asset_path(rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(config.ROOT, rel)


def _apply_window_icon(win: tk.Misc) -> None:
    """给窗口装上 assets/icon.ico（任务栏 + 左上角）。

    Tk 的 iconbitmap 在 Windows 上能直接吃 .ico；PhotoImage 只认 png/gif，
    所以 png 走 iconphoto，ico 走 iconbitmap，两条路都失败就静默跳过。
    """
    ico = _asset_path(config.ICON_PATH)
    png = os.path.splitext(ico)[0] + ".png"
    try:
        if os.path.exists(ico):
            win.iconbitmap(default=ico)
            return
    except tk.TclError:
        pass
    try:
        if os.path.exists(png):
            ph = tk.PhotoImage(data=base64.b64encode(
                np.fromfile(png, dtype=np.uint8).tobytes()))
            win.iconphoto(True, ph)
            setattr(win, "_icon_photo", ph)      # 必须留引用，否则会被 GC 掉
    except (tk.TclError, OSError, ValueError):
        pass


def _round_window_corners(win: tk.Misc) -> None:
    """让窗口本身也走系统圆角（Win11 DWMWA_WINDOW_CORNER_PREFERENCE）。

    只影响系统绘制的窗口外框；Win10 上调用会被忽略，不影响启动。
    """
    try:
        import ctypes
        win.update_idletasks()
        hwnd = int(win.winfo_id())
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        pref = ctypes.c_int(2)          # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 33,
                                  ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:                                    # noqa: BLE001
        pass


def _cover_path() -> str:
    return _asset_path(config.COVER_PATH)


def _load_image(path: str) -> np.ndarray | None:
    """读图（支持中文路径）。读不到就返回 None，界面自动退化成渐变占位。"""
    if not path or not os.path.exists(path):
        return None
    try:
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    except (OSError, ValueError):
        return None
    return img if img is not None and img.size else None


def _cover_fill(img: np.ndarray, w: int, h: int) -> np.ndarray:
    """把封面铺满 (w, h)。

    做法是两层：
      1. 底层 —— 原图按「裁切填满」放大，轻微模糊 + 压暗，用来补满四周；
      2. 上层 —— 原图按「完整显示」缩放居中，保证图里的字/画面一个像素都不裁。
    这样任何比例的图片放进来都不会被裁坏（直接把 16:9 截图丢进去也不会
    把标题切掉），同时窗口拉大拉小都不会露出黑边。
    """
    ih, iw = img.shape[:2]

    def resize_to(nw: int, nh: int) -> np.ndarray:
        return cv2.resize(img, (max(1, nw), max(1, nh)), interpolation=cv2.INTER_AREA)

    # 1) 背景：cover + 模糊 + 压暗
    k = max(w / iw, h / ih)
    bw, bh = max(w, int(round(iw * k))), max(h, int(round(ih * k)))
    back = resize_to(bw, bh)[(bh - h) // 2:(bh - h) // 2 + h,
                             (bw - w) // 2:(bw - w) // 2 + w]
    if back.shape[0] != h or back.shape[1] != w:      # 尺寸差一格时兜底
        back = resize_to(w, h)
    back = cv2.GaussianBlur(back, (0, 0), 12)
    back = (back.astype(np.float32) * 0.45).astype(np.uint8)

    # 2) 前景：contain 居中，带一点阴影缝合感
    k = min(w / iw, h / ih)
    fw, fh = max(1, int(round(iw * k))), max(1, int(round(ih * k)))
    fore = resize_to(fw, fh)
    x, y = (w - fw) // 2, (h - fh) // 2
    back[y:y + fh, x:x + fw] = fore
    return back


def _placeholder(w: int, h: int) -> np.ndarray:
    """没有封面图时的渐变占位块，保证界面不至于一块死黑。"""
    yy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
    top = np.array([18, 20, 32], np.float32)
    bot = np.array([28, 44, 70], np.float32)
    return np.clip(top + (bot - top) * yy, 0, 255).astype(np.uint8).repeat(w, axis=1)


class Cover(tk.Canvas):
    """封面图 + 左下角的 AutoPlotSkip 字样。图片缺失时显示渐变占位。"""

    CAPTION = "AutoPlotSkip"

    def __init__(self, master, **kw):
        super().__init__(master, bg=BG3, highlightthickness=0, bd=0, **kw)
        self._img = _load_image(_cover_path())
        self._photo = None
        self._size = (0, 0)
        self.bind("<Configure>", self._on_resize)

    def reload(self) -> None:
        self._img = _load_image(_cover_path())
        self._size = (0, 0)
        self._render()

    def _on_resize(self, event) -> None:
        self._render(event.width, event.height)

    def _render(self, w: int | None = None, h: int | None = None) -> None:
        w = int(w or self.winfo_width())
        h = int(h or self.winfo_height())
        if w < 40 or h < 40 or (w, h) == self._size:
            return
        self._size = (w, h)

        base = self._img if self._img is not None else _placeholder(w, h)
        view = _cover_fill(base, w, h)
        ok, buf = cv2.imencode(".png", view)
        if not ok:
            return
        self._photo = tk.PhotoImage(data=base64.b64encode(buf.tobytes()))

        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._photo)

        # 图片**左下角**那行字（用户指定）：先描一层暗影再写字，深色/浅色封面上都看得清
        x, y = 16, h - 12
        for dx, dy in ((1, 1), (0, 1), (1, 0)):
            self.create_text(x + dx, y + dy, anchor="sw", text=self.CAPTION,
                             fill="#000000", font=("Microsoft YaHei UI", 11))
        self.create_text(x, y, anchor="sw", text=self.CAPTION,
                         fill="#dfe4f2", font=("Microsoft YaHei UI", 11))


class NavItem(tk.Canvas):
    """侧栏的一行导航：图标 + 文字，选中时浅底 + 左侧亮条（照 BGI 的样子）。"""

    H = 40

    def __init__(self, master, glyph: str, text: str, command, *,
                 width: int = SIDEBAR_W):
        super().__init__(master, width=width - 16, height=self.H, bg=SIDE,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._glyph = glyph
        self._text = text
        self._command = command
        self._active = False
        self._hover = False
        self.bind("<Button-1>", lambda _e: self._command())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self._draw()

    def _on_enter(self, _evt=None) -> None:
        self._hover = True
        self._draw()

    def _on_leave(self, _evt=None) -> None:
        self._hover = False
        self._draw()

    def set_active(self, on: bool) -> None:
        self._active = bool(on)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_reqwidth(), self.H
        if self._active:
            round_rect(self, 0, 2, w, h - 2, 12, fill="#dfe4ee", outline="")
            round_rect(self, 6, h / 2 - 9, 10, h / 2 + 9, 2,
                       fill=FG, outline="")
            fg, weight = "#1b2a4a", "bold"
        elif self._hover:
            round_rect(self, 0, 2, w, h - 2, 12, fill="#e9edf4", outline="")
            fg, weight = FG, "normal"
        else:
            fg, weight = DIM, "normal"
        self.create_text(16, h / 2, text=self._glyph, fill=fg,
                         font=("Segoe UI Symbol", 11))
        self.create_text(38, h / 2, text=self._text, fill=fg, anchor="w",
                         font=("Microsoft YaHei UI", 10, weight))


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        # 标题就叫程序名（用户 2026-10-25 指定，不要「截图器，启动！」那串）
        self.title(__app_name__)
        self.configure(bg=BG)
        self.geometry("880x580")
        self.minsize(860, 560)
        _apply_window_icon(self)
        _round_window_corners(self)

        self.engine: Engine | None = None
        self.thread: threading.Thread | None = None
        self.grabber = ScreenGrabber()
        self.recognizer = Recognizer()

        # 选项全部默认开启（用户 2026-10-25 指定）
        self.follow_game = tk.BooleanVar(value=True)
        self.foreground_only = tk.BooleanVar(value=config.REQUIRE_FOREGROUND)
        self.activate_on_start = tk.BooleanVar(value=config.ACTIVATE_ON_START)
        self.minimize_on_start = tk.BooleanVar(value=config.MINIMIZE_ON_START)
        self.dry_run = tk.BooleanVar(value=False)

        settings.load()

        self.overlay = LogOverlay(self)
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._pump_job = self.after(100, self._pump)
        self.after(60, self._self_check)

    # -- 界面 ---------------------------------------------------------
    def _build(self) -> None:
        # pack 按调用顺序分配空间：先占住底部状态条，再分左右两栏，
        # 否则窗口一矮，最先被挤掉的恰恰是底栏。
        self._build_statusbar()

        body = tk.Frame(self, bg=BG)
        body.pack(side="top", fill="both", expand=True)

        self._build_sidebar(body)

        self.stage = tk.Frame(body, bg=BG)
        self.stage.pack(side="left", fill="both", expand=True)

        self.holder = tk.Frame(self.stage, bg=BG)
        self.holder.pack(side="top", fill="both", expand=True)

        self.pages: dict[str, tk.Frame] = {}
        for name in ("start", "tempo", "about"):
            self.pages[name] = tk.Frame(self.holder, bg=BG)
        self._build_page_start(self.pages["start"])
        self._build_page_tempo(self.pages["tempo"])
        self._build_page_about(self.pages["about"])
        self._show_page("start")

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self, bg=BG3)
        bar.pack(side="bottom", fill="x")
        tk.Frame(bar, bg=BORDER, height=1).pack(side="top", fill="x")

        inner = tk.Frame(bar, bg=BG3)
        inner.pack(fill="x", padx=14, pady=5)

        self.status = tk.Label(inner, text="● 未启动", bg=BG3, fg=DIM, font=FONT_UI)
        self.status.pack(side="left")

        self.hint = tk.Label(inner, text="", bg=BG3, fg=DIM, anchor="w", font=FONT_UI)
        self.hint.pack(side="left", fill="x", expand=True, padx=(14, 0))

    def _build_sidebar(self, host: tk.Frame) -> None:
        side = tk.Frame(host, bg=SIDE, width=SIDEBAR_W)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(host, bg=BORDER, width=1).pack(side="left", fill="y")

        # 顶部：小图标 + 名称，和 BGI 侧栏顶上那块标题对齐
        head = tk.Frame(side, bg=SIDE)
        head.pack(side="top", fill="x", padx=12, pady=(14, 16))
        IconTile(head, "✦", color=ACCENT, size=26, bg=SIDE, radius=7).pack(side="left")
        tk.Label(head, text=__app_name__, bg=SIDE, fg="#1d2330",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left", padx=(8, 0))

        self.nav_items: dict[str, NavItem] = {}
        nav = tk.Frame(side, bg=SIDE)
        nav.pack(side="top", fill="x", padx=(8, 8))
        for name, glyph, label in (("start", "▶", "启动"),
                                   ("tempo", "◐", "节奏"),
                                   ("about", "ⓘ", "关于")):
            item = NavItem(nav, glyph, label,
                           lambda n=name: self._show_page(n))
            item.pack(fill="x", pady=1)
            self.nav_items[name] = item

        # 底部：版本号
        tk.Label(side, text="v%s" % __version__, bg=SIDE, fg="#98a0ae",
                 font=FONT_MONO).pack(side="bottom", pady=(0, 12))

    def _show_page(self, name: str) -> None:
        for key, page in self.pages.items():
            if key != name:
                page.pack_forget()
        self.pages[name].pack(side="top", fill="both", expand=True)
        for key, item in self.nav_items.items():
            item.set_active(key == name)

    # -- 页 1：启动 ----------------------------------------------------
    def _build_page_start(self, page: tk.Frame) -> None:
        wrap = tk.Frame(page, bg=BG)
        wrap.pack(fill="x", padx=PAD_X, pady=(12, 0))
        self.cover = Cover(wrap, height=COVER_HEIGHT)
        self.cover.pack(fill="x")
        self.cover.configure(height=COVER_HEIGHT)

        host = tk.Frame(page, bg=BG)
        host.pack(fill="x", padx=PAD_X, pady=(12, 0))

        card = Card(host)
        card.pack(fill="x", pady=(0, 10))
        right = card.title_row(
            "▶", OK,
            __app_name__,
            "启动后自动识别剧情并跳过；同步日志会出现在游戏画面左下角。")
        self.btn_stop = PillButton(right, "■  停止", self.stop, width=96, height=34,
                                   fill="#d7dbe3", fg="#4a505c", bg=BG2)
        self.btn_stop.pack(side="right")
        self.btn_start = PillButton(right, "▶  启动", self.start, width=112, height=34,
                                    fill=OK, bg=BG2)
        self.btn_start.pack(side="right", padx=(0, 8))
        self.btn_stop.set_enabled(False)

        card = Card(host)
        card.pack(fill="x", pady=(0, 10))
        right = card.title_row(
            "▤", ACCENT,
            "日志浮层",
            "把同步日志贴在游戏左下角：不抢焦点、可点击穿透、不会被自己截图抓到。")
        self._toggle_with_label(right, "跟随游戏窗口", self.follow_game,
                                self._apply_follow)

        card = Card(host)
        card.pack(fill="x")
        card.title_row(
            "◆", "#b07cff",
            "运行行为",
            "「仅识别不点击」打开时不真的点，只把识别结果写进日志。")
        # 四个开关分两行两列：一行摆四个时会顶出卡片右边界（窄窗口更明显）
        grid = tk.Frame(card.body, bg=BG2)
        grid.pack(fill="x", pady=(10, 0))
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        for i, (label, var, cmd) in enumerate((
            ("启动后自动最小化", self.minimize_on_start, self._apply_minimize_on_start),
            ("启动时切游戏到前台", self.activate_on_start, self._apply_activate_on_start),
            ("仅前台时点击", self.foreground_only, self._apply_foreground_only),
            ("仅识别不点击", self.dry_run, self._apply_dry_run),
        )):
            cell = tk.Frame(grid, bg=BG2)
            cell.grid(row=i // 2, column=i % 2, sticky="w", pady=(0, 8 if i < 2 else 0))
            self._toggle_with_label(cell, label, var, cmd, pack_left=True)

    # -- 页 2：节奏 ----------------------------------------------------
    def _build_page_tempo(self, page: tk.Frame) -> None:
        host = tk.Frame(page, bg=BG)
        host.pack(fill="x", padx=PAD_X, pady=(14, 0))

        card = Card(host)
        card.pack(fill="x")
        right = card.title_row(
            "◐", WARN,
            "节奏",
            "拖动即时生效，下次启动也记得。默认 0.50s / 0.60s / x1.50。")
        PillButton(right, "还原默认", self._reset_tuning, width=88, height=30,
                   fill=BG3, bg=BG2, font=FONT_UI).pack(side="right")

        self.tuning_vars: dict[str, tk.DoubleVar] = {}
        self.tuning_labels: dict[str, tk.Label] = {}

        grid = tk.Frame(card.body, bg=BG2)
        grid.pack(fill="x", pady=(12, 0))

        for i, (name, (_label, lo, hi, text)) in enumerate(settings.EDITABLE.items()):
            row = tk.Frame(grid, bg=BG2)
            row.pack(fill="x", pady=(0, 8 if i < len(settings.EDITABLE) - 1 else 0))

            tk.Label(row, text=_label, bg=BG2, fg=DIM, width=11, anchor="w",
                     font=FONT_UI).pack(side="left")

            var = tk.DoubleVar(value=settings.get(name))
            self.tuning_vars[name] = var
            scale = RoundSlider(row, var, lo, hi, bg=BG2,
                                resolution=0.01 if hi <= 5 else 0.1,
                                command=lambda v, n=name: self._on_tuning(n, v))
            scale.pack(side="left", padx=(0, 12))
            self._tip(scale, text)

            lab = tk.Label(row, text=self._fmt(name, var.get()), bg=BG2, fg=FG,
                           width=6, anchor="w", font=FONT_MONO)
            lab.pack(side="left")
            self.tuning_labels[name] = lab

    # -- 页 3：关于 ----------------------------------------------------
    def _build_page_about(self, page: tk.Frame) -> None:
        host = tk.Frame(page, bg=BG)
        host.pack(fill="x", padx=PAD_X, pady=(14, 0))

        card = Card(host)
        card.pack(fill="x", pady=(0, 10))
        right = card.title_row(
            "ⓘ", ACCENT,
            "关于",
            "%s v%s · 崩坏：星穹铁道 自动剧情跳过 · 实现思路参照 BetterGI"
            % (__app_name__, __version__))
        self.btn_open = PillButton(right, "打开", self._open_source, width=84,
                                   height=32, fill=BG3, bg=BG2, font=FONT_UI)
        self.btn_open.pack(side="right")
        if not config.SOURCE_URL:
            self.btn_open.set_enabled(False)

        self.src_label = tk.Label(card.body, text=config.SOURCE_URL or "（未填写）",
                                  bg=BG2, fg=ACCENT if config.SOURCE_URL else DIM,
                                  font=FONT_MONO, anchor="w")
        self.src_label.pack(fill="x", padx=2, pady=(10, 0))
        self._fit_source()


    def _toggle_with_label(self, parent, label: str, var: tk.BooleanVar,
                           command, pack_left: bool = False) -> Toggle:
        """一行「勾选框 + 文字」。pack_left=True 时是格子内左对齐（行为卡用）。

        勾选框用原生 Checkbutton，文字直接交给它自己画：
        这样勾和字的间距由系统决定，不用手工调 padx；宽窗口下副标题也不会
        跟勾选框挤在一起。
        """
        toggle = Toggle(parent, var, command, text=label, bg=BG2)
        (toggle.pack(side="left") if pack_left else toggle.pack(side="right"))
        var.trace_add("write", lambda *_a: toggle.sync())
        return toggle

    def _fit_source(self) -> None:
        url = config.SOURCE_URL
        shown = url if len(url) <= 78 else url[:75] + "…"
        self.src_label.configure(text=shown or "（未填写）",
                                 fg=ACCENT if url else DIM)
        self.src_label.bind("<Enter>", lambda _e: self.hint.configure(text=url or
                                                                     "在 aps/config.py 里填 SOURCE_URL"))

    def _open_source(self) -> None:
        if not config.SOURCE_URL:
            self.hint.configure(text="还没填源码地址：改 aps/config.py 里的 SOURCE_URL")
            return
        try:
            webbrowser.open(config.SOURCE_URL)
            self.hint.configure(text="已在浏览器中打开源码地址")
        except Exception as exc:                       # noqa: BLE001
            self.hint.configure(text="打开失败：%s" % exc)

    # -- 节奏参数 ------------------------------------------------------
    @staticmethod
    def _fmt(name: str, value: float) -> str:
        return "x%.2f" % value if name == "TRIGGER_SCALE" else "%.2fs" % value

    def _tip(self, widget, text: str) -> None:
        widget.bind("<Enter>", lambda _e, t=text: self.hint.configure(text=t))
        widget.bind("<Leave>", lambda _e: self.hint.configure(text=""))

    def _on_tuning(self, name: str, value) -> None:
        try:
            v = settings.set_value(name, float(value))
        except (KeyError, TypeError, ValueError):
            return
        lab = self.tuning_labels.get(name)
        if lab is not None:
            lab.configure(text=self._fmt(name, v))

    def _reset_tuning(self) -> None:
        values = settings.reset_defaults()
        for name, var in self.tuning_vars.items():
            var.set(values[name])
            lab = self.tuning_labels.get(name)
            if lab is not None:
                lab.configure(text=self._fmt(name, values[name]))
        self._log("节奏", "已还原默认：%s" % settings.describe(), "ok")

    # -- 选项 ---------------------------------------------------------
    def _apply_follow(self) -> None:
        if not self.follow_game.get():
            self.overlay.detach()
            self._log("设置", "浮层已隐藏", "info")
            return
        window = self.grabber.ensure_window()
        if window is not None:
            self.overlay.attach(window)
            self._log("设置", "浮层跟随游戏窗口", "info")

    def _apply_foreground_only(self) -> None:
        want = bool(self.foreground_only.get())
        config.REQUIRE_FOREGROUND = want
        eng = self.engine
        if eng is not None:
            eng.clicker.require_foreground = want
        self._log("设置", "点击条件：%s" % ("仅在游戏位于前台时点击"
                                           if want else "游戏在后台也用消息点击"), "info")

    def _apply_minimize_on_start(self) -> None:
        config.MINIMIZE_ON_START = bool(self.minimize_on_start.get())
        self._log("设置", "启动后%s自动最小化" % ("会" if config.MINIMIZE_ON_START else "不会"),
                  "info")

    def _apply_activate_on_start(self) -> None:
        config.ACTIVATE_ON_START = bool(self.activate_on_start.get())
        self._log("设置", "启动时%s把游戏切到前台" % ("会" if config.ACTIVATE_ON_START else "不会"),
                  "info")

    def _apply_dry_run(self) -> None:
        eng = self.engine
        if eng is not None:
            eng.clicker.dry_run = bool(self.dry_run.get())
        self._log("设置", "仅识别不点击：%s" % ("已开启，只写日志不点击"
                                               if self.dry_run.get() else "已关闭"), "info")

    # -- 日志（唯一的日志出口：过滤 + 去坐标 → 浮层）---------------------
    def _log(self, tag: str, text: str, level: str = "info") -> None:
        tag, text = logutil.clean(tag, text)
        if not logutil.visible(tag, text, level):
            return
        self.overlay.push(tag, text, level)
        if level in ("error", "warn") and self.engine is None:
            # 没在跑时的报错（找不到窗口、权限不足）也在界面上给一行提示
            self.hint.configure(text=text)

    def _engine_log(self, tag: str, text: str, level: str = "info") -> None:
        self._log(tag, text, level)

    # -- 自检 ---------------------------------------------------------
    def _self_check(self) -> None:
        level = winutil.our_integrity()
        if level is None or level < winutil.IL_HIGH:
            self._log("权限", "本程序未以管理员运行（%s）。若游戏是管理员启动的，"
                              "点击会被系统丢弃。" % winutil.il_name(level), "warn")
        window = find_game_window()
        if window is not None:
            self.grabber.window = window
            self._report_window(window)
            if self.follow_game.get():
                self.overlay.attach(window)
        else:
            self.hint.configure(text="暂未找到「崩坏：星穹铁道」窗口：启动游戏后浮层会自动出现。")

    def _report_window(self, window) -> None:
        ok, why = winutil.can_send_to(window.hwnd)
        self._log("窗口", "已锁定：%s %dx%d" % (window.title, window.width, window.height),
                  "ok")
        self._log("权限" if ok else "权限不足", why, "ok" if ok else "error")

    # -- 控制 ---------------------------------------------------------
    def start(self) -> None:
        if self.engine is not None and self.engine.running:
            return
        window = self.grabber.ensure_window(force=True)
        if window is None:
            self._set_status("● 未找到游戏窗口", ERR)
            self._log("错误", "没找到「崩坏：星穹铁道」窗口，请先把游戏打开。", "error")
            return

        self._report_window(window)
        # 只在启动这一次切前台（用户勾选时）；循环里绝不反复切 —— 那正是"游戏卡住"的成因
        if self.activate_on_start.get() and not window.foreground:
            ok = activate_window(window.hwnd)
            window = self.grabber.ensure_window(force=True) or window
            if not ok:
                self._log("窗口", "切换到前台失败，请手动点一下游戏窗口", "warn")

        allowed, why = winutil.can_send_to(window.hwnd)
        if not allowed:
            self._log("权限不足", why, "error")
        if config.REQUIRE_FOREGROUND and not window.foreground:
            self._log("窗口", "游戏当前不在前台：点击会暂停，切回游戏即自动继续", "info")

        if self.follow_game.get():
            self.overlay.attach(window)
            if not self.overlay.capture_excluded:
                self._log("浮层", "无法把浮层排除在截图之外，识别可能受到干扰。", "warn")

        engine = Engine(self._engine_log, recognizer=self.recognizer,
                        grabber=self.grabber, dry_run=bool(self.dry_run.get()))
        engine.reset()
        self.engine = engine
        self.thread = threading.Thread(target=engine.run, name="aps-engine", daemon=True)
        self.thread.start()

        self.btn_start.set_enabled(False)
        self.btn_stop.set_enabled(True)
        self._set_status("● 运行中", OK)
        if self.minimize_on_start.get():
            self.iconify()

    def stop(self) -> None:
        if self.engine is None:
            return
        self.engine.stop()
        self.btn_start.set_enabled(True)
        self.btn_stop.set_enabled(False)
        self._set_status("● 已停止", DIM)
        self._log("系统", "已请求停止。", "warn")
        if self.state() == "iconic":
            self.deiconify()

    def _set_status(self, text: str, color: str = DIM) -> None:
        self.status.configure(text=text, fg=color)

    # -- 定时刷新 -----------------------------------------------------
    def _pump(self) -> None:
        try:
            self.overlay.pump()
            eng = self.engine
            if eng is not None and (not eng.running and self.thread is not None
                                    and not self.thread.is_alive()):
                self.btn_start.set_enabled(True)
                self.btn_stop.set_enabled(False)
                self._set_status("● 已停止", DIM)
                self.engine = None
            elif eng is None and self.follow_game.get():
                # 没在跑时也让浮层跟着游戏（游戏后开也能自己贴上来）
                window = self.grabber.ensure_window()
                if window is not None and (not self.overlay.visible
                                           or self.overlay._window is not window):
                    self.overlay.attach(window)
        except Exception as exc:                       # noqa: BLE001
            self.hint.configure(text="界面异常：%s: %s" % (type(exc).__name__, exc))
        self._pump_job = self.after(100, self._pump)

    def _on_close(self) -> None:
        # 先撤掉定时器，否则 destroy 之后回调还会被触发（Tcl 报
        # `invalid command name ..._pump`，窗口已没了却还在跑）
        job = getattr(self, "_pump_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
            self._pump_job = None
        try:
            if self.engine is not None:
                self.engine.stop()
            self.overlay.detach()
        finally:
            self.destroy()


def run() -> None:
    App().mainloop()
