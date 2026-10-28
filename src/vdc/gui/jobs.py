import time
from PySide6.QtCore import QThread, Signal


class Job(QThread):
    """Result is consumed only after finished, so QThread lifetime is unambiguous."""
    progress = Signal(str, int, int)

    @staticmethod
    def report(stage, completed=0, total=0):
        thread = QThread.currentThread()
        if isinstance(thread, Job):
            now = time.monotonic()
            if (stage != thread._last_stage or completed == total or
                    now-thread._last_progress >= .1):
                thread._last_stage, thread._last_progress = stage, now
                thread.progress.emit(stage, completed, total)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self._last_stage = None
        self._last_progress = 0
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.function()
        except Exception as exc:
            self.error = str(exc)
