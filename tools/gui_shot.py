# -*- coding: utf-8 -*-
"""截一张启动器界面图（README 用）。

    python tools/gui_shot.py            # 输出 docs/preview.png

会开一个假游戏窗口，让浮层自动贴上去，再截图，这样界面图和真机一致。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from aps import config
from aps.gui import App

SIZE = "880x580"

# 截图前把底部提示行还原成真实状态，别让演示日志的残留看起来像界面元素
HINT = "暂未找到「崩坏：星穹铁道」窗口：启动游戏后浮层会自动出现。"


def _pump(app, times: int = 30, delay: float = 0.02) -> None:
    for _ in range(times):
        app.update()
        time.sleep(delay)


def _ensure_visible(app) -> None:
    """窗口没真到前台时 ImageGrab 抓到的是一层黑罩（曾经截出全黑图）。

    系统偶尔会把我们的小窗最小化（本机实测过），所以这里反复拉起来直到
    `winfo_viewable()` 为真，再等一会儿让 tk 把内容画完。
    """
    for _ in range(40):
        if app.winfo_viewable() and app.state() == "normal":
            break
        app.deiconify()
        app.state("normal")
        app.lift()
        app.attributes("-topmost", True)
        _pump(app, 10)
    app.lift()
    app.attributes("-topmost", True)
    _pump(app, 40)
    time.sleep(0.4)
    _pump(app, 20)


def main() -> int:
    from PIL import ImageGrab

    app = App()
    app.geometry(SIZE + "+80+40")
    _ensure_visible(app)

    # 走一遍日志清洗，确认界面上只留点击类
    app._log("状态", "画面中没有剧情特征，等待剧情开始…", "info")
    app._log("说明", "有可点目标时的循环节奏", "info")
    app._log("剧情模式", "检测到剧情特征（跳过按钮）", "ok")
    app._log("点击", "跳过按钮 ▶|", "action")
    app._log("点击", "跳过确认 ✔", "warn")
    app._log("点击", "关闭面板 ✕", "action")
    app.hint.configure(text=HINT)
    _pump(app, 12, 0.03)

    out = os.path.join(config.ROOT, "docs", "preview.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    img = None
    for attempt in range(6):
        _ensure_visible(app)
        x, y = app.winfo_rootx(), app.winfo_rooty()
        w, h = app.winfo_width(), app.winfo_height()
        shot = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        arr = np.array(shot.convert("RGB"))
        # 全黑 / 全是同一个颜色 = 抓到了遮罩，重来
        if float(arr.mean()) > 8.0 and int(arr.std()) > 4:
            img = shot
            break
        print("第 %d 次截图是黑的（mean=%.1f），重试…" % (attempt + 1, float(arr.mean())))

    if img is None:
        print("截图失败：窗口一直没能到前台")
        app.overlay.detach()
        app.destroy()
        return 1

    img.save(out)
    print("saved", out, img.size)

    app.overlay.detach()
    app.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
