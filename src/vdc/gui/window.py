"""Phase 1 desktop workflow. Processing and capture are delegated to workers."""
from datetime import datetime
from pathlib import Path
import uuid
import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, QTimer, QSize, QEvent, QPoint
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFormLayout, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox, QSplitter, QListView,
    QSystemTrayIcon, QVBoxLayout, QWidget,
)
from vdc.app.session import Review, new_capture, read_capture
from vdc.capture.region import Region
from vdc.capture.worker import CaptureWorker
from vdc.export.pdf import export_session
from vdc.pagination.detector import PageBoundary, detect_page_boundaries
from vdc.stitching.store import SegmentStore
from .images import pixmap
from .jobs import Job
from .selection import RegionSelector


REASONS = {
    "no_reliable_overlap_or_render_changed": "没有找到可靠重叠：可能滚动过快、页面缩放或渲染发生变化。",
    "insufficient_texture": "当前区域过于空白，无法确定滚动位置。",
    "ambiguous_repeated_content": "内容重复，存在多个可能的滚动位置。",
    "frame_size_changed": "截图尺寸发生变化。",
    "before_session_start": "已滚到本次采集起点之前。",
}


def warning_text(value):
    if value.startswith("unsupported_background_or_clipped_edges"):
        return "部分区域看不清纸张侧边或背景，请检查裁切与分页"
    if value.startswith("uncertain_paper_geometry"):
        return "存在无法确认的纸张轮廓，请检查是否漏页"
    return {"no_reliable_page_boundaries": "未能可靠识别原始分页",
            "incomplete_or_uncertain_pages_require_review": "存在不完整或不确定页面，需要逐页检查"}.get(value, value)


def load_review(directory, force=False):
    store = SegmentStore(directory)
    width, height = store.state["width"], store.state["height"]
    if not height:
        raise ValueError("尚未保存有效画面。请开始采集，并在滚动前停留片刻。")
    review = None if force else Review.load(directory, width, height)
    if review is None:
        result = detect_page_boundaries(store.iter_blocks())
        if not result.pages:
            # Explicit provisional slices keep otherwise inaccessible content
            # editable. Never label these as recovered original pages.
            result.pages = [PageBoundary(i + 1, 0, top, width, min(height, top + 1600), 0, False)
                            for i, top in enumerate(range(0, height, 1600))]
            result.warnings.append("未识别原始分页：以下是每 1600 像素一段的待校正内容，请拆分/合并/裁切")
        review = Review(result.pages, result.warnings, width, height)
        review.save(directory)
    thumbs = []
    for page in review.pages:
        image = Image.fromarray(store.read_rows(page.top, page.bottom)[:, page.left:page.right])
        image.thumbnail((140, 180))
        thumbs.append(np.array(image))
    return store, review, thumbs


