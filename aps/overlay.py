# -*- coding: utf-8 -*-
"""游戏画面左下角的浮层日志窗 —— 复刻 BGI 的 MaskWindow 那种"贴在游戏上"的体验。

三个必须同时成立的性质（缺一个就不能用）：

1. **不抢焦点**：`WS_EX_NOACTIVATE` + `SW_SHOWNOACTIVATE`。否则日志一刷新游戏就
   失焦，全屏游戏最小化，体验直接毁掉。
2. **点击穿透**：`WS_EX_LAYERED | WS_EX_TRANSPARENT`。浮层盖在游戏左下角，
   用户和引擎的点击必须落到下面的游戏上，不能被它挡住。
3. **不被自己截图抓到**：`SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)`。
   实测（当时的 probe_wda 实验）：屏幕上照常显示，但 GDI 屏幕 BitBlt 里完全没有它
   —— 品红占比 1.000 → 0.000。没有这一条，浮层上的文字会跑进识别画面，
   把剧情识别搞乱。

窗口位置跟随游戏客户区左下角，游戏移动/改分辨率后自动重贴。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
import tkinter as tk

from . import config

user32 = ctypes.WinDLL("user32", use_last_error=True)

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CHILD = 0x40000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND
user32.GetDesktopWindow.argtypes = []
user32.GetDesktopWindow.restype = wt.HWND
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
user32.SetWindowDisplayAffinity.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL

LEVEL_COLOR = {
    "info": "#dcdce2",
    "ok": "#7fe08a",
    "warn": "#ffd166",
    "error": "#ff7b7b",
    "action": "#7fd1ff",
}

MAX_LINES = 200


class LogOverlay:
    """贴在游戏左下角的日志浮层。

    用法（全部在 Tk 主线程里调）：
        ov = LogOverlay(root)
        ov.attach(window)       # 贴到游戏客户区左下角
        ov.push("识别", "检测到剧情")
        ov.pump()               # 每个 UI tick 调一次：落队列 + 跟随游戏窗口
        ov.detach()
    """

    def __init__(self, master: tk.Misc, *, lines: int = 9,
                 width: int = 470, alpha: float = 0.82):
        self.master = master
        self.lines = lines
        self.width = width
        self.alpha = alpha
        self.visible = False
        self.hwnd = 0
        self._window = None       # GameWindow
        self._queue: list[tuple[str, str, str]] = []
        self._last_geom = None
        self._excluded = False
        self._last_pump = 0.0

        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", alpha)
        except tk.TclError:
            pass
        self.win.configure(bg="#0d0d10")

        self.text = tk.Text(
            self.win, bg="#0d0d10", fg="#dcdce2", bd=0, highlightthickness=0,
            wrap="none", font=("Microsoft YaHei UI", 9), padx=8, pady=5,
            height=lines, width=56, state="disabled", cursor="arrow",
        )
        self.text.pack(fill="both", expand=True)
        for name, color in LEVEL_COLOR.items():
            self.text.tag_configure(name, foreground=color)
        self.text.tag_configure("tag", foreground="#7a7a86")
        self.text.tag_configure("time", foreground="#5f5f6b")

        # 固定的几句提示（用户指定，一字不改）：贴在浮层最上方，不随日志滚走。
        # 三行分别是标题 / 使用建议 / 已知会误识别的活动。
        self.head = tk.Frame(self.win, bg="#0d0d10")
        head_font = ("Microsoft YaHei UI", 9)
        self.head_title = tk.Label(
            self.head, text=config.READY_TITLE, bg="#0d0d10", fg="#9fe0b4",
            font=("Microsoft YaHei UI", 9, "bold"), justify="left", anchor="w",
            wraplength=430, padx=8, pady=0,
        )
        self.head_title.pack(fill="x", padx=0, pady=(4, 1))
        self.head_body = tk.Label(
            self.head, text=config.READY_TEXT, bg="#0d0d10", fg="#c9c9d2",
            font=head_font, justify="left", anchor="w", wraplength=430,
            padx=8, pady=0,
        )
        self.head_body.pack(fill="x")
        self.head_warn = tk.Label(
            self.head, text=config.READY_WARN, bg="#0d0d10", fg="#ffc857",
            font=head_font, justify="left", anchor="w", wraplength=430,
            padx=8, pady=0,
        )
        self.head_warn.pack(fill="x", pady=(1, 5))
        self.head.pack(side="top", fill="x", before=self.text)

    # -- 原生样式：穿透 + 不激活 + 排除抓屏 ----------------------------
    def _resolve_hwnd(self) -> int:
        """拿到浮层的**顶层**窗口句柄。

        Tk 的 `winfo_id()` 给的是内部子窗口，真正的顶层是它的父窗口。
        但 `GetParent` 在某些情况（overrideredirect）下会直接返回桌面句柄，
        那样去改 exstyle 会动到整张桌面 —— 所以逐级校验，宁可退回子窗口。
        """
        child = int(self.win.winfo_id())
        desktop = int(user32.GetDesktopWindow())
        hwnd = child
        for _ in range(3):
            parent = int(user32.GetParent(hwnd) or 0)
            if not parent or parent == desktop:
                break
            hwnd = parent
        try:
            style = int(user32.GetWindowLongW(hwnd, GWL_STYLE))
        except Exception:                                    # noqa: BLE001
            return child
        # 顶层窗口的 WS_CHILD 必须是 0；否则说明我们拿到的是子窗口
        return hwnd if not (style & WS_CHILD) else child

    def _apply_native_style(self) -> None:
        try:
            self.win.update_idletasks()
            hwnd = self._resolve_hwnd()
            if not hwnd or hwnd == int(user32.GetDesktopWindow()):
                return
            self.hwnd = hwnd
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                  ex | WS_EX_LAYERED | WS_EX_TRANSPARENT
                                  | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
            ok = user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            self._excluded = bool(ok)
            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        except Exception:                                    # noqa: BLE001
            self._excluded = False

    # -- 贴到游戏窗口 --------------------------------------------------
    def attach(self, window) -> None:
        """把浮层贴到游戏客户区左下角。window 为 None 时隐藏。"""
        self._window = window
        if window is None:
            self.detach()
            return
        if not self.visible:
            self.win.deiconify()
            self._apply_native_style()
            self.visible = True
        self._reposition(force=True)

    def detach(self) -> None:
        if self.visible:
            try:
                self.win.withdraw()
            except tk.TclError:
                pass
            self.visible = False
        self._last_geom = None

    def _reposition(self, *, force: bool = False) -> None:
        w = self._window
        if w is None:
            return
        try:
            self.win.update_idletasks()
            ow = max(self.win.winfo_reqwidth(), self.width)
            oh = self.win.winfo_reqheight()
        except tk.TclError:
            return
        x = w.left + 12
        y = w.top + w.height - oh - 12
        geom = (x, y, ow, oh)
        if not force and geom == self._last_geom:
            return
        self._last_geom = geom
        try:
            self.win.geometry("%dx%d+%d+%d" % (ow, oh, x, y))
        except tk.TclError:
            return
        if self.hwnd:
            # 用 SetWindowPos 保持 TOPMOST 且**不激活**
            user32.SetWindowPos(self.hwnd, wt.HWND(HWND_TOPMOST), x, y, ow, oh,
                                SWP_NOACTIVATE | SWP_SHOWWINDOW)

    # -- 日志 ----------------------------------------------------------
    def push(self, tag: str, text: str, level: str = "info") -> None:
        """线程安全：只入队，真正的插入在 pump() 里做。"""
        self._queue.append((tag, text, level))
        if len(self._queue) > 400:
            del self._queue[:100]

    def pump(self) -> int:
        """UI tick 调用：落队列、跟随游戏窗口。返回本次插入的行数。"""
        n = 0
        if self._queue:
            batch, self._queue = self._queue, []
            self.text.configure(state="normal")
            for tag, text, level in batch:
                ts = time.strftime("%H:%M:%S", time.localtime())
                self.text.insert("end", ts + " ", ("time",))
                if tag:
                    self.text.insert("end", "[%s] " % tag, ("tag",))
                self.text.insert("end", text + "\n", (level if level in LEVEL_COLOR
                                                      else "info",))
                n += 1
            lines = int(self.text.index("end-1c").split(".")[0])
            if lines > MAX_LINES:
                self.text.delete("1.0", "%d.0" % (lines - MAX_LINES + 1))
            self.text.see("end")
            self.text.configure(state="disabled")
        if self.visible:
            self._reposition()
        return n

    def clear(self) -> None:
        self._queue.clear()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    @property
    def capture_excluded(self) -> bool:
        """浮层是否成功排除在截图之外（False 时识别可能被浮层文字干扰）。"""
        return self._excluded

    def rect(self) -> tuple[int, int, int, int] | None:
        """浮层在屏幕上的真实矩形 (x, y, w, h)；拿不到时返回 None。

        用 GetWindowRect 读实际位置而不是按 GameWindow 推算 —— 浮层是
        TOPMOST 的独立窗口，窗框会让推算值差几个像素，自检时容易误判。
        """
        if not self.hwnd:
            return None
        r = wt.RECT()
        try:
            if not user32.GetWindowRect(self.hwnd, ctypes.byref(r)):
                return None
        except OSError:
            return None
        w, h = int(r.right - r.left), int(r.bottom - r.top)
        if w <= 0 or h <= 0:
            return None
        return int(r.left), int(r.top), w, h

