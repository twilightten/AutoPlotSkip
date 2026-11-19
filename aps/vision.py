# -*- coding: utf-8 -*-
"""画面识别：把一帧截图翻译成"现在该点哪里"。

分两层，和 BetterGI 的 RecognitionObject 思路一致：

  1. 图标层（TemplateMatch）：跳过按钮 ▶|、剧情眼睛 ◉、对话记录、设置齿轮、
     底部推进 ▼、语句大箭头 →。这些是"亮线稿 + 深底"，用高通滤波后的
     灰度图做 TM_CCOEFF_NORMED。
  2. 结构层（ShapeCheck）：跳过确认弹窗、手机聊天面板、白底选项条、
     「对话结束」文字带、面板右上角 ✕。这些没有稳定的图标模板，
     只能按"浅色大块 / 连续亮带 / 暗色文字簇"的几何特征判断。

识别结果统一装进 `Scene`，引擎只读 Scene 做决策。

关于"纹理门槛"（TEXTURE_RATIO）：
  归一化相关系数在"几乎无纹理"的小 patch 上会失真——整块纯黑的区域
  也能拿到 0.99 分。所以匹配成功后还要再验一次：命中区域的灰度标准差
  必须达到模板自身标准差的 30%。实测这一条能干掉 image13/15 上
  big_arrow 的 0.789 假命中（hp 标准差 0.95 vs 模板 104.8）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import config


# --------------------------------------------------------------------- 模板
class TemplateSet:
    """加载 assets/tpl 下的模板与掩码，并按当前窗口宽度生成缩放缓存。"""

    def __init__(self, template_dir: str = config.TEMPLATE_DIR):
        self.dir = template_dir
        self.raw: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.hp_std: dict[str, float] = {}
        self._cache: dict[tuple[str, float], tuple[np.ndarray, np.ndarray]] = {}
        for key in config.TEMPLATES:
            name = config.TEMPLATES[key][0]
            path = os.path.join(template_dir, name)
            mask_path = os.path.join(template_dir, name.replace(".png", "_m.png"))
            if not os.path.exists(path):
                continue
            gray = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if gray is None:
                continue
            mask = None
            if os.path.exists(mask_path):
                mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
            if mask is not None and mask.shape != gray.shape:
                mask = None
            self.raw[key] = (gray, mask)
            self.hp_std[key] = float(_highpass(gray).std()) or 1.0

    def __contains__(self, key: str) -> bool:
        return key in self.raw

    def get(self, key: str, scale: float) -> tuple[np.ndarray, np.ndarray | None]:
        key2 = (key, round(scale, 4))
        hit = self._cache.get(key2)
        if hit is not None:
            return hit
        gray, mask = self.raw[key]
        if abs(scale - 1.0) > 1e-3:
            size = (max(1, int(round(gray.shape[1] * scale))),
                    max(1, int(round(gray.shape[0] * scale))))
            interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            gray = cv2.resize(gray, size, interpolation=interp)
            if mask is not None:
                mask = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
        val = (gray, mask)
        self._cache[key2] = val
        return val


# --------------------------------------------------------------------- 工具
def _highpass(gray: np.ndarray, sigma: float = 11.0) -> np.ndarray:
    """高通滤波：去掉大范围的明暗背景，只留线稿，抗屏幕亮度差异。"""
    g = gray.astype(np.float32)
    blur = cv2.GaussianBlur(g, (0, 0), sigma)
    return g - blur


def _roi(img: np.ndarray, rel: tuple[float, float, float, float]):
    h, w = img.shape[:2]
    x0, y0, x1, y1 = rel
    return (max(0, int(x0 * w)), max(0, int(y0 * h)),
            min(w, int(x1 * w)), min(h, int(y1 * h)))


def _bright_components(gray: np.ndarray, thresh: int, min_area_ratio: float,
                       limit: int = 0):
    """列出所有够大的"亮块"，按面积从大到小，返回 [(x, y, w, h, area_ratio), ...]。

    比只取最大的那一块可靠：聊天面板不一定是画面里最大的一片亮区
    （用户三月七那张图里，右侧浅绿天空连成的亮块一度比面板还大）。
    """
    mask = (gray > thresh).astype(np.uint8)
    if not mask.any():
        return []
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    num, _labels, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    if num <= 1:
        return []
    h, w = gray.shape[:2]
    total = float(w * h)
    out = []
    for i in range(1, num):
        x, y, cw, ch, area = (int(v) for v in stats[i])
        ratio = area / total
        if ratio < min_area_ratio:
            continue
        out.append((x, y, cw, ch, ratio))
    out.sort(key=lambda c: c[4], reverse=True)
    return out[:limit] if limit > 0 else out


def _largest_bright_component(gray: np.ndarray, thresh: int, min_area_ratio: float):
    """在灰度图里找面积最大的"亮块"，返回 (x, y, w, h, area_ratio) 或 None。"""
    found = _bright_components(gray, thresh, min_area_ratio, limit=1)
    return found[0] if found else None


def _runs(flags: np.ndarray, gap: int = 0) -> list[tuple[int, int]]:
    """把布尔数组切成连续 True 的区间 [(start, end_exclusive), ...]。

    gap>0 时，间隔不超过 gap 行的两段会被合并（用于跨过选项条里的文字行）。
    """
    out: list[tuple[int, int]] = []
    n = len(flags)
    i = 0
    while i < n:
        if flags[i]:
            j = i
            while j < n and flags[j]:
                j += 1
            if out and gap and (i - out[-1][1]) <= gap:
                out[-1] = (out[-1][0], j)
            else:
                out.append((i, j))
            i = j
        else:
            i += 1
    return out


def _longest_run(row: np.ndarray) -> int:
    """一行布尔数组里最长连续 True 的长度。"""
    if not row.any():
        return 0
    d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return int((ends - starts).max())


def _panel_bg(gray: np.ndarray, panel: Panel) -> float:
    """取面板内部的底色中位亮度（避开四边，防止被圆角/边框拉低）。"""
    x0 = panel.x + int(panel.w * 0.25)
    x1 = panel.x + int(panel.w * 0.75)
    y0 = panel.y + int(panel.h * 0.30)
    y1 = panel.y + int(panel.h * 0.70)
    x1 = min(gray.shape[1], max(x0 + 1, x1))
    y1 = min(gray.shape[0], max(y0 + 1, y1))
    sub = gray[y0:y1, x0:x1]
    if sub.size == 0:
        return 0.0
    return float(np.median(sub))


# --------------------------------------------------------------------- 结果
@dataclass
class Hit:
    key: str
    x: int          # 窗口内坐标（像素）
    y: int
    w: int
    h: int
    score: float
    std: float = 0.0

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2


@dataclass
class Panel:
    x: int
    y: int
    w: int
    h: int

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def rel_point(self, rx: float, ry: float) -> tuple[int, int]:
        return (self.x + int(self.w * rx), self.y + int(self.h * ry))


@dataclass
class Dialog:
    """跳过确认弹窗：亮块本身 + 算出来的「确认」按钮位置。"""

    x: int
    y: int
    w: int
    h: int
    confirm: tuple[int, int] | None = None   # 点不到胶囊时留空，由触发器走兜底

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def bottom(self) -> int:
        return self.y + self.h


@dataclass
class Scene:
    width: int = 0
    height: int = 0
    icons: dict[str, Hit] = field(default_factory=dict)
    dialog: Dialog | None = None    # 跳过确认弹窗
    panel: Panel | None = None      # 手机聊天面板
    options: list[tuple[int, int, int, int]] = field(default_factory=list)
    chat_end: bool = False          # 面板底部出现「对话结束」
    close_x: tuple[int, int] | None = None

    # ---- 语义
    @property
    def in_story(self) -> bool:
        """文档：出现对话记录/设置齿轮/眼睛，或跳过按钮，即处于剧情模式。

        两个例外，都是"剧情标志被盖住了，但显然还在剧情里"：
          - **跳过确认弹窗**：它会压暗并盖住整条顶部图标栏，这会儿一个图标
            都匹配不到。但它必须找到白色胶囊按钮才算数 —— 只是"画面里有块
            亮矩形"是不作数的（现实场景里雪山、白墙、亮天空都能长篇大论）。
          - **手机聊天面板**：聊天模式本来就没有左上/右上那套剧情图标，
            判据换成"浅色卡片面板 + 四周压暗"，见 detect_panel。
        """
        return (any(k in self.icons for k in config.STORY_MARKERS)
                or self.panel is not None
                or (self.dialog is not None and self.dialog.confirm is not None))

    @property
    def has_skip(self) -> bool:
        return "skip_btn" in self.icons

    def icon(self, key: str) -> Hit | None:
        return self.icons.get(key)

    def describe(self) -> str:
        names = [config.TEMPLATES[k][1] for k in self.icons if k in config.TEMPLATES]
        parts = []
        if names:
            parts.append("图标:" + "/".join(names))
        if self.dialog:
            parts.append("跳过确认弹窗")
        if self.panel:
            extra = ""
            if self.options:
                extra = "·%d个选项" % len(self.options)
            if self.chat_end:
                extra += "·对话结束"
            parts.append("聊天面板" + extra)
        return " | ".join(parts) if parts else "无剧情特征"


# --------------------------------------------------------------------- 识别
class Recognizer:
    def __init__(self, templates: TemplateSet | None = None):
        self.tpl = templates or TemplateSet()

    # -- 图标 ---------------------------------------------------------
    def match_icon(self, gray: np.ndarray, key: str,
                   hp_full: np.ndarray | None = None) -> Hit | None:
        if key not in self.tpl:
            return None
        h, w = gray.shape[:2]
        base_scale = w / config.REF_WIDTH
        x0, y0, x1, y1 = _roi(gray, config.ROIS[key])
        roi = gray[y0:y1, x0:x1]
        if roi.size == 0:
            return None
        if hp_full is not None:
            hp_roi = hp_full[y0:y1, x0:x1]
        else:
            hp_roi = _highpass(roi)
        thresh = config.TEMPLATES[key][2]
        need_std = config.TEXTURE_RATIO * self.tpl.hp_std.get(key, 1.0)
        bright_min = config.BRIGHT_MIN.get(key, 0.0)

        # 先用基准尺度（1.0）扫一遍：命中且够好就直接返回，不再走多尺度。
        # 实测 1920 宽画面下 big_arrow 的 ROI 有 1114x561，6 个尺度全扫要 71ms，
        # 而绝大多数时候 base_scale 就已经能命中。只有没命中时才扩到其它尺度。
        best: Hit | None = None
        for rel in config.SCALES:
            if best is not None and best.score >= config.EARLY_STOP_SCORE:
                break
            scale = base_scale * rel
            tpl, _mask = self.tpl.get(key, scale)
            th, tw = tpl.shape[:2]
            if th >= roi.shape[0] or tw >= roi.shape[1]:
                continue
            hp_tpl = _highpass(tpl)
            res = cv2.matchTemplate(hp_roi, hp_tpl, cv2.TM_CCOEFF_NORMED)
            # 粗筛：先取候选区，再逐个验纹理
            cand = np.where(res >= thresh)
            if len(cand[0]) == 0:
                continue
            scores = res[cand]
            order = np.argsort(-scores)[:8]
            for idx in order:
                py, px = int(cand[0][idx]), int(cand[1][idx])
                sc = float(res[py, px])
                patch = hp_roi[py:py + th, px:px + tw]
                std = float(patch.std())
                if std < need_std:
                    continue
                if bright_min > 0.0:
                    raw = roi[py:py + th, px:px + tw]
                    if float((raw > 200).mean()) < bright_min:
                        continue
                if best is None or sc > best.score:
                    best = Hit(key, x0 + px, y0 + py, tw, th, sc, std)
                break
        return best

    def find_icons(self, gray: np.ndarray, keys=None) -> dict[str, Hit]:
        """逐个模板在各自 ROI 里匹配。

        高通滤波**只算 ROI 那一小块**：整图 1920x1080 做 sigma=11 的高斯
        要 40~60ms，而六个 ROI 加起来只有画面的一小部分，
        实测整帧识别从 141ms 降到 ~45ms，识别频率才跟得上剧情推进。
        """
        keys = list(keys or config.TEMPLATES.keys())
        out: dict[str, Hit] = {}
        for key in keys:
            hit = self.match_icon(gray, key)
            if hit is not None:
                out[key] = hit
        return out

    # -- 跳过确认弹窗 --------------------------------------------------
    def detect_dialog(self, gray: np.ndarray) -> Dialog | None:
        """找「是否跳过当前段落对话」弹窗，并顺带算好「确认」该点哪。

        两处真机实测修正：
          1. 长宽比上限从 2.80 放宽到 4.30 —— 真机弹窗是 960x264（3.64），
             原来直接被拒，所以用户反馈"看得到弹窗但从不点确认"。
          2. 确认按钮不再写死 CONFIRM_XY：在弹窗下方那条带子里找白胶囊，
             取最右边那个（弹窗里左取消、右确认）。
        """
        h, w = gray.shape[:2]
        # 注意这里用 0.05 而不是 0.08：真机弹窗占画面 12.2%，
        # 但如果将来遇到更小的读条式弹窗，0.08 会先把它滤掉。
        comp = _largest_bright_component(gray, config.DLG_BRIGHT_THR, 0.05)
        if comp is None:
            return None
        x, y, cw, ch, ratio = comp
        aspect = cw / max(1.0, float(ch))
        if not (config.DLG_MIN_AREA_RATIO <= ratio <= config.DLG_MAX_AREA_RATIO):
            return None
        lo, hi = config.DLG_ASPECT
        if not (lo <= aspect <= hi):
            return None
        # 弹窗是浮在压暗场景上的一块白卡：四条边都必须留有暗边。
        # 少了这一条，整片亮天空/雪坡（1718x897，占画面 74%）会被当成弹窗。
        ml, mr, mt, mb = config.DLG_MARGIN
        if x < ml * w or (w - x - cw) < mr * w:
            return None
        if y < mt * h or (h - y - ch) < mb * h:
            return None
        # 宽度也不该顶满画面（真弹窗实测都是画面的 50%）
        if cw > config.DLG_MAX_WIDTH_RATIO * w:
            return None
        # 必须基本居中
        mid = x + cw / 2.0
        if abs(mid - w / 2.0) > 0.10 * w:
            return None
        # 弹窗上沿要在画面上半部，避免把底部对话框误判成弹窗
        if y > 0.45 * h:
            return None
        # 中央必须有足够亮的主体（防止整屏都亮的过场被当成弹窗）
        x0, y0, x1, y1 = _roi(gray, config.DLG_CENTER)
        center = gray[y0:y1, x0:x1]
        if center.size == 0:
            return None
        if float((center > config.DLG_BRIGHT_THR).mean()) < config.DLG_BRIGHT_RATIO:
            return None
        box = Dialog(x, y, cw, ch)
        # 最后一道：弹窗是压在**偏暗**场景上的白卡，四周不能也亮成一片。
        # 实测四周中位亮度：真弹窗 image6 84 / 真机 chat_panel2 26，
        # 而雪山那张全屏发亮（均值 99）、四周没有暗边 —— 这一条独立成立。
        if not self._sits_on_dark(gray, box):
            return None
        box.confirm = self.find_dialog_confirm(gray, box)
        return box

    @staticmethod
    def _sits_on_dark(gray: np.ndarray, box: Dialog) -> bool:
        """弹窗四周那圈（避开画面以外）的中位亮度得明显低于弹窗本体。"""
        h, w = gray.shape[:2]
        pad = int(max(box.w, box.h) * 0.35)
        outs = []
        if box.y - pad > 0:
            outs.append(gray[box.y - pad:box.y, box.x:box.x + box.w])
        if box.y + box.h + pad < h:
            outs.append(gray[box.y + box.h:box.y + box.h + pad, box.x:box.x + box.w])
        if box.x - pad > 0:
            outs.append(gray[box.y:box.y + box.h, box.x - pad:box.x])
        if box.x + box.w + pad < w:
            outs.append(gray[box.y:box.y + box.h, box.x + box.w:box.x + box.w + pad])
        outs = [o for o in outs if o.size]
        if not outs:
            return True
        ring = float(np.median(np.concatenate([o.ravel() for o in outs])))
        inner = float(np.median(gray[box.y:box.y + box.h, box.x:box.x + box.w]))
        return ring <= inner - config.DLG_RING_GAP

    def find_dialog_confirm(self, gray: np.ndarray, box: Dialog):
        """在弹窗下方那条带里找白胶囊，取最右边的作为「确认」。"""
        y0 = box.y + int(box.h * config.DLG_PILL_BAND[0])
        y1 = min(gray.shape[0], box.y + int(box.h * config.DLG_PILL_BAND[1]))
        x0 = max(0, box.x)
        x1 = min(gray.shape[1], box.x + box.w)
        if y1 - y0 < 8 or x1 - x0 < 32:
            return None
        band = gray[y0:y1, x0:x1]
        mask = (band > config.DLG_PILL_BRIGHT_THR).astype(np.uint8)
        if not mask.any():
            return None
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        num, _labels, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
        pills = []
        for i in range(1, num):
            x, y, pw, ph, area = (int(v) for v in stats[i])
            if not (config.DLG_PILL_H[0] * box.h <= ph <= config.DLG_PILL_H[1] * box.h):
                continue
            if not (config.DLG_PILL_W[0] * box.w <= pw <= config.DLG_PILL_W[1] * box.w):
                continue
            if area < pw * ph * config.DLG_PILL_FILL:
                continue
            ar = pw / max(1.0, float(ph))
            if not (config.DLG_PILL_ASPECT[0] <= ar <= config.DLG_PILL_ASPECT[1]):
                continue
            pills.append((x0 + x + pw // 2, y0 + y + ph // 2))
        if not pills:
            return None
        pills.sort(key=lambda p: p[0])
        return pills[-1]

    # -- 聊天面板 ------------------------------------------------------
    def detect_panel(self, gray: np.ndarray) -> Panel | None:
        """找聊天面板。找不到就返回 None —— 这一层必须"宁缺勿滥"。

        用户实测过一个很糟的失败模式：画面上根本没有面板，但某个亮块被当成
        面板，而「对话结束」的文字带又恰好凑上了，于是程序不停地点右上角的 ✕，
        一直在关面板。所以这里加了三道结构性门槛：
          · 面板内部中位亮度够亮（是浅色卡片）
          · 内部标准差不大（不是暗部背景）
          · 比面板左右两侧那一圈明显更亮（是压在压暗场景上的浮层卡片）
        """
        h, w = gray.shape[:2]
        # 不止看最大的那块：面板未必是画面里最大的一片亮区。
        for x, y, cw, ch, ratio in _bright_components(
                gray, config.PANEL_BRIGHT_THR, 0.03, limit=6):
            if ratio < config.PANEL_MIN_AREA_RATIO:
                continue
            if x < config.PANEL_X_MIN * w:
                continue
            aspect = cw / max(1.0, float(ch))
            lo, hi = config.PANEL_ASPECT
            if not (lo <= aspect <= hi):
                continue
            if not self._looks_like_card(gray, x, y, cw, ch):
                continue
            return Panel(x, y, cw, ch)
        return None

    @staticmethod
    def _looks_like_card(gray: np.ndarray, x: int, y: int, w: int, h: int) -> bool:
        """内部亮、内部平、边界锐利 —— 三条都过才算卡片。

        边界这一条是本轮新增的主判据，替代原来"内外亮度差 60"的硬门槛。
        原因见 config.PANEL_EDGE_THR 注释：面板贴着画面右缘时右侧没有可比
        对象，内外差会掉到 39，真面板被拒；而真正的卡片四条边一定和外面
        切得开，雪坡/墙面那种连片亮块则切不开。
        """
        sub = gray[y:y + h, x:x + w]
        if sub.size < 256:
            return False
        sub = sub.astype(np.float32)
        med = float(np.median(sub))
        std = float(sub.std())
        if med < config.PANEL_MEDIAN_MIN:
            return False
        if std > config.PANEL_STD_MAX:
            return False
        if not Recognizer._has_crisp_border(gray, x, y, w, h):
            return False
        outs = []
        pad = int(w * 0.6)
        lx = max(0, x - pad)
        if lx < x:
            outs.append(gray[y:y + h, lx:x])
        rx = min(gray.shape[1], x + w + pad)
        if rx > x + w:
            outs.append(gray[y:y + h, x + w:rx])
        if not outs:
            return True                      # 面板贴边，没有可比对象，放宽
        outer = float(np.median(np.concatenate([o.ravel() for o in outs])))
        return (med - outer) >= config.PANEL_OUTER_GAP

    @staticmethod
    def _has_crisp_border(gray: np.ndarray, x: int, y: int, w: int, h: int) -> bool:
        """卡片四条边里有几条能和外面切得开。

        对每条存在的边，取边界内外各 PANEL_EDGE_RIM 像素做跨越跳变，统计
        跳变大于 PANEL_EDGE_THR 的像素占比；占比达到 PANEL_EDGE_CONT 算这条
        边"锐利"。贴到画面边缘的方向本来就没有外面，直接算锐利（面板压边
        是常态）。最后要求锐利边数 >= PANEL_EDGE_MIN_SIDES。
        """
        H, W = gray.shape[:2]
        d = int(config.PANEL_EDGE_RIM)
        thr = float(config.PANEL_EDGE_THR)
        lo, hi = float(config.PANEL_EDGE_CONT), float(config.PANEL_EDGE_MIN_SIDES)
        sides: list[bool] = []

        def _frac(outer: np.ndarray, inner: np.ndarray) -> float:
            if outer.size == 0:
                return 1.0
            step = np.abs(inner.astype(np.int16) - outer.astype(np.int16))
            return float((step > thr).mean())

        # 左：内部取 x+d 那一列，外部取 x-d 那一列
        if x - d < 0:
            sides.append(True)
        else:
            sides.append(_frac(gray[y:y + h, x - d], gray[y:y + h, x + d]) >= lo)
        # 右
        if x + w + d >= W:
            sides.append(True)
        else:
            sides.append(_frac(gray[y:y + h, x + w + d], gray[y:y + h, x + w - d]) >= lo)
        # 上
        if y - d < 0:
            sides.append(True)
        else:
            sides.append(_frac(gray[y - d, x:x + w], gray[y + d, x:x + w]) >= lo)
        # 下
        if y + h + d >= H:
            sides.append(True)
        else:
            sides.append(_frac(gray[y + h + d, x:x + w], gray[y + h - d, x:x + w]) >= lo)
        return sum(1 for s in sides if s) >= hi

    def find_options(self, gray: np.ndarray, panel: Panel):
        """在面板里找"白底选项条"。

        判据不是"亮像素占比"而是**最长连续亮段**：选项条是一整条近白圆角矩形，
        哪怕被文字切成两截，最长段仍占面板宽的 0.88；聊天气泡最长只有 0.62。
        实测 image15.jpeg 的 OPT 行全部恰好是 0.88，气泡行最高 0.62 —— 区分度很干净。

        选项条会被自家文字切成上下两段（中间那几行最长段掉到 0.45~0.52），
        所以允许跨过 ≤OPTION_MERGE_GAP 的断口把带子合起来。
        """
        sub = gray[panel.y:panel.bottom, panel.x:panel.x + panel.w]
        if sub.size < 64:
            return []
        ph, pw = sub.shape[:2]
        mask = (sub > config.OPTION_BRIGHT_THR).astype(np.uint8)
        if not mask.any():
            return []

        # 选项条是"几乎占满面板宽"的实心圆角矩形，整块连成一个连通域；
        # 聊天气泡同样连成一块，但宽度只有面板的 0.48~0.62。
        # 所以判据三条：宽度 ≥ 0.80 面板宽、填充率（面积/外接矩形）≥ 0.70、
        # 高度在面板高的 3%~13% 之间。实测 image15.jpeg 两条选项条
        # 分别是 (27,337,401x32, fill 0.92) 与 (27,380,401x32, fill 0.98)，
        # 气泡最高只有 (106,258,269x37, w/pw=0.59, fill 0.75)。
        num, _labels, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
        min_h = max(5, int(ph * config.OPTION_MIN_H_RATIO))
        max_h = max(min_h + 1, int(ph * config.OPTION_MAX_H_RATIO))
        y_floor = ph * config.OPTION_Y_MIN

        bands = []
        for i in range(1, num):
            x, y, w, h, area = (int(v) for v in stats[i])
            if y < y_floor:
                continue
            if h < min_h or h > max_h:
                continue
            if w < pw * config.OPTION_MIN_WIDTH_RATIO:
                continue
            if area < w * h * config.OPTION_MIN_FILL:
                continue
            bands.append((panel.x + x, panel.y + y, w, h))
        bands.sort(key=lambda b: b[1])
        return bands

    def detect_chat_end(self, gray: np.ndarray, panel: Panel) -> bool:
        """面板下部是否出现深色小字「对话结束」。

        实测：文字带高度 7~11px，位于面板高度 93%~96%，
        横向只占面板中间一小段（所以不能把整行分隔线当成它）。
        """
        y0 = panel.y + int(panel.h * config.CHAT_END_Y0)
        y1 = panel.y + int(panel.h * config.CHAT_END_Y1)
        band = gray[y0:y1, panel.x:panel.x + panel.w]
        if band.size < 16:
            return False
        pad = max(3, int(panel.w * 0.15))
        core = band[:, pad:panel.w - pad]
        dark = core < config.CHAT_END_DARK_THR
        per_row = dark.mean(axis=1)
        rows = per_row >= config.CHAT_END_ROW_RATIO
        for a, b in _runs(rows):
            height = b - a
            if height < config.CHAT_END_MIN_H or height > config.CHAT_END_MAX_H:
                continue
            # 文字带：横向只占中间一部分，不能是整条线
            span = np.nonzero(dark[a:b].any(axis=0))[0]
            if len(span) == 0:
                continue
            width_ratio = (span.max() - span.min() + 1) / max(1.0, core.shape[1])
            if width_ratio <= config.CHAT_END_MAX_SPAN:
                return True
        return False

    def detect_close_x(self, gray: np.ndarray, panel: Panel):
        """在面板右上角找深色 ✕，返回它的中心；找不到返回 None。

        真机实测（chat_panel.png，1919x1079）：面板是 (1106,186,694x713)，
        ✕ 的实际中心在 (1740,241) —— 注意它是 **4 个短笔画 + 一个中心点**画的，
        每个笔画只有 60~90 像素。原来的实现"取面积最大的暗连通域"会从四个
        笔画里随便挑一个（面积都是 66~67），结果点偏 11~12 像素就点空了。
        现在按面板宽的比例做闭运算，把四个笔画合成一整块，取整块的中心 ——
        实测正好落在 (1740,241)，与真 ✕ 重合。
        """
        x0 = panel.x + int(panel.w * config.CLOSE_X_WIN[0])
        x1 = panel.x + int(panel.w * config.CLOSE_X_WIN[2])
        y0 = panel.y + int(panel.h * config.CLOSE_X_WIN[1])
        y1 = panel.y + int(panel.h * config.CLOSE_X_WIN[3])
        x1 = min(gray.shape[1], x1)
        y1 = min(gray.shape[0], y1)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
        win = gray[y0:y1, x0:x1]
        # 优先用"面板底色 - 55"这种相对阈值：深色主题面板、浅色面板都适用。
        bg = _panel_bg(gray, panel)
        thr = bg - config.CLOSE_X_REL_GAP
        if bg < 40:
            thr = config.CLOSE_X_DARK_THR
        mask = (win < thr).astype(np.uint8)
        if not mask.any():
            return None
        # 先抹掉贴窗边的像素：面板圆角、边框在这种阈值下也是暗的，
        # 不抹掉的话圆角那一大块会盖过真正的 ✕（image13/15 就是这样）。
        e = max(1, int(min(win.shape) * config.CLOSE_X_EDGE_MASK))
        mask[:e, :] = 0
        mask[-e:, :] = 0
        mask[:, :e] = 0
        mask[:, -e:] = 0
        if not mask.any():
            return None
        k = max(5, int(panel.w * config.CLOSE_X_MERGE_RATIO) | 1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
        num, _labels, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
        best = None
        for i in range(1, num):
            x, y, w, h, area = (int(v) for v in stats[i])
            if area < config.CLOSE_X_MIN_AREA:
                continue
            # 贴到窗口边的块一律不要：面板右上角那一圈深色就是圆角和边框本身。
            # image13/15 那种浅色面板根本没有 ✕，只有圆角，之前正是把它当成了 ✕，
            # 于是"没有对话面板也在点关闭"。真机的 ✕ 是整个落在面板内部的
            # （实测 (1740,241)，面板右边界 1800，不贴边）。
            if x <= 0 or y <= 0 or x + w >= win.shape[1] or y + h >= win.shape[0]:
                continue
            if w > 0.5 * win.shape[1] or h > 0.6 * win.shape[0]:
                continue                      # 太大，多半是图标或边框
            if not (0.55 <= w / max(1.0, float(h)) <= 1.80):
                continue                      # ✕ 基本是方的
            if best is None or area > best[0]:
                best = (area, x, y, w, h)
        if best is None:
            return None
        _area, x, y, w, h = best
        return (x0 + x + w // 2, y0 + y + h // 2)

    # -- 总入口 -------------------------------------------------------
    def analyze(self, img_bgr: np.ndarray) -> Scene:
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
        h, w = gray.shape[:2]
        scene = Scene(width=w, height=h)
        scene.icons = self.find_icons(gray)

        # 先看有没有跳过确认弹窗：弹窗是压在最上面的模态框，
        # 它一出现，底下的聊天面板点什么都点不动，所以优先级最高。
        scene.dialog = self.detect_dialog(gray)
        if scene.dialog is not None:
            return scene

        panel = self.detect_panel(gray)
        if panel is not None:
            # 用户实测要求：左上角还有「对话记录 / 设置齿轮」这类常规剧情标识时，
            # 说明这是普通剧情对话、不是手机聊天面板 —— 宁可不当面板处理。
            # 真机上正是这条把"普通对话被误判成面板、于是一直点右上角 ✕"堵住。
            if any(k in scene.icons for k in config.PANEL_BLOCKED_BY_STORY_ICONS):
                panel = None
        if panel is not None:
            scene.panel = panel
            scene.options = self.find_options(gray, panel)
            if not scene.options:
                scene.chat_end = self.detect_chat_end(gray, panel)
            if scene.chat_end:
                scene.close_x = self.detect_close_x(gray, panel)
        return scene
