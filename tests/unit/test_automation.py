import numpy as np
from vdc.app.fixtures import make_fixture
from vdc.automation.engine import AutoEngine, visible_bottom
from vdc.automation.engine import AutoSettings
import pytest
from vdc.stitching.store import SegmentStore


class Reader:
    def __init__(self, canvas, ratio=1):
        self.canvas, self.position, self.ratio = canvas, 0, ratio
        self.calls = []

    def scroll(self, units):
        self.calls.append(units)
        self.position = max(0, min(len(self.canvas)-360, self.position+round(units*self.ratio)))

    def grab(self):
        return self.canvas[self.position:self.position+360].copy()


def run(engine, reader, steps=3000):
    for i in range(steps):
        event = engine.observe(reader.grab(), i*.1)
        if event.state in ("auto_finished", "needs_recovery"):
            return event
    raise AssertionError("Automatic loop did not terminate")


def test_auto_scroll_reconstructs_pixels_and_stops_at_visible_bottom(tmp_path):
    fixture = make_fixture(page_count=3)
    reader = Reader(fixture.canvas)
    engine = AutoEngine(SegmentStore(tmp_path), reader)
    event = run(engine, reader)
    assert event.state == "auto_finished"
    assert engine.no_motion >= 4
    assert np.array_equal(engine.store.read_rows(0, len(fixture.canvas)), fixture.canvas)


def test_overshoot_rolls_back_then_retries_with_smaller_step(tmp_path):
    fixture = make_fixture(page_count=3)
    reader = Reader(fixture.canvas, ratio=4)
    engine = AutoEngine(SegmentStore(tmp_path), reader)
    event = run(engine, reader)
    assert event.state == "auto_finished", event
    assert reader.calls[:3] == [120, -120, 60]
    assert np.array_equal(engine.store.read_rows(0, len(fixture.canvas)), fixture.canvas)


def test_stationary_reader_without_bottom_is_not_success(tmp_path):
    reader = Reader(make_fixture().canvas[:360])
    event = run(AutoEngine(SegmentStore(tmp_path), reader), reader)
    assert event.state == "needs_recovery"
    assert "页底" in event.reason


def test_blank_does_not_guess_displacement(tmp_path):
    reader = Reader(np.full((1000, 640, 3), 255, np.uint8))
    engine = AutoEngine(SegmentStore(tmp_path), reader)
    event = run(engine, reader)
    assert event.state == "needs_recovery"
    assert engine.store.state["height"] == 360
    assert len(reader.calls) == 1 + engine.BLANK_MAX_COMMANDS
    assert reader.calls[0] == 60
    assert all(value == 30 for value in reader.calls[1:])
    import json
    paths = list((tmp_path / "fragments").glob("*/manifest.json"))
    assert len(paths) == 1
    manifest = json.loads(paths[0].read_text(encoding="utf-8"))
    assert manifest["status"] == "unresolved"
    assert len(manifest["frames"]) == 7
    assert not manifest["connected"]


def test_loading_animation_has_bounded_wait_and_no_initial_scroll(tmp_path):
    reader = Reader(make_fixture().canvas)
    engine = AutoEngine(SegmentStore(tmp_path), reader)
    for i in range(45):
        frame = reader.grab() if i % 2 else np.zeros((360, 640, 3), np.uint8)
        event = engine.observe(frame, i*.1)
        if event.state == "needs_recovery":
            break
    assert event.state == "needs_recovery"
    assert not reader.calls
    assert engine.store.state["height"] == 0


def test_bottom_evidence_requires_background_after_paper():
    fixture = make_fixture()
    assert visible_bottom(fixture.canvas[-360:])
    assert not visible_bottom(fixture.canvas[:360])
    assert not visible_bottom(np.full((360, 640, 3), 255, np.uint8))
    bordered = fixture.canvas[-360:].copy()
    bordered[-1] = 240
    assert visible_bottom(bordered)
    bordered[-8:] = 240
    assert not visible_bottom(bordered)


def test_delayed_motion_resets_end_confirmation(tmp_path):
    fixture = make_fixture(page_count=3)
    reader = Reader(fixture.canvas)
    engine = AutoEngine(SegmentStore(tmp_path), reader, AutoSettings(end_wait_ms=6000))
    # Temporarily ignore wheel commands at a visible intermediate page bottom.
    reader.position = fixture.bounds[0][3]+6-360
    reader.ratio = 0
    for i in range(45):
        event = engine.observe(reader.grab(), i*.1)
        assert event.state not in ("auto_finished", "needs_recovery")
    assert engine.no_motion > 0
    reader.ratio = 1
    for i in range(45, 70):
        event = engine.observe(reader.grab(), i*.1)
        assert event.state not in ("auto_finished", "needs_recovery")
    assert engine.no_motion == 0
    assert engine.stalled_since is None


def test_invalid_automatic_settings_are_rejected():
    with pytest.raises(ValueError):
        AutoSettings(step_percent=90)


def test_retry_limit_prevents_endless_rollback(tmp_path):
    reader = Reader(make_fixture(page_count=6).canvas, ratio=40)
    engine = AutoEngine(SegmentStore(tmp_path), reader)
    event = run(engine, reader)
    assert event.state == "needs_recovery"
    assert reader.calls == [120, -120, 60, -60, 30]
    assert engine.store.state["height"] == 360


def test_background_no_motion_is_not_reported_as_compatible_completion(tmp_path):
    reader = Reader(make_fixture(page_count=3).canvas[-360:])
    engine = AutoEngine(SegmentStore(tmp_path), reader, AutoSettings(input_mode="visible_background"))
    event = run(engine, reader)
    assert event.state == "needs_recovery"
    assert "定向滚动" in event.reason
