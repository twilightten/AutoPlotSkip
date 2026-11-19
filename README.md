# AutoPlotSkip

崩坏：星穹铁道自动剧情跳过 | Honkai: Star Rail auto plot skip

> 别问为什么会有这个，其实是我太懒了懒得点跳过

## 功能

- **自动跳过**：识别右上角跳过按钮 ▶|，点完自动确认「是否跳过当前段落对话」。
- **推进对话**：语句箭头 →、底部金色▼、手机聊天面板的推进点，循环自动点击。
- **聊天面板**：识别白底选项条、点选项、「对话结束」后点面板 ✕。

## 安装

[![](https://img.shields.io/github/v/release/twilightten/AutoPlotSkip?color=4e4c97)](../../releases)

下载 `AutoPlotSkip-v0.1.0.zip`，解压到任意目录，双击 `启动 AutoPlotSkip.bat` 即可。

从源码运行，只需要 Python 3.10+ 和两个库：

```bat
pip install -r requirements.txt
python main.py
```

无界面模式：`python main.py --cli`；只识别不点击：`python main.py --cli --dry-run`。

## 使用

点「▶ 启动」，切回游戏进剧情。游戏若是**以管理员身份**运行的，本程序也要以管理员身份运行，否则点击会被 Windows 的 UIPI 静默丢弃 —— 右键 exe 选「以管理员身份运行」即可。

> **不要去勾**「属性 → 兼容性 → 以管理员身份运行此程序」。那个勾会写进注册表兼容层，之后每次启动都被强制提权，反而每次都弹蓝色 SmartScreen。

节奏可以在「节奏」页实时调，改完立刻生效：

- **截图间隔** `0.50s` —— 弹窗一闪而过没点到，调小。
- **空转间隔** `0.60s` —— 没有可点目标时的慢节奏，省 CPU。
- **触发间隔倍率** `x1.50` —— 同一处被连点、点乱了，调大。

## 关于

- 感谢 [BetterGI](https://github.com/babalae/better-genshin-impact)，提供分层架构与触发器调度的思路。
- 仅供学习与交流。

## License

[MIT](LICENSE)
