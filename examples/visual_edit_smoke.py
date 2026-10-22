"""Render the visual editors using only generated documents; no desktop input."""
import argparse
from pathlib import Path
from vdc.app.main import enable_dpi_awareness
enable_dpi_awareness()
from PySide6.QtWidgets import QApplication
from vdc.app.fixtures import make_fixture
from vdc.gui.editor import VisualEditor, editor_context
from vdc.pagination.detector import PageBoundary
from vdc.stitching.store import SegmentStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    app = QApplication([])
    fixture = make_fixture(page_count=3)
    store = SegmentStore(args.output / "session")
    for frame in fixture.frames():
        assert store.add(frame).accepted
    page = PageBoundary(1, *fixture.bounds[0], .9, True)
    top, pixels = editor_context(store, page)
    for mode in ("crop", "split", "calibrate"):
        dialog = VisualEditor(pixels, top, page, mode, gap=5, store=store)
        dialog.show()
        app.processEvents()
        assert dialog.grab().save(str(args.output / f"{mode}.png"))
        dialog.close()
    print(f"Saved crop, split and calibration previews in {args.output}")


if __name__ == "__main__":
    main()
