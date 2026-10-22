import json
import numpy as np
from PIL import Image
from vdc.automation.engine import AutoEngine, cautious_step
from vdc.stitching.store import SegmentStore


class Reader:
    def __init__(self, canvas):
        self.canvas = canvas
        self.position = 0
        self.calls = []

    def scroll(self, units):
        self.calls.append(units)
        self.position = min(len(self.canvas)-360, max(0, self.position+units))

    def grab(self):
        return self.canvas[self.position:self.position+360].copy()


def test_early_reduction_ignores_stationary_vertical_paper_edges():
    frame = np.full((360, 640, 3), 255, np.uint8)
    frame[:, :20] = 170
    frame[:, -20:] = 170
    frame[10:120:4, 80:500] = 40
    assert cautious_step(frame, 120) == 60
    frame[190:350:4, 80:500] = 40
    assert cautious_step(frame, 120) == 120


def test_new_content_is_retained_but_not_joined_across_unknown_blank(tmp_path):
    canvas = np.full((1000, 640, 3), 255, np.uint8)
    canvas[440:650] = np.random.default_rng(41).integers(0, 255, (210, 640, 3), dtype=np.uint8)
    reader = Reader(canvas)
    store = SegmentStore(tmp_path)
    engine = AutoEngine(store, reader)
    for i in range(500):
        event = engine.observe(reader.grab(), i*.1)
        if event.state == "needs_recovery":
            break
    assert "重新出现内容" in event.reason
    assert store.state["height"] == 360
    assert np.all(store.read_rows(0, 360) == 255)
    manifest_path = next((tmp_path / "fragments").glob("*/manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    last = np.array(Image.open(manifest_path.parent / manifest["frames"][-1]["file"]))
    assert np.array_equal(last, reader.grab())
    assert np.any(last != 255)
    assert len(reader.calls) <= 7
    assert SegmentStore(tmp_path).state["height"] == 360


def test_probe_timeout_does_not_issue_another_input(tmp_path):
    reader = Reader(np.full((1200, 640, 3), 255, np.uint8))
    engine = AutoEngine(SegmentStore(tmp_path), reader)
    for i in range(100):
        event = engine.observe(reader.grab(), i*.1)
        if engine.mode == "blank_probe":
            break
    calls = list(reader.calls)
    event = engine.observe(reader.grab(), engine.probe_started+12.1)
    assert event.state == "needs_recovery"
    assert reader.calls == calls
    assert engine.store.state["height"] == 360


def test_stop_and_resume_preserve_separate_probe_attempts(tmp_path):
    reader = Reader(np.full((1200, 640, 3), 255, np.uint8))
    engine = AutoEngine(SegmentStore(tmp_path), reader)
    for i in range(100):
        engine.observe(reader.grab(), i*.1)
        if engine.mode == "blank_probe":
            break
    path = engine.probe_directory / "manifest.json"
    engine.reset(20)
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "interrupted"
    assert path.parent.joinpath("probe-00.png").exists()
    assert engine.probe_count == 0


def test_delayed_render_recovers_only_with_verified_overlap(tmp_path):
    from vdc.app.fixtures import make_fixture
    canvas = make_fixture().canvas
    reader = Reader(canvas)
    store = SegmentStore(tmp_path)
    engine = AutoEngine(store, reader)
    for i in range(100):
        # Simulate a temporarily blank rendered frame after the first wheel.
        frame = np.full_like(reader.grab(), 255) if reader.calls else reader.grab()
        engine.observe(frame, i*.1)
        if engine.mode == "blank_probe":
            break
    height = store.state["height"]
    assert height == 360
    for j in range(i+1, i+30):
        engine.observe(reader.grab(), j*.1)
        if store.state["height"] > height:
            break
    assert store.state["height"] > height
    assert np.array_equal(store.read_rows(0, store.state["height"]), canvas[:store.state["height"]])
    path = next((tmp_path / "fragments").glob("*/manifest.json"))
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "overlap_verified"
