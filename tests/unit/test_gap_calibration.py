import numpy as np
import pytest
from vdc.pagination.detector import PageBoundary, calibrate_page_boundaries


def clipped_document(count=32):
    # Same geometry as the reported session, but no user document content.
    height, period = 1347, 1370.4
    gaps = [(round(1350.4+k*period), round(1350.4+k*period)+
             (24 if k % 5 in (0, 2) else 23)) for k in range(count-1)]
    # Derive ends from the next gap to ensure equal page heights.
    gaps = [(a, round(1350.4+(k+1)*period)-height) for k, (a, b) in enumerate(gaps)]
    image = np.full((round(count*period)-30, 128, 3), 255, np.uint8)
    image[40::43, 15:110] = 180  # Text-like lines, not full-width separators.
    for a, b in gaps:
        image[a:b] = 235
    return image, gaps


def calibrate(image, anchor=None):
    anchor = anchor or PageBoundary(1, 0, 0, 128, 1351, .45, False)
    # Include block boundaries inside separator bands.
    return calibrate_page_boundaries(
        ((i, image[i:i+137]) for i in range(0, len(image), 137)), anchor, 23)


def test_clipped_sides_fractional_period_and_long_range_drift():
    image, gaps = clipped_document()
    result = calibrate(image)
    assert len(result.pages) == 32
    assert result.geometry["source"] == "manual_gap_refined"
    assert result.geometry["period"] == pytest.approx(1370.4, abs=.01)
    for k, page in enumerate(result.pages[1:-1], 1):
        assert (page.top, page.bottom) == (gaps[k-1][1], gaps[k][0])
        assert page.complete
    assert not result.pages[0].complete
    assert not result.pages[-1].complete
    assert result.geometry["verified_pages"] == 30


def test_missing_separator_remains_unverified_and_later_pages_recover():
    image, gaps = clipped_document()
    a, b = gaps[10]
    image[a:b] = 255
    result = calibrate(image)
    assert result.geometry["source"] == "manual_gap_refined"
    assert not result.pages[10].complete
    assert not result.pages[11].complete
    assert result.pages[12].complete
    assert result.pages[12].top == gaps[11][1]
    assert result.pages[-1].top == gaps[-1][1]


def test_isolated_gray_bar_cannot_supply_a_page_model():
    image = np.full((6000, 128, 3), 255, np.uint8)
    image[1350:1374] = 235
    result = calibrate(image)
    assert result.geometry["source"] == "manual"
    assert all(not p.complete for p in result.pages)


def test_internal_gray_bar_away_from_expected_boundary_is_ignored():
    image, gaps = clipped_document()
    image[700:724] = 235
    image[6000:6024] = 235
    result = calibrate(image)
    assert len(result.pages) == 32
    assert result.geometry["support"] == 31
    assert result.pages[1].top == gaps[0][1]


def test_anchor_in_middle_refines_in_both_directions():
    image, gaps = clipped_document()
    k = 15
    anchor = PageBoundary(1, 0, gaps[k-1][1]+2, 128, gaps[k][0]+3, .45, False)
    result = calibrate(image, anchor)
    assert result.geometry["support"] == 31
    assert len(result.pages) == 32
    assert result.pages[1].top == gaps[0][1]
    assert result.pages[-1].top == gaps[-1][1]
