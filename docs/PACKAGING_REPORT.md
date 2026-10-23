# Windows 免安装预发布 v0.4.2

日期：2026-10-23。Windows 11 x64 / Python 3.13 / PyInstaller 6.22.0。

新增可复现构建、便携启动器、错误日志、源代码附件、第三方许可证收集和 SHA256 校验。采集算法未修改。便携启动器默认将会话与日志写在 EXE 旁，避免快捷方式启动时因工作目录变化而找不到会话。

首次打包发现 PATH 中 Poppler 的 ICU 78 DLL 被误收集，无法提供 Qt 所需的 Windows ICU 导出符号。已隔离构建 PATH 并排除冲突 DLL，使用 Windows 11 系统 ICU；修复后重新生成 EXE 并验证。

验证：清空 PYTHONPATH/PYTHONHOME、PATH 仅保留 System32，从 tmp 工作目录运行打包 EXE，自检通过合成自动拼接（逐像素一致）、MSS 显示器读取、框选控件、3 页预览、裁切编辑、PDF 导出和渲染。另验证正常 Windows 主窗口出现与正常关闭，检查导出 PDF 渲染图。源码算法/GUI 已有 91 项回归通过。

验证不代表已在另一台完全未安装 Python 的电脑验收，也不替代真实浏览器、阅读器或原生滚轮的完整兼容性测试。预发布程序未代码签名。Release 附 Windows ZIP、对应源代码 ZIP 和 SHA256SUMS.txt；会话、截图、测试 PDF、凭据和虚拟环境不上传。
