# -*- coding: utf-8 -*-
"""鼠标点击层 —— 前台真实点击 / 后台消息点击 双后端，**永不主动抢焦点**。

v1 的两个致命问题（用户实测反馈）：

  1. **反复把游戏拉前台**。v1 每次点击前都调 SetForegroundWindow，游戏被不停
     置顶；用户想用鼠标点别的窗口，一秒就被抢回去 —— 表现就是「游戏卡住」。
  2. **只走 SendInput**。米哈游启动器拉起的游戏进程跑在 High 完整性（管理员），
     而以普通权限启动的 Python 发过去的输入会被 UIPI **静默丢弃**：SendInput
     返回成功，游戏却毫无反应 —— 这就是「根本点不出来」。

v2 策略：

  * 绝不主动激活 / 置顶游戏窗口。游戏不在前台就**暂停点击**（默认），
    等你切回游戏再继续 —— 屏幕爱显示什么显示什么，不会打断你。
  * 点击前用 ``winutil.can_send_to()`` 判断 UIPI 会不会吞掉这次输入。会被吞就
    直接返回失败并带上原因，由 GUI 明确提示「以管理员身份重启」。
  * 点击前检查目标点是否被别的窗口挡住（遮挡）—— 被挡住就不点，
    否则点到了压在上面的窗口，游戏里毫无反应，正是「点不出来」的另一种成因。
  * SendInput 只在前台、且权限足够时使用；点完立刻把光标放回原处。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from dataclasses import dataclass

from . import winutil

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
MOUSEEVENTF_VIRTUALDESK = 0x4000
GA_ROOT = 2

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEWHEEL = 0x020A
MK_LBUTTON = 0x0001

# 给合成事件打标记，方便将来做"过滤自己发出的输入"
APS_INPUT_TAG = 0xA95C1C

if ctypes.sizeof(ctypes.c_void_p) == 8:
    ULONG_PTR = ctypes.c_ulonglong
    LONG_PTR = ctypes.c_longlong
else:
    ULONG_PTR = ctypes.c_ulong
    LONG_PTR = ctypes.c_long


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTunion)]


user32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wt.UINT
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wt.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.ClientToScreen.restype = wt.BOOL
user32.GetForegroundWindow.restype = wt.HWND
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL


@dataclass
class ClickResult:
    ok: bool
    method: str          # sendinput / postmessage / blocked / covered / background-paused / dry / none
    detail: str = ""


def screen_size() -> tuple[int, int]:
    """主显示器的宽高。"""
    return (user32.GetSystemMetrics(SM_CXSCREEN),
            user32.GetSystemMetrics(SM_CYSCREEN))


def virtual_screen_rect() -> tuple[int, int, int, int]:
    """整个虚拟桌面（所有显示器合起来）的 (x, y, w, h)。

    多显示器时主屏的 (0,0) 不是虚拟桌面左上角：副屏可能在主屏左边或上面，
    此时虚拟桌面原点就是负数。`SendInput` 的绝对坐标默认只映射主屏，
    所以必须配 `MOUSEEVENTF_VIRTUALDESK` 并用这套矩形换算，否则点在副屏
    上的坐标会被挤回主屏 —— 表现为"点了但点错地方"。
    """
    vx = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    vy = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    vw = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    vh = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    if vw <= 1 or vh <= 1:                    # 极端情况下退回主屏
        sw, sh = screen_size()
        return (0, 0, sw, sh)
    return (vx, vy, vw, vh)


def _send(flags: int, x: int = 0, y: int = 0) -> bool:
    inp = INPUT(
        type=INPUT_MOUSE,
        u=_INPUTunion(mi=MOUSEINPUT(dx=x, dy=y, mouseData=0, dwFlags=flags,
                                    time=0, dwExtraInfo=APS_INPUT_TAG)),
    )
    return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) == 1


def move_to(x: int, y: int) -> None:
    """把光标瞬移到屏幕绝对坐标 (x, y)。

    用虚拟桌面矩形换算 + `MOUSEEVENTF_VIRTUALDESK`，多显示器也不会点偏。
    """
    vx, vy, vw, vh = virtual_screen_rect()
    if vw <= 1 or vh <= 1:
        return
    ax = int(round((x - vx) * 65535.0 / (vw - 1)))
    ay = int(round((y - vy) * 65535.0 / (vh - 1)))
    ax = 0 if ax < 0 else (65535 if ax > 65535 else ax)
    ay = 0 if ay < 0 else (65535 if ay > 65535 else ay)
    _send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay)



def restore_cursor(saved: "wt.POINT") -> bool:
    """把光标放回 saved 的位置，尽力做到像素级准确。

    两个坑（实测）：
      * 低完整性进程里 ``SetCursorPos`` 会被系统拒绝，返回 0 且不报错；
      * ``SendInput`` 绝对坐标要经过 0..65535 归一化，回来时会被"提高指针精确度"
        的指针加速再修正一次，能差几个像素。

    所以先用 SetCursorPos，不行就 SendInput，再读回实际位置做一次闭环修正。
    """
    if user32.SetCursorPos(saved.x, saved.y):
        return True
    for _ in range(3):
        move_to(saved.x, saved.y)
        time.sleep(0.012)
        now = wt.POINT()
        if not user32.GetCursorPos(ctypes.byref(now)):
            break
        dx, dy = saved.x - now.x, saved.y - now.y
        if abs(dx) <= 1 and abs(dy) <= 1:
            return True
        move_to(now.x + dx, now.y + dy)
        time.sleep(0.012)
    now = wt.POINT()
    if user32.GetCursorPos(ctypes.byref(now)):
        return abs(now.x - saved.x) <= 2 and abs(now.y - saved.y) <= 2
    return False


def sendinput_click(x: int, y: int, *, down_ms: float = 0.02,
                    restore_cursor_after: bool = True) -> bool:
    """真实鼠标点击（屏幕绝对坐标）。点完把光标放回原处，不打断用户。"""
    saved = wt.POINT()
    have_saved = restore_cursor_after and bool(user32.GetCursorPos(ctypes.byref(saved)))
    move_to(x, y)
    time.sleep(0.012)
    ok = _send(MOUSEEVENTF_LEFTDOWN)
    time.sleep(down_ms)
    ok = _send(MOUSEEVENTF_LEFTUP) and ok
    if have_saved:
        time.sleep(0.012)
        restore_cursor(saved)
    return ok


def post_click(hwnd: int, client_x: int, client_y: int, *,
               down_ms: float = 0.02) -> bool:
    """后台点击：往窗口投递鼠标消息，不需要前台，也不动真实光标。

    lParam 打包：低 16 位 x，高 16 位 y（客户区坐标，有符号 16 位）。
    先发一条 WM_MOUSEMOVE —— Unity 的 UI 事件系统要靠移动消息更新命中位置，
    少了它有时候会点到上一帧的坐标上。
    """
    if not hwnd:
        return False
    cx = max(-32000, min(32000, int(client_x)))
    cy = max(-32000, min(32000, int(client_y)))
    lparam = (cy << 16) | (cx & 0xFFFF)
    ok = bool(user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lparam))
    ok = bool(user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)) and ok
    time.sleep(down_ms)
    ok = bool(user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)) and ok
    return ok


def window_at(screen_x: int, screen_y: int) -> int:
    """屏幕该点最上层的顶层窗口句柄（用于判断遮挡）。"""
    pt = wt.POINT(int(screen_x), int(screen_y))
    hwnd = user32.WindowFromPoint(pt)
    if not hwnd:
        return 0
    root = user32.GetAncestor(hwnd, GA_ROOT)
    return int(root or hwnd)


GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080

user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wt.LONG


def _is_click_through(hwnd: int) -> bool:
    """这个窗口是不是「鼠标穿透」的叠层（点了会漏到下面的窗口）。

    NVIDIA GeForce Overlay、Xbox 游戏栏、「Windows 输入体验」、输入法候选条
    全屏盖在游戏上，但它们都是 `WS_EX_TRANSPARENT` / `WS_EX_LAYERED`：
    鼠标点下去会直接穿过它们。把它们当成遮挡，就会出现最气人的那种 bug ——
    游戏明明就在最前面，程序却一直觉得「被挡住了」而拒绝点击。
    """
    try:
        ex = int(user32.GetWindowLongW(wt.HWND(hwnd), GWL_EXSTYLE)) & 0xFFFFFFFF
    except Exception:                                        # noqa: BLE001
        return False
    return bool(ex & (WS_EX_TRANSPARENT | WS_EX_LAYERED))


def is_foreground(hwnd: int) -> bool:
    return bool(hwnd) and user32.GetForegroundWindow() == hwnd


def probe_cursor_control() -> tuple[bool, str]:
    """自检：本进程能不能真的把光标挪走。

    这是「点了半天没反应」里最隐蔽的一种：进程被放进一个收不到输入注入
    的桌面会话（远程会话、无输入队列的服务桌面、锁屏界面）时，`SendInput`
    和 `SetCursorPos` 都会静默失败，而点击函数只会返回 True/False，
    看不出是"没送到"还是"送错地方"。

    返回 (能否移动, 说明)。会把光标还原。
    """
    pt = wt.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return False, "读不到光标位置（GetCursorPos 失败）"
    saved = wt.POINT(pt.x, pt.y)
    sw, sh = screen_size()
    if sw <= 1 or sh <= 1:
        return False, "读不到屏幕尺寸"
    tx, ty = sw // 2, sh // 2
    # 目标正好是当前位置时，换个目标再试，免得误判成"动了"
    if abs(saved.x - tx) <= 2 and abs(saved.y - ty) <= 2:
        tx, ty = (tx + 120) % sw, (ty + 80) % sh

    move_to(tx, ty)
    time.sleep(0.03)
    got = wt.POINT()
    user32.GetCursorPos(ctypes.byref(got))
    if abs(got.x - tx) <= 2 and abs(got.y - ty) <= 2:
        restore_cursor(saved)
        return True, "光标可控（SendInput 正常）"

    if user32.SetCursorPos(tx, ty):
        time.sleep(0.03)
        user32.GetCursorPos(ctypes.byref(got))
        if abs(got.x - tx) <= 2 and abs(got.y - ty) <= 2:
            restore_cursor(saved)
            return True, "光标可控（SendInput 失效，但 SetCursorPos 可用）"

    user32.GetCursorPos(ctypes.byref(got))
    return False, ("光标无法被本进程移动（停在 %d,%d，期望 %d,%d）。"
                    "当前会话收不到输入注入，点击一定无效。"
                    % (got.x, got.y, tx, ty))


def _our_pid() -> int:
    """本进程 PID（用来把"自己的窗口"从遮挡判定里排除掉）。"""
    return int(ctypes.windll.kernel32.GetCurrentProcessId())


class Clicker:
    """统一点击入口。引擎只管调 `click()`，后端选择与各种拦截都在这里。"""

    def __init__(self, *, mode: str = "auto", dry_run: bool = False,
                 require_foreground: bool | None = None,
                 restore_cursor: bool | None = None):
        self.mode = mode              # auto / postmessage / sendinput
        self.dry_run = dry_run
        from . import config
        self.require_foreground = (config.REQUIRE_FOREGROUND
                                   if require_foreground is None
                                   else require_foreground)
        self.restore_cursor = (config.RESTORE_CURSOR
                               if restore_cursor is None else restore_cursor)
        self.last_result = ClickResult(False, "none", "尚未点击")
        self.last_method = ""
        self.counts = {"sendinput": 0, "postmessage": 0, "blocked": 0,
                       "covered": 0, "paused": 0}
        # 后台点击被证明无效后，锁定成只在前台用真实点击
        self.background_dead = False
        self._permission_checked: dict[int, tuple[bool, str]] = {}

    # -- 权限 ---------------------------------------------------------
    def permission(self, hwnd: int, *, recheck: bool = False) -> tuple[bool, str]:
        """能否给这个窗口发输入（UIPI 检查），结果按 hwnd 缓存。"""
        if not recheck and hwnd in self._permission_checked:
            return self._permission_checked[hwnd]
        try:
            verdict = winutil.can_send_to(hwnd)
        except Exception as exc:                             # noqa: BLE001
            verdict = (True, "权限检查失败（%s），按可发送处理" % exc)
        self._permission_checked[hwnd] = verdict
        return verdict

    # -- 点击 ---------------------------------------------------------
    def _pick(self, window) -> str:
        """选择本次点击用的后端。

        - 固定模式（postmessage / sendinput）直接用；
        - auto：游戏在前台 → 真实点击（唯一可靠的路径）；
                游戏不在前台 → 默认**不点**（返回 sendinput，随后被
                `_blocked_reason` 判成 paused 拦下）。只有显式关掉
                `require_foreground` 才退到后台消息点击。
        """
        if self.mode != "auto":
            return self.mode
        from . import config
        fg = getattr(window, "foreground", False)
        if fg:
            return "sendinput"
        if (not self.require_foreground and config.ALLOW_BACKGROUND_CLICK
                and not self.background_dead):
            return "postmessage"
        return "sendinput"

    def _covered_by(self, window, x: int, y: int) -> int:
        """点 (x, y) 时压在最上面、且**不属于**游戏/本程序的顶层窗口句柄。

        为什么不是简单的 `window_at(...) != hwnd`：`WindowFromPoint` 会被
        瞬态窗口（鼠标提示、输入法候选框、叠层覆盖）干扰，实测同样的点击
        时有时无。所以这里：
          * 重试三次，任一次看到游戏就算没被挡（瞬态窗口会消失）；
          * 本程序自己的窗口不算遮挡（浮层/启动器本来就可能压在上面）；
          * 游戏窗口的子窗口算游戏那边；
          * **鼠标穿透的叠层不算遮挡** —— NVIDIA GeForce Overlay、
            输入体验、输入法候选条这类窗口是 WS_EX_TRANSPARENT/LAYERED，
            盖满全屏但点击会直接漏到下面，把它们当遮挡会直接导致
            "游戏明明在最前面却一次都点不出去"。
        """
        sx, sy = window.to_screen(x, y)
        seen = 0
        for attempt in range(3):
            top = window_at(sx, sy)
            if not top:
                return 0
            top = int(top)
            if top == int(window.hwnd):
                return 0
            if user32.GetAncestor(top, GA_ROOT) == int(window.hwnd):
                return 0
            if winutil.pid_of_hwnd(top) == _our_pid():
                return 0
            if _is_click_through(top):
                return 0
            seen = top
            if attempt < 2:
                time.sleep(0.03)
        return seen

    def _blocked_reason(self, window, x: int, y: int, method: str) -> ClickResult | None:
        """点击前的所有否决条件。返回 None 表示可以点。"""
        fg = getattr(window, "foreground", False)

        # 1) 不在前台且要求前台 -> 暂停（绝不主动激活，避免"被置顶卡住"）
        if self.require_foreground and not fg and method == "sendinput":
            self.counts["paused"] += 1
            return ClickResult(False, "paused",
                               "游戏不在前台，已暂停点击（切回游戏即自动继续）")

        # 2) 目标点被别的窗口压住 -> 点下去也是点在别人的窗口上。
        #    但**游戏已经是前台窗口时根本不用查**：前台窗口就是输入的第一
        #    接收者，`WindowFromPoint` 在这时候只会被各种叠层干扰，查它反而
        #    会把正常点击全部否决掉（这正是"游戏在最前却点不出来"的成因）。
        if method == "sendinput" and not fg:
            blocker = self._covered_by(window, x, y)
            if blocker:
                self.counts["covered"] += 1
                return ClickResult(False, "covered", "点击位置被其它窗口挡住")
        return None

    def click(self, window, x: int, y: int, *,
              force: str | None = None) -> ClickResult:
        """在**窗口客户区坐标** (x, y) 上点一下。"""
        if window is None:
            return ClickResult(False, "none", "没有游戏窗口")

        method = force or self._pick(window)
        if self.dry_run:
            result = ClickResult(True, "dry", "演练，不真的点（%s @%d,%d）"
                                 % (method, x, y))
            self.last_result = result
            return result

        allowed, why = self.permission(window.hwnd)
        if not allowed:
            # UIPI 会吞掉两个后端的输入，点了也是白点 —— 直接说清楚。
            self.counts["blocked"] += 1
            result = ClickResult(False, "blocked", why)
            self.last_result = result
            return result

        blocked = self._blocked_reason(window, x, y, method)
        if blocked is not None:
            self.last_result = blocked
            return blocked

        ok = False
        detail = ""
        if method == "postmessage":
            ok = post_click(window.hwnd, x, y)
            detail = "后台点击"
        if method == "sendinput" or (method == "postmessage" and not ok):
            sx, sy = window.to_screen(x, y)
            ok = sendinput_click(sx, sy, restore_cursor_after=self.restore_cursor)
            method = "sendinput"
            detail = "前台点击"

        result = ClickResult(ok, method, detail)
        self.last_result = result
        if ok:
            self.last_method = method
            self.counts[method] = self.counts.get(method, 0) + 1
        return result

    def click_relative(self, window, rx: float, ry: float, **kw) -> ClickResult:
        if window is None:
            return ClickResult(False, "none", "没有游戏窗口")
        return self.click(window, int(rx * window.width), int(ry * window.height), **kw)
