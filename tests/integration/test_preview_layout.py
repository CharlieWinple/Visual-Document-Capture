import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QGraphicsScene, QDialog, QGraphicsLineItem
from vdc.gui.preview import PreviewView
from vdc.gui.window import MainWindow
from vdc.app.session import Review, new_capture, write_json
from vdc.capture.region import Region
from vdc.pagination.detector import PageBoundary
from vdc.stitching.store import SegmentStore


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait(app, predicate):
    until = time.monotonic()+15
    while time.monotonic() < until:
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Preview timed out")


def test_width_zoom_scroll_and_manual_scale_persist_on_resize(app):
    scene = QGraphicsScene()
    scene.setSceneRect(0, 0, 960, 5000)
    view = PreviewView(scene)
    view.resize(900, 600)
    view.show()
    try:
        wait(app, lambda: abs(view.transform().m11()*960-(view.viewport().width()-12)) < 1)
        assert view.verticalScrollBar().maximum() > 0
        width_scale = view.transform().m11()
        view.set_mode("page")
        assert view.transform().m11() < width_scale/2
        view.set_mode("actual")
        view.zoom(1.25)
        view.resize(700, 500)
        view.new_page()
        app.processEvents()
        assert view.transform().m11() == pytest.approx(1.25)
        assert view.verticalScrollBar().value() == view.verticalScrollBar().minimum()
    finally:
        view.close()


def test_focus_restores_panels_and_defaults_to_background(app, tmp_path):
    window = MainWindow(tmp_path)
    window.resize(1120, 820)
    window.show()
    try:
        app.processEvents()
        assert window.auto_settings.input_mode == "visible_background"
        assert window.details_panel.isHidden() and window.edit_panel.isHidden()
        assert window.view.height() > window.height()*.65
        window.details_toggle.setChecked(True)
        window.edit_toggle.setChecked(True)
        window.focus_button.setChecked(True)
        assert window.top_panel.isHidden() and window.sidebar.isHidden()
        assert window.edit_panel.isHidden() and window.details_panel.isHidden()
        window.focus_button.setChecked(False)
        assert not window.details_panel.isHidden() and not window.edit_panel.isHidden()
    finally:
        window.close()


def test_boundary_shows_contiguous_source_and_saved_mode_is_preserved(app, tmp_path, monkeypatch):
    store = SegmentStore(tmp_path)
    image = np.full((1200, 300, 3), 255, np.uint8)
    image[500:525] = 230
    store.add(image)
    pages = [PageBoundary(1, 0, 0, 300, 500, .45, False),
             PageBoundary(2, 0, 525, 300, 1200, .94, True)]
    Review(pages, [], 300, 1200).save(tmp_path)
    capture = new_capture(tmp_path, Region(0, 0, 300, 1200))
    capture['auto_settings'] = {'input_mode': 'foreground'}
    write_json(tmp_path/'capture.json', capture)
    window = MainWindow(tmp_path)
    inspected = []
    def inspect(dialog):
        view = dialog.findChild(PreviewView)
        lines = [item.line().y1() for item in view.scene().items() if isinstance(item, QGraphicsLineItem)]
        assert sorted(lines) == [500, 525]
        assert view.sceneRect().top() == 200
        assert view.sceneRect().bottom() == 825
        inspected.append(True)
        dialog.reject()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog, 'exec', inspect)
    try:
        window.load_directory(tmp_path)
        wait(app, lambda: window.review is not None and window.job is None)
        assert window.auto_settings.input_mode == 'foreground'
        window.view.set_mode('actual')
        window.page_jump.setValue(2)
        wait(app, lambda: window.job is None)
        assert window.pages.currentRow() == 1
        assert window.view.transform().m11() == pytest.approx(1.)
        window.pending_only.setChecked(True)
        wait(app, lambda: window.job is None)
        assert window.pages.item(1).isHidden()
        assert window.pages.currentRow() == 0
        window.inspect_boundary()
        wait(app, lambda: window.job is None and inspected)
    finally:
        window.close()
