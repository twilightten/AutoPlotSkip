# -*- coding: utf-8 -*-
"""AutoPlotSkip 入口。

    python main.py            启动图形界面
    python main.py --check    自检（模板/依赖/窗口），不开界面
    python main.py --cli      无界面跑引擎（终端里看同步日志）
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from aps import __app_name__  # noqa: E402  （单实例要把已有窗口拉到前台，用它当标题）


def _silence_stdio_if_frozen() -> None:
    """打包成 exe（--noconsole）后 sys.stdout/stderr 是 None，
    任何 print() 都会抛异常，把所有输出丢进 devnull。"""
    if not getattr(sys, "frozen", False):
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    devnull = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = devnull
    if sys.stderr is None:
        sys.stderr = devnull


_silence_stdio_if_frozen()


def _crash_log_dir() -> str:
    """崩溃日志写到哪里：打包后写在 exe 旁边，源码运行写在项目根目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return HERE


def _write_crash_log(exc_text: str) -> str | None:
    """把未捕获异常写进 crash.log —— 打包成窗口程序后没有控制台，
    看不到 traceback，只能靠这个文件回传。返回写成功的路径（失败为 None）。"""
    path = os.path.join(_crash_log_dir(), "crash.log")
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("=" * 70 + "\n")
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()) + "\n")
            fh.write(exc_text + "\n")
        return path
    except Exception:  # noqa: BLE001
        return None


def _show_fatal(title: str, text: str) -> None:
    """窗口程序出事时弹个原生消息框，别让用户只看到窗口「闪一下就没」。"""
    try:
        ctypes.windll.user32.MessageBoxW(None, text[:2000], title, 0x10)
    except Exception:  # noqa: BLE001
        pass


def _install_crash_handler() -> None:
    """兜底：任何未捕获异常都落 crash.log + 弹框，而不是静默退出。"""
    import traceback

    def hook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        path = _write_crash_log(text)
        _show_fatal(
            "AutoPlotSkip 出错了",
            text.strip().splitlines()[-1] if text.strip() else "未知错误",
        )
        print(("崩溃：%s\n已写入 %s" % (text, path)) if path else text,
              file=sys.stderr or sys.__stderr__ or open(os.devnull, "w"))

    sys.excepthook = hook
    # Tk 回调里的异常不会经过 excepthook，用 report_callback_exception 接管
    try:
        import tkinter

        def cb_hook(self, exc, val, tb):   # noqa: ANN001
            hook(exc, val, tb)

        tkinter.Tk.report_callback_exception = cb_hook
    except Exception:  # noqa: BLE001
        pass