class MainWindow(QMainWindow):
    def __init__(self, session_root=None):
        super().__init__()
        self.setWindowTitle("Visual Document Capture · 文档采集")
        self.resize(1120, 820)
        self.session_root = Path(session_root or Path.cwd() / "sessions")
        self.directory = None
        self.region = None
        self.worker = None
        self.job = None
        self.store = None
        self.review = None
        self.selector = None
        self.demo = None
        self.closing = False
        self.selecting = False
        self.metadata = {}
        self._build_ui()
        self._build_tray()
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry()
        self.winId()
        self.windowHandle().setScreen(screen)
        self.resize(min(1120, available.width() - 40), min(820, available.height() - 60))
        self.move(available.topLeft() + QPoint(20, 20))
        self.update_controls()

    def _button(self, label, callback, layout):
        button = QPushButton(label)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def _build_ui(self):
        body = QWidget()
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(22, 18, 22, 18)
        heading = QLabel("Visual Document Capture")
        heading.setStyleSheet("font-size: 24px; font-weight: 600; color: #174c66;")
        layout.addWidget(heading)
        layout.addWidget(QLabel("框选阅读区  →  手动滚动采集  →  检查分页  →  导出 PDF"))
        top = QHBoxLayout()
        self.new_button = self._button("新建 · 框选区域", self.select_region, top)
        self.open_button = self._button("打开已有会话", self.open_session, top)
        self.demo_button = self._button("打开演示阅读器", self.open_demo, top)
        top.addStretch()
        layout.addLayout(top)
        self.region_label = QLabel("尚未选择区域。框选时请保留纸张两侧背景和页间空隙。")
        self.path_label = QLabel("会话会自动保存到本地 sessions 文件夹。")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path_label.setWordWrap(True)
        layout.addWidget(self.region_label)
        layout.addWidget(self.path_label)
        controls = QHBoxLayout()
        self.start_button = self._button("开始采集", self.start_capture, controls)
        self.pause_button = self._button("暂停", self.pause_capture, controls)
        self.finish_button = self._button("结束并预览", self.finish_capture, controls)
        self.cancel_button = self._button("停止采集（保留会话）", self.cancel_capture, controls)
        controls.addStretch()
        layout.addLayout(controls)
        self.status = QLabel("就绪 · 可先打开演示阅读器，试用完整采集流程。")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("background: #e9f2f6; color: #173c50; padding: 12px; border-radius: 6px;")
        layout.addWidget(self.status)
        self.warning = QLabel("采集时窗口会最小化；点击任务栏恢复此窗口会自动暂停。每次滚动不超过半屏，并稍作停顿。")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #8a4b06;")
        layout.addWidget(self.warning)
        split = QSplitter()
        self.pages = QListWidget()
        self.pages.setMinimumWidth(180)
        self.pages.setMaximumWidth(250)
        self.pages.setIconSize(QSize(140, 180))
        self.pages.setViewMode(QListView.ViewMode.IconMode)
        self.pages.setMovement(QListView.Movement.Static)
        self.pages.setResizeMode(QListView.ResizeMode.Adjust)
        self.pages.setWrapping(False)
        self.pages.setFlow(QListView.Flow.TopToBottom)
        self.pages.currentRowChanged.connect(self.select_page)
        split.addWidget(self.pages)
        pane = QWidget()
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setBackgroundBrush(QColor("#dfe5ea"))
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        pane_layout.addWidget(self.view, 1)
        zoom = QHBoxLayout()
        self._button("适应窗口", self.fit_preview, zoom)
        self._button("放大", lambda: self.view.scale(1.25, 1.25), zoom)
        self._button("缩小", lambda: self.view.scale(0.8, 0.8), zoom)
        zoom.addStretch()
        pane_layout.addLayout(zoom)
        crop = QHBoxLayout()
        self.bounds = {}
        for key, label in [("left", "左"), ("top", "上"), ("right", "右"), ("bottom", "下")]:
            crop.addWidget(QLabel(label))
            spin = QSpinBox()
            spin.setRange(0, 2_000_000)
            self.bounds[key] = spin
            crop.addWidget(spin)
        self.apply_button = self._button("应用裁切", self.apply_crop, crop)
        pane_layout.addLayout(crop)
        pane_layout.addWidget(QLabel("边界单位为重建内容的像素坐标；右、下边界不包含在页面内。"))
        edit = QHBoxLayout()
        self.split_button = self._button("拆分此页", self.split_page, edit)
        self.merge_button = self._button("与下一页合并", self.merge_page, edit)
        self.delete_button = self._button("删除此页", self.delete_page, edit)
        self.redetect_button = self._button("重新识别分页", self.redetect, edit)
        edit.addStretch()
        pane_layout.addLayout(edit)
        split.addWidget(pane)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)
        footer = QHBoxLayout()
        self.reviewed = QCheckBox("已逐页检查，理解不完整或低置信度页面的提示")
        footer.addWidget(self.reviewed)
        footer.addStretch()
        self.export_button = self._button("导出多页 PDF", self.export_pdf, footer)
        layout.addLayout(footer)
        self.setStyleSheet("QPushButton { padding: 7px 12px; } QListWidget { background: #f5f8fa; } QSpinBox { padding: 3px; }")

    def _build_tray(self):
        icon = QPixmap(32, 32)
        icon.fill(QColor("#174c66"))
        painter = QPainter(icon)
        painter.setPen(QColor("white"))
        painter.drawText(icon.rect(), Qt.AlignmentFlag.AlignCenter, "V")
        painter.end()
        self.setWindowIcon(QIcon(icon))
        self.tray = QSystemTrayIcon(QIcon(icon), self)
        menu = QMenu(self)
        for label, callback in [("显示窗口 / 暂停", self.restore), ("继续采集", self.start_capture),
                                ("结束并预览", self.finish_capture)]:
            action = QAction(label, self)
            action.triggered.connect(callback)
            menu.addAction(action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.restore() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.setToolTip("Visual Document Capture")
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def update_controls(self):
        busy = self.job is not None or self.selecting
        running = self.worker is not None
        paused = running and self.worker.pause_event.is_set()
        for button in [self.new_button, self.open_button, self.demo_button]:
            button.setEnabled(not busy and not running)
        self.start_button.setText("继续采集" if running or self.store is not None else "开始采集")
        self.start_button.setEnabled(not busy and self.region is not None and (not running or paused))
        self.pause_button.setEnabled(running and not paused)
        self.finish_button.setEnabled(running)
        self.cancel_button.setEnabled(running)
        self.pages.setEnabled(not busy and not running)
        has_pages = self.review is not None and bool(self.review.pages)
        selected = has_pages and self.pages.currentRow() >= 0
        for button in [self.apply_button, self.split_button, self.merge_button, self.delete_button]:
            button.setEnabled(not busy and not running and selected)
        self.redetect_button.setEnabled(not busy and not running and self.store is not None)
        self.export_button.setEnabled(not busy and not running and has_pages)
        for spin in self.bounds.values():
            spin.setEnabled(not busy and not running and selected)

    def error(self, message):
        QMessageBox.warning(self, "需要处理", str(message))

    def run_job(self, function, success, message):
        if self.job is not None:
            return
        self.status.setText(message)
        job = Job(function, self)
        self.job = job
        self.update_controls()

        def done():
            self.job = None
            if job.error:
                self.status.setText(f"操作失败：{job.error}")
                if not self.closing:
                    self.error(job.error)
            elif not self.closing:
                try:
                    success(job.result)
                except Exception as exc:
                    self.status.setText(f"操作失败：{exc}")
                    self.error(exc)
            job.deleteLater()
            self.update_controls()
            if self.closing:
                self.close()
        job.finished.connect(done)
        job.start()

    def select_region(self):
        if self.worker or self.job:
            return
        self.selecting = True
        self.update_controls()
        self.hide()

        def begin():
            try:
                self.selector = RegionSelector(self)
                self.selector.selected.connect(self.region_selected)
                self.selector.cancelled.connect(self.selection_cancelled)
                self.selector.start()
            except Exception as exc:
                self.selection_cancelled()
                self.error(exc)
        QTimer.singleShot(300, begin)

    def selection_cancelled(self):
        if self.selector:
            self.selector.cleanup()
        self.selecting = False
        self.showNormal()
        self.update_controls()

    def region_selected(self, region):
        self.selecting = False
        self.showNormal()
        try:
            directory = self.session_root / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
            directory.mkdir(parents=True, exist_ok=False)
            metadata = new_capture(directory, region)
        except Exception as exc:
            self.update_controls()
            self.error(exc)
            return
        self.directory, self.region, self.metadata = directory, region, metadata
        self.store = self.review = None
        self.pages.clear()
        self.scene.clear()
        self.reviewed.setChecked(False)
        self.update_labels()
        self.status.setText("选区已保存。将阅读器滚动至起点，点击开始采集；窗口会最小化。")
        self.update_controls()

    def update_labels(self):
        if self.region:
            r = self.region
            self.region_label.setText(f"选区：({r.left}, {r.top}) · {r.width} × {r.height} 物理像素")
        else:
            self.region_label.setText("此会话没有屏幕选区，仅可预览和导出。")
        self.path_label.setText(f"会话：{self.directory.resolve()}")

    def start_capture(self):
        if self.job or self.selecting or self.region is None:
            return
        if self.worker:
            if not self.worker.pause_event.is_set():
                return
            self.showMinimized()
            QTimer.singleShot(900, self.resume_worker)
            return
        if (self.directory / "session.json").exists():
            response = QMessageBox.question(self, "继续已有会话", "请保持原阅读器、缩放和选区不变，并返回最后采集位置附近。继续会使已有分页修改失效，结束后重新识别。是否继续？")
            if response != QMessageBox.StandardButton.Yes:
                return
        try:
            # Recompute review on continuation even if no extra rows are added.
            review_path = self.directory / "review.json"
            if review_path.exists():
                review_path.replace(self.directory / "review.previous.json")
            self.store = self.review = None
            self.pages.clear()
            self.scene.clear()
            self.worker = CaptureWorker(self.directory, self.region, parent=self,
                                        blocked_window=int(self.winId()))
            self.worker.progress.connect(self.capture_progress)
            self.worker.problem.connect(self.capture_problem)
            self.worker.finished.connect(self.capture_finished)
            self.reviewed.setChecked(False)
            self.showMinimized()
            self.worker.start()
            self.update_controls()
        except Exception as exc:
            self.worker = None
            self.restore()
            self.error(exc)

    def resume_worker(self):
        if self.worker and self.isMinimized():
            self.worker.resume()
            self.update_controls()

    def pause_capture(self):
        if self.worker:
            self.worker.pause()
            self.status.setText("已请求暂停。可回到阅读器调整位置，再点击继续采集。")
            self.update_controls()

    def finish_capture(self):
        if self.worker:
            self.worker.stop()
            self.status.setText("正在结束采集并保存…")

    def cancel_capture(self):
        if self.worker:
            self.worker.stop(cancelled=True)

    def capture_progress(self, data):
        self.metadata = data
        states = {"starting": "准备采集", "capturing": "采集中", "paused": "已暂停",
                  "needs_recovery": "需要回滚恢复", "reviewing": "准备预览", "cancelled": "已停止", "error": "采集异常"}
        label = states.get(data.get("state"), "就绪")
        self.status.setText(f"{label} · 有效帧 {data.get('accepted_frames', 0)} · 已保存高度 {data.get('height', 0)} px · "
                            f"匹配质量 {data.get('quality', 0):.0%} · 分页将在结束后检测\n{data.get('notice', '')}")
        self.tray.setToolTip(f"VDC · {label}")
        self.update_controls()

    def capture_problem(self, reason):
        self.warning.setText(REASONS.get(reason, reason) + " 请返回最后成功采集位置附近，再继续。若结束，仅保留此前已确认内容。")
        self.restore()

    def capture_finished(self):
        worker = self.worker
        self.worker = None
        cancelled = worker.cancelled
        worker.deleteLater()
        self.update_controls()
        if self.closing:
            self.close()
            return
        self.restore()
        if cancelled:
            self.status.setText("采集已停止，已有内容仍保存在会话目录，可通过打开已有会话查看。")
            return
        self.load_directory(self.directory, force=True)

    def restore(self):
        self.pause_capture()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and not self.isMinimized() and self.worker:
            self.pause_capture()

    def open_session(self):
        directory = QFileDialog.getExistingDirectory(self, "选择包含 session.json 的会话文件夹", str(self.session_root))
        if directory:
            if not (Path(directory) / "session.json").is_file():
                self.error("该文件夹尚无 session.json，或没有保存过有效帧。")
                return
            self.load_directory(Path(directory))

    def load_directory(self, directory, force=False):
        directory = Path(directory)

        def loaded(result):
            self.directory = directory
            self.metadata = read_capture(directory)
            if self.metadata.get("state") in ("capturing", "starting", "paused"):
                self.metadata["needs_recovery"] = True
            self.region = Region(**self.metadata["region"]) if self.metadata.get("region") else None
            self.store, self.review, thumbs = result
            self.update_labels()
            self.present_review(thumbs)
        self.run_job(lambda: load_review(directory, force), loaded, "正在读取会话并识别分页…")

    def present_review(self, thumbs, selected=0):
        self.reviewed.setChecked(False)
        self.pages.blockSignals(True)
        self.pages.clear()
        for page, thumb in zip(self.review.pages, thumbs, strict=True):
            badge = "完整" if page.complete and page.confidence >= 0.8 else "待检查"
            item = QListWidgetItem(QIcon(pixmap(thumb)), f"第 {page.page_id} 页 · {badge}\n{page.right-page.left} × {page.bottom-page.top} px")
            item.setSizeHint(QSize(170, 230))
            if not page.complete or page.confidence < 0.8:
                item.setForeground(QColor("#9b5607"))
            self.pages.addItem(item)
        self.pages.blockSignals(False)
        warnings = self.review.export_warnings(self.metadata)
        self.warning.setText("；".join(map(warning_text, warnings)) if warnings else "分页已识别。建议逐页检查页眉、页脚和边界后再导出。")
        self.status.setText(f"分页预览 · {len(self.review.pages)} 页 · 修改会自动保存到本地会话")
        self.scene.clear()
        self.pages.setCurrentRow(min(selected, len(self.review.pages) - 1))
        self.update_controls()

    def select_page(self, index):
        if self.review is None or index < 0 or index >= len(self.review.pages) or self.job:
            return
        page = self.review.pages[index]
        for key, spin in self.bounds.items():
            spin.setMaximum(self.review.width if key in ("left", "right") else self.review.height)
            spin.setValue(getattr(page, key))

        def shown(pixels):
            self.scene.clear()
            self.scene.addPixmap(pixmap(pixels))
            self.scene.setSceneRect(self.scene.itemsBoundingRect())
            self.fit_preview()
            self.status.setText(f"第 {page.page_id} / {len(self.review.pages)} 页 · 置信度 {page.confidence:.0%} · "
                                + ("检测为完整页面" if page.complete else "不完整或人工修改，请检查"))
        self.run_job(lambda: self.store.read_rows(page.top, page.bottom)[:, page.left:page.right], shown, "正在加载页面…")

    def fit_preview(self):
        if not self.scene.sceneRect().isEmpty():
            self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def edit(self, operation):
        if self.job or self.worker or self.review is None:
            return
        index = self.pages.currentRow()
        if index < 0:
            return
        try:
            # Work on a copy; a failed disk write must not publish unsaved edits.
            updated = Review(self.review.pages, self.review.warnings, self.review.width, self.review.height)
            operation(updated, index)
            updated.save(self.directory)
            self.review = updated
            self.load_directory(self.directory)
        except Exception as exc:
            self.error(exc)

    def apply_crop(self):
        values = {key: spin.value() for key, spin in self.bounds.items()}
        self.edit(lambda review, index: review.update(index, **values))

    def split_page(self):
        index = self.pages.currentRow()
        if index < 0 or not self.review:
            return
        page = self.review.pages[index]
        if page.bottom - page.top < 2:
            return
        value, ok = QInputDialog.getInt(self, "拆分页面", "拆分处的全局纵向像素坐标：", (page.top + page.bottom) // 2, page.top + 1, page.bottom - 1)
        if ok:
            self.edit(lambda review, index: review.split(index, value))

    def merge_page(self):
        self.edit(lambda review, index: review.merge(index))

    def delete_page(self):
        if QMessageBox.question(self, "删除页面", "从导出列表删除此页？原始采集块仍保留，可重新识别恢复。") == QMessageBox.StandardButton.Yes:
            self.edit(lambda review, index: review.delete(index))

    def redetect(self):
        if QMessageBox.question(self, "重新分页", "重新检测会替换当前分页修改，是否继续？") == QMessageBox.StandardButton.Yes:
            self.load_directory(self.directory, force=True)

    def export_pdf(self):
        if not self.review or self.job or self.worker:
            return
        warnings = [warning_text(value) for value in self.review.export_warnings(self.metadata)]
        if warnings and not self.reviewed.isChecked():
            self.error("存在待确认页面或采集告警。请检查预览，并勾选底部的逐页检查确认框后再导出。\n\n" + "\n".join(warnings))
            return
        if warnings and QMessageBox.question(self, "确认导出当前内容", "以下问题仍然存在，导出不会补齐缺失内容：\n\n" + "\n".join(warnings) + "\n\n仍要导出当前页面吗？") != QMessageBox.StandardButton.Yes:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "导出图像 PDF", str(self.directory / "document.pdf"), "PDF 文件 (*.pdf)")
        if not filename:
            return
        destination = Path(filename)
        if destination.suffix.lower() != ".pdf":
            destination = destination.with_suffix(".pdf")
            if destination.exists() and QMessageBox.question(self, "覆盖文件", f"{destination.name} 已存在，是否覆盖？") != QMessageBox.StandardButton.Yes:
                return
        self.run_job(lambda: export_session(self.store, self.review.pagination(), destination,
                                           allow_uncertain=self.reviewed.isChecked()),
                     lambda count: self.status.setText(f"导出成功 · {count} 页 · {destination}"),
                     "正在导出无损图像 PDF…")

    def open_demo(self):
        from vdc.app.fixtures import make_fixture
        if self.demo:
            self.demo.showNormal()
            self.demo.raise_()
            return
        fixture = make_fixture()
        self.demo = QMainWindow()
        self.demo.setWindowTitle("VDC 演示阅读器 · 6 页合成文档 · 请手动滚动")
        area = QScrollArea()
        label = QLabel()
        label.setPixmap(pixmap(fixture.canvas))
        area.setWidget(label)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.demo.setCentralWidget(area)
        self.demo.resize(680, 700)
        self.demo.show()

    def closeEvent(self, event):
        if self.worker or self.job:
            self.closing = True
            if self.worker:
                self.worker.stop()
            self.status.setText("正在保存并退出，请稍候…")
            event.ignore()
            return
        if self.selector:
            self.selector.cleanup()
        if self.demo:
            self.demo.close()
        self.tray.hide()
        event.accept()
