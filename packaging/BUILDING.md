# Windows 免安装包

目标为 Windows 11 x64、Python 3.13。构建采用 PyInstaller onedir，保留独立运行库；将整个目录解压后运行 EXE，不需要 Python。程序未签名。

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[gui,dev]" -c requirements-tested.txt
.\.venv\Scripts\python.exe -m pip install -r packaging/requirements-build.txt
.\.venv\Scripts\python.exe packaging/build.py
```

输出 dist/release 中的免安装 ZIP、项目源代码 ZIP 和 SHA256SUMS.txt。构建脚本收集第三方许可证与版本记录，并排除 sessions、logs。源代码 ZIP 来自 HEAD；发布前先提交所有构建源文件，再运行 `packaging/build.py --assemble-only` 更新源代码与构建信息。

构建过程隔离 PATH，避免误收集其他软件的 DLL。Qt 使用 Windows 11 提供的 ICU ABI，不分发 PATH 中其他软件携带的 icuuc.dll。spec 同时排除此 DLL，避免环境污染。依赖的 Windows 系统组件仍由操作系统提供。

## 自检

运行打包 EXE 的 `--smoke-test <不存在的输出目录>`，会生成自己的合成文档，验证自动拼接、MSS 显示器访问、框选控件、分页预览、裁切编辑、PDF 导出和重新渲染，成功写入 result.json。可设置 `QT_QPA_PLATFORM=offscreen` 以免显示测试窗口；并清空 PYTHONPATH/PYTHONHOME、将 PATH 限制为 System32，从仓库外工作目录启动，检查不依赖开发环境模块。

这不是全新无 Python 电脑的验收，也不代替真实浏览器采集测试。启动错误写入程序旁 logs/application.log；正常会话位于程序旁 sessions。`--sessions-dir` 可覆盖保存目录。

## 发布

确认打包自检和压缩包解压测试通过后，推送源提交。`packaging/publish.py` 使用现有 Git 凭据创建 v0.4.4 草稿、上传准备好的三个附件、比对远端 SHA256 和大小后转为预发布 Release。凭据只保留在进程内，不打印或写入文件。已发布版本不会被覆盖。

技术参考：[PyInstaller 使用文档](https://www.pyinstaller.org/en/stable/usage.html)、[GitHub Releases API](https://docs.github.com/en/rest/releases/releases)、[附件 API](https://docs.github.com/en/rest/releases/assets)。
