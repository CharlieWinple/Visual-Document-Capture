"""Visual editing in original document coordinates, independent of zoom/DPI."""
import math
from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QGraphicsScene, QGraphicsView, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QVBoxLayout,
)
from .images import pixmap
from .jobs import Job


def editor_context(store, page):
    """Read just this page and surrounding source rows, including cropped pixels."""
    margin = max(400, (page.bottom-page.top)//2)
    top, bottom = max(0, page.top-margin), min(store.state["height"], page.bottom+margin)
    return top, store.read_rows(top, bottom)


class EditorView(QGraphicsView):
    changed = Signal()
    cursor_position = Signal(int, int)

    def __init__(self, pixels, top, page, mode, gap=20, parent=None):
        super().__init__(parent)
        scene = QGraphicsScene(self)
        self.setScene(scene)
        self.image_item = scene.addPixmap(pixmap(pixels))
        self.image_item.setPos(0, top)
        self.extent = QRectF(0, top, pixels.shape[1], len(pixels))
        scene.setSceneRect(self.extent)
        self.mode = mode
        self.values = {k: getattr(page, k) for k in ("left", "top", "right", "bottom")}
        self.split_y = (page.top+page.bottom)//2
        self.next_y = min(int(self.extent.bottom()), page.bottom+min(max(0, gap), (page.bottom-page.top)//4))
        self.active = None
        self.dragging = False
        self.history = []
        self.before_drag = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setBackgroundBrush(QColor("#dfe5ea"))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    def snapshot(self):
        return dict(self.values), self.split_y, self.next_y

    def replace_context(self, pixels, top):
        self.image_item.setPixmap(pixmap(pixels))
        self.image_item.setPos(0, top)
        self.extent = QRectF(0, top, pixels.shape[1], len(pixels))
        self.scene().setSceneRect(self.extent)
        self.fit()

    def remember(self, before):
        if before != self.snapshot():
            self.history.append(before)
            self.history = self.history[-100:]

    def undo(self):
        if self.history:
            self.values, self.split_y, self.next_y = self.history.pop()
            self.changed.emit()
            self.viewport().update()

    def set_coordinate(self, key, value):
        v = self.values
        value = int(round(value))
        if key == "left": v[key] = max(0, min(v["right"]-1, value))
        elif key == "right": v[key] = max(v["left"]+1, min(int(self.extent.right()), value))
        elif key == "top": v[key] = max(int(self.extent.top()), min(v["bottom"]-1, value))
        elif key == "bottom": v[key] = max(v["top"]+1, min(int(self.extent.bottom()), value))
        elif key == "split": self.split_y = max(v["top"]+1, min(v["bottom"]-1, value))
        elif key == "next": self.next_y = value
        self.next_y = max(v["bottom"], min(int(self.extent.bottom()),
                          v["bottom"]+(v["bottom"]-v["top"])//4, self.next_y))
        self.split_y = max(v["top"]+1, min(v["bottom"]-1, self.split_y))
        self.changed.emit()
        self.viewport().update()

    def rect_on_screen(self):
        v = self.values
        a = self.mapFromScene(QPointF(v["left"], v["top"]))
        b = self.mapFromScene(QPointF(v["right"], v["bottom"]))
        return QRectF(a, b)

    def hit(self, point):
        r = self.rect_on_screen()
        if self.mode == "split":
            return "split"
        if self.mode == "calibrate":
            y = self.mapFromScene(QPointF(0, self.next_y)).y()
            if (abs(point.y()-y) <= 8 and abs(point.y()-y) < abs(point.y()-r.bottom())
                    and r.left()-10 <= point.x() <= r.right()+10):
                return "next"
        horizontal = "left" if abs(point.x()-r.left()) <= 9 else "right" if abs(point.x()-r.right()) <= 9 else ""
        vertical = "top" if abs(point.y()-r.top()) <= 9 else "bottom" if abs(point.y()-r.bottom()) <= 9 else ""
        if horizontal and r.top()-9 <= point.y() <= r.bottom()+9:
            return horizontal+(","+vertical if vertical else "")
        if vertical and r.left()-9 <= point.x() <= r.right()+9:
            return vertical
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.active = self.hit(event.position())
            if self.active:
                self.before_drag = self.snapshot()
                self.dragging = True
                self.setFocus()
                self.move_active(event.position())
                event.accept()
                return
        super().mousePressEvent(event)

    def move_active(self, position):
        point = self.mapToScene(position.toPoint())
        for key in self.active.split(","):
            self.set_coordinate(key, point.x() if key in ("left", "right") else point.y())

    def mouseMoveEvent(self, event):
        p = self.mapToScene(event.position().toPoint())
        self.cursor_position.emit(round(p.x()), round(p.y()))
        if self.dragging:
            self.move_active(event.position())
            event.accept()
        else:
            target = self.hit(event.position())
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor if target and "," in target else
                Qt.CursorShape.SizeHorCursor if target in ("left", "right") else
                Qt.CursorShape.SizeVerCursor if target else Qt.CursorShape.OpenHandCursor)
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.dragging and event.button() == Qt.MouseButton.LeftButton:
            self.move_active(event.position())
            self.dragging = False
            self.remember(self.before_drag)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Z and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.undo()
            return
        delta = {Qt.Key.Key_Left: -1, Qt.Key.Key_Up: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_Down: 1}.get(event.key())
        if delta is not None and self.active:
            before = self.snapshot()
            delta *= 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            for key in self.active.split(","):
                horizontal = key in ("left", "right")
                if horizontal == (event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right)):
                    value = self.split_y if key == "split" else self.next_y if key == "next" else self.values[key]
                    self.set_coordinate(key, value+delta)
            self.remember(before)
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.zoom(1.25 if event.angleDelta().y() > 0 else .8)
        event.accept()

    def zoom(self, factor):
        if .03 <= self.transform().m11()*factor <= 12:
            self.scale(factor, factor)
            self.viewport().update()

    def fit(self):
        self.fitInView(self.extent, Qt.AspectRatioMode.KeepAspectRatio)
        scale = max(.001, self.transform().m11())
        self.fitInView(self.extent.adjusted(-60/scale, -40/scale, 10/scale, 10/scale),
                       Qt.AspectRatioMode.KeepAspectRatio)

    def drawForeground(self, painter, rect):
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect_on_screen()
        w, h = self.viewport().width(), self.viewport().height()
        if self.mode != "split":
            shade = QColor(15, 30, 45, 90)
            painter.fillRect(QRectF(0, 0, w, max(0, r.top())), shade)
            painter.fillRect(QRectF(0, r.bottom(), w, max(0, h-r.bottom())), shade)
            painter.fillRect(QRectF(0, r.top(), max(0, r.left()), r.height()), shade)
            painter.fillRect(QRectF(r.right(), r.top(), max(0, w-r.right()), r.height()), shade)
            painter.setPen(QPen(QColor("#007ea0"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r)
            painter.setBrush(QColor("white"))
            for x, y in [(r.left(), r.top()), (r.center().x(), r.top()), (r.right(), r.top()),
                         (r.left(), r.center().y()), (r.right(), r.center().y()),
                         (r.left(), r.bottom()), (r.center().x(), r.bottom()), (r.right(), r.bottom())]:
                painter.drawRect(QRectF(x-4, y-4, 8, 8))
        lines = [(self.split_y, "分页线", "#c05100")] if self.mode == "split" else (
            [(self.values["bottom"], "本页底边", "#007ea0"), (self.next_y, "下一页顶边", "#c05100")]
            if self.mode == "calibrate" else [])
        for y, label, color in lines:
            screen_y = self.mapFromScene(QPointF(0, y)).y()
            painter.setPen(QPen(QColor(color), 2))
            painter.drawLine(QPointF(r.left(), screen_y), QPointF(r.right(), screen_y))
            label_top = screen_y+3 if label == "下一页顶边" else screen_y-24
            label_left = max(56, r.right()-205) if label == "下一页顶边" else max(56, r.left())
            painter.fillRect(QRectF(label_left, label_top, 205, 21), QColor("#ffffff"))
            painter.drawText(QPointF(label_left+4, label_top+16), f"{label}  y={y} px")
        # Fixed-size rulers display original image coordinates at any scale.
        painter.fillRect(QRectF(0, 0, w, 25), QColor("#edf2f5"))
        painter.fillRect(QRectF(0, 0, 52, h), QColor("#edf2f5"))
        painter.setPen(QColor("#335466"))
        scale = max(.001, self.transform().m11())
        raw = 90/scale
        magnitude = 10**math.floor(math.log10(raw))
        step = max(1, int(next(n*magnitude for n in (1, 2, 5, 10) if n*magnitude >= raw)))
        a, b = self.mapToScene(0, 0), self.mapToScene(w, h)
        for coordinate in range(math.ceil(max(0, a.x())/step)*step,
                                math.floor(min(self.extent.right(), b.x())/step)*step+1, step):
            x = self.mapFromScene(QPointF(coordinate, 0)).x()
            if x >= 52:
                painter.drawLine(x, 18, x, 25)
                painter.drawText(x+3, 15, str(coordinate))
        for coordinate in range(math.ceil(max(self.extent.top(), a.y())/step)*step,
                                math.floor(min(self.extent.bottom(), b.y())/step)*step+1, step):
            y = self.mapFromScene(QPointF(0, coordinate)).y()
            if y >= 25:
                painter.drawLine(45, y, 52, y)
                painter.drawText(2, y-3, str(coordinate))
        painter.drawText(4, 16, "px")
        painter.restore()


class VisualEditor(QDialog):
    def __init__(self, pixels, top, page, mode="crop", gap=20, parent=None, store=None):
        super().__init__(parent)
        self.store, self.job = store, None
        self.setWindowTitle({"crop": "拖拽裁切", "split": "拖动分页线", "calibrate": "可视化一页标定"}[mode])
        self.resize(1050, 780)
        layout = QVBoxLayout(self)
        text = {"crop": "拖动蓝框四边或角点裁切；框外仍显示原始内容，可向外扩展。",
                "split": "点击分界位置放置分页线，拖动微调，确认后拆成两页。",
                "calibrate": "先将蓝框调整为一张完整页，再拖动橙线至下一页顶边；两线距离就是页间距。"}[mode]
        navigation = "使用滚动条平移视野。" if mode == "split" else "拖动框内空白处可平移视野。"
        hint = QLabel(text+" 滚轮缩放；选中边界后方向键微调，Shift 为 10 px。"+navigation)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.view = EditorView(pixels, top, page, mode, gap, self)
        layout.addWidget(self.view, 1)
        tools = QHBoxLayout()
        for title, callback in [("适应窗口", self.view.fit), ("放大", lambda: self.view.zoom(1.25)),
                                ("缩小", lambda: self.view.zoom(.8)), ("撤销调整", self.view.undo)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            tools.addWidget(button)
        self.position = QLabel("鼠标位置：—")
        self.expand = QPushButton("展开更多原始内容")
        self.expand.setEnabled(store is not None)
        self.expand.clicked.connect(self.expand_context)
        tools.addWidget(self.expand)
        tools.addWidget(self.position)
        tools.addStretch()
        layout.addLayout(tools)
        self.view.cursor_position.connect(lambda x, y: self.position.setText(f"鼠标位置：x={x}, y={y} px"))
        inputs = QHBoxLayout()
        self.spins = {}
        fields = [("split", "分页位置")] if mode == "split" else [("left", "左"), ("top", "上"), ("right", "右"), ("bottom", "下")]
        if mode == "calibrate": fields.append(("next", "下一页顶部"))
        for key, label in fields:
            inputs.addWidget(QLabel(label))
            spin = QSpinBox()
            spin.setRange(0, int(self.view.extent.right()) if key in ("left", "right") else
                          store.state["height"] if store else int(self.view.extent.bottom()))
            spin.setSuffix(" px")
            spin.valueChanged.connect(lambda value, k=key: self.numeric_change(k, value))
            self.spins[key] = spin
            inputs.addWidget(spin)
        layout.addLayout(inputs)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认应用")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.view.changed.connect(self.sync)
        self.sync()

    def expand_context(self):
        if self.job or self.store is None:
            return
        extent = self.view.extent
        margin = max(400, int(extent.height()/2))
        top = max(0, int(extent.top())-margin)
        bottom = min(self.store.state["height"], int(extent.bottom())+margin)
        if bottom-top > 12000:
            self.summary.setText("单次编辑最多展开 12000 行；可先应用当前修改，再选择其他页面编辑。")
            return
        self.expand.setEnabled(False)
        job = Job(lambda: (top, self.store.read_rows(top, bottom)), self)
        self.job = job
        def done():
            self.job = None
            if job.error:
                self.summary.setText(f"加载失败：{job.error}")
            else:
                offset, pixels = job.result
                self.view.replace_context(pixels, offset)
                self.sync()
            self.expand.setEnabled(top > 0 or bottom < self.store.state["height"])
            job.deleteLater()
        job.finished.connect(done)
        job.start()

    def done(self, result):
        if self.job:
            self.summary.setText("正在读取原始内容，请稍候再确认或关闭。")
            return
        super().done(result)

    def closeEvent(self, event):
        if self.job:
            event.ignore()
        else:
            super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        available = self.screen().availableGeometry()
        self.resize(min(self.width(), available.width()-40), min(self.height(), available.height()-70))
        self.view.fit()

    def numeric_change(self, key, value):
        before = self.view.snapshot()
        self.view.set_coordinate(key, value)
        self.view.remember(before)

    def sync(self):
        values = dict(self.view.values, split=self.view.split_y, next=self.view.next_y)
        for key, spin in self.spins.items():
            spin.blockSignals(True)
            spin.setValue(values[key])
            spin.blockSignals(False)
        self.summary.setText(f"页面 {values['right']-values['left']} × {values['bottom']-values['top']} px"
            + (f"　页间距：{self.gap} px" if self.view.mode == "calibrate" else "")
            + "　坐标对应原始采集内容；取消不会修改会话。")

    @property
    def gap(self):
        return self.view.next_y-self.view.values["bottom"]
