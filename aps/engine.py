# -*- coding: utf-8 -*-
"""行为引擎 —— 截图 → 识别 → 触发器调度 → 点击。

v1 是"一个大 while 循环里 if/else 决定点哪个"，照开发文档的顺序硬编码。
v2 换成 BetterGI 那套**实时触发器调度**（见 triggers.py）：

    Engine.run()
      └─ 每 LOOP_INTERVAL 滴答一次
           ├─ 截图（失败重试 / 窗口丢失自动重找）
           ├─ 识别一次，得到 Scene（所有 trigger 共享）
           ├─ Dispatcher 按优先级轮询各 trigger，命中就点，点完本帧结束
           └─ 什么都没点就放宽到 RECHECK_INTERVAL，省 CPU

触发器优先级（数字小的先跑）：

    10 跳过确认   —— 弹窗只停留很短，慢了就消失
    20 跳过按钮 ▶|
    30 语句箭头 →
    40 剧情选项 / 45 关闭面板 ✕ / 50 推进对话
    60 底部推进 ▼

和 v1 的另一个区别：**永不激活或置顶游戏窗口**。点击交给 input.Clicker，
它自带 UIPI 检查与后台点击，游戏在前台/后台都能工作，也不会打断用户。
"""
from __future__ import annotations

import time
from typing import Callable

import numpy as np

from . import config
from .input import Clicker
from .triggers import (ArrowTrigger, ChatEndTrigger, ChevronTrigger, Context,
                       DialogTrigger, Dispatcher, OptionTrigger,
                       PanelAdvanceTrigger, SkipTrigger, Trigger,
                       build_triggers)
from .vision import Recognizer, Scene


