"""Phase 1 desktop workflow. Processing and capture are delegated to workers."""
from datetime import datetime
from dataclasses import replace
from pathlib import Path
import uuid
import time
import json
import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, QTimer, QSize, QEvent, QPoint, QUrl
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFormLayout, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter, QListView,
    QSystemTrayIcon, QVBoxLayout, QWidget, QDialog, QDialogButtonBox, QComboBox, QGridLayout,
)
from vdc.app.session import Review, new_capture, read_capture
from vdc.capture.region import Region
from vdc.capture.worker import CaptureWorker
from vdc.capture.automatic import AutomaticCaptureWorker
from vdc.automation.engine import AutoSettings
from vdc.export.pdf import export_session
from vdc.pagination.detector import PageBoundary, detect_page_boundaries, calibrate_page_boundaries
from vdc.stitching.store import SegmentStore
from .images import pixmap
from .jobs import Job
from .selection import RegionSelector
from .editor import VisualEditor, editor_context


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
    return {"gap_guided_horizontal_bounds_require_review": "已按实际页间隔微调；左右裁切沿用人工标定，请检查首尾页和横向范围",
            "no_reliable_page_boundaries": "未能可靠识别原始分页",
            "page_geometry_conflict_requires_review": "部分页面与学习到的尺寸不符，请检查页间隔或混合尺寸页",
            "calibrated_boundaries_require_review": "有根据标定推算的页边，尚未获得视觉验证，请逐页确认",
            "geometry_assisted_boundaries_require_review": "部分页间隔由同尺寸规律辅助定位，视觉证据较弱，请检查",
            "incomplete_or_uncertain_pages_require_review": "存在不完整或不确定页面，需要逐页检查"}.get(value, value)


