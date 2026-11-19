# -*- coding: utf-8 -*-
"""生成 release/ 里「发给别人」用的附件。

**不再签名了**（实测结论，见 tools/build_exe.py 的 _sign() docstring）：

  给 exe 加 Authenticode 自签名是**负收益**。签完之后「属性 → 数字签名」
  里能看到签名者，Windows 就会拿这个签名者去查微软那边的信誉库；自签名
  证书在微软没有记录，于是照拦不误，而且拦得更「名正言顺」。
  实测对比：有签名 = 必弹蓝色 SmartScreen；不带签名（和 BetterGI.exe
  一样，数字签名页空白）= 只在第一次跳普通的「更多信息 → 仍要运行」。

所以现在 release/ 里只放：

  * `启动 AutoPlotSkip.bat`   双保险的启动入口（和直接双击 exe 等价）
  * `首次使用说明.txt`        给不懂技术的人看的三句话

旧版的 `AutoPlotSkip-CodeSigning.cer` / `首次运行-解除拦截.bat` 会在
main() 里被自动删除，免得留着误导人。
"""

from __future__ import annotations

import os
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(PROJ, "release")
CER_NAME = "AutoPlotSkip-CodeSigning.cer"
SUBJECT = "CN=AutoPlotSkip, O=AutoPlotSkip, C=CN"

# bat 里不要出现反引号（被 cmd 当转义），也尽量别用续行符 ^ —— 中文 + ^
# 很容易在 chcp 65001 下把下一行的开头吞掉，所以每一步都写成独立的单行。

# 编码坑（踩过一次）：中文 Windows 的 cmd 按**系统 ANSI 代码页（GBK）**解析
# .bat 文件本身，`chcp 65001` 只影响之后的输出，管不到正在解析的这份文件。
# 所以 bat 必须以 GBK(cp936) 写盘，否则一行里的中文会被切碎，后半截当成命令
# 去执行（实测报 `'任库' is not recognized as an internal or external command`）。
# 同理，PowerShell 单行命令里也不放中文，中文提示全交给 echo。

BAT = r"""@echo off
rem 先把控制台代码页切回 936，否则中文在 UTF-8 终端下会被按 65001 解析成乱码。
chcp 936 >nul
setlocal
cd /d "%~dp0"
title AutoPlotSkip

rem 为什么这个 bat 只做「直接启动」、绝不申请管理员权限：
rem   提权必须走 ShellExecute(runas)，而 ShellExecute 在启动 exe 前会先弹
rem   「打开文件 - 安全警告 / 你想运行此文件吗?」——就是这个框让用户以为
rem   程序在搞鬼。实测：直接启动零弹框，一旦 runas 就必弹。
rem   所以默认按普通权限启动；需要管理员时请手动右键 exe 选「以管理员身份运行」。

if not exist "%~dp0AutoPlotSkip\AutoPlotSkip.exe" goto NOEXE

start "" "%~dp0AutoPlotSkip\AutoPlotSkip.exe"
exit /b 0

:NOEXE
echo [X] 找不到 AutoPlotSkip\AutoPlotSkip.exe
echo     请把整个文件夹一起拷走，不要只拿这个 bat。
echo.
pause
exit /b 1
"""

