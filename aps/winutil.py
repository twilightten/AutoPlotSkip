# -*- coding: utf-8 -*-
"""Windows 进程权限相关的小工具（纯 ctypes）。

为什么需要这个：
  米哈游启动器（以及由它拉起的游戏进程）常常以**管理员权限**运行，完整性级别是
  High(0x3000)；而普通双击启动的 Python 只有 Medium/Low。Windows 的 UIPI
  （User Interface Privilege Isolation）会**静默丢弃**低完整性进程发往高完整性
  窗口的输入：SendInput / PostMessage 都返回成功，但游戏什么反应都没有。

  这就是"根本点不出来"的真正原因 —— 不是坐标错，也不是窗口没激活，
  而是权限不够。所以启动时必须先检测，权限不足就提示用户以管理员身份重启。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenIntegrityLevel = 25
SW_SHOWNORMAL = 1

IL_UNTRUSTED = 0x0000
IL_LOW = 0x1000
IL_MEDIUM = 0x2000
IL_MEDIUM_PLUS = 0x2100
IL_HIGH = 0x3000
IL_SYSTEM = 0x4000

IL_NAMES = {
    IL_UNTRUSTED: "Untrusted",
    IL_LOW: "Low",
    IL_MEDIUM: "Medium",
    IL_MEDIUM_PLUS: "MediumPlus",
    IL_HIGH: "High(管理员)",
    IL_SYSTEM: "System",
}

advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
advapi32.OpenProcessToken.restype = wt.BOOL
advapi32.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wt.DWORD, ctypes.POINTER(wt.DWORD)]
advapi32.GetTokenInformation.restype = wt.BOOL
advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wt.DWORD]
advapi32.GetSidSubAuthority.restype = ctypes.POINTER(ctypes.c_uint32)
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.GetCurrentProcess.restype = wt.HANDLE
kernel32.GetCurrentProcessId.restype = wt.DWORD
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
shell32.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR,
                                  wt.LPCWSTR, ctypes.c_int]
shell32.ShellExecuteW.restype = ctypes.c_void_p


def _integrity_from_token(tok) -> int | None:
    size = wt.DWORD(0)
    advapi32.GetTokenInformation(tok, TokenIntegrityLevel, None, 0,
                                 ctypes.byref(size))
    if size.value == 0:
        return None
    buf = ctypes.create_string_buffer(max(size.value, 64))
    if not advapi32.GetTokenInformation(tok, TokenIntegrityLevel, buf, size,
                                        ctypes.byref(size)):
        return None
    sid_ptr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
    if not sid_ptr:
        return None
    count = advapi32.GetSidSubAuthorityCount(ctypes.c_void_p(sid_ptr))[0]
    return advapi32.GetSidSubAuthority(ctypes.c_void_p(sid_ptr), count - 1)[0]


def integrity_of_pid(pid: int) -> int | None:
    """取某个进程的完整性级别；拿不到（权限不足/进程已退出）返回 None。"""
    if not pid:
        return None
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        tok = wt.HANDLE()
        if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)):
            return None
        try:
            return _integrity_from_token(tok)
        finally:
            kernel32.CloseHandle(tok)
    finally:
        kernel32.CloseHandle(h)


def our_integrity() -> int | None:
    tok = wt.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY,
                                     ctypes.byref(tok)):
        return None
    try:
        return _integrity_from_token(tok)
    finally:
        kernel32.CloseHandle(tok)


def is_admin() -> bool:
    """当前进程是否以管理员（High 完整性）身份运行。"""
    level = our_integrity()
    if level is None:
        return False
    return level >= IL_HIGH


def il_name(level: int | None) -> str:
    if level is None:
        return "未知"
    return IL_NAMES.get(level, "0x%X" % level)


def pid_of_hwnd(hwnd: int) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def same_or_higher_than_us(hwnd: int) -> bool:
    """目标窗口所在进程的完整性级别是否**高于**我们 —— 高的话输入会被 UIPI 丢弃。"""
    ours = our_integrity()
    theirs = integrity_of_pid(pid_of_hwnd(hwnd))
    if ours is None or theirs is None:
        return False
    return theirs > ours


def can_send_to(hwnd: int) -> tuple[bool, str]:
    """能不能给这个窗口发输入，返回 (可否, 说明)。"""
    ours = our_integrity()
    theirs = integrity_of_pid(pid_of_hwnd(hwnd))
    if ours is None or theirs is None:
        return True, "无法读取完整性级别，按可发送处理"
    if theirs > ours:
        return False, ("游戏进程完整性级别 %s 高于本程序 %s，Windows 会丢弃点击。"
                       "请右键 AutoPlotSkip →「以管理员身份运行」。"
                       % (il_name(theirs), il_name(ours)))
    return True, "权限正常（游戏 %s / 本程序 %s）" % (il_name(theirs), il_name(ours))


class SHELLEXECUTEINFOW(ctypes.Structure):
    """ShellExecuteExW 的参数结构（只用到前几个字段，其余按 ABI 对齐即可）。"""
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wt.HWND),
        ("lpVerb", wt.LPCWSTR),
        ("lpFile", wt.LPCWSTR),
        ("lpParameters", wt.LPCWSTR),
        ("lpDirectory", wt.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wt.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wt.LPCWSTR),
        ("hkeyClass", wt.HKEY),
        ("dwHotKey", wt.DWORD),
        ("hIconOrMonitor", wt.HANDLE),
        ("hProcess", wt.HANDLE),
    ]


# ShellExecuteEx 的 fMask 位
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
SEE_MASK_FLAG_NO_UI = 0x00000400      # 关键：屏蔽「打开文件 - 安全警告」这类 shell 弹窗
ERROR_CANCELLED = 1223

shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
shell32.ShellExecuteExW.restype = wt.BOOL


def relaunch_as_admin(*, extra_args: list[str] | None = None,
                      argv0: str | None = None) -> bool:
    """用 UAC 提权重启自己。成功发起提权返回 True（调用方应立刻退出）。

    为什么不用 ShellExecuteW：
      ShellExecuteW 走的是 shell 的「打开文件」流程，对没签名的 exe 会先弹一个
      「打开文件 - 安全警告 / 你想运行此文件吗?」的框，用户以为程序在搞鬼。
      ShellExecuteExW 加上 SEE_MASK_FLAG_NO_UI 就只留 UAC 那一个系统授权框。
    """
    if is_admin():
        return False
    exe = sys.executable
    params: list[str] = []
    if getattr(sys, "frozen", False):
        target = exe
    else:
        target = exe
        script = argv0 or os.path.abspath(sys.argv[0])
        params.append('"%s"' % script)
    params.extend(extra_args if extra_args is not None else sys.argv[1:])
    arg_line = " ".join(params) if params else None
    workdir = os.path.dirname(os.path.abspath(argv0 or sys.argv[0])) or os.getcwd()

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = (SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
                  | SEE_MASK_FLAG_NO_UI)
    info.lpVerb = "runas"
    info.lpFile = target
    info.lpParameters = arg_line
    info.lpDirectory = workdir
    info.nShow = SW_SHOWNORMAL
    ok = shell32.ShellExecuteExW(ctypes.byref(info))
    if ok:
        # 提权后的新进程我们不需要，交给系统收尾，避免句柄泄漏
        if info.hProcess:
            try:
                kernel32.CloseHandle(info.hProcess)
            except Exception:
                pass
        return True
    # 失败：1223 = 用户在 UAC 框点了「否」；其余（5/31/1155…）也都是没提起来
    return False