def load_review(directory, force=False, existing_store=None):
    store = existing_store or SegmentStore(directory, lambda n, total: Job.report("校验已保存图像", n, total))
    width, height = store.state["width"], store.state["height"]
    if not height:
        raise ValueError("尚未保存有效画面。请开始采集，并在滚动前停留片刻。")
    review = None if force else Review.load(directory, width, height)
    if review is None:
        result = detect_page_boundaries(store.iter_blocks(), lambda n: Job.report("识别页边", n, height))
        if not result.pages:
            # Explicit provisional slices keep otherwise inaccessible content
            # editable. Never label these as recovered original pages.
            result.pages = [PageBoundary(i + 1, 0, top, width, min(height, top + 1600), 0, False, "provisional")
                            for i, top in enumerate(range(0, height, 1600))]
            result.warnings.append("未识别原始分页：以下是每 1600 像素一段的待校正内容，请拆分/合并/裁切")
        review = Review(result.pages, result.warnings, width, height, result.geometry)
        review.save(directory)
    thumbs = []
    for index, page in enumerate(review.pages):
        Job.report("生成页面预览", index, len(review.pages))
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
        self.auto_settings = AutoSettings()
        self.undo_stack = []
        self.quiet_preview = False
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
        layout.addWidget(QLabel("框选阅读区  →  手动 / 自动滚动采集  →  检查分页  →  导出 PDF"))
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
        self.automatic = QCheckBox("自动滚动")
        self.automatic.setToolTip("在自动设置中选择前台滚动或副屏可见、不占用鼠标的滚动方式。")
        controls.addWidget(self.automatic)
        self.auto_settings_button = self._button("自动设置", self.configure_automatic, controls)
        self.automatic.toggled.connect(self.update_controls)
        self.automatic.toggled.connect(self.show_capture_hint)
        self.start_button = self._button("开始采集", self.start_capture, controls)
        self.pause_button = self._button("暂停", self.pause_capture, controls)
        self.finish_button = self._button("结束并预览", self.finish_capture, controls)
        self.cancel_button = self._button("停止采集（保留会话）", self.cancel_capture, controls)
        self.recovery_button = self._button("查看恢复位置", self.show_recovery, controls)
        controls.addStretch()
        layout.addLayout(controls)
        self.status = QLabel("就绪 · 可先打开演示阅读器，试用完整采集流程。")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("background: #e9f2f6; color: #173c50; padding: 12px; border-radius: 6px;")
        layout.addWidget(self.status)
        self.processing = QProgressBar()
        self.processing.hide()
        layout.addWidget(self.processing)
        self.warning = QLabel("支持缓慢连续滚动；主窗口恢复时自动暂停。位置待确认时请减速，无法验证时会暂停提示回滚。")
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
        self.page_heading = QLabel("页面预览 · 采集后可检查和调整页面")
        self.page_heading.setStyleSheet("font-weight: 600; color: #174c66; padding: 6px;")
        pane_layout.addWidget(self.page_heading)
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
        edit = QHBoxLayout()
        edit.addWidget(QLabel("页面编辑"))
        self.visual_crop_button = self._button("调整裁切", lambda: self.visual_edit("crop"), edit)
        self.visual_crop_button.setToolTip("拖动蓝框调整这一页保留的范围；不会增加页数。")
        self.split_button = self._button("拆分页面", self.split_page, edit)
        self.split_button.setToolTip("将分割线拖到两页之间，把当前页面拆成两页。")
        self.merge_button = self._button("与下一页合并", self.merge_page, edit)
        self.undo_button = self._button("撤销修改", self.undo_edit, edit)
        edit.addStretch()
        pane_layout.addLayout(edit)
        self.precise_toggle = QPushButton("+ 精确调整 · 像素坐标")
        self.precise_toggle.setCheckable(True)
        self.precise_toggle.setStyleSheet("text-align: left; color: #174c66; border: none;")
        pane_layout.addWidget(self.precise_toggle)
        self.precise_panel = QWidget()
        self.precise_panel.setObjectName("precisePanel")
        precise = QVBoxLayout(self.precise_panel)
        crop = QGridLayout()
        self.bounds = {}
        for i, (key, label) in enumerate([("left", "左边界 X"), ("right", "右边界 X"), ("top", "上边界 Y"), ("bottom", "下边界 Y")]):
            crop.addWidget(QLabel(label), i // 2, (i % 2) * 2)
            spin = QSpinBox()
            spin.setRange(0, 2_000_000)
            spin.setSuffix(" px")
            spin.setToolTip("在整条拼接长图中的边界坐标，不是裁掉的像素数；预览缩放不影响数值。")
            self.bounds[key] = spin
            crop.addWidget(spin, i // 2, (i % 2) * 2 + 1)
            spin.valueChanged.connect(self.update_crop_size)
        precise.addLayout(crop)
        note = QLabel("以整条拼接长图左上角为 (0, 0)，向右为 X、向下为 Y。\n数值是边界位置，不是裁掉多少；右、下边界不包含在保留区域内。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #526577;")
        precise.addWidget(note)
        apply_row = QHBoxLayout()
        self.crop_size = QLabel("保留尺寸：—")
        apply_row.addWidget(self.crop_size)
        apply_row.addStretch()
        self.apply_button = self._button("应用边界调整", self.apply_crop, apply_row)
        precise.addLayout(apply_row)
        pane_layout.addWidget(self.precise_panel)
        self.precise_panel.hide()
        self.precise_toggle.toggled.connect(self.precise_panel.setVisible)
        self.precise_toggle.toggled.connect(lambda checked: self.precise_toggle.setText(
            ("−" if checked else "+") + " 精确调整 · 像素坐标"))
        self.precise_toggle.toggled.connect(lambda: QTimer.singleShot(0, self.fit_preview))
        more = QHBoxLayout()
        more.addWidget(QLabel("其他操作"))
        self.calibrate_button = self._button("可视化标定", self.calibrate, more)
        self.redetect_button = self._button("重新识别分页", self.redetect, more)
        self.fragments_button = self._button("查看保留片段", self.open_fragments, more)
        self.fragments_button.setToolTip("打开探测截图及连接记录文件夹。截图不会整批加入 PDF；连接情况请查看清单。")
        more.addStretch()
        self.delete_button = self._button("删除此页", self.delete_page, more)
        self.delete_button.setStyleSheet("color: #a23b3b;")
        pane_layout.addLayout(more)
        split.addWidget(pane)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)
        footer = QHBoxLayout()
        self.reviewed = QCheckBox("已逐页检查，理解不完整或低置信度页面的提示")
        footer.addWidget(self.reviewed)
        footer.addStretch()
        self.export_button = self._button("导出多页 PDF", self.export_pdf, footer)
        layout.addLayout(footer)
        self.setStyleSheet("QPushButton { padding: 7px 10px; border: 1px solid #ccd7df; border-radius: 5px; background: #ffffff; } QPushButton:hover { background: #edf5fa; border-color: #739db4; } QPushButton:disabled { color: #929da5; background: #f5f7f9; } QListWidget { background: #f5f8fa; border: 1px solid #d7e0e7; border-radius: 6px; } QSpinBox { padding: 4px; } QWidget#precisePanel { background: #eef3f7; border-radius: 6px; }")

    def update_crop_size(self):
        if not hasattr(self, "crop_size"):
            return
        v = {key: spin.value() for key, spin in self.bounds.items()}
        width, height = v["right"]-v["left"], v["bottom"]-v["top"]
        self.crop_size.setText(f"待应用尺寸：{width} × {height} px" if width > 0 and height > 0
                              else "边界无效：右须大于左，下须大于上")

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
        for label, callback in [("显示窗口", self.restore), ("暂停采集", self.pause_capture), ("继续采集", self.start_capture),
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
        self.fragments_button.setEnabled(bool(self.directory and (self.directory / "fragments").exists()))
        busy = self.job is not None or self.selecting
        running = self.worker is not None
        self.automatic.setEnabled(not busy and not running)
        self.auto_settings_button.setEnabled(not busy and not running and self.automatic.isChecked())
        paused = running and self.worker.pause_event.is_set()
        self.recovery_button.setEnabled(bool(self.directory and not busy and (not running or paused)
                                            and (self.directory / "session.json").exists()))
        for button in [self.new_button, self.open_button, self.demo_button]:
            button.setEnabled(not busy and not running)
        self.start_button.setText("继续采集" if running or self.store is not None else
                                 "开始自动采集" if self.automatic.isChecked() else "开始采集")
        self.start_button.setEnabled(not busy and self.region is not None and (not running or paused))
        self.pause_button.setEnabled(running and not paused)
        self.finish_button.setEnabled(running)
        self.cancel_button.setEnabled(running)
        self.pages.setEnabled(not busy and not running)
        has_pages = self.review is not None and bool(self.review.pages)
        selected = has_pages and self.pages.currentRow() >= 0
        for button in [self.apply_button, self.split_button, self.merge_button, self.delete_button, self.calibrate_button, self.visual_crop_button]:
            button.setEnabled(not busy and not running and selected)
        self.redetect_button.setEnabled(not busy and not running and self.store is not None)
        self.export_button.setEnabled(not busy and not running and has_pages)
        self.undo_button.setEnabled(not busy and not running and bool(self.undo_stack))
        for spin in self.bounds.values():
            spin.setEnabled(not busy and not running and selected)

    def error(self, message):
        QMessageBox.warning(self, "需要处理", str(message))

    def background_capture(self):
        return bool(self.worker and getattr(self.worker, "background", False))

    def show_capture_hint(self):
        if not self.automatic.isChecked():
            text = "支持缓慢连续滚动；主窗口恢复时自动暂停。位置待确认时请减速。"
        elif self.auto_settings.input_mode == "visible_background":
            text = "副屏可见模式：不移动鼠标或切换焦点，可在另一窗口工作。请勿遮挡、移动或最小化阅读器；Ctrl+Shift+F8 / 托盘暂停。"
        else:
            text = "前台模式：开始后有 2.5 秒准备时间。鼠标会移入选区；Esc、移出鼠标或切换窗口可暂停。"
        self.warning.setText(text)

    def configure_automatic(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("自动滚动设置")
        form = QFormLayout(dialog)
        mode = QComboBox()
        mode.addItem("前台滚动（使用鼠标）", "foreground")
        mode.addItem("副屏可见（不占用鼠标和焦点，试验版）", "visible_background")
        mode.setCurrentIndex(mode.findData(self.auto_settings.input_mode))
        form.addRow("滚动方式", mode)
        controls = {}
        for key, label, low, high, suffix in [
            ("step_percent", "目标步长（视口高度）", 15, 45, "%"),
            ("settle_ms", "每步最短等待（越大越慢）", 300, 2000, " ms"),
            ("sample_ms", "截图观察间隔", 50, 500, " ms"),
            ("end_wait_ms", "疑似末尾持续确认时间", 4000, 30000, " ms"),
        ]:
            spin = QSpinBox()
            spin.setRange(low, high)
            spin.setSuffix(suffix)
            spin.setValue(getattr(self.auto_settings, key))
            form.addRow(label, spin)
            controls[key] = spin
        note = QLabel("步长由实际画面位移反馈调整。阅读器加载较慢时，请增加末尾确认时间。")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.auto_settings = AutoSettings(**{key: spin.value() for key, spin in controls.items()}, input_mode=mode.currentData())
            self.show_capture_hint()

    def run_job(self, function, success, message, quiet=False):
        if self.job is not None:
            return
        self.status.setText(message)
        job = Job(function, self)
        self.job = job
        started = time.monotonic()
        stage = [message, 0, 0]
        self.processing.setRange(0, 0)
        self.processing.show()
        timer = QTimer(self)
        def refresh():
            label, completed, total = stage
            detail = f" · {completed}/{total}" if total else ""
            self.status.setText(f"{label}{detail} · 已用时 {time.monotonic()-started:.0f} 秒")
        def progress(label, completed, total):
            stage[:] = [label, completed, total]
            self.processing.setRange(0, total)
            self.processing.setValue(completed)
            refresh()
        job.progress.connect(progress)
        timer.timeout.connect(refresh)
        timer.start(1000)
        self.update_controls()

        def done():
            timer.stop()
            timer.deleteLater()
            self.processing.hide()
            self.job = None
            if job.error:
                self.status.setText(f"操作失败：{job.error}")
                if not self.closing and not quiet:
                    self.error(job.error)
            elif not self.closing:
                try:
                    success(job.result)
                except Exception as exc:
                    self.status.setText(f"操作失败：{exc}")
                    if not quiet:
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
        self.undo_stack.clear()
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
            if self.background_capture():
                self.worker.resume()
                self.update_controls()
            else:
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
            self.undo_stack.clear()
            self.pages.clear()
            self.scene.clear()
            worker_class = AutomaticCaptureWorker if self.automatic.isChecked() else CaptureWorker
            self.worker = worker_class(self.directory, self.region, parent=self,
                                      delay=2.5 if self.automatic.isChecked() else 1.2,
                                      blocked_window=int(self.winId()),
                                      **({"settings": self.auto_settings} if self.automatic.isChecked() else {}))
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
        states = {"starting": "准备采集", "capturing": "采集中", "paused": "已暂停", "pending": "位置暂待确认",
                  "auto_waiting": "自动采集 · 等待并验证", "auto_rollback": "自动采集 · 回滚验证",
                  "auto_blank_probe": "自动采集 · 空白探测（尚未连接）",
                  "auto_finished": "已到可见页底",
                  "needs_recovery": "需要回滚恢复", "reviewing": "准备预览", "cancelled": "已停止", "error": "采集异常"}
        label = states.get(data.get("state"), "就绪")
        self.status.setText(f"{label} · 有效帧 {data.get('accepted_frames', 0)} · 已保存高度 {data.get('height', 0)} px · "
                            f"匹配质量 {data.get('quality', 0):.0%} · 分页将在结束后检测\n{data.get('notice', '')}")
        self.tray.setToolTip(f"VDC · {label}")
        self.update_controls()

    def capture_problem(self, reason):
        self.warning.setText(REASONS.get(reason, reason) + " 点击“查看恢复位置”，对照最后可信截图调整阅读器，再继续。若结束，仅保留此前已确认内容。")
        if self.background_capture():
            self.tray.showMessage("VDC 已暂停", REASONS.get(reason, reason), QSystemTrayIcon.MessageIcon.Warning)
        else:
            self.restore()

    def show_recovery(self):
        if not self.directory or self.job or (self.worker and not self.worker.pause_event.is_set()):
            return
        directory = self.directory
        def read_reference():
            # Read only the committed reference, without decoding the whole session.
            state = json.loads((directory / "session.json").read_text(encoding="utf-8"))
            name = state.get("reference")
            if not name:
                raise ValueError("尚无可信截图，请从起点重新开始采集")
            SegmentStore._safe_filename(name)
            with Image.open(directory / name) as image:
                pixels = np.array(image.convert("RGB"))
            return state, pixels
        def show(result):
            state, pixels = result
            dialog = QDialog(self)
            dialog.setWindowTitle("恢复位置 · 最后可信截图")
            dialog.resize(850, 700)
            layout = QVBoxLayout(dialog)
            position = state["position"]
            page = next((p.page_id for p in self.review.pages if p.top <= position < p.bottom), None) if self.review else None
            location = f"，位于当前分页第 {page} 页附近" if page else ""
            label = QLabel(f"可信截图范围：y={position}–{position+len(pixels)} px{location}。"
                           f"已保存至 {state['height']} px。\n"
                           "请在阅读器中找到图中相同文字或图形，保持原来的缩放和窗口位置，"
                           "滚动到与截图充分重叠后关闭此窗口，点击继续采集。程序会再次验证连接位置。"
                           "这里的页序不是文档印刷页码。")
            label.setWordWrap(True)
            layout.addWidget(label)
            scene = QGraphicsScene(dialog)
            scene.addPixmap(pixmap(pixels))
            view = QGraphicsView(scene)
            layout.addWidget(view)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            dialog.show()
            view.fitInView(scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            dialog.exec()
        self.run_job(read_reference, show, "正在读取最后可信截图…")

    def capture_finished(self):
        worker = self.worker
        self.worker = None
        cancelled = worker.cancelled
        background = getattr(worker, "background", False)
        worker.deleteLater()
        self.update_controls()
        if self.closing:
            self.close()
            return
        if not background:
            self.restore()
        if cancelled:
            self.status.setText("采集已停止，已有内容仍保存在会话目录，可通过打开已有会话查看。")
            return
        if background:
            self.tray.showMessage("VDC 采集已停止", "可从托盘打开窗口查看分页预览；不会自动切换你的工作窗口。")
        self.load_directory(self.directory, force=True, quiet=background)

    def restore(self):
        if not self.background_capture():
            self.pause_capture()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and not self.isMinimized() and self.worker and not self.background_capture():
            self.pause_capture()

    def open_session(self):
        directory = QFileDialog.getExistingDirectory(self, "选择包含 session.json 的会话文件夹", str(self.session_root))
        if directory:
            if not (Path(directory) / "session.json").is_file():
                self.error("该文件夹尚无 session.json，或没有保存过有效帧。")
                return
            self.load_directory(Path(directory))

    def load_directory(self, directory, force=False, selected=0, quiet=False, reuse_store=False):
        directory = Path(directory)
        self.quiet_preview = quiet

        def loaded(result):
            if self.directory != directory or force:
                self.undo_stack.clear()
            self.directory = directory
            self.metadata = read_capture(directory)
            self.metadata["retained_fragments"] = (directory / "fragments").exists()
            self.auto_settings = AutoSettings(**self.metadata.get("auto_settings", {}))
            self.automatic.setChecked(self.metadata.get("mode") == "automatic")
            if self.metadata.get("state") in ("capturing", "starting", "paused", "pending", "auto_waiting", "auto_rollback", "auto_blank_probe"):
                self.metadata["needs_recovery"] = True
            self.region = Region(**self.metadata["region"]) if self.metadata.get("region") else None
            self.store, self.review, thumbs = result
            self.update_labels()
            self.present_review(thumbs, selected)
        existing_store = self.store if reuse_store and self.directory == directory and not force and not self.worker else None
        self.run_job(lambda: load_review(directory, force, existing_store), loaded, "正在读取会话并识别分页…", quiet=quiet)

    def present_review(self, thumbs, selected=0):
        joins = self.store.state.get("geometry_joins", [])
        if joins:
            self.review.pages = [replace(p, complete=False, confidence=min(p.confidence, .65), source="height_connected")
                if any(j["position"] < p.bottom and j["end"] > p.top for j in joins) else p
                for p in self.review.pages]
            note = "存在页高辅助连接：位置由页面尺寸推算，请核对连接处、页序和内容；并非纯视觉定位确认"
            if note not in self.review.warnings:
                self.review.warnings.append(note)
        self.reviewed.setChecked(False)
        self.pages.blockSignals(True)
        self.pages.clear()
        for page, thumb in zip(self.review.pages, thumbs, strict=True):
            badge = "完整" if page.complete and page.confidence >= 0.8 else "待检查"
            if page.source == "provisional":
                name, badge = "内容段", "未识别分页"
            else:
                name = "页"
                if page.source == "calibrated":
                    badge = "标定推算 · 待验证"
                elif page.source == "geometry_verified":
                    badge = "尺寸辅助 · 待检查"
                elif page.source == "height_connected":
                    badge = "页高连接 · 待检查"
            item = QListWidgetItem(QIcon(pixmap(thumb)), f"第 {page.page_id} {name} · {badge}\n{page.right-page.left} × {page.bottom-page.top} px")
            item.setSizeHint(QSize(170, 230))
            if not page.complete or page.confidence < 0.8:
                item.setForeground(QColor("#9b5607"))
            self.pages.addItem(item)
        self.pages.blockSignals(False)
        warnings = self.review.export_warnings(self.metadata)
        self.warning.setText("；".join(map(warning_text, warnings)) if warnings else "分页已识别。建议逐页检查页眉、页脚和边界后再导出。")
        if self.metadata.get("completion_reason") == "visible_bottom_and_no_motion":
            self.warning.setText("自动滚动已结束：多次无位移且可见页底。请核对最后一页，慢加载内容可能需要继续采集。\n" + self.warning.text())
        if self.review.geometry:
            g = self.review.geometry
            method = {"learned": "自动学习", "manual_refined": "人工标定＋自动微调", "manual_gap_refined": "页间隔自动微调"}.get(g["source"], "人工标定")
            self.warning.setText(self.warning.text() + f"\n{method}：页高约 {g['height']} px，页间距约 {g['gap']} px，周期约 {g['period']} px。")
            if "verified_pages" in g:
                self.warning.setText(self.warning.text() + f" 已验证 {g['verified_pages']} 页，待确认 {g['unverified_pages']} 页。")
        self.status.setText(f"分页预览 · {len(self.review.pages)} 页 · 修改会自动保存到本地会话")
        self.scene.clear()
        self.pages.setCurrentRow(min(selected, len(self.review.pages) - 1))
        self.update_controls()

    def open_fragments(self):
        if self.directory and (self.directory / "fragments").exists():
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str((self.directory / "fragments").resolve()))):
                self.error("无法打开片段文件夹：" + str(self.directory / "fragments"))

    def select_page(self, index):
        if self.review is None or index < 0 or index >= len(self.review.pages) or self.job:
            return
        page = self.review.pages[index]
        self.page_heading.setText(f"第 {index+1} / {len(self.review.pages)} 页 · {page.right-page.left} × {page.bottom-page.top} px · "
                                  + ("检测为完整页面" if page.complete else "需要检查"))
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
        self.run_job(lambda: self.store.read_rows(page.top, page.bottom)[:, page.left:page.right], shown, "正在加载页面…",
                     quiet=self.quiet_preview)

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
            updated = Review(self.review.pages, self.review.warnings, self.review.width, self.review.height, self.review.geometry)
            operation(updated, index)
            updated.save(self.directory)
            self.undo_stack.append(self.review)
            self.undo_stack = self.undo_stack[-20:]
            self.review = updated
            self.load_directory(self.directory, selected=index, reuse_store=True)
        except Exception as exc:
            self.error(exc)

    def apply_crop(self):
        values = {key: spin.value() for key, spin in self.bounds.items()}
        self.edit(lambda review, index: review.update(index, **values))

    def split_page(self):
        self.visual_edit("split")

    def undo_edit(self):
        if self.job or self.worker or not self.undo_stack:
            return
        try:
            restored = self.undo_stack[-1]
            restored.save(self.directory)
            self.undo_stack.pop()
            self.review = restored
            self.load_directory(self.directory)
        except Exception as exc:
            self.error(exc)

    def visual_edit(self, mode):
        index = self.pages.currentRow()
        if self.job or self.worker or not self.review or index < 0:
            return
        values = {key: spin.value() for key, spin in self.bounds.items()}
        page = PageBoundary(0, **values, confidence=.5, complete=False, source="manual")
        if mode == "split":
            page = self.review.pages[index]
        try:
            self.review.validate(page)
            if mode == "split" and page.bottom-page.top < 2:
                raise ValueError("当前页面高度不足以拆分")
        except ValueError as exc:
            self.error(exc)
            return
        default_gap = round((self.review.geometry or {}).get("gap", 20))
        if index+1 < len(self.review.pages):
            observed_gap = self.review.pages[index+1].top-page.bottom
            if 0 <= observed_gap <= (page.bottom-page.top)//4:
                default_gap = observed_gap
        def shown(result):
            top, pixels = result
            dialog = VisualEditor(pixels, top, page, mode, default_gap, self, store=self.store)
            try:
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                if mode == "crop":
                    self.edit(lambda review, i: review.update(i, **dialog.view.values))
                elif mode == "split":
                    self.edit(lambda review, i: review.split(i, dialog.view.split_y))
                else:
                    anchor = PageBoundary(0, **dialog.view.values, confidence=.5, complete=False, source="manual")
                    self.apply_calibration(anchor, dialog.gap)
            finally:
                dialog.deleteLater()
        self.run_job(lambda: editor_context(self.store, page), shown, "正在加载原始页面与相邻区域…")

    def merge_page(self):
        self.edit(lambda review, index: review.merge(index))

    def delete_page(self):
        if QMessageBox.question(self, "删除页面", "从导出列表删除此页？原始采集块仍保留，可重新识别恢复。") == QMessageBox.StandardButton.Yes:
            self.edit(lambda review, index: review.delete(index))

    def redetect(self):
        if QMessageBox.question(self, "重新分页", "重新检测会替换当前分页修改，是否继续？") == QMessageBox.StandardButton.Yes:
            self.load_directory(self.directory, force=True)

    def calibrate(self):
        self.visual_edit("calibrate")

    def apply_calibration(self, anchor, gap):
        if not self.review or self.job or self.worker or self.pages.currentRow() < 0:
            return
        try:
            self.review.validate(anchor)
        except ValueError as exc:
            self.error(exc)
            return
        if QMessageBox.question(self, "应用同尺寸标定", "将按当前完整页和间距重建分页列表。未获视觉验证的页边仍会标为待确认。是否替换当前分页编辑？") != QMessageBox.StandardButton.Yes:
            return
        def work():
            result = calibrate_page_boundaries(self.store.iter_blocks(), anchor, gap,
                lambda n: Job.report("微调标定与验证页边", n, self.store.state["height"]))
            review = Review(result.pages, result.warnings, self.store.state["width"], self.store.state["height"], result.geometry)
            review.save(self.directory)
            return load_review(self.directory, existing_store=self.store)
        def shown(result):
            self.undo_stack.append(self.review)
            self.undo_stack = self.undo_stack[-20:]
            self.store, self.review, thumbs = result
            self.present_review(thumbs)
        self.run_job(work, shown, "正在验证标定页与其他页面的尺寸…")

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
                                           allow_uncertain=self.reviewed.isChecked(), progress=Job.report),
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
        self.demo.setWindowTitle("VDC 演示阅读器 · 6 页合成文档")
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
