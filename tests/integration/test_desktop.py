"""Offscreen Qt tests exercise workflow, real thread lifetime and export dialogs."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time
from pathlib import Path
import numpy as np
import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from vdc.app.fixtures import make_fixture
from vdc.app.session import new_capture, read_capture
from vdc.capture.region import Region
from vdc.capture.worker import CaptureWorker
from vdc.gui.selection import SelectionOverlay
from vdc.gui.window import MainWindow
from vdc.stitching.store import SegmentStore


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_for(app, condition, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        # Release Python's GIL so the Python QThread.run body can make progress.
        time.sleep(0.01)
    raise AssertionError("Qt workflow timed out")


def test_selection_drag_resize_and_confirm(app):
    screen = app.primaryScreen()
    geo = screen.geometry()
    dpr = screen.devicePixelRatio()
    overlay = SelectionOverlay(screen, {"left": -1000, "top": 20, "width": round(geo.width() * dpr), "height": round(geo.height() * dpr)})
    selected = []
    overlay.accepted.connect(selected.append)
    overlay.show()
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
    QTest.mouseMove(overlay, QPoint(400, 400))
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(400, 400))
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(400, 250))
    QTest.mouseMove(overlay, QPoint(450, 250))
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(450, 250))
    QTest.keyClick(overlay, Qt.Key.Key_Return)
    assert selected == [Region(-1000 + round(100 * dpr), 20 + round(100 * dpr), round(350 * dpr), round(300 * dpr))]
    overlay.close()


def test_worker_pause_failure_recovery_and_finish(app, tmp_path):
    fixture = make_fixture()
    frame = [fixture.canvas[:360]]

    class Source:
        def __init__(self, region): pass
        def grab(self): return frame[0].copy()
        def close(self): pass

    region = Region(0, 0, 640, 360)
    new_capture(tmp_path, region)
    worker = CaptureWorker(tmp_path, region, Source, delay=0)
    problems = []
    worker.problem.connect(problems.append)
    worker.start()
    try:
        wait_for(app, lambda: (tmp_path / "session.json").exists())
        worker.pause()
        wait_for(app, lambda: read_capture(tmp_path).get("state") == "paused")
        frame[0] = fixture.canvas[1300:1660]
        worker.resume()
        wait_for(app, lambda: bool(problems))
        assert read_capture(tmp_path)["needs_recovery"]
        assert SegmentStore(tmp_path).state["height"] == 360
        frame[0] = fixture.canvas[120:480]
        worker.resume()
        wait_for(app, lambda: read_capture(tmp_path).get("height") == 480)
        assert not read_capture(tmp_path)["needs_recovery"]
    finally:
        worker.stop()
        assert worker.wait(5000)
    assert read_capture(tmp_path)["state"] == "reviewing"


def test_window_preview_edit_and_pdf_export(app, tmp_path, monkeypatch):
    fixture = make_fixture(page_count=3)
    directory = tmp_path / "session"
    store = SegmentStore(directory)
    for frame in fixture.frames():
        assert store.add(frame).accepted
    window = MainWindow(tmp_path)
    errors = []
    monkeypatch.setattr(window, "error", errors.append)
    window.show()
    try:
        window.load_directory(directory)
        wait_for(app, lambda: window.job is None and window.review is not None)
        assert window.pages.count() == 3
        assert len(window.scene.items()) == 1
        window.bounds["left"].setValue(window.review.pages[0].left + 2)
        window.apply_crop()
        wait_for(app, lambda: window.job is None)
        assert not window.review.pages[0].complete
        window.export_pdf()
        assert errors and "勾选" in errors[-1]
        errors.clear()
        window.reviewed.setChecked(True)
        destination = tmp_path / "desktop.pdf"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(destination), "PDF"))
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        window.export_pdf()
        wait_for(app, lambda: window.job is None)
        import pymupdf
        with pymupdf.open(destination) as pdf:
            assert len(pdf) == 3
            assert pdf[0].rect.width == (fixture.pages[0].shape[1] - 2) * .75
        assert not errors
    finally:
        window.close()
        wait_for(app, lambda: window.job is None)


def test_stationary_capture_does_not_write_additional_files(tmp_path):
    frame = make_fixture().canvas[:360]
    store = SegmentStore(tmp_path)
    store.add(frame)
    before = {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()}
    assert store.add(frame).status == "stationary"
    assert before == {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()}


def test_second_writer_is_rejected_without_changing_session(app, tmp_path):
    from PySide6.QtCore import QLockFile
    region = Region(0, 0, 640, 360)
    new_capture(tmp_path, region)
    before = read_capture(tmp_path)
    lock = QLockFile(str(tmp_path / "capture.lock"))
    assert lock.tryLock(0)
    worker = CaptureWorker(tmp_path, region, delay=0)
    problems = []
    worker.problem.connect(problems.append)
    try:
        worker.start()
        wait_for(app, lambda: bool(problems))
        assert worker.wait(5000)
        assert "另一个" in problems[0]
        assert read_capture(tmp_path) == before
    finally:
        worker.stop()
        worker.wait(5000)
        lock.unlock()


def test_close_waits_for_background_job(app, tmp_path):
    window = MainWindow(tmp_path)
    window.show()
    window.run_job(lambda: time.sleep(.08), lambda _: None, "test")
    window.close()
    assert window.closing
    wait_for(app, lambda: window.job is None and not window.isVisible())