class Engine:
    """主循环。日志通过 `log` 回调抛出，GUI 负责显示。"""

    def __init__(self, log: Callable[..., None], recognizer: Recognizer | None = None,
                 grabber=None, *, dry_run: bool = False, clicker: Clicker | None = None):
        self.log = log
        self.recognizer = recognizer or Recognizer()
        if grabber is None:
            from .capture import ScreenGrabber
            grabber = ScreenGrabber()
        self.grabber = grabber
        self.clicker = clicker or Clicker(dry_run=dry_run)
        self.clicker.dry_run = dry_run
        self.dry_run = dry_run
        self.input_channel_ok: bool | None = None   # 光标自检结果（None=还没测）

        self.running = False
        self.stats = {"frames": 0, "clicks": 0, "skips": 0, "panels": 0}
        self.last_scene: Scene | None = None
        self.last_frame = None          # 最近一帧 BGR（诊断 / 回放用）
        self.last_action = None         # 兼容 GUI：最近一次点击的 (kind, x, y)

        self._stop_requested = False
        self._lost_frames = 0
        self._last_idle_log = 0.0
        self._last_signature = ""
        self._state = "init"            # init / story / idle / paused
        self._stuck_key = ""
        self._stuck_count = 0
        self._stuck_warned = False
        self._last_perm_warn = 0.0
        self._warned_foreground = False

        # 点击有效性（"点了到底生效没有"）
        self._thumb = None              # 上一次点击前的画面缩略
        self._pending_check = False     # 刚点过，下一帧要验证效果
        self._pending_method = ""       # 刚才那次点击用的后端
        self._no_effect = 0             # 连续多少次点击画面毫无变化
        self._no_effect_warned = False
        self._bg_probe_fail = 0         # 后台点击连续无效次数
        self._paused_logged = 0.0

        self.dispatcher = Dispatcher(self._dispatch_log, clock=time.perf_counter,
                                     clicker=self.click_at)
        self._fired_this_tick = False
        self._build_triggers()

    # -- 触发器表 ------------------------------------------------------
    def _build_triggers(self) -> None:
        for trig in build_triggers():
            self.dispatcher.register(trig)

    def trigger(self, name: str) -> Trigger | None:
        for t in self.dispatcher.triggers:
            if t.name == name:
                return t
        return None

    # -- 对外 ---------------------------------------------------------
    def stop(self) -> None:
        self._stop_requested = True
        self.dispatcher.stop()

    def reset(self) -> None:
        self._lost_frames = 0
        self._last_signature = ""
        self._state = "init"
        self._stop_requested = False
        self._stuck_key = ""
        self._stuck_count = 0
        self._stuck_warned = False
        self._last_perm_warn = 0.0
        self._warned_foreground = False
        self._thumb = None
        self._pending_check = False
        self._pending_method = ""
        self._no_effect = 0
        self._no_effect_warned = False
        self._bg_probe_fail = 0
        self._paused_logged = 0.0
        self.dispatcher.reset()

    # -- 主循环 -------------------------------------------------------
    def run(self) -> None:
        self.running = True
        self._stop_requested = False
        self._log("引擎启动", "等待游戏窗口（崩坏：星穹铁道）…")
        self._verify_input_channel()
        try:
            while not self._stop_requested:
                t0 = time.perf_counter()
                try:
                    self.tick()
                except Exception as exc:                     # noqa: BLE001
                    self._log("异常", "%s: %s" % (type(exc).__name__, exc), level="error")
                    time.sleep(0.5)
                # 这一帧点到了东西就按正常节奏跑；没有可点目标就放宽，省 CPU
                # 间隔每帧现读，界面上改「截图间隔」立刻生效，不用重启
                spend = time.perf_counter() - t0
                target = (config.LOOP_INTERVAL if self._fired_this_tick
                          else config.RECHECK_INTERVAL)
                delay = max(config.MIN_LOOP_INTERVAL, target - spend)
                end = time.perf_counter() + delay
                while not self._stop_requested and time.perf_counter() < end:
                    time.sleep(0.02)
        finally:
            self.running = False
            self._log("引擎停止", "已处理 %d 帧，点击 %d 次"
                      % (self.stats["frames"], self.stats["clicks"]))

    # -- 单帧 ---------------------------------------------------------
    def tick(self):
        """跑一帧。返回本帧触发的 trigger（None 表示没做事，便于测试）。"""
        self._fired_this_tick = False
        window = self.grabber.ensure_window()
        if window is None:
            self._set_state("idle", "未找到游戏窗口：请先启动「崩坏：星穹铁道」", throttle=True)
            return None

        frame = self.grabber.grab()
        if frame is None:
            self._set_state("idle", "截图失败：%s" % (self.grabber.last_error or "未知原因"),
                            throttle=True)
            return None

        self.last_frame = frame
        self.stats["frames"] += 1
        self._check_effect(frame)
        scene = self.recognizer.analyze(frame)
        self.last_scene = scene
        self.dispatcher.last_scene = scene
        self.dispatcher.last_frame = frame

        if not scene.in_story:
            self._lost_frames += 1
            if self._lost_frames >= config.LOST_SCENE_FRAMES:
                self._set_state("idle", "画面中没有剧情特征，等待剧情开始…", throttle=True)
            return None

        self._lost_frames = 0
        self._warn_foreground_once(window)
        if self._state != "story":
            self._state = "story"
            self._log("剧情模式", "检测到剧情特征（%s）" % self._describe_marks(scene))
        else:
            sig = scene.describe()
            if sig != self._last_signature:
                self._last_signature = sig
                self._log("识别", sig)

        ctx = Context(frame, scene, window, self.dispatcher.clock())
        fired = self.dispatcher.dispatch(ctx)
        if fired is None:
            self._set_state("story", "剧情进行中，暂无可点目标…", throttle=True)
        return fired

    # -- 由 Dispatcher 回调 -------------------------------------------
    def _dispatch_log(self, tag: str, text: str, level: str = "info") -> None:
        self._log(tag, text, level)

    def click_at(self, ctx: Context, x: int, y: int, trig: Trigger) -> bool:
        """Dispatcher 调这里，把 (x, y) 交给点击层。"""
        # 点之前记一张缩略图，下一帧拿来比"画面到底变没变"
        self._thumb = _thumb(self.last_frame)
        result = self.clicker.click(ctx.window, x, y)
        self._fired_this_tick = result.ok
        if not result.ok:
            if result.method == "blocked":
                self._warn_permission(result.detail)
            elif result.method == "paused":
                now = time.time()
                if now - self._paused_logged >= config.FOREGROUND_PAUSE_LOG_INTERVAL:
                    self._paused_logged = now
                    self._set_state("paused", result.detail)
            elif result.method == "covered":
                now = time.time()
                if now - self._paused_logged >= config.FOREGROUND_PAUSE_LOG_INTERVAL:
                    self._paused_logged = now
                    self._set_state("paused", result.detail)
            else:
                self._log("点击失败", "%s：%s" % (trig.label, result.detail), level="error")
            return False

        self.stats["clicks"] += 1
        kind = _KIND_OF.get(trig.name, "other")
        if kind == "skip":
            self.stats["skips"] += 1
        elif kind in ("panel", "option", "close"):
            self.stats["panels"] += 1
        self.last_action = _ActionView(kind, trig.name, x, y)
        self._pending_check = True
        self._pending_method = result.method
        # 点击日志只报"点了什么"，不报坐标（用户 2026-10-25 指定）。
        # 坐标对使用者没有意义，浮层上还占了半行。
        self._log("点击", trig.label, level="action" if kind != "confirm" else "warn")
        self._track_stuck(trig)
        return True

    # -- 点击有效性 ----------------------------------------------------
    def _verify_input_channel(self) -> None:
        """启动后自检一次：光标到底能不能被本进程移动。

        不能的话，后面每一次点击都是白点 —— 与其让用户盯着"点了 200 次
        也没反应"，不如一开始就说清楚。默认只在配置里打开时做（会把光标
        闪一下）；真出问题时由 `_check_effect` 自动补做一次。
        """
        if self.dry_run or not config.PROBE_CURSOR_ON_START:
            return
        self._probe_input("启动自检")

    def _probe_input(self, why: str) -> bool:
        try:
            from .input import probe_cursor_control
            ok, detail = probe_cursor_control()
        except Exception as exc:                             # noqa: BLE001
            self._log("输入自检", "跳过（%s: %s）" % (type(exc).__name__, exc), "warn")
            return True
        self.input_channel_ok = ok
        if ok:
            self._log("输入自检", "%s：%s" % (why, detail), "ok")
        else:
            self._log("输入自检", "%s：%s" % (why, detail), level="error")
            self._log("输入自检",
                      "光标都不受控，SendInput 送出的点击必然无效。"
                      "请确认本程序是在你自己的桌面上运行，而不是远程/无头会话。",
                      level="error")
        return ok

    def _check_effect(self, frame) -> None:
        """上一帧点过，这一帧看看画面有没有变化 —— 没有就是在做无用功。

        演练模式（dry_run）不真的点，画面当然不会变，所以要整段跳过，
        否则会刷出一条吓人的「点击无效，请以管理员运行」。
        """
        if not self._pending_check or self.dry_run:
            self._pending_check = False
            return
        prev = self._thumb
        self._pending_check = False
        cur = _thumb(frame)
        if prev is None or cur is None:
            return
        diff = float(np.abs(cur.astype(np.float32) - prev.astype(np.float32)).mean())
        if diff >= config.EFFECT_DIFF_THR:
            self._no_effect = 0
            self._bg_probe_fail = 0
            return

        self._no_effect += 1
        if self._pending_method == "postmessage":
            self._bg_probe_fail += 1
            if (self._bg_probe_fail >= config.BACKGROUND_PROBE_CLICKS
                    and not self.clicker.background_dead):
                self.clicker.background_dead = True
                self._log("点击诊断", "后台消息点击连续 %d 次没有反应，改用前台真实点击"
                          "（请让游戏窗口保持在前台）" % self._bg_probe_fail, "warn")

        if self._no_effect >= config.NO_EFFECT_WARN_CLICKS and not self._no_effect_warned:
            self._no_effect_warned = True
            self._log("点击无效",
                      "连续 %d 次点击后画面完全没有变化，游戏很可能没收到点击。"
                      "最常见原因：本程序不是以管理员身份运行，而游戏是。"
                      "请关闭本程序，右键 AutoPlotSkip.exe 选择「以管理员身份运行」。"
                      % self._no_effect, level="error")
            # 顺手做一次光标自检，把"到底是权限还是整个输入通道不通"分清
            if self.input_channel_ok is None:
                self._probe_input("点击无效时补测")

    # -- 提示 ---------------------------------------------------------
    def _warn_foreground_once(self, window) -> None:
        """游戏不在前台时提一次：说明会用后台点击，而不是去抢焦点。"""
        if self._warned_foreground:
            return
        self._warned_foreground = True
        if getattr(window, "foreground", False):
            self._log("窗口", "游戏在前台，使用真实鼠标点击（点完把光标放回原处）", "ok")
            return
        # 不在前台时的行为必须说准：默认配置下是**暂停**，不是后台点击。
        if config.REQUIRE_FOREGROUND:
            self._log("窗口", "游戏不在前台：点击已暂停，切回游戏就自动继续 —— "
                              "程序不会去抢你的焦点", "warn")
        else:
            self._log("窗口", "游戏不在前台，改用后台消息点击 —— 不抢焦点，"
                              "但部分游戏会忽略消息点击（无效时会自动改回真实点击）",
                      "warn")

    def _warn_permission(self, detail: str) -> None:
        now = time.time()
        if now - self._last_perm_warn < 8.0:
            return
        self._last_perm_warn = now
        self._log("权限不足", detail, level="error")

    def _track_stuck(self, trig: Trigger) -> None:
        """连续点同一类目标却没进展时提醒一次。"""
        key = trig.name
        if key == self._stuck_key:
            self._stuck_count += 1
        else:
            self._stuck_key = key
            self._stuck_count = 1
            self._stuck_warned = False
            return
        if self._stuck_count >= config.STUCK_ALERT_CLICKS and not self._stuck_warned:
            self._stuck_warned = True
            self._log("提示", "连续 %d 次点「%s」都没有变化，可能这段剧情已经结束"
                      % (self._stuck_count, key), level="warn")

    # -- 日志 ---------------------------------------------------------
    def _log(self, tag: str, text: str, level: str = "info") -> None:
        try:
            self.log(tag, text, level)
        except TypeError:
            self.log(tag, text)

    def _set_state(self, state: str, text: str, *, throttle: bool = False) -> None:
        now = time.time()
        changed = self._state != state or not throttle
        self._state = state
        if not throttle:
            self._log("状态", text)
            self._last_idle_log = now
            return
        if changed or now - self._last_idle_log >= config.IDLE_LOG_INTERVAL:
            self._log("状态", text)
            self._last_idle_log = now

    @staticmethod
    def _describe_marks(scene: Scene) -> str:
        names = [config.TEMPLATES[k][1] for k in scene.icons if k in config.TEMPLATES]
        if scene.panel is not None:
            names.append("聊天面板")
        return "/".join(names) if names else "无"


class _ActionView:
    """给 GUI 画点击点用的小对象（保持 v1 的 .x/.y/.kind/.clickable 接口）。"""

    __slots__ = ("kind", "label", "x", "y")

    def __init__(self, kind: str, label: str, x: int, y: int):
        self.kind = kind
        self.label = label
        self.x = x
        self.y = y

    @property
    def clickable(self) -> bool:
        return True


_KIND_OF = {
    "跳过确认": "confirm",
    "跳过按钮": "skip",
    "语句箭头": "arrow",
    "剧情选项": "option",
    "关闭面板": "close",
    "推进对话": "panel",
    "底部推进": "chevron",
}


def _thumb(frame):
    """把一帧压成很小的灰度缩略图，用来判断"点击后画面有没有变化"。"""
    if frame is None:
        return None
    try:
        import cv2
        w, h = config.EFFECT_SIZE
        return cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA).astype(np.uint8)
    except Exception:                                        # noqa: BLE001
        return None


def kind_of(trigger) -> str:
    """触发器（或它的名字）→ 对外的事件类型（GUI 颜色/统计/自检都按这个分类）。"""
    name = getattr(trigger, "name", trigger)
    return _KIND_OF.get(name, "other")

