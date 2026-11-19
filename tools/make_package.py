# -*- coding: utf-8 -*-
"""把 release/ 整理成一个「别人解压就能用」的干净包，放到桌面。

原则：**只留运行必需的东西**
  * 程序本体：AutoPlotSkip\\AutoPlotSkip.exe + _internal\\
  * 启动入口：启动 AutoPlotSkip.bat
  * 说明书：使用说明.txt
  * 排障小工具：修复-每次弹窗.bat（真出问题时用得上，不是测试残留）

明确剔除：
  * README.md          开发者文档（目录结构 / 回归测试 / 改源码），发给别人看不懂
  * version_info.txt   只在打包时给 PyInstaller 读，运行时不碰
  * icon.png           运行时只认 icon.ico 与 cover.png，icon.png 是源素材
  * opencv_videoio_ffmpeg*.dll
                       26.7 MB 的视频编解码 dll；本程序只用 imread/imdecode，
                       实测删掉后主界面与识别链路均正常

用法（先构建，再打包）：
    python tools\\build_exe.py
    python tools\\make_extras.py
    python tools\\make_package.py
"""

from __future__ import annotations

import os
import shutil
import zipfile

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(PROJ, "release")
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
STAGE = os.path.join(PROJ, "build", "_pkg")
NAME = "AutoPlotSkip-v0.1.0"
TOP = os.path.join(STAGE, NAME)
ZIP = os.path.join(DESKTOP, NAME + ".zip")

# 相对 AutoPlotSkip\ 内部要删掉的文件
DROP = [
    "_internal/assets/version_info.txt",
    "_internal/assets/icon.png",
    "_internal/cv2/opencv_videoio_ffmpeg4110_64.dll",
    "首次使用说明.txt",          # 换成新写的 使用说明.txt 放到顶层
]

GUIDE = """\
AutoPlotSkip —— 崩坏：星穹铁道 自动剧情跳过 v0.1.0
================================================================

【怎么用】

  1. 解压后，双击  启动 AutoPlotSkip.bat
     （也可以直接双击  AutoPlotSkip\\AutoPlotSkip.exe，效果一样）
  2. 点界面上的「▶ 启动」
  3. 切回游戏、进剧情即可。同步日志会贴在游戏画面左下角。

  关掉程序：点界面上的「■ 停止」，或直接关窗口。


【它做什么】

  盯着游戏画面，认出这几种东西就去点一下，把剧情推过去：

    * 右上角「跳过 ▶|」按钮 —— 点完会自动再点弹出来的「确认」
    * 屏幕中央的语句大箭头 →
    * 对话框右下角的推进标记
    * 手机聊天面板（推进对话、选选项、点「对话结束」后的 ✕）

  剧情外它也可能会试着点，所以别一边刷材料一边挂着。
  本程序不联网、不改游戏文件，只做「截图识别 + 模拟鼠标点击」。


【按钮与开关】

  启动 / 停止          开始 / 结束自动识别
  截图间隔             多久抓一帧画面（越小越灵敏，也越吃 CPU）
  触发间隔             两次点击之间至少隔多久，防止点太快
  跟随游戏窗口         日志浮层跟着游戏窗口跑（关掉则固定位置）
  仅识别不点击         只把识别结果写进日志，不真的点 —— 用来调试
  启动后自动最小化     点「启动」后把自己的窗口收进任务栏
  启动时切游戏到前台   把游戏窗口拉到最前面
  仅前台时点击         游戏不在前台就暂停，避免误点别的窗口

  节奏参数会自动存进 settings.json（在 exe 旁边），下次打开还是你上次的值。


【日志浮层】

  贴在游戏画面左下角，三行固定文案 + 实时动作：

      AutoPlotSkip已就绪
      请尽量避免在使用该功能时进行除剧情外的其他活动，如刷材料等。
      崩坏：星穹铁道

  它不会抢焦点，鼠标点上去会穿透到游戏，也不会被自己的截图抓到。


【点了没反应 / 权限不足】

  如果日志里出现「权限不足」或「连续点击没有反应」，说明游戏是以管理员
  身份运行的，而本程序不是 —— 这种情况下 Windows 会静默丢弃点击。

  解决：关掉程序 → 右键 AutoPlotSkip.exe →「以管理员身份运行」。

  ※ 不要去勾「属性 → 兼容性 → 以管理员身份运行此程序」那个复选框。
    那个勾会把路径写进注册表，之后每次启动都会被强制提权，
    反而每次都弹蓝色的「Windows 已保护你的电脑」。


【第一次打开被 Windows 拦（SmartScreen）】

  本程序没有买微软的数字签名证书（和 BetterGI 一样），所以从网上下载
  或聊天软件传过来的文件，第一次打开时 Windows 会先拦一下：

      点「更多信息」→ 点「仍要运行」，之后不再弹。

  这是每个没有商业证书的新程序都会遇到的，不是病毒提示。


【如果每次都弹蓝色 SmartScreen】

  那属于异常，不再是正常提示。原因：注册表里被记了一条
  「强制以管理员身份运行」的兼容层（多半是之前点过「以管理员身份运行」
  或用过旧版本）。修复：

    双击  修复-每次弹窗.bat  ，它会只删除指向本程序的那一条记录。

  手动修也可以：Win+R → regedit →
    HKEY_CURRENT_USER\\Software\\Microsoft\\Windows NT\\CurrentVersion
      \\AppCompatFlags\\Layers
  右边凡是指向 AutoPlotSkip.exe 的行（值形如「~ RUNASADMIN」）删掉。


【常见问题】

  Q: 提示「未找到游戏窗口」？
  A: 先把崩坏：星穹铁道开起来，窗口标题要能看到「崩坏：星穹铁道」。
     全屏独占模式下如果抓不到画面，换成「无边框窗口」再试。

  Q: 日志有识别结果，但游戏里没反应？
  A: 八成是权限问题，见上面【点了没反应 / 权限不足】。

  Q: 点得太快 / 太慢？
  A: 在「节奏」里调「截图间隔」和「触发间隔」，边调边看日志。

  Q: 想换封面图 / 图标？
  A: 覆盖 AutoPlotSkip\\_internal\\assets\\cover.png 或 icon.ico 即可。


【系统要求】

  Windows 10 / 11 64 位。不需要装 Python、不需要装 .NET，
  _internal 文件夹是依赖，删了就跑不起来 —— 拷贝时整个文件夹一起拷。


【说明】

  本项目仅供学习交流，与米哈游 / HoYoverse 无关，未获其授权或认可。
  不修改游戏文件、不读写游戏内存、不联网。
  使用本工具可能违反游戏用户协议，由此产生的一切后果由使用者自行承担。
"""


