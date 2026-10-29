"""Explicit packaged-runtime smoke check using only generated content."""
import json
import time
from pathlib import Path


def main(directory):
    from vdc.app.main import enable_dpi_awareness
    enable_dpi_awareness()
    import numpy as np
    import pymupdf
    import mss
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from vdc.app.fixtures import make_fixture
    from vdc.automation.engine import AutoEngine
    from vdc.stitching.store import SegmentStore
    from vdc.gui.window import MainWindow
    from vdc.gui.selection import SelectionOverlay
    from vdc.gui.editor import VisualEditor, editor_context
    from vdc.export.pdf import export_session
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    app = QApplication([])
    fixture = make_fixture(page_count=3)
    store = SegmentStore(directory / "session")
    class Reader:
        position = 0
        def scroll(self, units):
            self.position = min(len(fixture.canvas)-360, max(0, self.position+units))
    reader = Reader()
    engine = AutoEngine(store, reader)
    for i in range(3000):
        event = engine.observe(fixture.canvas[reader.position:reader.position+360], i*.1)
        if event.state in ("auto_finished", "needs_recovery"):
            break
    assert event.state == "auto_finished", event.reason
    assert np.array_equal(store.read_rows(0, len(fixture.canvas)), fixture.canvas)
    screen = app.primaryScreen()
    with mss.mss() as source:
        monitor = source.monitors[1]
        assert monitor["width"] > 0
    overlay = SelectionOverlay(screen, monitor)
    selected = []
    overlay.accepted.connect(selected.append)
    overlay.show()
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(80, 80))
    QTest.mouseMove(overlay, QPoint(400, 350))
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(400, 350))
    QTest.keyClick(overlay, Qt.Key.Key_Return)
    assert selected
    overlay.close()
    window = MainWindow(directory)
    assert window.auto_settings.input_mode == "visible_background"
    window.show()
    window.load_directory(directory / "session")
    deadline = time.monotonic()+30
    while (window.job or window.review is None) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert window.review is not None and window.job is None
    assert len(window.review.pages) == 3
    assert window.view.mode == "width"
    window.view.set_mode("actual")
    assert window.view.transform().m11() == 1
    window.focus_button.setChecked(True)
    app.processEvents()
    assert window.top_panel.isHidden() and window.sidebar.isHidden()
    window.focus_button.setChecked(False)
    window.view.set_mode("width")
    app.processEvents()
    page = window.review.pages[0]
    top, pixels = editor_context(store, page)
    editor = VisualEditor(pixels, top, page, parent=window, store=store)
    editor.show()
    app.processEvents()
    editor.view.set_coordinate("left", page.left+2)
    assert editor.view.values["left"] == page.left+2
    editor.reject()
    window.grab().save(str(directory / "preview.png"))
    export_session(store, window.review.pagination(), directory / "sample.pdf")
    with pymupdf.open(directory / "sample.pdf") as doc:
        assert len(doc) == 3
        assert doc[0].get_pixmap().width > 0
    window.close()
    app.processEvents()
    (directory / "result.json").write_text(json.dumps({"passed": True, "pages": 3,
        "checks": ["automatic pixel reconstruction", "MSS monitor access", "selection", "page preview", "crop editor", "PDF export and render", "width preview and 100% zoom", "focus mode", "background default"]}, indent=2), encoding="utf-8")
