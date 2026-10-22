"""Opt-in visible desktop check against ONLY the project's synthetic reader.

Run: python examples/desktop_smoke.py --screen 0 --pages 6 --output output/desktop-primary
This temporarily shows a test reader and selection overlay. It does not automate
or inspect other applications, and does not implement product auto-scrolling.
"""
import argparse
import json
from pathlib import Path
import time

from vdc.app.main import enable_dpi_awareness
enable_dpi_awareness()
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QScrollArea
import mss
from vdc.app.fixtures import make_fixture
from vdc.app.session import new_capture
from vdc.capture.region import logical_to_physical
from vdc.capture.worker import CaptureWorker
from vdc.capture.automatic import AutomaticCaptureWorker
from vdc.export.pdf import export_session
from vdc.gui.images import pixmap
from vdc.gui.selection import SelectionOverlay
from vdc.gui.window import MainWindow, load_review


def pump(app, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screen", type=int, default=0)
    parser.add_argument("--pages", type=int, default=6)
    parser.add_argument("--automatic", action="store_true", help="Test native wheel input and automatic completion")
    parser.add_argument("--continuous-speed", type=float, default=0,
                        help="Logical pixels/second; move without waiting for capture between frames")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    app = QApplication([])
    screen = app.screens()[args.screen]
    geo = screen.geometry()
    with mss.mss() as source:
        monitor = next(m for m in source.monitors[1:] if m["left"] == geo.x() and m["top"] == geo.y())
    fixture = make_fixture(page_count=args.pages)
    if args.automatic:
        # Render a known-page-count local PDF before screen capture. Only this
        # test reader uses PDF internals; production capture sees screen pixels.
        import io
        import pymupdf
        from PIL import Image
        import numpy as np
        with pymupdf.open() as pdf:
            for pixels in fixture.pages:
                stream = io.BytesIO()
                Image.fromarray(pixels).save(stream, format="PNG")
                page = pdf.new_page(width=pixels.shape[1], height=pixels.shape[0])
                page.insert_image(page.rect, stream=stream.getvalue())
            pdf.save(args.output / "source.pdf")
        with pymupdf.open(args.output / "source.pdf") as pdf:
            for page, (left, top, right, bottom) in zip(pdf, fixture.bounds):
                raster = page.get_pixmap(alpha=False)
                fixture.canvas[top:bottom, left:right] = np.frombuffer(raster.samples, dtype=np.uint8).reshape(raster.height, raster.width, 3)
    viewer = QMainWindow()
    viewer.setWindowFlags(viewer.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
    viewer.setWindowTitle("VDC desktop test · synthetic content only")
    area = QScrollArea()
    label = QLabel()
    label.setPixmap(pixmap(fixture.canvas))
    area.setWidget(label)
    viewer.setCentralWidget(area)
    viewer.winId()
    viewer.windowHandle().setScreen(screen)
    viewer.setGeometry(geo.x() + 60, geo.y() + 100, 680, 420)
    viewer.show()
    pump(app, .8)
    origin = area.viewport().mapToGlobal(QPoint(0, 0)) - geo.topLeft()
    h = area.viewport().height()
    if area.viewport().width() < 640:
        raise RuntimeError("Test viewport is too narrow")
    expected = logical_to_physical((origin.x(), origin.y(), 640, h), (geo.width(), geo.height()), monitor,
                                   screen.devicePixelRatio())
    overlay = SelectionOverlay(screen, monitor)
    selections = []
    overlay.accepted.connect(selections.append)
    overlay.show()
    overlay.raise_()
    pump(app, .2)
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=origin)
    QTest.mouseMove(overlay, origin + QPoint(640, h))
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=origin + QPoint(640, h))
    QTest.keyClick(overlay, Qt.Key.Key_Return)
    overlay.close()
    if selections != [expected]:
        raise AssertionError(f"Selection mapping mismatch: {selections} vs {expected}")
    pump(app, .4)
    directory = args.output / "session"
    directory.mkdir()
    new_capture(directory, expected)
    viewer.raise_()
    viewer.activateWindow()
    pump(app, .4)
    if args.automatic:
        # Activate ONLY our owned synthetic reader with a native click. Qt's
        # activation request alone can be denied by Windows foreground policy.
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("user32")
        api.WindowFromPoint.argtypes = [wintypes.POINT]
        api.WindowFromPoint.restype = wintypes.HWND
        api.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        api.GetAncestor.restype = wintypes.HWND
        point = wintypes.POINT(expected.left+expected.width//2, expected.top+expected.height//2)
        if api.GetAncestor(api.WindowFromPoint(point), 2) != int(viewer.winId()):
            raise AssertionError("Synthetic reader is obstructed; refusing test input")
        api.SetCursorPos(point.x, point.y)
        api.mouse_event(2, 0, 0, 0, 0)
        api.mouse_event(4, 0, 0, 0, 0)
        pump(app, .3)
    worker = (AutomaticCaptureWorker if args.automatic else CaptureWorker)(directory, expected, delay=.2)
    events, problems = [], []
    worker.progress.connect(lambda event: events.append(event))
    worker.problem.connect(problems.append)
    worker.start()
    positions = list(range(0, area.verticalScrollBar().maximum() + 1, 120))
    if positions[-1] != area.verticalScrollBar().maximum():
        positions.append(area.verticalScrollBar().maximum())
    started = time.monotonic()
    try:
        if args.automatic:
            deadline = time.monotonic()+180
            while worker.isRunning():
                pump(app, .03)
                if problems or time.monotonic() > deadline:
                    raise AssertionError(f"Automatic capture failed: {problems}; {events[-1:]}")
            if not events or events[-1].get("completion_reason") != "visible_bottom_and_no_motion":
                raise AssertionError(f"No automatic completion: {events[-1:]}")
            expected_y = round(area.verticalScrollBar().maximum()*screen.devicePixelRatio())
            if abs(events[-1].get("position", -99999)-expected_y) > 3:
                raise AssertionError(f"Automatic final position mismatch: {events[-1:]}")
            positions = []
        if args.continuous_speed > 0:
            deadline = time.monotonic() + 8
            while not events or events[-1].get("height", 0) == 0:
                pump(app, .02)
                if problems or time.monotonic() > deadline:
                    raise AssertionError(f"Initial capture failed: {problems}")
            moving_started = time.monotonic()
            maximum = area.verticalScrollBar().maximum()
            while area.verticalScrollBar().value() < maximum:
                area.verticalScrollBar().setValue(min(maximum, round(
                    (time.monotonic() - moving_started) * args.continuous_speed)))
                pump(app, .016)
                if problems:
                    raise AssertionError(f"Continuous capture failed: {problems}")
            positions = [maximum]
        for index, position in enumerate(positions):
            area.verticalScrollBar().setValue(position)
            deadline = time.monotonic() + 8
            # Every step must produce a trusted captured position before moving.
            expected_y = round(position * screen.devicePixelRatio())
            while time.monotonic() < deadline:
                pump(app, .04)
                if problems:
                    raise AssertionError(f"Capture failed at {position}: {problems}")
                if events and events[-1].get("height", 0) > 0 and abs(events[-1].get("position", -9999) - expected_y) <= 1:
                    break
            else:
                raise AssertionError(f"No capture at {position}: {events[-1:]}")
        worker.stop()
        while worker.isRunning():
            pump(app, .05)
    finally:
        worker.stop()
        worker.wait(5000)
        viewer.close()
    store, review, thumbs = load_review(directory)
    result = {"screen": screen.name(), "dpr": screen.devicePixelRatio(), "region": expected.as_dict(),
              "source_pages": args.pages, "detected_pages": len(review.pages), "warnings": review.warnings,
              "frames": len(positions), "seconds": round(time.monotonic() - started, 3),
              "continuous_speed": args.continuous_speed,
              "automatic": args.automatic,
              "scroll_commands": events[-1].get("scroll_commands", 0) if events else 0,
              "accepted_frames": events[-1].get("accepted_frames", 0) if events else 0,
              "problems": problems, "pages": [p.__dict__ for p in review.pages]}
    if len(review.pages) == len(fixture.bounds):
        scale = screen.devicePixelRatio()
        errors = [abs(actual - round(truth * scale)) for page, bounds in zip(review.pages, fixture.bounds)
                  for actual, truth in zip((page.left, page.top, page.right, page.bottom), bounds)]
        result["max_page_edge_error_px"] = max(errors)
    (args.output / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if len(review.pages) != args.pages or review.warnings or result.get("max_page_edge_error_px", 999) > 3:
        raise AssertionError(f"Pagination failed: {result}")
    export_session(store, review.pagination(), args.output / "desktop.pdf")
    window = MainWindow()
    window.show()
    window.load_directory(directory)
    deadline = time.monotonic() + 10
    while window.job is not None and time.monotonic() < deadline:
        pump(app, .05)
    if window.job or not window.scene.items():
        raise AssertionError("Desktop preview did not finish")
    window.grab().save(str(args.output / "preview.png"))
    window.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