def _set_dpi_aware() -> None:
    """让 GetClientRect / 截图坐标都是物理像素，避免 125% 缩放下点偏。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
    except Exception:                                    # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:                                # noqa: BLE001
            pass


def _maybe_elevate(argv: list[str]) -> bool:
    """**默认绝不自动提权**，只在用户显式 `--elevate` 时才提。

    米哈游的游戏进程通常是管理员权限（High 完整性），普通权限的程序发过去的
    点击会被 Windows 的 UIPI 静默丢弃 —— 表现就是「怎么点都没反应」。所以
    需要管理员权限，但**不能偷偷自己提**：

    实测踩过的坑（两次都栽在这里）：
      1. `ShellExecute(runas)` 走资源管理器的「打开文件」流程，会先弹
         「打开文件 - 安全警告 / 你想运行此文件吗?」，用户以为程序在搞鬼；
         `SEE_MASK_FLAG_NO_UI` / NOZONECHECKS 都压不住。
      2. **更致命**：只要程序自己发起过一次 runas，Windows 的 Compatibility
         Assistant 就会把路径写进
         `HKCU\\Software\\Microsoft\\Windows NT\\CurrentVersion\\AppCompatFlags\\Layers`
         = `~ RUNASADMIN`。之后**每次**双击都会强制提权，而 Windows 对
         「要求提权 + 发布者在微软无信誉」的程序必先弹蓝色 SmartScreen。
         这就是用户反复报「每次打开还是弹 SmartScreen」的真正原因 ——
         清单里明明是 asInvoker，是注册表兼容层在捣鬼。

    所以现在的策略：默认普通权限启动，权限不够时在日志/界面里明确提示用户
    「右键 → 以管理员身份运行」（右键提权不会写兼容层、也不弹 SmartScreen）。
    """
    from aps import winutil

    if "--no-elevate" in argv:
        return False
    if winutil.is_admin():
        return False
    # 只有显式 --elevate 才走提权（给高级用户/脚本用，且会留下兼容层）
    if "--elevate" not in argv:
        print("当前不是管理员权限：若游戏以管理员运行，点击可能无效。"
              "请关掉后右键程序选「以管理员身份运行」。")
        return False
    print("正在申请管理员权限（--elevate 显式指定）…")
    if winutil.relaunch_as_admin(extra_args=[a for a in argv if a != "--no-elevate"]):
        return True
    print("提权被取消，将以普通权限继续运行（点击可能无效）。")
    return False


def _report_privilege() -> None:
    """只「检查 + 提示」，绝不提权。

    界面里的日志区也会显示这条（引擎启动时会报权限），这里只是保证
    用命令行/双击启动时控制台（源码运行时）也能看到，方便排查
    「点了没反应」到底是不是 UIPI 拦的。
    """
    from aps import winutil

    try:
        if winutil.is_admin():
            return
        print("当前不是管理员权限。若游戏以管理员身份运行，点击会被 Windows 丢弃；"
              "此时请右键程序选「以管理员身份运行」。")
    except Exception:  # noqa: BLE001  权限探测失败不该挡住启动
        pass


def _check() -> int:
    from aps import config
    from aps.capture import find_game_window, grab
    from aps.vision import Recognizer

    print("AutoPlotSkip 自检")
    print("  模板目录:", config.TEMPLATE_DIR)
    rec = Recognizer()
    missing = [k for k in config.TEMPLATES if k not in rec.tpl]
    print("  已加载模板: %d/%d %s" % (len(rec.tpl.raw), len(config.TEMPLATES),
                                   ("缺少: " + ",".join(missing)) if missing else ""))
    for key in config.TEMPLATES:
        if key in rec.tpl:
            g, m = rec.tpl.raw[key]
            print("    %-12s %2dx%-2d  mask=%s" % (key, g.shape[1], g.shape[0],
                                                 "yes" if m is not None else "no"))

    win = find_game_window()
    if win is None:
        print("  游戏窗口: 未找到（先启动崩坏：星穹铁道）")
        return 1 if missing else 0
    print("  游戏窗口: %s  %dx%d @(%d,%d)"
          % (win.title, win.width, win.height, win.left, win.top))
    img = grab(win)
    if img is None:
        print("  截图: 失败")
        return 1
    print("  截图: OK %dx%d mean=%.1f" % (img.shape[1], img.shape[0], float(img.mean())))
    scene = rec.analyze(img)
    print("  识别:", scene.describe())
    return 0 if not missing else 1


def _cli(dry_run: bool) -> int:
    from aps import logutil, settings
    from aps.capture import ScreenGrabber
    from aps.engine import Engine
    from aps.vision import Recognizer

    def log(tag, text, level="info"):
        # 和界面/浮层用同一套清洗：说明类、状态类不打印，点击日志去掉坐标
        tag, text = logutil.clean(tag, text)
        if not logutil.visible(tag, text, level):
            return
        ts = time.strftime("%H:%M:%S", time.localtime())
        print("%s [%s] %s" % (ts, tag, text), flush=True)

    # 和图形界面共用 settings.json，界面里调好的节奏命令行也生效
    settings.load()
    log("节奏", settings.describe())

    eng = Engine(log, recognizer=Recognizer(), grabber=ScreenGrabber(), dry_run=dry_run)
    try:
        eng.run()
    except KeyboardInterrupt:
        print("\n已中断")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="崩坏：星穹铁道 自动剧情跳过")
    ap.add_argument("--check", action="store_true", help="自检后退出")
    ap.add_argument("--cli", action="store_true", help="无界面运行")
    ap.add_argument("--dry-run", action="store_true", help="只识别不点击")
    ap.add_argument("--no-elevate", action="store_true",
                    help="不要自动申请管理员权限")
    ap.add_argument("--elevate", action="store_true",
                    help="立即以管理员身份重启（不等自检）")
    ap.add_argument("--interval", type=float, default=None,
                    help="截图间隔秒数（覆盖 settings.json，例如 0.12）")
    ap.add_argument("--scale", type=float, default=None,
                    help="触发间隔倍率（覆盖 settings.json，例如 1.5）")
    args = ap.parse_args(argv)
    raw = sys.argv[1:] if argv is None else list(argv)

    _set_dpi_aware()
    _install_crash_handler()

    # 单实例：已经开着一个就别再开第二个。
    # 必须放在申请管理员权限**之前** —— 否则每双击一次都会先弹一个 UAC，
    # 用户点了「是」才发现原来已经开着了，体验很差。
    # 只有图形界面才需要单实例；--check / --cli 是命令行工具，可以并行跑。
    if not (args.check or args.cli):
        from aps import single

        if single.is_running():
            single.focus_existing(__app_name__)
            return 0

    # 命令行的节奏参数优先于 settings.json（不落盘，只影响这一次运行）
    if args.interval is not None or args.scale is not None:
        from aps import config, settings
        if args.interval is not None:
            config.LOOP_INTERVAL = settings.clamp("LOOP_INTERVAL", args.interval)
        if args.scale is not None:
            config.TRIGGER_SCALE = settings.clamp("TRIGGER_SCALE", args.scale)

    # 提权只在显式 --elevate 时发生（见 _maybe_elevate 的说明：
    # 自己发起 runas 会被 Compatibility Assistant 记进 RUNASADMIN 兼容层，
    # 之后每次双击都强制提权 → 每次都弹蓝色 SmartScreen）。
    if args.elevate and _maybe_elevate(raw):
        return 0
    if args.check:
        return _check()
    if args.cli:
        return _cli(args.dry_run)

    if not args.no_elevate:
        _report_privilege()

    from aps import single

    if not single.acquire():
        # 竞态兜底：两次双击几乎同时启动，探测时都还没占锁。
        single.focus_existing(__app_name__)
        return 0

    from aps.gui import run
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
