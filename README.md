# 影之诗助手 v1.1.4

适用于影之诗及影之幻境的桌面助手，包含记牌器、战绩统计、AI 陪玩和卡牌双语字幕。

## 下载与启动

在 [Releases](https://github.com/nanamitoki39-afk/svwba/releases/latest) 下载完整 ZIP，完整解压后运行根目录的 `启动.bat`。首次启动会检查运行环境并准备缺少的依赖；AI 陪玩需要自行配置 API。

详细使用方法见 [使用说明.txt](使用说明.txt)。本仓库提供程序源码，运行时资源使用发布页中的配套 ZIP。

## 开发与验证

Windows、Python 3.11+、Node.js 18+。运行库由 `svwb-probe/setup_env.ps1` 检查；本仓库的自动化测试位于 `tests/`，可运行 `python -m unittest discover -s tests`。

字幕翻译来源及授权标记保留在助手控制台；Live2D 模型的作者和使用要求见使用说明。
