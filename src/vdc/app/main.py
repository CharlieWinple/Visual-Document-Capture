"""Desktop entry point: establish DPI awareness before importing Qt/MSS."""
import argparse
from pathlib import Path
import sys


def enable_dpi_awareness():
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass  # Qt also requests per-monitor awareness on supported Windows.


def main():
    parser = argparse.ArgumentParser(description="Visual Document Capture desktop")
    parser.add_argument("--session", type=Path, help="Open an existing session")
    parser.add_argument("--sessions-dir", type=Path, default=Path.cwd() / "sessions")
    args = parser.parse_args()
    enable_dpi_awareness()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from vdc.gui.window import MainWindow
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Visual Document Capture")
    window = MainWindow(args.sessions_dir)
    window.show()
    if args.session:
        QTimer.singleShot(0, lambda: window.load_directory(args.session))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
