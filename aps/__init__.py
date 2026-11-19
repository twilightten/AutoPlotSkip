# -*- coding: utf-8 -*-
"""AutoPlotSkip —— 崩坏：星穹铁道 自动剧情跳过。

结构参考 BetterGI（better-genshin-impact）的 GameTask 分层：

    capture.py  取画面（GDI BitBlt / PrintWindow）
    vision.py   识别画面（模板匹配 + 结构性判断）
    engine.py   决策与点击循环（优先级：跳过 > 确认 > 大箭头 > 底部▼）
    gui.py      启动器窗口，左下角同步滚动日志

只依赖标准库 + opencv-python + numpy，不需要 .NET / 管理员权限。
"""
from __future__ import annotations

__all__ = ["__version__", "__app_name__"]

__version__ = "0.1.0"
__app_name__ = "AutoPlotSkip"