def main() -> int:
    if not os.path.isdir(SRC):
        print("release/ 不存在，先跑 tools/build_exe.py")
        return 1

    if os.path.isdir(STAGE):
        shutil.rmtree(STAGE)
    os.makedirs(TOP)

    # 1) 拷程序本体
    inner_src = os.path.join(SRC, "AutoPlotSkip")
    inner_dst = os.path.join(TOP, "AutoPlotSkip")
    print("拷贝程序本体 …")
    shutil.copytree(inner_src, inner_dst)

    # 2) 剪裁
    print("剪裁 …")
    for rel in DROP:
        path = os.path.join(inner_dst, rel.replace("/", os.sep))
        if os.path.exists(path):
            size = os.path.getsize(path)
            os.remove(path)
            print("  删除 %-58s %8.1f KB" % (rel, size / 1024.0))
        else:
            print("  [跳过] 不存在：%s" % rel)

    # 3) 顶层附件
    print("放顶层附件 …")
    for extra in ("启动 AutoPlotSkip.bat", "修复-每次弹窗.bat"):
        src = os.path.join(SRC, extra)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(TOP, extra))
            print("  %s" % extra)
        else:
            print("  [跳过] 不存在：%s（先跑 tools/make_extras.py）" % extra)
    guide = os.path.join(TOP, "使用说明.txt")
    with open(guide, "wb") as fh:
        fh.write(GUIDE.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    print("  已生成 使用说明.txt（%.1f KB）" % (os.path.getsize(guide) / 1024.0))

    # 4) 统计
    files = []
    total = 0
    for root, _dirs, names in os.walk(TOP):
        for n in names:
            p = os.path.join(root, n)
            files.append(p)
            total += os.path.getsize(p)
    print("打包前：%d 个文件，%.1f MB" % (len(files), total / 1048576.0))

    # 5) 打 zip（中文名用 UTF-8 标志位，Win10 自带解压认得）
    print("压缩到桌面 …")
    if os.path.exists(ZIP):
        os.remove(ZIP)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in files:
            arc = os.path.join(NAME, os.path.relpath(p, TOP)).replace(os.sep, "/")
            zf.write(p, arc)
    print("  %s  %.1f MB" % (ZIP, os.path.getsize(ZIP) / 1048576.0))

    # 6) 列出顶层
    print("\n包内结构：")
    print("  %s/" % NAME)
    for name in sorted(os.listdir(TOP)):
        p = os.path.join(TOP, name)
        if os.path.isdir(p):
            n = sum(len(f) for _r, _d, f in os.walk(p))
            print("    %s\\   (%d 个文件)" % (name, n))
        else:
            print("    %s" % name)

    shutil.rmtree(STAGE, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
