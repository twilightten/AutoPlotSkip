# -*- coding: utf-8 -*-
"""从开发文档自带截图里精确抠出"图标类"模板，输出到 assets/tpl/。

文档里的 image2/3/4/5/10/12 虽然就是图标本身，但它们是黑色背景上的白色线稿，
直接拿去匹配会受游戏画面亮度影响。这里改为从**真实截图**（image1.png /
image11.png，均为 1268x714 的 PNG，无压缩失真）按固定 ROI 抠出图标，
同时生成一张"形状掩码"（灰度>25 的像素），供掩码匹配使用。

输出：
    assets/tpl/<key>.png     灰度模板（图标原尺，基准宽 1268）
    assets/tpl/<key>_m.png   掩码（0/255）

用法：python tools/mk_templates2.py
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA = os.path.join(ROOT, "testdata", "doc")
OUT = os.path.join(ROOT, "assets", "tpl")

# key -> (源图, 搜索 ROI(x0,y0,x1,y1) 绝对像素, 说明)
SPECS = {
    "history_btn": ("image1.png", (44, 20, 84, 60), "左上 对话记录"),
    "gear_btn":    ("image1.png", (125, 20, 165, 60), "左上 设置齿轮"),
    "skip_btn":    ("image1.png", (938, 20, 1002, 60), "右上 跳过 ▶|"),
    "eye_btn":     ("image1.png", (1172, 20, 1220, 60), "右上 眼睛 ◉"),
    "chevron":     ("image1.png", (605, 640, 655, 685), "底部 推进箭头 ▼"),
    "big_arrow":   ("image11.png", (855, 466, 900, 502), "选项行 ▶ 语句箭头"),
}


def extract(src: str, roi, key: str):
    im = cv2.imread(os.path.join(MEDIA, src))
    if im is None:
        raise SystemExit("缺少源图 %s" % src)
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    x0, y0, x1, y1 = roi
    sub = g[y0:y1, x0:x1]
    # 图标是亮线稿：取 >60 的最大连通域，再向外扩 1px
    m = (sub > 60).astype(np.uint8)
    n, _lab, st, _c = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        raise SystemExit("%s: ROI 内没找到图标" % key)
    best = max(range(1, n), key=lambda i: st[i][4])
    bx, by, bw, bh, _a = st[best]
    # 合并同一 ROI 里与主块水平/垂直相邻的其他块（比如 ▶ 后面那根竖线）
    x0b, y0b, x1b, y1b = bx, by, bx + bw, by + bh
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a < 8:
            continue
        if not (x + w < bx - 6 or x > bx + bw + 6 or y + h < by - 6 or y > by + bh + 6):
            x0b, y0b = min(x0b, x), min(y0b, y)
            x1b, y1b = max(x1b, x + w), max(y1b, y + h)
    pad = 2
    x0b = max(0, x0b - pad); y0b = max(0, y0b - pad)
    x1b = min(sub.shape[1], x1b + pad); y1b = min(sub.shape[0], y1b + pad)
    tpl = sub[y0b:y1b, x0b:x1b]
    mask = np.where(tpl > 25, 255, 0).astype(np.uint8)
    return tpl, mask, (x0 + x0b, y0 + y0b, x1b - x0b, y1b - y0b)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    tiles = []
    for key, (src, roi, desc) in SPECS.items():
        tpl, mask, abs_box = extract(src, roi, key)
        cv2.imwrite(os.path.join(OUT, key + ".png"), tpl)
        cv2.imwrite(os.path.join(OUT, key + "_m.png"), mask)
        print("%-12s %-12s %2dx%-2d  绝对位置 %s  %s"
              % (key, src, tpl.shape[1], tpl.shape[0], abs_box, desc))
        show = cv2.resize(tpl, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
        show = cv2.cvtColor(show, cv2.COLOR_GRAY2BGR)
        tiles.append(cv2.copyMakeBorder(show, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=(0, 200, 0)))
    h = max(t.shape[0] for t in tiles)
    w = sum(t.shape[1] for t in tiles)
    sheet = np.full((h, w, 3), 30, np.uint8)
    x = 0
    for t in tiles:
        sheet[0:t.shape[0], x:x + t.shape[1]] = t
        x += t.shape[1]
    cv2.imwrite(os.path.join(ROOT, "build", "_tpl_sheet.png"), sheet)
    print("拼图 -> build/_tpl_sheet.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
