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
from vdc.capture.automatic import AutomaticCaptureWorker
from vdc.automation.windows import InputInterrupted
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
        assert (tmp_path / "diagnostics" / "failure.json").exists()
        assert len(list((tmp_path / "diagnostics").glob("*.png"))) == 3
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


def test_window_calibration_replaces_provisional_segments(app, tmp_path, monkeypatch):
    from vdc.gui.editor import VisualEditor
    from PySide6.QtWidgets import QDialog
    store = SegmentStore(tmp_path / "session")
    store.add(np.full((1600, 640, 3), 255, np.uint8))
    window = MainWindow(tmp_path)
    errors = []
    monkeypatch.setattr(window, "error", errors.append)
    def confirm(dialog):
        dialog.view.set_coordinate("next", dialog.view.values["bottom"]+25)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(VisualEditor, "exec", confirm)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    try:
        window.load_directory(store.directory)
        wait_for(app, lambda: window.job is None and window.review is not None)
        for key, value in {"left": 40, "right": 600, "top": 20, "bottom": 520}.items():
            window.bounds[key].setValue(value)
        window.calibrate()
        wait_for(app, lambda: window.job is None)
        assert not errors
        assert len(window.review.pages) == 4
        assert window.review.geometry["source"] == "manual"
        assert all(not p.complete for p in window.review.pages)
    finally:
        window.close()


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


def test_automatic_worker_focus_pause_resume_and_stop(app, tmp_path):
    fixture = make_fixture(page_count=3)
    position, focus, calls = [0], [True], []
    class Source:
        def __init__(self, region): pass
        def grab(self): return fixture.canvas[position[0]:position[0]+360].copy()
        def close(self): pass
    class Driver:
        def __init__(self, region): pass
        def check(self):
            if not focus[0]:
                raise InputInterrupted("test focus changed")
        def rearm(self): self.check()
        def scroll(self, units):
            self.check()
            calls.append(units)
            position[0] = max(0, min(len(fixture.canvas)-360, position[0]+units))
    region = Region(0, 0, 640, 360)
    new_capture(tmp_path, region)
    worker = AutomaticCaptureWorker(tmp_path, region, Source, delay=0, driver_factory=Driver)
    problems = []
    worker.problem.connect(problems.append)
    worker.start()
    try:
        wait_for(app, lambda: len(calls) >= 2)
        focus[0] = False
        wait_for(app, lambda: bool(problems))
        assert worker.pause_event.is_set()
        count = len(calls)
        # A paused worker must issue no more input or advance the store.
        time.sleep(.2)
        assert len(calls) == count
        position[0] = SegmentStore(tmp_path).state["position"]
        focus[0] = True
        worker.resume()
        wait_for(app, lambda: len(calls) > count)
    finally:
        worker.stop()
        assert worker.wait(5000)
    count = len(calls)
    time.sleep(.1)
    assert len(calls) == count
    metadata = read_capture(tmp_path)
    assert metadata["state"] == "reviewing"
    assert metadata["mode"] == "automatic"
    assert metadata["auto_settings"]["step_percent"] == 30


def test_automatic_mode_controls(app, tmp_path):
    window = MainWindow(tmp_path)
    try:
        assert not window.automatic.isChecked()
        assert not window.auto_settings_button.isEnabled()
        window.automatic.setChecked(True)
        assert window.auto_settings_button.isEnabled()
        assert window.start_button.text() == "开始自动采集"
    finally:
        window.close()


def test_blank_probe_worker_preserves_fragments_after_stop_and_reopen(app, tmp_path):
    import json
    calls = []
    class Source:
        def __init__(self, region): pass
        def grab(self): return np.full((360, 640, 3), 255, np.uint8)
        def close(self): pass
    class Driver:
        def __init__(self, region): pass
        def check(self): pass
        def rearm(self): pass
        def scroll(self, units): calls.append(units)
    region = Region(0, 0, 640, 360)
    new_capture(tmp_path, region)
    worker = AutomaticCaptureWorker(tmp_path, region, source_factory=Source, driver_factory=Driver, delay=0)
    problems = []
    worker.problem.connect(problems.append)
    worker.start()
    try:
        wait_for(app, lambda: bool(problems), timeout=15)
        assert worker.pause_event.is_set()
        assert len(calls) == 7
        assert read_capture(tmp_path)["retained_fragments"]
    finally:
        worker.stop()
        assert worker.wait(5000)
    path = next((tmp_path / "fragments").glob("*/manifest.json"))
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "unresolved"
    window = MainWindow(tmp_path)
    try:
        window.load_directory(tmp_path)
        wait_for(app, lambda: window.review is not None and window.job is None)
        assert window.fragments_button.isEnabled()
        assert "独立保留" in window.warning.text()
        assert window.store.state["height"] == 360
    finally:
        window.close()


@pytest.mark.parametrize("background", [False, True])
def test_automatic_window_finishes_into_preview(app, tmp_path, monkeypatch, background):
    import vdc.gui.window as window_module
    from vdc.automation.engine import AutoSettings
    fixture = make_fixture(page_count=1)
    position = [0]
    class Source:
        def __init__(self, region): pass
        def grab(self): return fixture.canvas[position[0]:position[0]+360].copy()
        def close(self): pass
    class Driver:
        def __init__(self, region): pass
        def check(self): pass
        def rearm(self): pass
        def scroll(self, units):
            position[0] = max(0, min(len(fixture.canvas)-360, position[0]+units))
    def factory(*args, **kwargs):
        kwargs.update(delay=0, blocked_window=None, source_factory=Source, driver_factory=Driver)
        return AutomaticCaptureWorker(*args, **kwargs)
    monkeypatch.setattr(window_module, "AutomaticCaptureWorker", factory)
    window = MainWindow(tmp_path)
    problems = []
    restored = []
    original_restore = window.restore
    def restore():
        restored.append(True)
        original_restore()
    monkeypatch.setattr(window, "restore", restore)
    monkeypatch.setattr(window, "error", problems.append)
    try:
        window.region_selected(Region(0, 0, 640, 360))
        window.auto_settings = AutoSettings(end_wait_ms=4000,
            input_mode="visible_background" if background else "foreground")
        window.automatic.setChecked(True)
        window.start_capture()
        if background:
            window.showNormal()  # Showing controls on the other monitor must not pause.
        wait_for(app, lambda: window.worker is None and window.job is None and window.review is not None, timeout=25)
        assert not problems
        assert len(window.review.pages) == 1
        assert window.metadata["completion_reason"] == "visible_bottom_and_no_motion"
        assert window.export_button.isEnabled()
        assert len(window.scene.items()) == 1
        assert bool(restored) is not background
    finally:
        if window.worker:
            window.worker.stop()
            wait_for(app, lambda: window.worker is None)
        window.close()


def test_background_problem_is_passive_and_mode_can_be_selected(app, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QDialog, QComboBox
    window = MainWindow(tmp_path)
    notices, restored = [], []
    monkeypatch.setattr(window.tray, "showMessage", lambda *args: notices.append(args))
    monkeypatch.setattr(window, "restore", lambda: restored.append(True))
    def choose(dialog):
        dialog.findChild(QComboBox).setCurrentIndex(1)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", choose)
    try:
        window.automatic.setChecked(True)
        window.configure_automatic()
        assert window.auto_settings.input_mode == "visible_background"
        window.worker = SimpleNamespace(background=True)
        window.capture_problem("测试暂停")
        assert notices and not restored
    finally:
        window.worker = None
        window.close()
