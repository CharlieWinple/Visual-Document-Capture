from PySide6.QtCore import QThread


class Job(QThread):
    """Result is consumed only after finished, so QThread lifetime is unambiguous."""
    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.function()
        except Exception as exc:
            self.error = str(exc)