# 「每次打开都弹蓝色 SmartScreen」的修复脚本。
# 病因：注册表 HKCU\...\AppCompatFlags\Layers 里被写了一条
#   <exe 路径>  ->  "~ RUNASADMIN"
# 这是 Compatibility Assistant 在程序自己发起过 runas 之后记下来的。
# 一旦有这条，Windows 每次启动都会先走提权流程；对「要求提权 + 发布者在微软
# 没有信誉」的程序，Windows 就会先弹蓝色 SmartScreen。
# 删掉这条即可恢复到「第一次才提示」的正常状态。
FIX_BAT = r"""@echo off
rem 必须先把代码页切回 936：cmd 是用**当前控制台代码页**去解析 bat 文件字节的。
rem 如果用户从 UTF-8 终端（或某些 IDE）启动，控制台是 65001，这份 GBK 写盘的
rem 中文会被按 UTF-8 解析成乱码再输出（实测满屏「锟斤拷」）。
chcp 936 >nul
setlocal enabledelayedexpansion
title 修复 AutoPlotSkip 每次弹 SmartScreen

echo ============================================================
echo  修复：每次打开 AutoPlotSkip 都弹蓝色「Windows 已保护你的电脑」
echo ============================================================
echo.
echo  原因：注册表里被记了一条「强制以管理员身份运行」的兼容层。
echo        删掉它就行。本脚本只删指向 AutoPlotSkip.exe 的那几行，
echo        不动别的程序。
echo.

set "LAYER=HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
set "STORE=HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Compatibility Assistant\Store"
set FOUND=0

echo [1/2] 清理 Layers ...
for /f "tokens=1,*" %%A in ('reg query "!LAYER!" 2^>nul') do (
    set "KEY=%%A"
    set "VAL=%%B"
    echo !KEY! | find /i "AutoPlotSkip" >nul 2>&1
    if not errorlevel 1 (
        echo       删除 !KEY!
        reg delete "!LAYER!" /v "!KEY!" /f >nul 2>&1
        set FOUND=1
    )
)

echo [2/2] 清理 Compatibility Assistant 记录 ...
for /f "tokens=1,*" %%A in ('reg query "!STORE!" 2^>nul') do (
    set "KEY=%%A"
    echo !KEY! | find /i "AutoPlotSkip" >nul 2>&1
    if not errorlevel 1 (
        echo       删除 !KEY!
        reg delete "!STORE!" /v "!KEY!" /f >nul 2>&1
        set FOUND=1
    )
)

echo.
if "!FOUND!"=="1" (
    echo [OK] 已清理完成。现在重新双击 AutoPlotSkip 试试。
) else (
    echo [-] 没找到 AutoPlotSkip 相关的兼容层记录 ——
    echo     说明这次弹窗可能只是「文件从网上传过来」的首次提示。
    echo     弹的时候点「更多信息」再点「仍要运行」，以后就不会弹了。
)
echo.
echo 另外请自查：右键 exe - 属性 - 兼容性 - 里面
echo   「以管理员身份运行此程序」这个勾**不要打**，
echo   打了等于又写回一条兼容层，下次启动照样弹。
echo.
pause
exit /b 0
"""

TXT = r"""AutoPlotSkip —— 崩坏：星穹铁道 自动剧情跳过
================================================

【怎么用】
  1. 双击 AutoPlotSkip\AutoPlotSkip.exe
     或者双击同目录的「启动 AutoPlotSkip.bat」（效果一样）
  2. 点界面上的「启动」
  3. 回游戏进剧情，日志会贴在游戏画面左下角

  如果日志里出现「权限不足 / 连续点击没有反应」，说明游戏是管理员权限
  而本程序不是。这时：关掉程序 → 右键 AutoPlotSkip.exe →
  「以管理员身份运行」。（右键提权不会弹 SmartScreen）

【为什么会弹「Windows 已保护你的电脑」（SmartScreen）】

  只有一种情况会弹，而且**只在第一次**：文件是从网上/聊天软件传过来的
  （带了「来自其他计算机」标记），Windows 对没买微软证书的新程序先拦一次。
  点「更多信息」→「仍要运行」，之后不再弹。

  如果你**每次**打开都弹，那不是正常现象，八成是注册表里被记了一条
  「强制以管理员身份运行」的兼容层。修复方法（二选一）：

    方法一：双击本文件夹里的「修复-每次弹窗.bat」（推荐）

    方法二：手动删除注册表项
      1. Win+R 输入 regedit 回车
      2. 定位到
         HKEY_CURRENT_USER\Software\Microsoft\Windows NT\CurrentVersion
           \AppCompatFlags\Layers
      3. 右侧凡是指向 AutoPlotSkip.exe 的行（值形如「~ RUNASADMIN」）
         全部右键删除
      4. 重新双击 exe

  另外：exe **没有数字签名**，和 BetterGI.exe 一样「属性 → 数字签名」页是
  空的。千万别再给它装自签名证书 —— 装了反而每次必拦，因为 Windows 会拿
  签名者去查微软信誉库，自签名证书查不到。

【它做什么】
  盯住游戏画面，认出「跳过按钮 / 剧情选项 / 对话箭头 / 聊天面板的 ✕」
  就去点一下，把剧情对话推过去。日志显示在游戏画面左下角的小浮层里。

【注意】
  * 游戏以管理员身份运行时，本程序也要以管理员身份运行
    （右键 exe →「以管理员身份运行」；**不要**用「属性→兼容性→以管理员身份
    运行此程序」那个勾，它会写进注册表，之后每次启动都弹 SmartScreen）
  * 不要一边刷材料一边挂着，剧情外的界面它也会试着点
  * 本程序不联网、不改游戏文件，只做截图识别 + 模拟鼠标点击
"""


