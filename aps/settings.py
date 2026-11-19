# -*- coding: utf-8 -*-
"""可调参数的运行时读写 —— 界面上的「截图间隔 / 触发间隔」就落在这里。

设计上有三条约束：

1. **改完立刻生效，不用重启**。引擎每帧都现读 `config.LOOP_INTERVAL`，
   触发器每次 `ready()` 都现算，所以这里只要改 `config` 上的值就行。
2. **存盘，下次启动还记得**。写到 `settings.json`（跟程序同目录），
   启动时 `load()` 把值盖回 `config`。
3. **只允许改白名单里的项**，免得界面上一个手误把无关参数写坏。
"""
from __future__ import annotations

import json
import os

from . import config

# 界面能调的项 -> (中文名, 最小值, 最大值, 说明)
# 值都是秒；倍率是纯数字。
# 区间是照着"实际用得上"划的，并且让默认值落在滑块中段附近，
# 免得一打开界面三根滑块全挤在最左边、看不出还能往哪调。
EDITABLE: dict[str, tuple[str, float, float, str]] = {
    "LOOP_INTERVAL": (
        "截图间隔", 0.20, 1.20,
        "有可点目标时的循环节奏：每隔这么久抓一帧、识别一次。调小反应更快、更吃 CPU。"),
    "RECHECK_INTERVAL": (
        "空转间隔", 0.15, 1.60,
        "画面里没有可点目标时的节奏：放慢一点省 CPU，剧情过场不必每帧都看。"),
    "TRIGGER_SCALE": (
        "触发间隔倍率", 0.40, 2.60,
        "所有点击冷却与触发间隔的总体缩放。1.0 是默认节奏，调大更稳、调小更激进。"),
}

SETTINGS_PATH = os.path.join(config.DATA_ROOT, "settings.json")


def get(name: str) -> float:
    return float(getattr(config, name, 0.0))


def clamp(name: str, value: float) -> float:
    """把数值夹到该项允许的范围内。"""
    _label, lo, hi, _doc = EDITABLE[name]
    return max(lo, min(hi, float(value)))


def set_value(name: str, value: float, *, save: bool = True) -> float:
    """改一个可调参数（会夹到合法范围），返回实际生效的值。"""
    if name not in EDITABLE:
        raise KeyError("不可调的参数：%s" % name)
    v = clamp(name, value)
    setattr(config, name, v)
    if save:
        dump()
    return v


def current() -> dict[str, float]:
    return {name: get(name) for name in EDITABLE}


def reset_defaults() -> dict[str, float]:
    """全部恢复默认值并落盘。"""
    for name, (_l, _lo, _hi, _d) in EDITABLE.items():
        setattr(config, name, DEFAULTS[name])
    dump()
    return current()


# 出厂默认值：模块导入时就地取一份，之后改 config 不影响它
DEFAULTS: dict[str, float] = {name: get(name) for name in EDITABLE}


def load() -> dict[str, float]:
    """启动时读 settings.json，把值盖回 config。读失败就保持默认。"""
    if not os.path.exists(SETTINGS_PATH):
        return current()
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return current()
    if not isinstance(data, dict):
        return current()
    for name, value in data.items():
        if name not in EDITABLE:
            continue                       # 老版本残留的键，忽略
        try:
            setattr(config, name, clamp(name, float(value)))
        except (TypeError, ValueError):
            continue
    return current()


def dump() -> bool:
    """把当前可调参数写进 settings.json。写不了（比如目录只读）就安静放弃。"""
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
            json.dump(current(), fh, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def describe() -> str:
    """给日志用的一行摘要。"""
    return "截图间隔 %.2fs / 空转 %.2fs / 触发倍率 x%.2f" % (
        get("LOOP_INTERVAL"), get("RECHECK_INTERVAL"), get("TRIGGER_SCALE"))
