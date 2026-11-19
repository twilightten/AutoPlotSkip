# -*- coding: utf-8 -*-
"""把 AutoPlotSkip 打包成单文件 exe（PyInstaller）。

用法（在项目根目录）：
    python tools/build_exe.py             # 打包 dist/AutoPlotSkip/AutoPlotSkip.exe（文件夹版，推荐）
    python tools/build_exe.py --onefile   # 打包成单个 exe（启动慢、个别机器解包会失败）

说明：
  * 资源 assets/（模板 + 封面）会被塞进 exe，运行时解到 sys._MEIPASS；
    aps/config.py 的 ROOT 已经按 sys._MEIPASS 处理，settings.json 写到 exe 旁边。
  * 生成的是**窗口程序**（--noconsole），双击不弹黑框；
    程序自己会申请管理员权限（UAC），否则点不动以管理员运行的游戏。
  * PyInstaller 装在 _buildtools/ 里（不污染系统 Python），找不到会自动装。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
BUILDTOOLS = os.path.join(PROJ, "_buildtools")
NAME = "AutoPlotSkip"

# 产物目录。历史原因：旧 dist/ 里那个已签名 exe 曾被杀软/索引服务锁住句柄，
# 整个目录删不掉也改不了名，`--distpath dist` 会直接失败。所以现在统一产到
# release/，dist/ 只当历史垃圾留着（不再写入）。
DIST = os.path.join(PROJ, "release")


def _ensure_pyinstaller() -> None:
    if BUILDTOOLS not in sys.path:
        sys.path.insert(0, BUILDTOOLS)
    try:
        import PyInstaller  # noqa: F401
        return
    except ImportError:
        pass
    print("正在安装 PyInstaller 到 _buildtools/ …")
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "--target", BUILDTOOLS,
        "pyinstaller", "--disable-pip-version-check",
    ])
    sys.path.insert(0, BUILDTOOLS)


def _sign(exe: str) -> None:
    """调用 tools/sign_exe.ps1 给 exe 自签名（**默认不做**，只有 -​-sign 才走）。

    历史与实测结论（重要）：
      加 Authenticode 数字签名这条路，对「发给别人」是**负收益**。
      签名之后「属性 → 数字签名」里能看到签名者，但这恰恰会让 Windows
      把 exe 当成「有发布者的正规软件」去查微软那边的信誉库；自签名证书的
      发布者在微软没有信誉记录，于是照拦，而且拦得更「理直气壮」——
      实测用户那边：有签名 = 必弹蓝色 SmartScreen，没签名（像 BetterGI
      那样）反而只是第一次的「更多信息 → 仍要运行」。

      所以打包默认**不签名**，让 exe 的「数字签名」页保持空白，
      和 BetterGI 的 BetterGI.exe 一个样子。要用签名版自己加 -​-sign。

    优先用 pwsh（PowerShell 7），退回 Windows PowerShell 5.1 ——
    实测 5.1 在这台机器上跑 `Get-ChildItem Cert:\\CurrentUser\\My` 会报
    DriveNotFoundException: 找不到驱动器 Cert，导致签名静默失败。
    """
    ps1 = os.path.join(PROJ, "tools", "sign_exe.ps1")
    if not os.path.exists(ps1):
        return
    print("\n正在给 exe 自签名（--sign）…")
    last_rc = None
    for shell in ("pwsh", "powershell"):
        try:
            rc = subprocess.call(
                [shell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", ps1, "-Exe", exe],
                cwd=PROJ,
            )
        except FileNotFoundError:
            continue                                    # 这个 shell 没装，换下一个
        except Exception as exc:                        # noqa: BLE001
            print("  签名脚本启动失败：%s" % exc)
            return
        if rc == 0:
            return
        last_rc = rc
    if last_rc is not None:
        print("  签名没成功（退出码 %d）；exe 仍可运行。" % last_rc)


def main(argv: list[str]) -> int:
    """默认打成**文件夹版**（onedir）：双击 `dist/AutoPlotSkip/AutoPlotSkip.exe`。

    单文件版（--onefile）虽然只有一个 exe，但它每次启动都要把 60MB 内容解包到
    临时目录：启动慢 10~20 秒，而且在部分机器上因沙箱/杀软限制会直接失败
    （实测报 `Failed to extract VCRUNTIME140.dll: Permission denied` /
    `Could not create temporary directory!`）。所以默认不用它。

    签名策略：**默认不签名**。理由见 _sign() docstring —— 自签名反而会
    触发蓝色 SmartScreen，不带签名才和 BetterGI.exe 一致。要签名加 --sign。
    """
    onedir = "--onefile" not in argv
    # --console：打成带控制台的调试版（名字加 _console 后缀，不覆盖正式版），
    # 双击/命令行启动能看到 traceback，用定位「exe 一开就退出」这类问题。
    console = "--console" in argv
    name = NAME + "_console" if console else NAME
    _ensure_pyinstaller()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", name,
        "--onedir" if onedir else "--onefile",
        "--console" if console else "--windowed",   # 正式版不要黑框
        # 注意：**不要加 --uac-admin**。
        # 加了它清单里就是 requireAdministrator，Windows 11 对「要求提权 + 发布者
        # 不被微软认可」的程序，双击时会先弹蓝色的 SmartScreen「已保护你的电脑」，
        # 而自签名证书（哪怕已装进受信任根）微软那边根本不认，绕不过去。
        # 实测：清单 asInvoker 时双击零弹框（`--no-elevate` 跑一晚上都没弹过）。
# 需要管理员时由用户手动右键「以管理员身份运行」解决 —— 那种方式
        # 走的是标准 UAC 授权框，用户熟悉，也不会触发 SmartScreen。
        "--distpath", DIST,
        "--workpath", os.path.join(PROJ, "build"),
        "--specpath", os.path.join(PROJ, "build"),
        # 资源：模板 + 封面
        "--add-data", os.path.join(PROJ, "assets") + os.pathsep + "assets",
        # tkinter 的 tcl/tk 数据由 PyInstaller 自动带上；opencv/numpy 同理
        "--collect-submodules", "cv2",
        "--exclude-module", "matplotlib",
        "--exclude-module", "scipy",
        "--exclude-module", "pandas",
        "--exclude-module", "pytest",
        # 图标（有就带上）
        *(["--icon", os.path.join(PROJ, "assets", "icon.ico")]
          if os.path.exists(os.path.join(PROJ, "assets", "icon.ico")) else []),
        # 版本资源：填上「属性 → 详细信息」里的产品名/版本/版权。
        # 没有它 exe 的这些字段全是空的，SmartScreen 更容易直接拦下来。
        *(["--version-file", os.path.join(PROJ, "assets", "version_info.txt")]
          if os.path.exists(os.path.join(PROJ, "assets", "version_info.txt")) else []),
        os.path.join(PROJ, "main.py"),
    ]

    print(" ".join(cmd))
    # PyInstaller 装在 _buildtools 里，子进程要靠 PYTHONPATH 才 import 得到
    env = dict(os.environ)
    env["PYTHONPATH"] = BUILDTOOLS + os.pathsep + env.get("PYTHONPATH", "")
    rc = subprocess.call(cmd, cwd=PROJ, env=env)
    if rc != 0:
        print("打包失败，退出码", rc)
        return rc

    out = os.path.join(DIST, name + ("" if onedir else ".exe"))
    if os.path.isdir(out):
        out = os.path.join(out, name + ".exe")
    if os.path.exists(out):
        size = os.path.getsize(out) / (1024.0 * 1024.0)
        print("已生成 %s（%.1f MB）" % (out, size))
        # 顺手把 README 也复制到 release，方便整包发出去。
        # 注意：**不要**把根目录的 run.bat 拷进去 —— 那是给源码运行用的，
        # 里面用 PowerShell Start-Process -Verb RunAs 自动提权，会弹
        # 「打开文件 - 安全警告 / 你想运行此文件吗?」。分发包里只留
        # tools/make_extras.py 生成的「启动 AutoPlotSkip.bat」。
        for extra in ("README.md",):
            src = os.path.join(PROJ, extra)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(DIST, extra))
        # 默认**不签名**（详见 _sign() 的 docstring）：
        # 带 Authenticode 签名反而必弹蓝色 SmartScreen，因为 Windows 会拿
        # 签名者去查微软信誉库，自签名证书查不到，拦得更彻底。
        # 不带签名就和 BetterGI.exe 一样，「数字签名」页是空的。
        # 想要签名版：python tools/build_exe.py --sign
        if "--sign" in argv and not console:
            _sign(out)
    else:
        print("没有找到产物：", out)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
