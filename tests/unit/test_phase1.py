from pathlib import Path
import numpy as np
import pytest
from vdc.capture.region import Region, logical_to_physical, validate_monitor
from vdc.capture.stability import StableFrames
from vdc.app.session import Review
from vdc.pagination.detector import PageBoundary


@pytest.mark.parametrize("scale", [1, 1.25, 1.5, 2])
def test_monitor_mapping_preserves_negative_origin(scale):
    monitor = {"left": -2400, "top": -100, "width": int(1600 * scale), "height": int(900 * scale)}
    region = logical_to_physical((100, 80, 500, 400), (1600, 900), monitor)
    assert region == Region(-2400 + int(100 * scale), -100 + int(80 * scale), int(500 * scale), int(400 * scale))
    validate_monitor(region, [monitor])


def test_region_crossing_monitor_is_rejected():
    with pytest.raises(ValueError):
        logical_to_physical((1500, 0, 200, 200), (1600, 900), {"left": 0, "top": 0, "width": 1600, "height": 900})
    with pytest.raises(ValueError):
        validate_monitor(Region(-50, 0, 100, 200), [{"left": 0, "top": 0, "width": 1600, "height": 900}])


def test_fractional_dpi_uses_qt_scale_not_rounded_screen_ratio():
    region = logical_to_physical((61, 93, 640, 427), (1707, 960),
        {"left": 0, "top": 0, "width": 2560, "height": 1440}, scale=1.5)
    assert region == Region(92, 140, 960, 640)


def test_stability_requires_two_matching_observations():
    stable = StableFrames()
    a = np.zeros((120, 100, 3), np.uint8)
    b = np.full_like(a, 255)
    assert not stable.observe(a)
    assert not stable.observe(b)
    assert stable.observe(b.copy())
    stable.reset()
    assert not stable.observe(b)


def test_review_split_merge_crop_roundtrip_and_warnings(tmp_path):
    review = Review([PageBoundary(1, 10, 20, 190, 800, .95, True)], [], 200, 1000)
    review.split(0, 400)
    assert [(p.top, p.bottom) for p in review.pages] == [(20, 400), (400, 800)]
    review.merge(0)
    assert len(review.pages) == 1
    review.update(0, 12, 30, 180, 790)
    assert not review.pages[0].complete
    review.save(tmp_path)
    loaded = Review.load(tmp_path, 200, 1000)
    assert loaded.pages == review.pages
    assert loaded.export_warnings({"needs_recovery": True})
    assert Review.load(tmp_path, 200, 1001) is None
    with pytest.raises(ValueError):
        loaded.update(0, 0, 0, 400, 500)
    with pytest.raises(ValueError):
        loaded.split(0, 900)
    loaded.delete(0)
    assert not loaded.pages


def test_overlapping_pages_warn_before_export():
    review = Review([PageBoundary(1, 0, 0, 200, 600, .95, True),
                     PageBoundary(2, 0, 500, 200, 1000, .95, True)], [], 200, 1000)
    assert any("重叠" in warning for warning in review.export_warnings({}))
