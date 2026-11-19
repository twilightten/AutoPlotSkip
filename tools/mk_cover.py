# -*- coding: utf-8 -*-
"""生成启动器中间那张默认封面图（assets/cover.png）。

只是给个不难看的占位：深蓝渐变 + 稀疏星点 + 标题。想换成自己的图，
**直接把图片覆盖到 assets/cover.png 就行**，不用改代码（也可以在
aps/config.py 的 COVER_PATH 里指到别的路径）。

    python tools/mk_cover.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# 比例对齐启动器里那块横幅画布（1040 宽窗口下实测 831x200，约 4.15:1），
# 这样默认状态下图片正好铺满、四周不会露出模糊垫底。
W, H = 1600, 300
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "assets", "cover.png")

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
)


def _font(size: int):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def main() -> int:
    # 1) 斜向渐变底：左上更深，右下透一点青
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    t = (xx / W) * 0.55 + (yy / H) * 0.45
    top = np.array([16, 18, 30], np.float32)
    bot = np.array([28, 46, 74], np.float32)
    base = top[None, None, :] + (bot - top)[None, None, :] * t[:, :, None]
    # 右上角一团柔光
    dx, dy = (xx - W * 0.78) / (W * 0.55), (yy - H * 0.18) / (H * 0.9)
    glow = np.exp(-(dx * dx + dy * dy) * 2.2)[:, :, None]
    base = base + glow * np.array([26, 22, 12], np.float32)
    img = np.clip(base, 0, 255).astype(np.uint8)

    # 2) 星点：固定种子，保证每次生成一致
    rng = np.random.default_rng(20261025)
    for _ in range(260):
        x = int(rng.uniform(0, W - 1))
        y = int(rng.uniform(0, H - 1))
        r = int(rng.choice([0, 0, 0, 1]))
        a = float(rng.uniform(0.25, 0.85))
        color = np.array([235, 240, 255], np.float32)
        y0, y1 = max(0, y - r), min(H, y + r + 1)
        x0, x1 = max(0, x - r), min(W, x + r + 1)
        patch = img[y0:y1, x0:x1].astype(np.float32)
        img[y0:y1, x0:x1] = (patch * (1 - a) + color * a).astype(np.uint8)

    im = Image.fromarray(img).convert("RGBA")

    # 3) 一道细金线，暗示「轨道」
    line = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(line)
    d.line([(-40, H * 0.86), (W * 0.62, H * 0.30)], fill=(224, 190, 110, 46), width=3)
    im = Image.alpha_composite(im, line)

    # 4) 标题
    d = ImageDraw.Draw(im)
    title = "崩坏：星穹铁道"
    sub = "自动剧情跳过"
    f1, f2 = _font(int(H * 0.26)), _font(int(H * 0.115))
    x, y = int(W * 0.055), int(H * 0.22)
    d.text((x + 2, y + 2), title, font=f1, fill=(0, 0, 0, 90))
    d.text((x, y), title, font=f1, fill=(238, 241, 250, 255))
    d.text((x + 4, y + int(H * 0.30)), sub, font=f2, fill=(150, 200, 235, 235))
    d.line([(x + 4, y + int(H * 0.28)), (x + 4 + int(W * 0.075), y + int(H * 0.28))],
           fill=(224, 190, 110, 200), width=3)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    im.convert("RGB").save(OUT, "PNG")
    print("saved", OUT, "%dx%d" % (W, H))
    return 0


if __name__ == "__main__":
    sys.exit(main())
