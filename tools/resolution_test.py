# -*- coding: utf-8 -*-
"""分辨率/缩放回归：把开发文档的截图按不同宽度等比缩放，
逐张检查「识别 → 触发器」是否仍然正确。

真机上客户的窗口宽可能是 1024 也可能是 3440（带鱼屏），
模板是按 1268 宽抠的，所以这条回归专门盯缩放鲁棒性。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

from aps import config
from aps.engine import kind_of
from aps.triggers import build_triggers, plan_action
from aps.vision import Recognizer

MEDIA = os.path.join(config.ROOT, "testdata", "doc")

CASES = {
    "image1.png": "skip",       # 普通对话：右上角有跳过 ▶|
    "image6.jpeg": "confirm",   # 跳过确认弹窗
    "image11.png": "arrow",     # 白大箭头 → 的语句
    "image9.png": "chevron",    # 没跳过没箭头 → 底部 ▼
    "image15.jpeg": "option",   # 聊天面板 + 选项
    "image13.jpeg": "close",    # 对话结束 → ✕
    "image2.png": "none",       # 单张图标，不是剧情画面
    "image10.png": "none",
    "image12.png": "none",
    "image17.png": "none",
}

WIDTHS = (1024, 1280, 1366, 1440, 1600, 1920, 2560, 3440)


def main() -> int:
    rec = Recognizer()
    triggers = build_triggers()
    bad = 0
    for width in WIDTHS:
        cells = []
        for name, expect in CASES.items():
            img = cv2.imdecode(np.fromfile(os.path.join(MEDIA, name), dtype=np.uint8),
                               cv2.IMREAD_COLOR)
            if img is None:
                print("!! 读不到", name)
                return 2
            k = width / img.shape[1]
            img = cv2.resize(img, (int(img.shape[1] * k), int(img.shape[0] * k)),
                             interpolation=cv2.INTER_AREA)
            scene = rec.analyze(img)
            plan = plan_action(scene, triggers=triggers)
            got = kind_of(plan[0].name) if plan is not None else "none"
            good = got == expect
            if not good:
                bad += 1
            cells.append("%s:%s" % (name.split(".")[0].replace("image", "i"),
                                    got if good else "✗" + got))
        print("%5d  %s" % (width, "  ".join(cells)))

    print("-" * 70)
    print("分辨率 %d 个，用例 %d 组，失败 %d 组"
          % (len(WIDTHS), len(WIDTHS) * len(CASES), bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
