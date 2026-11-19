# -*- coding: utf-8 -*-
"""实时触发器调度 —— 照搬 BetterGI 的 TaskTriggerDispatcher 思路。

BetterGI 的自动剧情不是"一个大 while 循环里 if/else"，而是：

    TaskTriggerDispatcher
      └─ 每 ~50ms 滴答一次
           ├─ 按 Priority 从小到大遍历所有启用的 ITaskTrigger
           ├─ 每个 trigger 有独立的 Cooldown / 触发间隔（不是全局节流）
           ├─ 谁的 OnTick 返回 true，就认为"这一帧的输入已被消费"，
           │   更后面的 trigger 本轮直接跳过
           └─ 所有 trigger 共享同一个截图缓存，一帧只识别一次

这样做的三个好处，正是 v1 缺的：

  1. **响应快**：跳过确认弹窗这类"你慢一步它就消失了"的东西，给它最高优先级
     和最短间隔，不会被低频的"底部 ▼ 推进"拖住。
  2. **互不干扰**：点跳过之后要等弹窗，但点 ▼ 的节奏不该被影响 —— 各自计时。
  3. **可观测**：每个 trigger 有自己的名字和命中统计，日志里能直接看到
     "跳过触发 / 确认触发"，而不是一句含糊的"点击"。

本模块只负责调度；识别在 vision.py，点击在 input.py。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from . import config
from .vision import Scene


# --------------------------------------------------------------------- 基类
@dataclass
class Trigger:
    """一个可独立开关、独立冷却的实时触发器。"""

    name: str                       # 显示名，出现在日志里
    priority: int                   # 越小越先跑
    interval: float                 # 最短触发间隔（秒）
    cooldown: float                 # 触发成功后的冷却（秒）

    enabled: bool = True
    hint: str = ""                  # 日志/标签里附带的符号提示，如 "▶|"、"▼"
    last_fire: float = field(default=0.0, repr=False)
    hits: int = field(default=0, repr=False)

    @property
    def label(self) -> str:
        return ("%s %s" % (self.name, self.hint)).strip()

    # -- 子类实现 ------------------------------------------------------
    def match(self, scene: Scene, ctx: "Context") -> object | None:
        """看这一帧的场景，返回可点击目标（Truthy）或 None。"""
        raise NotImplementedError

    def plan(self, target, scene: Scene, ctx: "Context") -> tuple[int, int] | None:
        """把目标翻译成窗口客户区坐标 (x, y)。返回 None 表示本帧放弃。"""
        raise NotImplementedError

    # -- 调度用 --------------------------------------------------------
    def wait(self) -> float:
        """两次触发之间至少要等多久。

        `interval` 是"看它一眼"的节奏，`cooldown` 是"点完之后别急着再点"的
        间隔 —— 取两者较大值。只按 interval 走的话，跳过按钮 0.25 秒点一次、
        根本没用到 SKIP_COOLDOWN，实测会出现连续猛点同一个位置把画面点乱。

        最后再乘一个全局倍率 `config.TRIGGER_SCALE`：界面上那个「触发间隔」
        滑块改的就是它，一个数就能把所有触发器的节奏整体调慢/调快，
        不用挨个改 CONFIRM_COOLDOWN / SKIP_COOLDOWN…
        """
        scale = float(getattr(config, "TRIGGER_SCALE", 1.0) or 1.0)
        return max(float(self.interval), float(self.cooldown)) * scale

    def ready(self, now: float) -> bool:
        if not self.enabled:
            return False
        return now >= self.last_fire + self.wait()

    def fire(self, now: float) -> None:
        self.last_fire = now
        self.hits += 1

    def reset(self) -> None:
        self.last_fire = 0.0
        self.hits = 0


class Context:
    """一帧的共享上下文：截图、识别结果、窗口。"""

    __slots__ = ("frame", "scene", "window", "now")

    def __init__(self, frame, scene: Scene, window, now: float):
        self.frame = frame
        self.scene = scene
        self.window = window
        self.now = now

    @property
    def w(self) -> int:
        return self.scene.width

    @property
    def h(self) -> int:
        return self.scene.height


# --------------------------------------------------------------------- 调度器
class Dispatcher:
    """按优先级轮询触发器。一帧只识别一次，多个 trigger 共享结果。"""

    def __init__(self, log: Callable[[str, str, str], None],
                 *, clock: Callable[[], float] = time.perf_counter,
                 clicker: Callable[[Context, int, int, Trigger], bool] | None = None):
        self.log = log
        self.clock = clock
        self.clicker = clicker
        self.triggers: list[Trigger] = []
        self.running = False
        self._stop = False
        self.frames = 0
        self.dispatched = 0
        self.last_scene: Scene | None = None
        self.last_frame = None
        self.last_fired: str = ""

    def register(self, *triggers: Trigger) -> None:
        self.triggers.extend(triggers)
        self.triggers.sort(key=lambda t: (t.priority, t.name))

    def reset(self) -> None:
        for t in self.triggers:
            t.reset()
        self.frames = 0
        self.dispatched = 0
        self.last_scene = None
        self.last_frame = None
        self.last_fired = ""
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    # -- 单帧 ----------------------------------------------------------
    def dispatch(self, ctx: Context) -> Trigger | None:
        """跑一轮：返回本轮真正触发的 trigger（None 表示这帧没做事）。"""
        self.frames += 1
        fired: Trigger | None = None
        for trig in self.triggers:
            if not trig.ready(ctx.now):
                continue
            try:
                target = trig.match(ctx.scene, ctx)
            except Exception as exc:                          # noqa: BLE001
                self.log("触发器异常", "%s: %s: %s"
                         % (trig.name, type(exc).__name__, exc), "error")
                continue
            if target is None:
                continue
            plan = trig.plan(target, ctx.scene, ctx)
            if plan is None:
                continue
            x, y = plan
            ok = self._click(ctx, x, y, trig)
            if ok:
                trig.fire(ctx.now)
                self.dispatched += 1
                self.last_fired = trig.name
                fired = trig
                break            # 本帧输入已被消费，后面的 trigger 让路
        return fired

    def _click(self, ctx: Context, x: int, y: int, trig: Trigger) -> bool:
        if self.clicker is None:
            raise RuntimeError("Dispatcher 未绑定 clicker")
        return self.clicker(ctx, x, y, trig)


# --------------------------------------------------------------------- 具体触发器
class DialogTrigger(Trigger):
    """「是否跳过当前段落对话」确认弹窗 —— 最高优先级。

    这个弹窗只会停留很短时间，慢一步就没了，所以间隔设成最短的 0.12s，
    而且一旦命中就点「确认」。
    """

    def __init__(self, confirm_xy: tuple[float, float], **kw):
        kw.setdefault("hint", "✔")
        super().__init__(name="跳过确认", **kw)
        self.confirm_xy = confirm_xy

    def match(self, scene: Scene, ctx: Context):
        # 光有"一块亮矩形"不算弹窗：必须真的找到了那个白色胶囊按钮。
        # 少了这一条，画面里任何大块亮场景都会触发一次 (0.610, 0.7045) 点击。
        dlg = scene.dialog
        if dlg is None or not getattr(dlg, "confirm", None):
            return None
        return dlg

    def plan(self, target, scene: Scene, ctx: Context):
        # 识别层已经算出了「确认」胶囊的实际位置就直接用；算不出来才退回固定比例。
        confirm = getattr(target, "confirm", None)
        if confirm:
            return int(confirm[0]), int(confirm[1])
        rx, ry = self.confirm_xy
        return int(rx * scene.width), int(ry * scene.height)


class SkipTrigger(Trigger):
    """右上角「跳过 ▶|」按钮 —— 剧情模式里最优先点它。"""

    def __init__(self, icon_key: str = "skip_btn", **kw):
        kw.setdefault("hint", "▶|")
        super().__init__(name="跳过按钮", **kw)
        self.icon_key = icon_key

    def match(self, scene: Scene, ctx: Context):
        return scene.icon(self.icon_key)

    def plan(self, target, scene: Scene, ctx: Context):
        return int(target.cx), int(target.cy)


class ArrowTrigger(Trigger):
    """带白色大箭头 → 的语句：点它，剧情会自动往下走。"""

    def __init__(self, icon_key: str = "big_arrow", **kw):
        kw.setdefault("hint", "→")
        super().__init__(name="语句箭头", **kw)
        self.icon_key = icon_key

    def match(self, scene: Scene, ctx: Context):
        return scene.icon(self.icon_key)

    def plan(self, target, scene: Scene, ctx: Context):
        # 箭头本身很窄，点箭头右侧一点点更稳（落在整条语句的中间带）
        x = min(int(target.cx + target.w * 0.5), scene.width - 4)
        return x, int(target.cy)


class OptionTrigger(Trigger):
    """手机聊天面板里的白底选项条：随便点一个（文档原话）。"""

    def __init__(self, **kw):
        kw.setdefault("hint", "▭")
        super().__init__(name="剧情选项", **kw)

    def match(self, scene: Scene, ctx: Context):
        if not scene.options:
            return None
        return scene.options[0]

    def plan(self, target, scene: Scene, ctx: Context):
        x, y, w, h = target
        return int(x + w // 2), int(y + h // 2)


class ChatEndTrigger(Trigger):
    """面板底部出现「对话结束」→ 点面板右上角 ✕ 关掉。"""

    def __init__(self, fallback: tuple[float, float], **kw):
        kw.setdefault("hint", "✕")
        super().__init__(name="关闭面板", **kw)
        self.fallback = fallback

    def match(self, scene: Scene, ctx: Context):
        if scene.panel is None:
            return None
        return scene.chat_end

    def plan(self, target, scene: Scene, ctx: Context):
        if scene.close_x:
            return int(scene.close_x[0]), int(scene.close_x[1])
        rx, ry = self.fallback
        return scene.panel.rel_point(rx, ry)


class PanelAdvanceTrigger(Trigger):
    """聊天面板没有选项、也没结束时，循环点面板中部推进对话。"""

    def __init__(self, advance_rel: tuple[float, float], **kw):
        kw.setdefault("hint", "")
        super().__init__(name="推进对话", **kw)
        self.advance_rel = advance_rel

    def match(self, scene: Scene, ctx: Context):
        return scene.panel

    def plan(self, target, scene: Scene, ctx: Context):
        rx, ry = self.advance_rel
        return scene.panel.rel_point(rx, ry)


class ChevronTrigger(Trigger):
    """底部金色 ▼：剧情对话最通用的推进键，优先级最低。"""

    def __init__(self, icon_key: str = "chevron", **kw):
        kw.setdefault("hint", "▼")
        super().__init__(name="底部推进", **kw)
        self.icon_key = icon_key

    def match(self, scene: Scene, ctx: Context):
        return scene.icon(self.icon_key)

    def plan(self, target, scene: Scene, ctx: Context):
        return int(target.cx), int(target.cy)


# --------------------------------------------------------------------- 触发器表
def build_triggers() -> list[Trigger]:
    """按优先级组装整张触发器表（数字小的先跑）。

    顺序直接对应开发文档写的优先级：
        跳过确认 > 跳过 ▶| > 白色大箭头 → > 选项/关闭面板 > 推进对话 > 底部 ▼
    """
    return [
        # 10：弹窗停留极短，间隔压到最短，慢了就消失
        DialogTrigger(config.CONFIRM_XY, priority=10,
                      interval=config.CONFIRM_INTERVAL,
                      cooldown=config.CONFIRM_COOLDOWN),
        # 20：文档第一优先 —— 见到跳过就点
        SkipTrigger(priority=20, interval=config.LOOP_INTERVAL,
                    cooldown=config.SKIP_COOLDOWN),
        # 30：文档里"其次"的白色大箭头
        ArrowTrigger(priority=30, interval=config.LOOP_INTERVAL,
                     cooldown=config.CLICK_COOLDOWN),
        # 40：聊天面板出现白底选项条 —— 随便点一个
        OptionTrigger(priority=40, interval=config.PANEL_COOLDOWN,
                      cooldown=config.OPTION_COOLDOWN),
        # 45：面板底部「对话结束」—— 点右上角 ✕ 收尾（要排在"推进对话"前）
        ChatEndTrigger(config.CLOSE_X_FALLBACK, priority=45,
                       interval=config.PANEL_COOLDOWN,
                       cooldown=config.CLOSE_COOLDOWN),
        # 50：面板里既没选项也没结束 —— 循环点面板中部推进
        PanelAdvanceTrigger(config.PANEL_ADVANCE_REL, priority=50,
                            interval=config.PANEL_COOLDOWN,
                            cooldown=config.PANEL_COOLDOWN),
        # 60：兜底，点底部金色 ▼
        ChevronTrigger(priority=60, interval=config.LOOP_INTERVAL,
                       cooldown=config.CLICK_COOLDOWN),
    ]


def plan_action(scene: Scene, now: float = 1e9,
                triggers: list[Trigger] | None = None) -> tuple[Trigger, tuple[int, int]] | None:
    """只看不点：按优先级找出这一帧**应该**点哪个触发器、点在哪。

    给自检/回归用（不需要真的构造 Dispatcher 与点击器）。
    `now` 取一个很大的值，表示所有触发器都已冷却完毕。
    """
    ctx = Context(None, scene, None, now)
    for trig in (triggers if triggers is not None else build_triggers()):
        try:
            target = trig.match(scene, ctx)
        except Exception:                                    # noqa: BLE001
            continue
        if target is None:
            continue
        plan = trig.plan(target, scene, ctx)
        if plan is None:
            continue
        return trig, plan
    return None
