import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time
import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from vdc.gui.editor import VisualEditor
from vdc.gui.window import MainWindow
from vdc.pagination.detector import PageBoundary
from vdc.app.fixtures import make_fixture
from vdc.app.session import Review
from vdc.stitching.store import SegmentStore


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait(app, predicate):
    deadline = time.monotonic()+10
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate(): return
        time.sleep(.01)
    raise AssertionError("Editor workflow timed out")


def drag(view, start, finish):
    a, b = view.mapFromScene(QPointF(*start)), view.mapFromScene(QPointF(*finish))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=a)
    QTest.mouseMove(view.viewport(), b)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=b)


@pytest.mark.parametrize("zoom", [.7, 1.4])
def test_crop_drag_uses_original_coordinates_and_undo(app, zoom):
    page = PageBoundary(1, 100, 1200, 500, 1500, .9, True)
    dialog = VisualEditor(np.full((700, 640, 3), 255, np.uint8), 1000, page)
    dialog.show()
    app.processEvents()
    view = dialog.view
    view.resetTransform()
    view.scale(zoom, zoom)
    view.centerOn(300, 1350)
    original = view.snapshot()
    try:
        drag(view, (100, 1350), (80, 1350))
        assert abs(view.values["left"]-80) <= 1
        assert dialog.spins["left"].value() == view.values["left"]
        QTest.keyClick(view, Qt.Key.Key_Right)
        assert abs(view.values["left"]-81) <= 1
        view.undo()
        view.undo()
        assert view.snapshot() == original
        dialog.spins["top"].setValue(1100)
        assert view.values["top"] == 1100
    finally:
        dialog.close()


def test_split_click_and_calibration_gap(app):
    page = PageBoundary(1, 80, 100, 560, 600, .9, True)
    pixels = np.full((850, 640, 3), 240, np.uint8)
    split = VisualEditor(pixels, 0, page, "split")
    split.show()
    app.processEvents()
    drag(split.view, (300, 300), (300, 420))
    assert abs(split.view.split_y-420) <= 1
    split.close()
    dialog = VisualEditor(pixels, 0, page, "calibrate", gap=20)
    dialog.show()
    app.processEvents()
    try:
        drag(dialog.view, (300, 620), (300, 650))
        assert abs(dialog.gap-50) <= 1
        assert "页间距" in dialog.summary.text()
        dialog.view.set_coordinate("next", 2000)
        assert dialog.gap == 125  # Geometry constraint matches calibration API.
        dialog.view.set_coordinate("bottom", 625)
        assert dialog.view.next_y >= 625
    finally:
        dialog.close()


def test_confirm_cancel_and_persistent_undo(app, tmp_path, monkeypatch):
    fixture = make_fixture(page_count=3)
    store = SegmentStore(tmp_path / "session")
    for frame in fixture.frames(): assert store.add(frame).accepted
    window = MainWindow(tmp_path)
    errors = []
    monkeypatch.setattr(window, "error", errors.append)
    try:
        window.load_directory(store.directory)
        wait(app, lambda: window.job is None and window.review is not None)
        original = list(window.review.pages)
        raw_before = (store.directory / "review.json").read_bytes()
        def cancel(dialog):
            dialog.view.set_coordinate("left", 120)
            return QDialog.DialogCode.Rejected
        monkeypatch.setattr(VisualEditor, "exec", cancel)
        window.visual_edit("crop")
        wait(app, lambda: window.job is None)
        assert (store.directory / "review.json").read_bytes() == raw_before
        def confirm(dialog):
            if dialog.view.mode == "crop":
                dialog.view.set_coordinate("left", original[0].left+8)
            else:
                dialog.view.set_coordinate("split", original[0].top+200)
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(VisualEditor, "exec", confirm)
        window.visual_edit("crop")
        wait(app, lambda: window.job is None)
        assert window.review.pages[0].left == original[0].left+8
        assert window.undo_button.isEnabled()
        window.split_page()
        wait(app, lambda: window.job is None)
        assert len(window.review.pages) == 4
        window.undo_edit()
        wait(app, lambda: window.job is None)
        assert len(window.review.pages) == 3
        window.undo_edit()
        wait(app, lambda: window.job is None)
        assert window.review.pages == original
        reopened = Review.load(store.directory, store.state["width"], store.state["height"])
        assert reopened.pages == original
        assert not errors
    finally:
        window.close()


def test_expanding_context_keeps_edit_and_blocks_close_while_loading(app, tmp_path):
    fixture = make_fixture(page_count=3)
    store = SegmentStore(tmp_path)
    for frame in fixture.frames(): assert store.add(frame).accepted
    page = PageBoundary(1, 100, 300, 500, 500, .5, False)
    dialog = VisualEditor(store.read_rows(250, 550), 250, page, store=store)
    dialog.show()
    app.processEvents()
    dialog.view.set_coordinate("left", 90)
    dialog.expand_context()
    dialog.reject()
    assert dialog.isVisible()
    wait(app, lambda: dialog.job is None)
    assert dialog.view.extent.top() == 0
    assert dialog.view.extent.bottom() == 950
    assert dialog.view.values["left"] == 90
    dialog.close()


def test_job_progress_and_recovery_reference(app, tmp_path, monkeypatch):
    import threading
    from PySide6.QtWidgets import QLabel
    from vdc.gui.jobs import Job
    store = SegmentStore(tmp_path)
    fixture = make_fixture()
    store.add(fixture.canvas[:360])
    store.add(fixture.canvas[100:460])
    window = MainWindow(tmp_path)
    release = threading.Event()
    try:
        def work():
            Job.report("测试处理阶段", 3, 10)
            release.wait(5)
        window.run_job(work, lambda _: None, "开始测试")
        wait(app, lambda: "3/10" in window.status.text())
        assert window.processing.maximum() == 10
        assert window.processing.value() == 3
        release.set()
        wait(app, lambda: window.job is None)
        assert window.processing.isHidden()
        window.directory = tmp_path
        window.update_controls()
        assert window.recovery_button.isEnabled()
        labels = []
        def inspect_dialog(dialog):
            labels.extend(label.text() for label in dialog.findChildren(QLabel))
            dialog.reject()
            return QDialog.DialogCode.Rejected
        monkeypatch.setattr(QDialog, "exec", inspect_dialog)
        window.show_recovery()
        wait(app, lambda: window.job is None and bool(labels))
        assert any("y=100–460" in text for text in labels)
        assert any("充分重叠" in text for text in labels)
    finally:
        release.set()
        wait(app, lambda: window.job is None)
        window.close()
