Visual Document Capture 0.4.3 — Windows x64 免安装预览版

1. 将 ZIP 完整解压到可写目录，例如“文档”文件夹下。
2. 双击 VisualDocumentCapture.exe。无需安装 Python。
3. 不要单独移动 EXE；_internal 文件夹是运行依赖。
4. 采集会话保存在程序旁的 sessions，日志在 logs。升级前保留这两个目录。

首次体验建议先打开演示阅读器，框选纸张及两侧背景，避开窗口边框、工具栏和滚动条。
副屏模式需在自动设置中选择“副屏可见”，阅读区必须保持可见，不能最小化或被遮挡。
暂停快捷键：副屏模式 Ctrl+Shift+F8；前台模式 Esc。
页高辅助连接、低置信度或不完整页面需要检查后导出。

这是未签名的预发布程序。Windows 可能显示发布者未知提示；发布文件的 SHA256 校验值随 Release 提供。
当前验证平台：Windows 11 x64。未完成独立无 Python 电脑和各类阅读器兼容性验收。
图片 PDF 不含可搜索文字层。长空白、混合页尺寸及连续空白页仍可能暂停。

源代码、使用说明和问题反馈：
https://github.com/CharlieWinple/Visual-Document-Capture

第三方组件的许可证见 THIRD_PARTY_LICENSES；详细依赖版本见 BUILD_INFO.json。
