# Visual Document Capture

面向 Windows 11 的本地、视觉优先文档采集项目。从用户选定区域的可见像素恢复连续内容和原始分页，不读取 DOM、浏览器内部数据、PDF.js 或网络原始资源。

**当前为 Phase 0 命令行技术原型，不是已完成的桌面软件。** 已实现合成文档、截图模拟、保守纵向配准、磁盘块拼接、分页与多页 PDF 导出。屏幕框选、实时采集、分页编辑 GUI 和自动滚动尚未实现。

## 安装与运行

需要 Python 3.12+。开发环境使用 Python 3.13。Windows PowerShell 在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\vdc-validate.exe --output output/phase0
```

不需要激活虚拟环境。重复运行时指定新的输出目录，例如 `output/phase0-run2`；验证器拒绝覆盖非空目录。所有处理均在本地执行。

如需复现本次已验证依赖版本，安装时添加 `-c requirements-tested.txt`。本轮实测 19 项测试通过，三组共 32 页合成数据精确重建；详见技术报告。

验证器输出：

- `REPORT.md` / `metrics.json`：定量结果、边界案例和依赖版本。
- `sample/input/source.pdf`：已知原始页图生成的测试 PDF。
- `sample/input/frames/` / `ground_truth.json`：截图序列及真实位置。
- `sample/reconstructed.pdf`：只通过截图拼接与分页生成的多页 PDF。
- `sample/session/`：可重新打开的磁盘图像块和会话清单。
- `sample/alignment.json` / `pagination.json`：位移和分页诊断。
- `fast/` / `twenty_pages/`：更快滚动及 20 页合成样本的结果。

运行安装后的 `vdc-validate --help` 可查看参数。Linux/macOS 可用 `.venv/bin/python` 与 `.venv/bin/vdc-validate` 验证算法，但目标桌面平台仍为 Windows 11。

## 算法接口

```python
from vdc.stitching.store import SegmentStore
from vdc.pagination.detector import detect_page_boundaries
from vdc.export.pdf import export_session

store = SegmentStore("sessions/demo")
for rgb_frame in frames:  # RGB uint8，尺寸保持不变
    alignment = store.add(rgb_frame)
    if not alignment.accepted:
        print(alignment.reason)  # 停止；返回最后可信位置重新获取画面
        break
else:
    pagination = detect_page_boundaries(store.iter_blocks())
    export_session(store, pagination, "output/document.pdf")
```

同一会话只允许一个写入者。每次可信新增内容保存为 PNG 块，再原子替换清单；重启重新加载最后可信参考帧。失败帧不会更新位置。Phase 0 不实现首帧之前内容的前插，反向越过会话起点会报警。

## 当前限制

- 假设纵向整数位移、稳定渲染、至少 25% 视口重叠；不保证真实浏览器中的亚像素滚动、鼠标光标、固定工具栏、浮层及动态加载可匹配。
- 分页目前要求近白纸张、均匀较暗背景、可见左右纸边。纯白背景或裁掉纸边时无法确认分页；首尾不完整页面会标记并阻止默认导出。
- 分页置信度是启发式分值，不是成功概率；当前尚未融合跨帧证据与人工校正。
- 重复内容存在视觉不可辨识情形。即使已有重复纹理检测，也不能保证发现跳过完全相同页面。
- 合成数据按精确像素生成，不模拟抗锯齿变化、阴影或复杂网站 UI；成功率不能代表真实使用成功率。
- 磁盘块避免长期持有整条文档图像，但 PDF 导出库内部内存、分页行几何的线性增长及长会话资源上限尚未做完整性能验收。
- 输出为图像 PDF，保留图像比例和像素；默认 96 DPI 只是输出尺寸约定，无法推断原 PDF 物理尺寸、矢量内容或文字层。

## 下一阶段与首次真实采集

本版尚不能框选屏幕。Phase 1 将实现 PySide6 + MSS：框选 → 手动滚动 → 线程采集 → 错误恢复 → 分页预览与修正 → 导出。真实采集验收步骤见 `docs/WINDOWS_ACCEPTANCE.md`，当前全部待验证。

工程设计见 `DEVELOPMENT_PLAN.md`；技术验证结果和失败分析见 `docs/PHASE0_REPORT.md`。Phase 2 的自动滚动、Phase 3 后台窗口和 Phase 4 打包只保留计划及协议接口。

项目不会上传截图或绕过访问控制。GitHub 同步需项目所有者确认；截图、会话、生成 PDF 与虚拟环境已排除在 Git 之外。

## 技术参考

- [OpenCV 模板匹配文档](https://docs.opencv.org/4.x/d4/dc6/tutorial_py_template_matching.html)：用于候选位移搜索；本项目另加重叠验证和歧义拒绝。
- [PyMuPDF 页面接口](https://pymupdf.readthedocs.io/en/latest/page.html)：用于设置各页尺寸并嵌入截图图像。
