# -*- coding: utf-8 -*-
"""同步日志的清洗与过滤 —— 用户 2026-10-25 的两条外观要求。

1. **说明类、状态类日志不显示**。滑块的 tooltip（tag「说明」）、
   引擎每帧刷新的「状态」（"画面中没有剧情特征，等待剧情开始…"）、
   「识别」（每变一次就刷一行场景摘要）这些对使用者没有信息量，
   全是噪音。它们仍然会进 `aps/logutil` 的过滤名单 `config.LOG_HIDDEN_TAGS`，
   想看全的把它改成 `()` 即可。

2. **点击类日志去掉坐标**。原来是
       [点击] 跳过按钮 ▶| @(1189,134) 前台点击
       [点击] 关闭面板 ✕ @(1740,241) 前台点击
   现在统一成
       [点击] 跳过按钮 ▶|
       [点击] 关闭面板 ✕
   聊天面板那几条（剧情选项 / 推进对话 / 关闭面板）走的是同一个出口，
   所以一并在 `clean()` 里剥掉，不用逐处改。

warn / error 级别的日志**永远显示**，哪怕 tag 在隐藏名单里 —— 出错时必须让人看见。
"""
from __future__ import annotations

import re

from . import config

# 形如 "@(1189,134)" / " @(1189, 134)" / "(1740,241)" / "屏幕(1740,241)"
_COORD_PARENS = re.compile(r"\s*@?\(\s*\d{1,5}\s*,\s*\d{1,5}\s*\)")
# 形如 " @1189,134" 或 " 1189,134"（没有括号的兜底写法）
_COORD_BARE = re.compile(r"\s*@?\b\d{3,5}\s*[,，]\s*\d{3,5}\b")
# 点击后端也会混进日志正文（"... 真实点击 屏幕"），对使用者同样没意义，一并剥掉。
# 只剥"后端名"，不动别的词 —— 顺序上长词在前，避免 "后台点击" 被 "点击" 先切一刀。
_BACKEND_WORDS = re.compile(r"\s*(?:真实点击|后台点击|前台点击|SendInput|PostMessage"
                            r"|屏幕|窗口DC|PrintWindow|演练)\s*")
_TRAILING_WS = re.compile(r"\s+$")
# 连续空格收尾
_SPACES = re.compile(r"\s{2,}")


def strip_coords(text: str) -> str:
    """去掉文字里的坐标片段和后端名，顺便把多余空格压成一个。"""
    if not text:
        return text
    out = _COORD_PARENS.sub("", text)
    out = _COORD_BARE.sub("", out)
    out = _BACKEND_WORDS.sub(" ", out)
    out = _SPACES.sub(" ", out).strip()
    return _TRAILING_WS.sub("", out)


def clean(tag: str, text: str) -> tuple[str, str]:
    """把一条日志洗成"能给人看"的样子：tag 去空白、正文去坐标。"""
    return (tag or "").strip(), strip_coords(text)


def hidden_tags() -> tuple[str, ...]:
    """当前配置里不显示的 tag 名单（现读 config，改完立刻生效）。"""
    return tuple(getattr(config, "LOG_HIDDEN_TAGS", ()))


def visible(tag: str, text: str, level: str = "info") -> bool:
    """这条日志该不该显示。"""
    if not (text or "").strip():
        return False
    if level in ("warn", "error"):
        return True                      # 告警和错误永远显示，不参与过滤
    return (tag or "") not in hidden_tags()
