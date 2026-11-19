# -*- coding: utf-8 -*-
"""单实例保护。

双击第二次 exe 时不该再开一个一模一样的窗口：两个实例会同时抢游戏画面、
同时发点击，浮层也会叠两层。这里用命名互斥体（named mutex）做判定：

  * `is_running()`  只探测、不占锁 —— 用来在申请管理员权限**之前**就拦住第二次启动，
    否则用户每双击一次都会先吃一个 UAC 弹窗。
  * `acquire()`     真正占锁（句柄持有到进程结束），防止两次启动同时跨过探测。

用互斥体而不是「枚举窗口找标题」的好处：不受窗口被最小化、标题被改、
或者窗口还没建出来这几种时机问题影响，进程级别的判定永远准。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "Local\\AutoPlotSkip.SingleInstance.v1"

kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL

user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL

SW_RESTORE = 9

_handle: int | None = None      # 占到的锁句柄，进程活着就一直不关


def is_running(name: str = MUTEX_NAME) -> bool:
    """只问「有没有别的实例占着锁」，不改变任何状态。

    以 bInitialOwner=False 创建：同名互斥体已存在时 Windows 返回它的句柄并把
    last_error 置成 ERROR_ALREADY_EXISTS；立刻关掉这个探测句柄即可，不会影响
    正在占锁的那个进程。
    """
    h = kernel32.CreateMutexW(None, False, name)
    err = ctypes.get_last_error()
    if not h:
        return False
    kernel32.CloseHandle(h)
    return err == ERROR_ALREADY_EXISTS


def acquire(name: str = MUTEX_NAME) -> bool:
    """真正占锁。返回 True 表示我是唯一实例（句柄留着别关）。"""
    global _handle
    if _handle is not None:
        return True
    h = kernel32.CreateMutexW(None, True, name)
    err = ctypes.get_last_error()
    if not h:
        # 创建失败（极罕见）时别挡住用户，当作可以启动
        return True
    if err == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(h)
        return False
    _handle = h
    return True


def focus_existing(title: str) -> bool:
    """把已经在跑的那个窗口拉到前台，让用户知道「它已经开着」。"""
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    return True
