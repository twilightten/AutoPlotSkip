# -*- coding: utf-8 -*-
"""离线自检：把开发文档里的截图喂给识别器 + 触发器表，核对输出是否符合作者预期。

    python tools/selftest.py

不截图、不点击，纯离线，用来回归「识别 → 决策」这两层。
用的是引擎真实使用的触发器表（triggers.build_triggers），不是另写一套判断。
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

from aps import config                      # noqa: E402
from aps.engine import kind_of              # noqa: E402
from aps.triggers import build_triggers, plan_action  # noqa: E402
from aps.vision import Recognizer           # noqa: E402

MEDIA = os.path.join(ROOT, "testdata", "doc")

# 触发器名 → 对外事件类型（自检里用 kind 断言，比断言中文名稳）
EXPECT_KIND = {
    "跳过确认": "confirm",
    "跳过按钮": "skip",
    "语句箭头": "arrow",
    "剧情选项": "option",
    "关闭面板": "close",
    "推进对话": "panel",
    "底部推进": "chevron",
}

# (文件, 期望动作 kind, 期望动作标签里应含字样, 备注)
CASES = [
    ("image1.png",   "skip",    "跳过",   "有跳过按钮 ▶| → 第一时间点跳过"),
    ("image11.png",  "arrow",   "箭头",   "无跳过按钮，有白色大箭头 → 点它"),
    ("image9.png",   "chevron", "▼",      "无跳过无箭头 → 点底部金色 ▼"),
    ("image6.jpeg",  "confirm", "确认",   "弹出「是否跳过当前段落对话」→ 点确认"),
    ("image15.jpeg", "option",  "选项",   "聊天面板出现选项 → 随便点一个"),
    ("image13.jpeg", "close",   "✕",      "面板底部「对话结束」→ 点右上角 ✕"),
]


def load(name: str):
    return cv2.imdecode(np.fromfile(os.path.join(MEDIA, name), dtype=np.uint8),
                        cv2.IMREAD_COLOR)


def main() -> int:
    rec = Recognizer()
    triggers = build_triggers()
    fails = 0
    print("=" * 96)
    print("AutoPlotSkip 离线识别自检 —— 数据源：AutoPlotSkip开发文档.docx 内嵌截图")
    print("=" * 96)
    for name, want_kind, want_word, note in CASES:
        img = load(name)
        if img is None:
            print("[跳过] %-12s 读不到文件" % name)
            fails += 1
            continue
        scene = rec.analyze(img)
        plan = plan_action(scene, triggers=triggers)
        if plan is None:
            got_kind, got_label = "none", "（无动作）"
        else:
            trig, (x, y) = plan
            got_kind, got_label = kind_of(trig.name), "%s %s" % (trig.label, "→(%d,%d)" % (x, y))
        ok = got_kind == want_kind and want_word in got_label
        mark = "PASS" if ok else "FAIL"
        if not ok:
            fails += 1
        print("[%s] %-12s %-46s -> %-8s %s"
              % (mark, name, scene.describe()[:46], got_kind, got_label))
        if not ok:
            print("        期望: kind=%s 标签含「%s」" % (want_kind, want_word))
        else:
            print("        %s" % note)

    # 反向用例：单张图标裁图不是剧情画面，必须什么都不点
    print("-" * 96)
    for name in ("image17.png", "image18.png", "image19.png", "image2.png",
                 "image10.png", "image12.png"):
        img = load(name)
        if img is None:
            continue
        scene = rec.analyze(img)
        plan = plan_action(scene, triggers=triggers)
        ok = plan is None and not scene.in_story
        if not ok:
            fails += 1
        print("[%s] %-12s %-46s -> %s"
              % ("PASS" if ok else "FAIL", name, scene.describe()[:46],
                 "无动作" if plan is None else kind_of(plan[0].name)))

    print("-" * 96)
    print("模板 %d 个，触发器 %d 个，用例 %d 组，失败 %d 组"
          % (len(config.TEMPLATES), len(triggers), len(CASES), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
