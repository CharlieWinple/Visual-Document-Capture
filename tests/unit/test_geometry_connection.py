import json
import numpy as np
import pytest
from vdc.stitching.store import SegmentStore
from vdc.automation.geometry import learn_anchor, propose_connection
from vdc.automation.engine import AutoEngine


def sample(tmp_path, mixed=False):
    canvas = np.full((2500, 300, 3), 170, np.uint8)
    for i in range(4):
        top = 12+i*612
        canvas[top:top+600, 30:270] = 255
        rng = np.random.default_rng(i)
        for row in (60, 250):
            canvas[top+row:top+row+20, 60:240] = rng.integers(0, 180, (20, 180, 3), dtype=np.uint8)
    if mixed:
        canvas[12+590:12+600] = 170
    top = 12+3*612
    end = top+550
    store = SegmentStore(tmp_path)
    store.add(canvas[:end])
    # Seed a known contiguous prefix and a normal 300-row viewport reference.
    store.previous = canvas[end-300:end].copy()
    store.state.update(position=end-300, reference=store._save_image(store.previous, "reference"))
    store.manifest_path.write_text(json.dumps(store.state), encoding="utf-8")
    return store, canvas, top


def test_connects_only_observed_pixels_and_persists_inferred_evidence(tmp_path):
    store, canvas, top = sample(tmp_path)
    anchor = learn_anchor(store)
    assert anchor["support"] == 3
    frame = canvas[top+310:top+610]
    evidence = propose_connection(store, frame, anchor)
    assert evidence["position"] == top+310
    result = store.add_geometry(frame, evidence)
    assert result.confidence < .8
    reopened = SegmentStore(tmp_path)
    assert reopened.state["geometry_joins"][0]["requires_review"]
    assert np.array_equal(reopened.read_rows(0, top+610), canvas[:top+610])


def test_mixed_page_heights_do_not_supply_model(tmp_path):
    store, _, _ = sample(tmp_path, mixed=True)
    assert learn_anchor(store) is None


def test_corrupted_overlap_or_unseen_gap_cannot_be_connected(tmp_path):
    store, canvas, top = sample(tmp_path)
    anchor = learn_anchor(store)
    frame = canvas[top+310:top+610].copy()
    frame[50:70, 50:200] = 0
    assert propose_connection(store, frame, anchor) is None
    before = store.state.copy()
    with pytest.raises(ValueError):
        store.add_geometry(frame, {"position": top+310})
    with pytest.raises(ValueError):
        store.add_geometry(canvas[top+310:top+610], {"position": store.state["height"]+1})
    assert store.state == before


def test_no_visible_bottom_or_changed_width_is_rejected(tmp_path):
    store, canvas, top = sample(tmp_path)
    anchor = learn_anchor(store)
    assert propose_connection(store, canvas[top+280:top+580], anchor) is None
    frame = canvas[top+310:top+610].copy()
    frame[:290, 30:40] = 170
    assert propose_connection(store, frame, anchor) is None


def test_automatic_probe_uses_geometry_and_keeps_review_requirement(tmp_path):
    store, canvas, top = sample(tmp_path)
    class Driver:
        def scroll(self, units): pass
    engine = AutoEngine(store, Driver())
    event = engine.probe_blank(canvas[top+280:top+580], 0)
    assert event.state == "auto_blank_probe"
    event = engine.probe_blank(canvas[top+310:top+610], 1)
    assert event.alignment.status == "geometry_inferred"
    assert engine.mode == "forward"
    assert len(store.state["geometry_joins"]) == 1
    manifest = json.loads(next((tmp_path / "fragments").glob("*/manifest.json")).read_text(encoding="utf-8"))
    assert manifest["connected"]
    assert manifest["connection"]["requires_review"]


def test_full_automatic_capture_learns_then_connects_blank_and_blocks_unreviewed_export(tmp_path):
    from vdc.pagination.detector import detect_page_boundaries
    from vdc.export.pdf import export_session
    _, canvas, top = sample(tmp_path / "source")
    for i in range(3):
        y = 12+i*612
        canvas[y+30:y+570, 60:240] = np.random.default_rng(80+i).integers(0, 180, (540, 180, 3), dtype=np.uint8)
    canvas = canvas[:top+612]
    class Reader:
        position = 0
        def scroll(self, units):
            self.position = min(len(canvas)-300, max(0, self.position+units))
    reader = Reader()
    store = SegmentStore(tmp_path / "capture")
    engine = AutoEngine(store, reader)
    for i in range(3000):
        event = engine.observe(canvas[reader.position:reader.position+300].copy(), i*.1)
        if event.state in ("auto_finished", "needs_recovery"):
            break
    assert event.state == "auto_finished", event
    assert len(store.state["geometry_joins"]) == 1
    assert np.array_equal(store.read_rows(0, len(canvas)), canvas)
    pagination = detect_page_boundaries(store.iter_blocks())
    assert len(pagination.pages) == 4
    with pytest.raises(ValueError, match="review"):
        export_session(store, pagination, tmp_path / "must-not-export.pdf")
