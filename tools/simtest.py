# -*- coding: utf-8 -*-
"""离线联调：把开发文档里的截图喂给引擎，走完整条「截图→识别→触发器调度→点击」链路。

和 selftest.py 的区别：
  selftest.py  只测识别层（Recognizer.analyze 输出对不对）
  simtest.py   连引擎一起测（触发器优先级、各自冷却、日志、统计）

引擎用 dry_run=True 跑，不会真的往屏幕上点，但决策与日志和真机一致。
运行：python tools/simtest.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from aps.capture import GameWindow  # noqa: E402
from aps.engine import Engine  # noqa: E402

MEDIA = os.path.join(ROOT, "testdata", "doc")

# (文件名, 期望触发器名, 说明)
CASES = [
    ("image1.png",   "跳过按钮", "有跳过按钮 ▶| → 立刻跳"),
    ("image6.jpeg",  "跳过确认", "弹出确认框 → 点确认"),
    ("image11.png",  "语句箭头", "无跳过，有白色大箭头 → 点它"),
    ("image9.png",   "底部推进", "无跳过无箭头 → 点底部金色 ▼"),
    ("image15.jpeg", "剧情选项", "聊天面板出现选项 → 随便点一个"),
    ("image13.jpeg", "关闭面板", "面板底部「对话结束」→ 点右上角 ✕"),
    ("image2.png",   None,       "纯图标截图，不该有任何动作"),
]


class FakeGrabber:
    """假截图器：直接返回给定的静态帧，不碰真实屏幕。"""

    def __init__(self, window: GameWindow, frame):
        self.window = window
        self.frame = frame
        self.last_error = None
        self.calls = 0

    def ensure_window(self, *, force: bool = False):
        return self.window

    def grab(self):
        self.calls += 1
        return self.frame


def load(name: str):
    path = os.path.join(MEDIA, name)
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def run_case(name: str, frame, expect: str | None):
    h, w = frame.shape[:2]
    window = GameWindow(0, name, 0, 0, w, h)
    logs: list[str] = []

    def log(tag, text, level="info"):
        logs.append("%s %s" % (tag, text))

    eng = Engine(log, grabber=FakeGrabber(window, frame), dry_run=True)
    fired = eng.tick()
    got = fired.name if fired is not None else None
    ok = got == expect

    detail = "触发=%s" % (got or "（无）")
    if fired is not None:
        detail += "  点击=(%d,%d)" % (eng.last_action.x, eng.last_action.y)

    # 冷却检查：紧接着再跑一帧，同一个触发器应当被自己的间隔拦住
    if fired is not None:
        fired2 = eng.tick()
        if fired2 is not None and fired2.name == fired.name:
            ok = False
            detail += "  [冷却失效：同一触发器连续触发]"

    # 优先级检查：同一帧里出现多个目标时，只允许优先级最高的那个动手
    if fired is not None:
        highest = min([t for t in eng.dispatcher.triggers
                       if t.match(eng.last_scene, None) is not None] or
                      [fired], key=lambda t: t.priority)
        if highest.name != fired.name:
            ok = False
            detail += "  [优先级错误：应当先跑 %s]" % highest.name

    return ok, detail, logs


def main() -> int:
    if not os.path.isdir(MEDIA):
        print("找不到文档图片目录：", MEDIA)
        return 2

    fail = 0
    print("=" * 78)
    print("AutoPlotSkip 离线联调 —— 引擎全链路（dry_run，不真实点击）")
    print("=" * 78)
    for name, expect, note in CASES:
        img = load(name)
        if img is None:
            print("[SKIP] %-13s 读不到" % name)
            continue
        ok, detail, logs = run_case(name, img, expect)
        print("[%s] %-13s 期望 %-8s %s"
              % ("PASS" if ok else "FAIL", name, expect or "无动作", detail))
        print("        %s" % note)
        for line in logs[:4]:
            print("          · %s" % line)
        if not ok:
            fail += 1

    # 触发器表自检：优先级必须严格递增，名字不能重复
    eng = Engine(lambda *a, **k: None, grabber=FakeGrabber(None, np.zeros((8, 8, 3), np.uint8)),
                 dry_run=True)
    prios = [t.priority for t in eng.dispatcher.triggers]
    names = [t.name for t in eng.dispatcher.triggers]
    if prios != sorted(prios):
        print("[FAIL] 触发器优先级不是升序：", prios)
        fail += 1
    else:
        print("[PASS] 触发器优先级升序：%s"
              % " < ".join("%d:%s" % (t.priority, t.name)
                           for t in eng.dispatcher.triggers))
    if len(set(names)) != len(names):
        print("[FAIL] 触发器重名：", names)
        fail += 1

    print("-" * 78)
    print("用例 %d 组，失败 %d 组" % (len(CASES), fail))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
