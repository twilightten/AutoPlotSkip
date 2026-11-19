# -*- coding: utf-8 -*-
"""回归：聊天面板判据 + 节奏参数（截图间隔 / 触发倍率）。

这两块都是用户真机反馈后加的，容易被后面的改动悄悄弄坏，所以单独看住：

1. **聊天面板判据**。用户实测「进聊天框时提示无剧情特征、不点」和
   「聊天框里一直点右上角 ✕」是同一个根源 —— 面板检测的阈值卡错位置。
   样本里既有**必须**认成面板的（含用户真机那张带浅绿天空的三月七面板），
   也有**绝不能**认成面板的（跳过确认弹窗、普通剧情画面）。两边一起验。

2. **节奏参数**。滑块改的值必须真的能落到 config、能换算出新的触发器
   节奏、能存盘再读回来，越界的值要被夹住。

用法：
    python tools/settings_test.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

from aps import config, settings, triggers, vision

SHOTS = os.path.join(config.ROOT, "testdata", "shots")
MEDIA = os.path.join(config.ROOT, "testdata", "doc")

# (相对目录, 文件名, 应该认出面板吗, 期望动作名)
PANEL_CASES = [
    # —— 必须认成聊天面板 ——
    (SHOTS, "chat_no_feature.png", True, "剧情选项"),   # 用户 2026-10-24 报的那张
    (SHOTS, "chat_option_1.png", True, "剧情选项"),     # 贴右边缘、内外差只有 39
    (SHOTS, "chat_panel.png", True, "关闭面板"),
    (MEDIA, "image13.jpeg", True, "关闭面板"),          # 文档：对话结束
    (MEDIA, "image15.jpeg", True, "剧情选项"),          # 文档：白底选项条
    # —— 绝不能认成面板 ——
    (MEDIA, "image6.jpeg", False, "跳过确认"),          # 跳过确认弹窗
    (SHOTS, "chat_panel2.png", False, "跳过确认"),      # 真机弹窗
    (MEDIA, "image9.png", False, "底部推进"),           # 普通剧情画面
    (MEDIA, "image11.png", False, "语句箭头"),          # 普通剧情画面
    (MEDIA, "image2.png", False, None),                 # 纯图标，无动作
]

# 弹窗判据：这里最容易出的是"把大块亮场景当成弹窗"。
# 用户 2026-10-24 报的那张雪山夜景，整片雪坡灰度 >195 连成 1718x897
# （占画面 74%），居中、够亮、长宽比 1.92 也合格，四条判据全过。
# 之后加了"四边必须留暗边"（DLG_MARGIN）才挡住。
# (相对目录, 文件名, 应该认出弹窗吗, 期望动作名)
DIALOG_CASES = [
    (SHOTS, "scene_no_feature.png", False, None),       # 雪山夜景：亮块占 74%，贴满边
    (MEDIA, "image6.jpeg", True, "跳过确认"),           # 文档弹窗 634x293
    (SHOTS, "chat_panel2.png", True, "跳过确认"),       # 真机弹窗 960x264
    (MEDIA, "image13.jpeg", False, "关闭面板"),         # 聊天面板不是弹窗
    (SHOTS, "chat_no_feature.png", False, "剧情选项"),  # 聊天面板不是弹窗
]

fails: list[str] = []


def load(path: str):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def check_panels(rec: vision.Recognizer) -> None:
    print("=" * 78)
    print("聊天面板判据")
    print("=" * 78)
    for folder, name, want_panel, want_action in PANEL_CASES:
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            print("  跳过（缺图）%s" % name)
            continue
        scene = rec.analyze(load(path))
        got_panel = scene.panel is not None
        act = triggers.plan_action(scene)
        act_name = act[0].name if act else None

        ok = (got_panel == want_panel) and (act_name == want_action)
        mark = "PASS" if ok else "FAIL"
        print("[%s] %-22s 面板=%-5s 动作=%-8s  期望 面板=%-5s 动作=%s"
              % (mark, name, got_panel, act_name or "无", want_panel,
                 want_action or "无"))
        if scene.panel is not None:
            p = scene.panel
            print("        面板 %.0f,%.0f %dx%d" % (p.x, p.y, p.w, p.h))
        if not ok:
            fails.append("面板判据：%s" % name)


def check_panel_config() -> None:
    """阈值本身也要落在实测数据的安全区里，别被随手改窄。"""
    print()
    print("阈值范围")
    assert config.PANEL_BRIGHT_THR == 200, "面板亮阈值又动了？实测 185 会把天空连进来"
    assert config.PANEL_OUTER_GAP <= 60, "内外亮度差门槛 > 60 会漏掉真机带浅色底的面板"
    assert config.PANEL_MEDIAN_MIN >= 190
    assert config.PANEL_STD_MAX >= 45
    # 边界判据是主判据，不能被削到失效
    assert config.PANEL_EDGE_MIN_SIDES >= 3, "至少要 3 条边锐利才算是卡片"
    assert config.PANEL_EDGE_CONT <= 0.70, "边界占比门槛 > 0.70 会漏掉真面板"
    print("  [PASS] PANEL_BRIGHT_THR=%d  PANEL_OUTER_GAP=%d  MEDIAN_MIN=%d  STD_MAX=%d"
          % (config.PANEL_BRIGHT_THR, config.PANEL_OUTER_GAP,
             config.PANEL_MEDIAN_MIN, config.PANEL_STD_MAX))
    print("  [PASS] PANEL_EDGE_THR=%d  EDGE_CONT=%.2f  EDGE_MIN_SIDES=%d"
          % (config.PANEL_EDGE_THR, config.PANEL_EDGE_CONT,
             config.PANEL_EDGE_MIN_SIDES))


def check_tuning() -> None:
    print()
    print("=" * 78)
    print("节奏参数")
    print("=" * 78)

    # 1) 三项都在、默认值落下区间中部
    base = dict(settings.DEFAULTS)
    for name, (_label, lo, hi, _doc) in settings.EDITABLE.items():
        v = base[name]
        assert lo <= v <= hi, "%s 默认值 %s 不在 [%s,%s]" % (name, v, lo, hi)
        mid = (lo + hi) / 2.0
        assert abs(v - mid) <= (hi - lo) * 0.35, \
            "%s 默认值 %s 挤在滑块边上（中段是 %.2f）" % (name, v, mid)
    assert abs(base["LOOP_INTERVAL"] - 0.50) < 1e-9, "截图间隔默认值应为 0.50s"
    assert abs(base["TRIGGER_SCALE"] - 1.50) < 1e-9, "触发间隔倍率默认值应为 x1.50"
    print("  [PASS] 默认值都在滑块中段：%s" % settings.describe())

    # 2) 改值 -> config 立刻变
    old = config.LOOP_INTERVAL
    settings.set_value("LOOP_INTERVAL", 0.35, save=False)
    assert abs(config.LOOP_INTERVAL - 0.35) < 1e-9
    print("  [PASS] 改截图间隔 -> config.LOOP_INTERVAL = %.2f" % config.LOOP_INTERVAL)

    # 3) 倍率真的换算到触发器节奏上（基准先归一到 x1.00，默认值已改成 x1.50）
    settings.set_value("TRIGGER_SCALE", 1.0, save=False)
    base_wait = {t.name: t.wait() for t in triggers.build_triggers()}
    settings.set_value("TRIGGER_SCALE", 2.0, save=False)
    doubled = {t.name: t.wait() for t in triggers.build_triggers()}
    for name, w0 in base_wait.items():
        w1 = doubled[name]
        assert abs(w1 - w0 * 2.0) < 0.02, "%s 倍率没生效：%.2f -> %.2f" % (name, w0, w1)
    print("  [PASS] 触发倍率 x2.0：跳过按钮 %.2fs -> %.2fs"
          % (base_wait["跳过按钮"], doubled["跳过按钮"]))

    # 4) 越界要夹住
    settings.set_value("LOOP_INTERVAL", 99, save=False)
    assert config.LOOP_INTERVAL <= settings.EDITABLE["LOOP_INTERVAL"][2]
    settings.set_value("LOOP_INTERVAL", -5, save=False)
    assert config.LOOP_INTERVAL >= settings.EDITABLE["LOOP_INTERVAL"][1]
    print("  [PASS] 越界值被夹到合法区间")

    # 5) 存盘 / 读回 / 未知键忽略
    settings.set_value("LOOP_INTERVAL", 0.42)
    settings.set_value("TRIGGER_SCALE", 1.30)
    assert os.path.exists(settings.SETTINGS_PATH)
    config.LOOP_INTERVAL = 0.50
    config.TRIGGER_SCALE = 1.50
    settings.load()
    assert abs(config.LOOP_INTERVAL - 0.42) < 1e-9, "读回来不对"
    assert abs(config.TRIGGER_SCALE - 1.30) < 1e-9, "读回来不对"
    print("  [PASS] 存盘后重新 load 能读回 0.42s / x1.30")

    bad = dict(settings.current())
    bad["NOT_A_REAL_KEY"] = 123
    import json
    with open(settings.SETTINGS_PATH, "w", encoding="utf-8") as fh:
        json.dump(bad, fh)
    settings.load()
    print("  [PASS] settings.json 里的多余键被忽略，没炸")

    # 6) 还原默认 + 恢复现场
    vals = settings.reset_defaults()
    assert abs(vals["LOOP_INTERVAL"] - 0.50) < 1e-9
    print("  [PASS] 还原默认 -> %s" % settings.describe())

    config.LOOP_INTERVAL = old
    settings.reset_defaults()
    try:
        os.remove(settings.SETTINGS_PATH)
    except OSError:
        pass


def check_dialogs(rec: vision.Recognizer) -> None:
    print()
    print("=" * 78)
    print("跳过确认弹窗判据")
    print("=" * 78)
    for folder, name, want_dlg, want_action in DIALOG_CASES:
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            print("  跳过（缺图）%s" % name)
            continue
        scene = rec.analyze(load(path))
        got_dlg = scene.dialog is not None
        act = triggers.plan_action(scene)
        act_name = act[0].name if act else None
        ok = (got_dlg == want_dlg) and (act_name == want_action)
        print("[%s] %-22s 弹窗=%-5s 动作=%-8s  期望 弹窗=%-5s 动作=%s"
              % ("PASS" if ok else "FAIL", name, got_dlg, act_name or "无",
                 want_dlg, want_action or "无"))
        if got_dlg:
            d = scene.dialog
            print("        弹窗 %.0f,%.0f %dx%d 确认=%s"
                  % (d.x, d.y, d.w, d.h, d.confirm))
        if not ok:
            fails.append("弹窗判据：%s" % name)


def check_dialog_config() -> None:
    """四边留白这道新判据也要看住，别被随手调成 0。"""
    ml, mr, mt, mb = config.DLG_MARGIN
    assert min(ml, mr, mt, mb) >= 0.02, "四边留白小于 2%，雪山那类亮场景会重新混进来"
    assert max(ml, mr, mt, mb) <= 0.12, "四边留白太大，真机小弹窗会被误杀"
    print("  四边留白 左%.0f%% 右%.0f%% 上%.0f%% 下%.0f%%"
          % (ml * 100, mr * 100, mt * 100, mb * 100))


def main() -> int:
    rec = vision.Recognizer()
    check_panels(rec)
    check_dialogs(rec)
    check_panel_config()
    check_dialog_config()
    check_tuning()
    print()
    print("-" * 78)
    if fails:
        print("失败 %d 项：%s" % (len(fails), "、".join(fails)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
