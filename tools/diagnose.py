# -*- coding: utf-8 -*-
"""一键诊断：`python tools/diagnose.py`

用户报「点不出来」时，按 BGI 排障的顺序把每一环单独测一遍，直接指出断在哪：

  1. 进程权限（UIPI）—— 本程序 vs 游戏，谁高谁低
  2. 游戏窗口 —— 找不找得到、尺寸、是否前台
  3. 截图 —— 屏幕 DC / 窗口 DC / PrintWindow 三路各自能不能拿到画面
  4. 识别 —— 当前画面被判成什么，命中了哪些图标、分数多少
  5. 点击链路 —— 用 dry_run 跑一遍 Clicker，看它会选哪个后端、被谁拦下

只读，不点任何东西。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

from aps import capture, config, winutil
from aps.input import Clicker, is_foreground, window_at
from aps.triggers import plan_action
from aps.vision import Recognizer

OK = "[ OK ]"
BAD = "[FAIL]"
WARN = "[WARN]"
LINE = "-" * 74


def head(text: str) -> None:
    print()
    print(LINE)
    print(text)
    print(LINE)


def check_privilege() -> None:
    head("1. 进程权限（UIPI 决定的点击能不能送达）")
    ours = winutil.our_integrity()
    print("  本程序完整性级别：%s (%s)  管理员=%s"
          % (winutil.il_name(ours), hex(ours or 0), winutil.is_admin()))
    game = capture.find_game_window()
    if game is None:
        print("  %s 没找到游戏窗口，跳过权限对比" % WARN)
        return
    pid = winutil.pid_of_hwnd(game.hwnd)
    theirs = winutil.integrity_of_pid(pid) if pid else None
    print("  游戏进程 PID=%s 完整性级别：%s (%s)"
          % (pid, winutil.il_name(theirs), hex(theirs or 0)))
    allowed, why = winutil.can_send_to(game.hwnd)
    print("  %s %s" % (OK if allowed else BAD, why))
    if not allowed:
        print("       → 这是「点不出来」最常见的原因。"
              "请关闭本程序，右键 python.exe 或终端选「以管理员身份运行」。")


def check_window() -> "capture.GameWindow | None":
    head("2. 游戏窗口")
    game = capture.find_game_window()
    if game is None:
        print("  %s 未找到。请先启动「崩坏：星穹铁道」。" % BAD)
        print("      若游戏已开着仍找不到，运行 python tools/windows_probe.py 看窗口清单。")
        return None
    print("  %s 标题=%r 句柄=0x%X" % (OK, game.title, game.hwnd))
    print("      客户区 %dx%d @(%d,%d)" % (game.width, game.height,
                                          game.left, game.top))
    fg = is_foreground(game.hwnd)
    top = window_at(game.left + game.width // 2, game.top + game.height // 2)
    print("      前台=%s  窗口中心最上层句柄=%s（%s）"
          % (fg, hex(top), "就是游戏" if top == game.hwnd else "不是游戏，被别的窗口压着"))
    if top and top != game.hwnd:
        from aps.input import _is_click_through
        if _is_click_through(top):
            print("         该叠层是 WS_EX_TRANSPARENT/LAYERED（鼠标穿透），"
                  "不算遮挡，点击会落到游戏上。")
        else:
            print("         %s 该窗口会真的吃掉点击（NVIDIA 叠加层、输入法、"
                  "其它程序窗口）。把它挪开或最小化。" % WARN)
    if not fg:
        print("      → 当前配置只在前台点击（REQUIRE_FOREGROUND=%s）；"
              "不在前台时程序会暂停而不是去抢焦点。" % config.REQUIRE_FOREGROUND)
    return game


def check_capture(game: "capture.GameWindow") -> "np.ndarray | None":
    head("3. 截图（三路 GDI 各自能不能拿到画面）")
    methods = [
        ("屏幕 BitBlt", capture._capture_screen),
        ("窗口 DC", capture._capture_window_dc),
        ("PrintWindow", capture._capture_printwindow),
    ]
    best = None
    best_name = ""
    for name, func in methods:
        try:
            img = func(game)
        except Exception as exc:                             # noqa: BLE001
            print("  %s %-12s 异常：%s" % (BAD, name, exc))
            continue
        if img is None:
            print("  %s %-12s 返回空" % (BAD, name))
            continue
        mean = float(img.mean())
        good = mean > config.MIN_FRAME_MEAN
        print("  %s %-12s %dx%d mean=%.1f"
              % (OK if good else WARN, name, img.shape[1], img.shape[0], mean))
        if good and (best is None or mean > float(best.mean())):
            best, best_name = img, name
    if best is None:
        print("  %s 三路都拿不到有效画面 —— 游戏可能最小化了，或被独占全屏挡住。" % BAD)
        return None
    print("      实际使用：%s" % best_name)
    path = os.path.join(config.ROOT, "_diagnose_frame.png")
    cv2.imencode(".png", best)[1].tofile(path)
    print("      已保存当前画面：%s" % path)
    return best


def check_vision(frame: "np.ndarray") -> None:
    head("4. 识别")
    scene = Recognizer().analyze(frame)
    print("  画面判定：%s" % scene.describe())
    print("  处于剧情模式：%s" % scene.in_story)
    if scene.icons:
        for key, hit in sorted(scene.icons.items()):
            print("    %-12s 分数=%.3f 位置=(%d,%d) %dx%d std=%.1f"
                  % (key, hit.score, hit.x, hit.y, hit.w, hit.h, hit.std))
    else:
        print("    没有命中任何图标")
    plan = plan_action(scene, now=1e9)
    if plan is None:
        print("  决策：无动作（这一帧没有可点的目标）")
    else:
        print("  决策：%s → 点 (%d,%d)" % (plan[0].label, plan[1][0], plan[1][1]))


def check_click(game: "capture.GameWindow") -> None:
    head("5. 点击链路（演练，不会真的点）")
    for mode in ("auto", "sendinput", "postmessage"):
        clicker = Clicker(mode=mode, dry_run=True)
        res = clicker.click(game, game.width // 2, game.height // 2)
        print("  模式=%-12s 后端=%-12s ok=%s  %s"
              % (mode, res.method, res.ok, res.detail))
    print("  说明：auto 在前台用真实点击，后台用消息点击（若允许）；")
    print("        被 Windows 拦下时会显示 blocked 并附权限说明。")


def check_input_env() -> None:
    """输入注入是否真的能落到屏幕上。

    这一项是「点不出来」的隐藏杀手：进程被放到一个没有真实输入队列的
    桌面会话里时，SendInput 和 SetCursorPos 都会返回失败，但程序本身
    不自知，你只会看到「点了 100 次什么都没发生」。
    """
    head("0. 输入环境（光标能不能被程序移动）")
    from aps.input import move_to, restore_cursor, screen_size
    import ctypes
    import ctypes.wintypes as wt
    import time

    user32 = ctypes.windll.user32
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    start = (pt.x, pt.y)
    sw, sh = screen_size()
    print("  屏幕 %dx%d  当前光标 (%d,%d)" % (sw, sh, start[0], start[1]))

    tx, ty = sw // 2, sh // 2
    move_to(tx, ty)
    time.sleep(0.05)
    user32.GetCursorPos(ctypes.byref(pt))
    moved = abs(pt.x - tx) <= 2 and abs(pt.y - ty) <= 2
    if moved:
        print("  %s SendInput 移动正常" % OK)
        restore_cursor(wt.POINT(*start))
    else:
        user32.SetCursorPos(tx, ty)
        time.sleep(0.03)
        user32.GetCursorPos(ctypes.byref(pt))
        moved2 = abs(pt.x - tx) <= 2 and abs(pt.y - ty) <= 2
        if moved2:
            print("  %s SendInput 被拒，但 SetCursorPos 单独调用有效" % WARN)
            print("      点击会走 SendInput，而它在这个会话里送不出去；"
                  "建议在正常桌面会话运行。")
            restore_cursor(wt.POINT(*start))
        else:
            print("  %s 光标无法被本进程移动（SendInput 与 SetCursorPos 都失败）" % BAD)
            print("      读到位置 (%d,%d)，期望 (%d,%d)" % (pt.x, pt.y, tx, ty))
            print("      → 本进程所在的桌面会话收不到输入注入。"
                  "请在「你自己的桌面」上直接以管理员身份运行 main.py；")
            print("        如果在远程/无头会话里跑，点击一定不会生效。")


def main() -> int:
    print("AutoPlotSkip 诊断")
    print("  工作目录：%s" % config.ROOT)
    check_input_env()
    check_privilege()
    game = check_window()
    if game is None:
        print()
        print("结论：没找到游戏窗口，后面的检查无法进行。")
        return 1
    frame = check_capture(game)
    if frame is None:
        return 1
    check_vision(frame)
    check_click(game)
    print()
    print(LINE)
    print("诊断完成。若 1 显示权限不足，先解决权限；否则看 4 的分数与决策。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
