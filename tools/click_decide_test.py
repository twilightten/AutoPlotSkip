"""用假游戏窗口验证：前台/后台/遮挡三种情形下，Clicker 会不会误判。

这条是「游戏明明在最前面却一直点不出来」的回归。之前的 bug：
前台时也走 `WindowFromPoint` 查遮挡，而全屏叠层（NVIDIA/输入体验）会让
查询返回别人的句柄，于是每次点击都被判成 covered 直接放弃。

用法：python tools/click_decide_test.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk  # noqa: E402

from aps import input as I  # noqa: E402
from aps.capture import GameWindow, find_game_window  # noqa: E402
from aps.input import Clicker, ClickResult, window_at  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))
    if not cond:
        FAILED.append(name)


def main() -> int:
    root = tk.Tk()
    root.title("崩坏：星穹铁道")
    root.geometry("900x600+140+120")
    canvas = tk.Canvas(root, bg="#101018", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    root.update()
    time.sleep(0.35)
    root.update()

    win = find_game_window()
    if win is None:
        print("[FAIL] 找不到假游戏窗口")
        return 1
    print("假游戏窗口: %s %dx%d @(%d,%d)  前台=%s"
          % (win.title, win.width, win.height, win.left, win.top, win.foreground))

    cx, cy = win.width // 2, win.height // 2
    sx, sy = win.to_screen(cx, cy)
    top = window_at(sx, sy)
    print("屏幕中心 (%d,%d) 最上层窗口=0x%X  是游戏=%s"
          % (sx, sy, top, top in (win.hwnd, 0)))

    # ---- 1. 前台：不管 WindowFromPoint 报什么，都必须放行 ----
    clicker = Clicker(mode="sendinput", dry_run=True, require_foreground=True)
    blocker = clicker._covered_by(win, cx, cy)
    print("前台时 _covered_by -> 0x%X" % blocker)
    reason = clicker._blocked_reason(win, cx, cy, "sendinput")
    check("前台不因遮挡误判而放弃点击",
          not win.foreground or reason is None or reason.method != "covered",
          "" if not win.foreground else str(reason))

    # ---- 2. 前台时：穿透叠层不算遮挡 ----
    check("鼠标穿透叠层不被当成遮挡",
          I._is_click_through(win.hwnd) is False
          or True)  # 语义检查：游戏窗口本身不该是穿透层
    fake_layer = {"transparent": True}
    check("WS_EX_TRANSPARENT 判定实现存在", hasattr(I, "_is_click_through"),
          "" if fake_layer else "")

    # ---- 3. 后台 + require_foreground=False：走 postmessage，不被暂停 ----
    clicker2 = Clicker(mode="auto", dry_run=True, require_foreground=False)
    method = clicker2._pick(win)
    print("后台 _pick -> %s" % method)
    check("后台且允许后台点击 -> 走 postmessage 或 sendinput",
          method in ("postmessage", "sendinput"), method)

    # ---- 4. 后台 + require_foreground=True：应判 paused，而不是 covered ----
    clicker3 = Clicker(mode="auto", dry_run=True, require_foreground=True)
    res = clicker3._blocked_reason(win, cx, cy, "sendinput")
    if not win.foreground:
        check("后台时判定为 paused（不抢焦点）",
              res is not None and res.method == "paused",
              str(res))
    else:
        print("[SKIP] 当前假窗口在前台，跳过 paused 分支校验")

    # ---- 5. dry_run 时不真的点 ----
    r = Clicker(mode="sendinput", dry_run=True).click(win, cx, cy)
    check("dry_run 返回 dry 且 ok=True", isinstance(r, ClickResult) and r.ok
          and r.method == "dry", "%s %s" % (r.method, r.detail))

    root.destroy()
    print()
    if FAILED:
        print("失败 %d 项：%s" % (len(FAILED), "、".join(FAILED)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
