# -*- coding: utf-8 -*-
"""看一眼浮层长什么样：临时关掉 WDA_EXCLUDEFROMCAPTURE（否则它连我们自己的
截图都不进），截一张全屏图，再把它恢复。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np
import tkinter as tk
from PIL import ImageGrab

from aps import config, logutil
from aps.overlay import LogOverlay, WDA_EXCLUDEFROMCAPTURE, user32

MEDIA = os.path.join(config.ROOT, "testdata", "doc")

root = tk.Tk()
root.geometry("300x200+1500+780")
root.update()

game = tk.Toplevel()
game.title("崩坏：星穹铁道")
game.geometry("1280x720+80+60")
game.configure(bg="black")
label = tk.Label(game, bd=0, highlightthickness=0, bg="black")
label.pack(fill="both", expand=True)
game.update()
img = cv2.imdecode(np.fromfile(os.path.join(MEDIA, "image11.png"), dtype=np.uint8),
                   cv2.IMREAD_COLOR)
cw, ch = label.winfo_width(), label.winfo_height()
k = min(cw / img.shape[1], ch / img.shape[0])
img = cv2.resize(img, (int(img.shape[1] * k), int(img.shape[0] * k)),
                 interpolation=cv2.INTER_AREA)
canvas = np.zeros((ch, cw, 3), np.uint8)
canvas[:img.shape[0], :img.shape[1]] = img
ok, buf = cv2.imencode(".png", canvas)
photo = tk.PhotoImage(data=buf.tobytes())
label.configure(image=photo)
label.image = photo
for _ in range(6):
    game.update()

from aps.capture import find_game_window

win = find_game_window()
print("找到:", win)

ov = LogOverlay(root)
ov.attach(win)
msgs = [
    # 走的是真机上同一套清洗 + 过滤：说明类/状态类会被滤掉，
    # 点击类只留"点了什么"。所以这份列表故意写得和引擎真实日志一样啰嗦。
    ("系统", "AutoPlotSkip v2 已启动，日志同步到游戏左下角", "ok"),
    ("窗口", "已锁定：崩坏：星穹铁道 1280x720", "info"),
    ("权限", "权限正常（游戏 High / 本程序 High）", "ok"),
    ("剧情模式", "检测到剧情特征（跳过按钮/剧情眼睛/对话记录）", "info"),
    ("识别", "有跳过按钮 ▶|，优先点跳过", "info"),
    ("点击", "跳过按钮 ▶| @(963,40) 真实点击 屏幕(1043,121)", "action"),
    ("识别", "弹出「是否跳过当前段落对话」", "warn"),
    ("点击", "跳过确认 ✔ @(773,503)", "warn"),
    ("识别", "剧情进行中，底部出现推进 ▼", "info"),
    ("点击", "底部推进 ▼ @(973,719)", "action"),
    ("点击", "关闭面板 ✕ @(1178,134)", "action"),
]
for m in msgs:
    # 走和真机一模一样的清洗 + 过滤，截图效果才和实际一致
    tag, text = logutil.clean(m[0], m[1])
    if logutil.visible(tag, text, m[2]):
        ov.push(tag, text, m[2])
for _ in range(30):
    ov.pump()
    root.update()
    time.sleep(0.03)

# 临时关掉排除抓屏，只为截图看效果
if ov.hwnd:
    user32.SetWindowDisplayAffinity(ov.hwnd, 0)
for _ in range(10):
    root.update()
time.sleep(0.2)

shot = ImageGrab.grab()
x, y = win.left, win.top
out = os.path.join(config.ROOT, "docs", "overlay.png")
shot.crop((x, y, x + win.width, y + win.height)).save(out)
print("saved", out)

# 恢复
if ov.hwnd:
    user32.SetWindowDisplayAffinity(ov.hwnd, WDA_EXCLUDEFROMCAPTURE)
ov.detach()
root.destroy()
