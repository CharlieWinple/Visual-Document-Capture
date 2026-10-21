import numpy as np
import pytest
from vdc.app.fixtures import make_fixture
from vdc.stitching.alignment import estimate_scroll_offset


@pytest.mark.parametrize("offset", [-180, -73, 0, 52, 170, 240])
def test_signed_offsets(offset):
    canvas = make_fixture().canvas
    top = 400
    result = estimate_scroll_offset(canvas[top:top+360], canvas[top+offset:top+offset+360])
    assert result.accepted, result
    assert result.offset == offset


def test_blank_is_unknown_even_if_identical():
    blank = np.full((360, 640, 3), 255, np.uint8)
    assert estimate_scroll_offset(blank, blank).reason == "insufficient_texture"


def test_periodic_patterns_are_ambiguous():
    rng = np.random.default_rng(4)
    image = np.tile(rng.integers(0, 255, (24, 640, 3), dtype=np.uint8), (15, 1, 1))
    assert estimate_scroll_offset(image, np.roll(image, 24, axis=0)).reason == "ambiguous_repeated_content"


def test_no_overlap_and_zoom_rejected():
    import cv2
    canvas = make_fixture().canvas
    first = canvas[:360]
    assert not estimate_scroll_offset(first, canvas[1300:1660]).accepted
    scaled = cv2.resize(first, None, fx=1.1, fy=1.1)[:360, :640]
    assert not estimate_scroll_offset(first, scaled).accepted
    assert estimate_scroll_offset(first, first[:300]).reason == "frame_size_changed"
