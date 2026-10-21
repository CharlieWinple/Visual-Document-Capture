import numpy as np
from vdc.app.fixtures import make_fixture
from vdc.pagination.detector import detect_page_boundaries


def test_mixed_sizes_and_internal_blanks():
    fixture = make_fixture()
    result = detect_page_boundaries([(0, fixture.canvas)])
    assert not result.warnings
    assert [(p.left, p.top, p.right, p.bottom) for p in result.pages] == fixture.bounds
    assert all(p.complete for p in result.pages)


def test_partial_pages_are_not_complete():
    fixture = make_fixture()
    result = detect_page_boundaries([(0, fixture.canvas[150:-150])])
    assert not result.pages[0].complete
    assert not result.pages[-1].complete
    assert result.warnings


def test_no_visible_paper_edges_reports_unknown():
    result = detect_page_boundaries([(0, np.full((500, 600, 3), 255, np.uint8))])
    assert not result.pages
    assert "no_reliable_page_boundaries" in result.warnings


def test_thin_reader_border_does_not_hide_visible_paper_edges():
    fixture = make_fixture()
    fixture.canvas[:, -1] = 243
    result = detect_page_boundaries([(0, fixture.canvas)])
    assert not result.warnings
    assert [(p.left, p.top, p.right, p.bottom) for p in result.pages] == fixture.bounds
