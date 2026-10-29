"""Reading preview with persistent width/page/manual zoom modes."""
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import QGraphicsView


class PreviewView(QGraphicsView):
    zoom_changed = Signal(float)

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.mode = "width"
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setMinimumSize(200, 150)

    def set_mode(self, mode):
        self.mode = mode
        if mode == "actual":
            self.resetTransform()
            self.mode = "manual"
            self.zoom_changed.emit(1.)
        else:
            self.apply_mode()

    def apply_mode(self):
        rect = self.sceneRect()
        if rect.isEmpty():
            return
        if self.mode == "width":
            scale = max(.02, (self.viewport().width()-12)/rect.width())
            self.resetTransform()
            self.scale(scale, scale)
        elif self.mode == "page":
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.zoom_changed.emit(self.transform().m11())

    def new_page(self):
        self.apply_mode()
        self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())

    def zoom(self, factor):
        scale = self.transform().m11()
        target = min(12., max(.02, scale*factor))
        self.mode = "manual"
        self.scale(target/scale, target/scale)
        self.zoom_changed.emit(target)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            before = self.mapToScene(event.position().toPoint())
            self.zoom(1.25 if event.angleDelta().y() > 0 else .8)
            after = self.mapToScene(event.position().toPoint())
            self.translate(after.x()-before.x(), after.y()-before.y())
            event.accept()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.apply_mode)
