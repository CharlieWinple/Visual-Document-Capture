运行 `vdc-validate --output output/phase0` 生成 6 页主样本、快速滚动样本和 20 页样本。参见项目 README 的安装命令。

以上验证器是离线像素处理，不会移动鼠标或采集实际屏幕。

`desktop_smoke.py` 是需显式运行的 Windows 桌面验收脚本，会短暂显示自带合成阅读器和选区覆盖层，采集其真实屏幕像素。脚本仅调整自己测试窗口的滚动条，不控制其他应用，不属于产品自动滚动功能：

```powershell
.\.venv\Scripts\python.exe examples/desktop_smoke.py --screen 0 --pages 20 --output output/desktop-test
```

`--screen` 为 Qt 显示器列表索引，必须选择足够显示测试窗口的屏幕。输出目录需不存在。首次交互体验建议直接使用主程序的「打开演示阅读器」。