def _export_cer() -> str | None:
    """把当前机器上那张自签名证书的公开部分导出来。"""
    out = os.path.join(DIST, CER_NAME)
    try:
        os.remove(out)
    except OSError:
        pass
    ps = (
        "$c = Get-ChildItem Cert:\\CurrentUser\\My | "
        "Where-Object { $_.Subject -eq '" + SUBJECT + "' -and $_.HasPrivateKey } | "
        "Sort-Object NotAfter -Descending | Select-Object -First 1; "
        "if (-not $c) { exit 1 }; "
        "Export-Certificate -Cert $c -FilePath '" + out + "' -Type CERT | Out-Null"
    )
    rc = 1
    for shell in ("pwsh", "powershell"):
        try:
            rc = subprocess.call([shell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                                  "-Command", ps])
        except FileNotFoundError:
            continue
        if rc == 0:
            break
    if rc != 0 or not os.path.exists(out):
        print("  [!] 没导出证书 —— 先跑一次 tools/sign_exe.ps1 再打包")
        return None
    print("  已导出证书：%s（%.1f KB）" % (
        os.path.join("release", CER_NAME), os.path.getsize(out) / 1024.0))
    return out


def _write(name: str, text: str, encoding: str = "utf-8") -> str:
    """写成 CRLF 换行。bat 必须用系统 ANSI(GBK)，txt 用 UTF-8。"""
    path = os.path.join(DIST, name)
    data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode(encoding)
    with open(path, "wb") as fh:
        fh.write(data)
    print("  已生成：%s（%s）" % (os.path.join("release", name), encoding))
    return path


def main() -> int:
    if not os.path.isdir(DIST):
        print("release/ 不存在 —— 先跑 python tools\\build_exe.py")
        return 1
    print("生成分发附件 …")
    _write("启动 AutoPlotSkip.bat", BAT, "cp936")
    _write("修复-每次弹窗.bat", FIX_BAT, "cp936")
    _write("首次使用说明.txt", TXT)
    # 旧版附件（导出证书 + 先装证书再双击那套，已废弃）已经不需要了 —— 现在打包
    # 根本不签名，留着这些反而会让人以为「要装证书才不弹」。顺手清掉。
    for legacy in ("首次运行-解除拦截.bat", CER_NAME):
        p = os.path.join(DIST, legacy)
        if os.path.exists(p):
            try:
                os.remove(p)
                print("  已删除过时附件：%s" % legacy)
            except OSError:
                pass
    # 顺手把整个 release/AutoPlotSkip 文件夹也复制一份说明，免得用户只拷文件夹
    inner = os.path.join(DIST, "AutoPlotSkip")
    if os.path.isdir(inner):
        for name, text in (("首次使用说明.txt", TXT),):
            with open(os.path.join(inner, name), "wb") as fh:
                fh.write(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
        print("  已把说明放进 release/AutoPlotSkip/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
