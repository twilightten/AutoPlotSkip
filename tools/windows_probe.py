# -*- coding: utf-8 -*-
"""枚举可见窗口 + 完整性级别（IL），用于判断点击为什么被拦。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenIntegrityLevel = 25

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
kernel32.GetCurrentProcessId.restype = wt.DWORD

IL_NAMES = {0x0000: "Untrusted", 0x1000: "Low", 0x2000: "Medium",
            0x2100: "MediumPlus", 0x3000: "High(管理员)", 0x4000: "System"}


def il_of_pid(pid: int):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    tok = wt.HANDLE()
    if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)):
        kernel32.CloseHandle(h)
        return None
    size = wt.DWORD(0)
    advapi32.GetTokenInformation(tok, TokenIntegrityLevel, None, 0, ctypes.byref(size))
    buf = ctypes.create_string_buffer(max(size.value, 64))
    ok = advapi32.GetTokenInformation(tok, TokenIntegrityLevel, buf, size, ctypes.byref(size))
    level = None
    if ok:
        # TOKEN_MANDATORY_LABEL { SID_AND_ATTRIBUTES { PSID Sid; DWORD Attributes; } }
        sid_ptr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        if sid_ptr:
            n = advapi32.GetSidSubAuthorityCount(ctypes.c_void_p(sid_ptr))
            cnt = n[0]
            sub = advapi32.GetSidSubAuthority(ctypes.c_void_p(sid_ptr), cnt - 1)
            level = sub[0]
    kernel32.CloseHandle(tok)
    kernel32.CloseHandle(h)
    return level


def main():
    print("本进程 pid=%d IL=%s" % (
        kernel32.GetCurrentProcessId(),
        IL_NAMES.get(il_of_pid(kernel32.GetCurrentProcessId()))))
    rows = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 2)
        user32.GetWindowTextW(hwnd, buf, n + 2)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        il = il_of_pid(pid.value)
        rows.append(((r.right - r.left) * (r.bottom - r.top), hwnd, buf.value[:40],
                     cls.value[:24], pid.value, il))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    rows.sort(reverse=True)
    print("%-11s %-8s %-30s %-22s %s" % ("size", "hwnd", "title", "class", "IL"))
    for area, hwnd, title, cls, pid, il in rows[:18]:
        print("%-11d 0x%06X %-30s %-22s %s(pid %d)" % (
            area, hwnd, title, cls, IL_NAMES.get(il, il), pid))

    fg = user32.GetForegroundWindow()
    print("\n前台 hwnd=0x%X" % fg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
