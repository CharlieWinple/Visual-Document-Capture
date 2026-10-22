import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time
import numpy as np
from PySide6.QtWidgets import QApplication
from vdc.stitching.store import SegmentStore
from vdc.gui.window import MainWindow


def test_inferred_page_is_flagged_after_reopen(tmp_path):
    import json
    store = SegmentStore(tmp_path)
    image = np.full((620, 300, 3), 170, np.uint8)
    image[10:610, 30:270] = 255
    store.add(image)
    store.state["geometry_joins"] = [{"position": 200, "end": 620, "requires_review": True}]
    store.manifest_path.write_text(json.dumps(store.state), encoding="utf-8")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    try:
        window.load_directory(tmp_path)
        deadline = time.monotonic()+10
        while time.monotonic() < deadline and (window.review is None or window.job):
            app.processEvents()
            time.sleep(.01)
        assert window.review is not None
        assert window.review.pages[0].source == "height_connected"
        assert not window.review.pages[0].complete
        assert "页高连接" in window.pages.item(0).text()
        assert "页高辅助连接" in window.warning.text()
    finally:
        window.close()
