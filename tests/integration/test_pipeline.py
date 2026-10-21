import json
import numpy as np
import pymupdf
import pytest
from vdc.app.fixtures import make_fixture
from vdc.stitching.store import SegmentStore
from vdc.pagination.detector import detect_page_boundaries
from vdc.export.pdf import export_session, export_multipage_pdf


@pytest.mark.parametrize("seed,step", [(42, 83), (17, 149), (91, 205)])
def test_pixels_page_order_and_lossless_pdf(tmp_path, seed, step):
    fixture = make_fixture(seed=seed, step=step)
    store = SegmentStore(tmp_path / "session")
    for frame in fixture.frames():
        result = store.add(frame)
        assert result.accepted, result
    reopened = SegmentStore(tmp_path / "session")
    assert np.array_equal(reopened.read_rows(0, len(fixture.canvas)), fixture.canvas)
    pages = detect_page_boundaries(reopened.iter_blocks())
    assert len(pages.pages) == len(fixture.pages)
    target = tmp_path / "result.pdf"
    assert export_session(reopened, pages, target) == len(fixture.pages)
    with pymupdf.open(target) as document:
        for page, expected in zip(document, fixture.pages, strict=True):
            h, w = expected.shape[:2]
            assert page.rect.width == w * 0.75
            assert page.rect.height == h * 0.75
            pix = document.extract_image(page.get_images()[0][0])
            import io
            from PIL import Image
            actual = np.array(Image.open(io.BytesIO(pix["image"])).convert("RGB"))
            assert np.array_equal(actual, expected)


def test_failure_does_not_advance_and_can_recover(tmp_path):
    fixture = make_fixture()
    store = SegmentStore(tmp_path)
    assert store.add(fixture.canvas[:360]).accepted
    before = json.loads(store.manifest_path.read_text())
    assert not store.add(fixture.canvas[1300:1660]).accepted
    assert store.state == before
    assert store.add(fixture.canvas[100:460]).offset == 100
    reopened = SegmentStore(tmp_path)
    assert reopened.add(fixture.canvas[180:540]).offset == 80


def test_uncertain_export_is_blocked(tmp_path):
    store = SegmentStore(tmp_path / "session")
    store.add(make_fixture().canvas[100:460])
    result = detect_page_boundaries(store.iter_blocks())
    with pytest.raises(ValueError, match="review"):
        export_session(store, result, tmp_path / "bad.pdf")


def test_export_failure_preserves_existing_file(tmp_path):
    target = tmp_path / "existing.pdf"
    target.write_bytes(b"keep existing file")
    with pytest.raises(ValueError):
        export_multipage_pdf([], target)
    assert target.read_bytes() == b"keep existing file"


def test_manifest_write_failure_preserves_last_trusted_state(tmp_path, monkeypatch):
    from pathlib import Path
    fixture = make_fixture()
    store = SegmentStore(tmp_path)
    store.add(fixture.canvas[:360])
    before = json.loads(store.manifest_path.read_text())
    original_replace = Path.replace

    def fail_commit(path, target):
        if path.name == "session.json.tmp":
            raise OSError("simulated disk failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_commit)
    with pytest.raises(OSError, match="disk failure"):
        store.add(fixture.canvas[100:460])
    assert store.state == before
    reopened = SegmentStore(tmp_path)
    assert reopened.state == before
    assert np.array_equal(reopened.read_rows(0, 360), fixture.canvas[:360])
