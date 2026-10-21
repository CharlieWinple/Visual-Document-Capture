import numpy as np
import pytest
from vdc.app.fixtures import make_fixture
from vdc.pagination.detector import detect_page_boundaries, calibrate_page_boundaries, PageBoundary
from vdc.stitching.store import SegmentStore
from vdc.stitching.alignment import estimate_scroll_offset
from vdc.capture.tracker import ContinuousTracker
from vdc.app.session import Review


def uniform_document(count=5, height=500, gap=25, bg=237):
    canvas = np.full((20+count*(height+gap), 640, 3), bg, np.uint8)
    bounds = []
    for i in range(count):
        top = 20+i*(height+gap)
        canvas[top:top+height, 40:600] = 255
        bounds.append((40, top, 600, top+height))
    return canvas, bounds


@pytest.mark.parametrize("background", [40, 172, 225, 237, 245])
def test_background_adapts_and_learns_repeated_page_size(background):
    canvas, bounds = uniform_document(bg=background)
    result = detect_page_boundaries([(0, canvas)])
    assert [(p.left, p.top, p.right, p.bottom) for p in result.pages] == bounds
    assert not result.warnings
    assert result.geometry["period"] == 525
    assert result.geometry["support"] == 5


def test_mixed_sizes_not_forced_to_same_model():
    fixture = make_fixture()
    result = detect_page_boundaries([(0, fixture.canvas)])
    assert result.geometry is None
    with pytest.raises(ValueError, match="不同尺寸"):
        calibrate_page_boundaries([(0, fixture.canvas)], result.pages[0], 12)


def test_model_cannot_invent_a_gap_in_blank_paper():
    canvas, bounds = uniform_document()
    # Last two pages are actually a single tall sheet: no background gap.
    canvas[bounds[3][3]:bounds[4][1], 40:600] = 255
    result = detect_page_boundaries([(0, canvas)])
    assert len(result.pages) == 4
    assert result.pages[-1].bottom-result.pages[-1].top == 1025


def test_calibration_predictions_remain_unconfirmed_and_persist(tmp_path):
    canvas = np.full((1600, 640, 3), 255, np.uint8)
    anchor = PageBoundary(1, 40, 20, 600, 520, .5, False)
    result = calibrate_page_boundaries([(0, canvas)], anchor, 25)
    assert len(result.pages) == 4  # Includes the visible tail of the fourth page.
    assert all(not p.complete and p.source == "calibrated" for p in result.pages)
    review = Review(result.pages, result.warnings, 640, 1600, result.geometry)
    review.save(tmp_path)
    loaded = Review.load(tmp_path, 640, 1600)
    assert loaded.geometry == result.geometry
    assert loaded.export_warnings({})


def test_learned_period_uses_weak_gap_evidence_but_requires_review():
    canvas, bounds = uniform_document(count=6)
    # Two thin white rails bridge one real gap and fool the row-edge detector.
    a, b = bounds[4][3], bounds[5][1]
    canvas[a:b, 40:44] = 255
    canvas[a:b, 596:600] = 255
    result = detect_page_boundaries([(0, canvas)])
    assert result.geometry is not None
    assert len(result.pages) == 6
    assert [(p.top, p.bottom) for p in result.pages] == [(r[1], r[3]) for r in bounds]
    assert all(p.source == "geometry_verified" and not p.complete for p in result.pages[-2:])
    assert "geometry_assisted_boundaries_require_review" in result.warnings


def test_every_observation_can_move_no_pause_required(tmp_path):
    fixture = make_fixture(page_count=3)
    tracker = ContinuousTracker(SegmentStore(tmp_path))
    positions = list(range(0, len(fixture.canvas)-360, 47)) + [len(fixture.canvas)-360]
    for i, top in enumerate(positions):
        result = tracker.observe(fixture.canvas[top:top+360], now=i*.06)
        assert result.state == "capturing", (top, result)
    assert np.array_equal(tracker.store.read_rows(0, len(fixture.canvas)), fixture.canvas)


def test_transient_blank_recovery_never_guesses_position(tmp_path):
    fixture = make_fixture()
    tracker = ContinuousTracker(SegmentStore(tmp_path))
    tracker.observe(fixture.canvas[:360], now=0)
    blank = np.full((360, 640, 3), 255, np.uint8)
    assert tracker.observe(blank, now=.1).state == "pending"
    assert tracker.store.state["height"] == 360
    result = tracker.observe(fixture.canvas[100:460], now=.2)
    assert result.recovered
    assert tracker.store.state["position"] == 100
    assert np.array_equal(tracker.store.read_rows(0, 460), fixture.canvas[:460])


def test_pure_blank_times_out_and_stays_bounded(tmp_path):
    blank = np.full((360, 640, 3), 255, np.uint8)
    tracker = ContinuousTracker(SegmentStore(tmp_path), pending_seconds=.5)
    tracker.observe(blank, now=0)
    assert tracker.observe(blank, now=.1).state == "pending"
    assert tracker.observe(blank, now=.7).state == "needs_recovery"
    assert tracker.store.state["height"] == 360
    assert tracker.store.state["position"] == 0
    assert tracker.first_failed.nbytes + tracker.last_failed.nbytes == blank.nbytes*2


def test_faint_watermarks_are_useful_texture():
    import cv2
    canvas = np.full((1000, 640, 3), 255, np.uint8)
    for y in range(15, 1000, 35):
        cv2.putText(canvas, f"local watermark {y}", (40+(y%3)*60, y), cv2.FONT_HERSHEY_SIMPLEX,
                    .6, (246, 246, 246), 1, cv2.LINE_AA)
    result = estimate_scroll_offset(canvas[100:460], canvas[173:533])
    assert result.accepted and result.offset == 73
