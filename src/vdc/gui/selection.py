"""One overlay per screen, each with an explicit physical monitor mapping."""
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QObject
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget
from vdc.capture.region import logical_to_physical


class SelectionOverlay(QWidget):
    accepted = Signal(object)
    cancelled = Signal()

    def __init__(self, screen, monitor):
        super().__init__()
        self.screen_info, self.monitor = screen, monitor
        self.snapshot = screen.grabWindow(0)
        self.selection = QRectF()
        self.anchor = QPointF()
        self.before = QRectF()
        self.mode = ""
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.winId()
        self.windowHandle().setScreen(screen)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.snapshot)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 115))
        if not self.selection.isEmpty():
            painter.save()
            painter.setClipRect(self.selection)
            painter.drawPixmap(self.rect(), self.snapshot)
            painter.restore()
            painter.setPen(QPen(QColor("#27d8c2"), 2))
            painter.drawRect(self.selection)
            for point in [self.selection.topLeft(), self.selection.topRight(),
                          self.selection.bottomLeft(), self.selection.bottomRight()]:
                painter.fillRect(QRectF(point.x() - 4, point.y() - 4, 8, 8), QColor("#27d8c2"))
        painter.fillRect(16, 16, min(self.width() - 32, 760), 64, QColor(15, 28, 43, 235))
        painter.setPen(QColor("white"))
        painter.drawText(QRectF(30, 20, self.width() - 60, 56), Qt.AlignmentFlag.AlignVCenter,
                         "拖动框选；拖动边缘可调整，内部可移动。Enter 确认 · Esc 取消\n请保留纸张左右背景及页间空隙，避开工具栏与滚动条。")

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.anchor = event.position()
        self.before = QRectF(self.selection)
        p, r, hit = self.anchor, self.selection, 10
        self.mode = ""
        if not r.isEmpty() and r.adjusted(-hit, -hit, hit, hit).contains(p):
            if abs(p.x() - r.left()) < hit:
                self.mode += "l"
            elif abs(p.x() - r.right()) < hit:
                self.mode += "r"
            if abs(p.y() - r.top()) < hit:
                self.mode += "t"
            elif abs(p.y() - r.bottom()) < hit:
                self.mode += "b"
            self.mode = self.mode or "move"
        else:
            self.mode = "new"
            self.selection = QRectF(p, p)
        self.update()

    def mouseMoveEvent(self, event):
        if not event.buttons() & Qt.MouseButton.LeftButton:
            return
        p = QPointF(max(0, min(self.width(), event.position().x())),
                    max(0, min(self.height(), event.position().y())))
        if self.mode == "new":
            r = QRectF(self.anchor, p).normalized()
        elif self.mode == "move":
            delta = p - self.anchor
            r = QRectF(self.before)
            r.moveLeft(max(0, min(self.width() - r.width(), r.left() + delta.x())))
            r.moveTop(max(0, min(self.height() - r.height(), r.top() + delta.y())))
        else:
            r = QRectF(self.before)
            if "l" in self.mode:
                r.setLeft(p.x())
            if "r" in self.mode:
                r.setRight(p.x())
            if "t" in self.mode:
                r.setTop(p.y())
            if "b" in self.mode:
                r.setBottom(p.y())
            r = r.normalized()
        self.selection = r.intersected(QRectF(self.rect()))
        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            r = self.selection
            try:
                region = logical_to_physical((r.x(), r.y(), r.width(), r.height()),
                                             (self.width(), self.height()), self.monitor,
                                             self.screen_info.devicePixelRatio())
            except ValueError:
                return
            self.accepted.emit(region)


class RegionSelector(QObject):
    selected = Signal(object)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.overlays = []

    def start(self):
        import mss
        with mss.mss() as capture:
            monitors = capture.monitors[1:]
        screens = QApplication.screens()
        # On Windows Qt keeps native screen origins while scaling screen sizes.
        # Validate rather than guessing if a virtual display has unusual geometry.
        mappings = []
        for screen in screens:
            rect, dpr = screen.geometry(), screen.devicePixelRatio()
            candidates = [m for m in monitors if m["left"] == rect.x() and m["top"] == rect.y()
                          and abs(m["width"] - rect.width() * dpr) <= 3
                          and abs(m["height"] - rect.height() * dpr) <= 3]
            if len(candidates) != 1:
                raise ValueError(f"无法可靠映射显示器 {screen.name()} 的物理坐标，请检查显示缩放配置")
            mappings.append((screen, candidates[0]))
        self.overlays = [SelectionOverlay(screen, monitor) for screen, monitor in mappings]
        for overlay in self.overlays:
            overlay.accepted.connect(self.finish)
            overlay.cancelled.connect(self.cancel)
            overlay.show()
        # Let the overlay under the pointer receive Enter/Escape.
        from PySide6.QtGui import QCursor
        for overlay in self.overlays:
            if overlay.geometry().contains(QCursor.pos()):
                overlay.activateWindow()
                overlay.setFocus()

    def cleanup(self):
        for overlay in self.overlays:
            overlay.close()
            overlay.deleteLater()
        self.overlays = []

    def finish(self, region):
        self.cleanup()
        self.selected.emit(region)

    def cancel(self):
        self.cleanup()
        self.cancelled.emit()
