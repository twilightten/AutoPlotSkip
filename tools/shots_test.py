# -*- coding: utf-8 -*-
"""用户真机截图的回归（testdata/shots/）。这些图对应实机反馈过的四个 bug：

  1. chat_panel.png  面板底部「对话结束」→ 必须点中右上角真正的 ✕（(1740,241)）。
     旧实现取"面积最大的暗连通域"，从 ✕ 的四个笔画里随便挑一个，点偏 11~12px 全落空。
  2. chat_panel2.png 「是否跳过当前段落对话」弹窗 → 必须点「确认」胶囊，不能干看着。
     旧实现把弹窗长宽比上限卡在 2.80，真机弹窗是 3.64，直接被拒。
  3. 普通剧情对话（image13/15 那种浅色面板 + 左上角常规图标）不能被当成聊天面板，
     否则会一直点右上角 ✕。判据：左上角还有对话记录/设置齿轮/眼睛 时不做面板动作。
  4. 面板 ✕ 必须落在面板内部；面板圆角不能被当成 ✕。

    python tools/shots_test.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import cv2                                   # noqa: E402
import numpy as np                           # noqa: E402

from aps.engine import kind_of               # noqa: E402
from aps.triggers import build_triggers, plan_action  # noqa: E402
from aps.vision import Recognizer            # noqa: E402

SHOTS = os.path.join(ROOT, "testdata", "shots")
MEDIA = os.path.join(ROOT, "testdata", "doc")


def load(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def case_file(name, in_docs=False):
    base = MEDIA if in_docs else SHOTS
    path = os.path.join(base, name)
    return path if os.path.exists(path) else None


# (文件, 在文档目录里?, 期望 kind, 期望点击点容差)
CASES = [
    ("chat_panel.png", False, "close", (1740, 241), 14,
     "真机聊天面板：点中右上角真 ✕"),
    ("chat_panel2.png", False, "confirm", (1168, 672), 14,
     "真机跳过确认弹窗：点中「确认」胶囊"),
    ("chat_option_1.png", False, "option", (1451, 720), 16,
     "真机选项面板（贴右边缘，内外亮度差只有 39）：必须认成面板并点选项"),
    ("chat_no_feature.png", False, "option", (1451, 720), 16,
     "真机聊天面板：两条白底选项，点其中一条"),
    ("image13.jpeg", True, "close", (1178, 134), 16,
     "文档面板：没有 ✕ 时退回面板右上角兜底点"),
    ("image15.jpeg", True, "option", (958, 476), 16,
     "文档面板：两条白底选项，随便点一个"),
    ("scene_no_feature.png", False, "none", None, 0,
     "雪山夜景：整片雪坡在灰度上连成一大块，绝不能当成跳过确认弹窗"),
]


def main() -> int:
    rec = Recognizer()
    triggers = build_triggers()
    fails = 0
    print("=" * 96)
    print("AutoPlotSkip 真机截图回归（testdata/shots）")
    print("=" * 96)

    for name, in_docs, want_kind, want_xy, tol, note in CASES:
        path = case_file(name, in_docs)
        if path is None:
            print("[跳过] %-16s 缺图" % name)
            continue
        scene = rec.analyze(load(path))
        plan = plan_action(scene, triggers=triggers)
        if plan is None:
            got_kind, got_xy = "none", None
        else:
            trig, got_xy = plan
            got_kind = kind_of(trig.name)
        dist = (max(abs(got_xy[0] - want_xy[0]), abs(got_xy[1] - want_xy[1]))
                if (got_xy and want_xy) else (0 if got_kind == want_kind else 9999))
        ok = got_kind == want_kind and dist <= tol
        fails += 0 if ok else 1
        print("[%s] %-16s %-30s -> %-8s %s%s"
              % ("PASS" if ok else "FAIL", name, scene.describe()[:30],
                 got_kind, got_xy or "-",
                 "" if ok else "  期望 %s@%s(±%d)" % (want_kind, want_xy, tol)))
        if ok:
            print("        %s" % note)

    # 三个反向断言：普通剧情画面不能被判成聊天面板
    print("-" * 96)
    for name in ("image1.png", "image9.png", "image11.png"):
        path = case_file(name, True)
        if path is None:
            continue
        scene = rec.analyze(load(path))
        ok = scene.panel is None and scene.close_x is None
        fails += 0 if ok else 1
        print("[%s] %-16s 有左上角常规图标 -> 不当聊天面板（panel=%s close_x=%s）"
              % ("PASS" if ok else "FAIL", name, scene.panel, scene.close_x))

    print("-" * 96)
    print("用例 %d 组，失败 %d 组" % (len(CASES) + 3, fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
