# -*- coding: utf-8 -*-
"""浮层日志窗自检：不抢焦点 / 点击穿透 / 不被截图抓到 / 跟随窗口。

不需要游戏，用一个普通 Tk 窗口假装成"游戏窗口"跑一遍即可。
    python tools/overlay_test.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aps import overlay as ov_mod                       # noqa: E402
from aps.capture import GameWindow, ScreenGrabber       # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080


def main() -> int:
    root = tk.Tk()
    root.title("overlay-test-host")
    root.geometry("900x600+200+150")
    root.configure(bg="#202030")
    root.update()

    ov = ov_mod.LogOverlay(root, lines=6, width=430)
    win = GameWindow(int(root.winfo_id()), "overlay-test-host",
                     root.winfo_rootx(), root.winfo_rooty() + 20,
                     900, 560)
    ov.attach(win)
    root.update()
    time.sleep(0.2)
    root.update()
    # 宿主窗口必须压在最上层：否则别的窗口（浏览器/播放器）会飘到采样区上，
    # 对照区亮度随机跳动，第 4 项判据会假失败（实测均值在 42~58 之间乱跳）。
    try:
        root.attributes("-topmost", True)
        root.lift()
    except tk.TclError:
        pass
    root.update()
    time.sleep(0.25)
    root.update()

    fails = 0

    # 1) 句柄有效且是顶层窗口
    hwnd = ov.hwnd
    style = int(user32.GetWindowLongW(hwnd, ov_mod.GWL_STYLE))
    top_level = not (style & ov_mod.WS_CHILD)
    print("[%s] 顶层句柄 hwnd=%#x 是顶层=%s" %
          ("OK  " if (hwnd and top_level) else "FAIL", hwnd, top_level))
    fails += 0 if (hwnd and top_level) else 1

    # 2) exstyle 三个关键位
    ex = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
    for name, bit in (("WS_EX_TRANSPARENT", WS_EX_TRANSPARENT),
                      ("WS_EX_NOACTIVATE", WS_EX_NOACTIVATE),
                      ("WS_EX_TOOLWINDOW", WS_EX_TOOLWINDOW)):
        ok = bool(ex & bit)
        print("[%s] 扩展样式 %s" % ("OK  " if ok else "FAIL", name))
        fails += 0 if ok else 1

    # 3) 抓屏排除
    ok = ov.capture_excluded
    print("[%s] SetWindowDisplayAffinity(EXCLUDEFROMCAPTURE) 成功=%s"
          % ("OK  " if ok else "FAIL", ok))
    fails += 0 if ok else 1

    # 4) 浮层不该出现在屏幕 BitBlt 里
    #    判据用「对照区差分」而不是绝对亮度：宿主窗口底色 #202030 经
    #    alpha=0.82 的浮层压暗后会接近浮层底色，绝对阈值不可靠（实测
    #    25.2 与 26.0 只差 0.8，误报）。改为比较同一张截图里
    #    「浮层所在矩形」与「浮层正上方同尺寸的纯宿主矩形」的均值：
    #    如果浮层被 EXCLUDEFROMCAPTURE 正确排除，两者应当完全一致。
    grabber = ScreenGrabber(allow_any=True)
    ov.push("测试", "浮层可见性探针", "action")
    root.update()
    ov.pump()
    root.update()
    time.sleep(0.15)
    img = grabber.grab()
    if img is None:
        print("[FAIL] 截图失败：%s" % grabber.last_error)
        fails += 1
    else:
        h, w = img.shape[:2]
        # 用浮层自己的真实矩形（而不是推算的），这样不会被窗口边框偏移坑到
        orect = ov.rect() if hasattr(ov, "rect") else None
        if orect is None:
            ox, oy, ow, oh = (win.left + 12,
                              win.top + win.height
                              - ov.win.winfo_reqheight() - 12,
                              ov.win.winfo_reqwidth(),
                              ov.win.winfo_reqheight())
        else:
            ox, oy, ow, oh = orect
        ox, oy = max(0, ox), max(0, oy)
        cy = max(0, oy - oh - 40)          # 浮层正上方、同尺寸
        patch = img[oy:oy + oh, ox:ox + ow]
        ctrl = img[cy:cy + oh, ox:ox + ow]
        if patch.size == 0 or ctrl.size == 0 or ctrl.shape != patch.shape:
            print("[FAIL] 浮层区域越界 (patch=%s ctrl=%s)"
                  % (patch.shape, ctrl.shape))
            fails += 1
        else:
            # 浮层底色 #0d0d10(≈13)，宿主 #202030(≈35)；若被抓到，
            # patch 会明显比 ctrl 暗。
            diff = float(ctrl.mean()) - float(patch.mean())
            ok = diff < 2.0
            print("[%s] 浮层未混进截图：浮层区=%.1f 对照区=%.1f 差=%.2f"
                  "（差<2 视为已排除）"
                  % ("OK  " if ok else "FAIL",
                     float(patch.mean()), float(ctrl.mean()), diff))
            fails += 0 if ok else 1

    # 5) 焦点没有被抢走
    fg_before = int(user32.GetForegroundWindow())
    ov.push("测试", "焦点探针" * 6, "info")
    ov.pump()
    root.update()
    time.sleep(0.2)
    fg_after = int(user32.GetForegroundWindow())
    ok = fg_after != hwnd
    print("[%s] 浮层不抢前台：前=%#x 后=%#x" %
          ("OK  " if ok else "FAIL", fg_before, fg_after))
    fails += 0 if ok else 1

    # 6) 跟随：宿主窗口移动后浮层跟着走
    root.geometry("900x600+400+200")
    root.update()
    time.sleep(0.1)
    win2 = GameWindow(int(root.winfo_id()), "overlay-test-host",
                      root.winfo_rootx(), root.winfo_rooty() + 20, 900, 560)
    ov.attach(win2)
    root.update()
    ov.pump()
    root.update()
    ox = ov.win.winfo_rootx()
    ok = abs(ox - (win2.left + 12)) <= 2
    print("[%s] 跟随游戏窗口：浮层x=%d 期望=%d"
          % ("OK  " if ok else "FAIL", ox, win2.left + 12))
    fails += 0 if ok else 1

    ov.detach()
    root.update()
    print("-" * 62)
    print("失败 %d 项" % fails)
    root.destroy()
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
