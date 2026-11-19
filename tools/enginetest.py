# -*- coding: utf-8 -*-
"""端到端验证：开一个标题为「崩坏：星穹铁道」的窗口，里面贴文档截图，
然后走**真机那条路**（找窗口 → GDI 截图 → 识别 → 触发器 → 引擎循环 → 日志）。

这里**真的把 `Engine.run()` 跑起来**，主循环、各自冷却、节流、日志回调、
停止请求全部覆盖到。强制 dry_run，不会动鼠标。
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np
import tkinter as tk

from aps import config
from aps.capture import ScreenGrabber
from aps.engine import Engine

MEDIA = os.path.join(config.ROOT, "testdata", "doc")

# 每一步要等多久：必须盖过最长的那条冷却，否则会出现「上一步的冷却还没
# 走完，这一步的触发器全都还没 ready」的假失败（曾经就是这样翻车的）。
STEP_WAIT = max(config.LOOP_INTERVAL, config.SKIP_COOLDOWN,
                config.CONFIRM_COOLDOWN, config.OPTION_COOLDOWN,
                config.CLOSE_COOLDOWN, config.PANEL_COOLDOWN) + 0.30

# (截图, 期望动作)
STEPS = [
    ("image1.png", "skip"),
    ("image6.jpeg", "confirm"),
    ("image11.png", "arrow"),
    ("image9.png", "chevron"),
    ("image15.jpeg", "option"),
    ("image13.jpeg", "close"),
    ("image2.png", "none"),
]

# 期望动作 -> 日志里应该出现的关键字（校验"点对了东西"，而不只是"点了"）
KIND_KEYWORD = {
    "skip": "跳过按钮 ▶|",
    "confirm": "跳过确认 ✔",
    "arrow": "语句箭头 →",
    "chevron": "底部推进 ▼",
    "option": "剧情选项 ▭",
    "close": "关闭面板 ✕",
}


def show(label: tk.Label, root: tk.Tk, name: str, cw: int, ch: int) -> None:
    img = cv2.imdecode(np.fromfile(os.path.join(MEDIA, name), dtype=np.uint8),
                       cv2.IMREAD_COLOR)
    k = min(cw / img.shape[1], ch / img.shape[0])
    img = cv2.resize(img, (int(img.shape[1] * k), int(img.shape[0] * k)),
                     interpolation=cv2.INTER_AREA)
    canvas = np.zeros((ch, cw, 3), np.uint8)
    canvas[:] = (18, 18, 22)
    canvas[:img.shape[0], :img.shape[1]] = img
    ok, buf = cv2.imencode(".png", canvas)
    photo = tk.PhotoImage(data=buf.tobytes())
    label.configure(image=photo)
    label.image = photo
    for _ in range(4):
        root.update()
    time.sleep(0.25)
    root.update()


def _pin_window(grabber, root: tk.Tk) -> None:
    """把 grabber 钉在 tk 假窗口上，避免抓到机器上真游戏窗口。

    机器上真开着游戏时，`find_game_window(allow_any=True)` 会挑面积最大的
    那个窗口（真游戏 1920x1080），测试就变成在看真游戏画面。
    这里直接把窗口固定住，并让 `ensure_window()` 不再重找。
    """
    from aps.capture import GameWindow, _rects

    hwnd = root.winfo_id()
    left, top, cw, ch = _rects(hwnd)
    grabber.window = GameWindow(hwnd, "崩坏：星穹铁道", left, top, cw, ch)
    grabber.ensure_window = lambda *, force=False: grabber.window


def main() -> int:
    root = tk.Tk()
    root.title("崩坏：星穹铁道")
    root.geometry("1280x800+80+80")
    label = tk.Label(root, bd=0, highlightthickness=0, bg="black")
    label.pack(fill="both", expand=True)
    root.update()
    cw, ch = label.winfo_width(), label.winfo_height()

    logs: list[tuple[str, str, str]] = []
    lock = threading.Lock()

    def log(tag: str, text: str, level: str = "info") -> None:
        with lock:
            logs.append((tag, text, level))

    engine = Engine(log, grabber=ScreenGrabber(allow_any=True), dry_run=True)
    # 把截图器钉死在这个 tk 假窗口上。否则机器上真开着游戏时，
    # find_game_window(allow_any=True) 会挑面积最大的那个窗口（真游戏 1920x1080），
    # 测试就变成在看真游戏画面，前面几步全部假失败。
    _pin_window(engine.grabber, root)
    engine.reset()
    thread = threading.Thread(target=engine.run, name="aps-enginetest", daemon=True)

    # 先让窗口就位再起引擎，否则引擎第一帧可能在窗口还没出现时抓空
    show(label, root, "image1.png", cw, ch)
    thread.start()

    fails = 0
    for name, expect in STEPS:
        frames0 = engine.stats["frames"]
        with lock:
            before = len(logs)          # 从这一刻起的所有日志都属于这一步
        show(label, root, name, cw, ch)

        # 等引擎真的处理到新画面（至少 2 帧）
        deadline = time.time() + 3.0
        while engine.stats["frames"] < frames0 + 2 and time.time() < deadline:
            time.sleep(0.02)
        with lock:
            settled = len(logs)         # 新画面已被处理过一次的位置
        time.sleep(STEP_WAIT)           # 留够一个循环 + 最长冷却

        with lock:
            acts_all = [t for k, t, lv in logs[before:] if k == "点击"]
            acts_settled = [t for k, t, lv in logs[settled:] if k == "点击"]

        if expect == "none":
            # 只要求"新画面被看到之后"不再有动作；切换瞬间残留的那一次点击
            # 属于上一张图，不算在这一步头上。
            ok = not acts_settled
            got = acts_settled[-1] if acts_settled else ""
        else:
            word = KIND_KEYWORD.get(expect, expect)
            hit = [t for t in acts_all if word in t]
            ok = bool(hit)
            got = hit[0] if hit else (acts_all[-1] if acts_all else "")
        fails += 0 if ok else 1
        print("%s %-12s 期望动作=%-8s 本步日志=%s"
              % ("[OK]  " if ok else "[FAIL]", name, expect, got or "（无动作）"))

    engine.stop()
    thread.join(timeout=3.0)
    root.destroy()

    with lock:
        total = len(logs)
    print("-" * 70)
    print("日志共 %d 条；最后 8 条：" % total)
    for tag, text, level in logs[-8:]:
        print("  [%s] %s" % (tag, text))
    print("失败 %d 组；引擎线程已停：%s" % (fails, not thread.is_alive()))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
