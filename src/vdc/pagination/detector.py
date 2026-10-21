"""White paper on a darker uniform background. No page-size assumptions."""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class PageBoundary:
    page_id: int
    left: int
    top: int
    right: int
    bottom: int
    confidence: float
    complete: bool


@dataclass
class PaginationResult:
    pages: list[PageBoundary]
    warnings: list[str]


def detect_page_boundaries(blocks) -> PaginationResult:
    """Consume ordered (top, RGB block) pairs; retain row geometry, not images."""
    rows = []
    expected_top = 0
    invalid = 0
    for top, image in blocks:
        if top != expected_top:
            raise ValueError("Blocks must be contiguous and ordered")
        h, w = image.shape[:2]
        expected_top += h
        for row in image:
            gray = row.mean(axis=1)
            sides = np.concatenate((gray[:4], gray[-4:]))
            background = float(np.median(sides))
            if background > 225 or np.ptp(sides) > 15:
                rows.append(None)
                invalid += 1
                continue
            white = np.flatnonzero(gray >= 245)
            if len(white) == 0:
                rows.append(None)
                continue
            left, right = int(white[0]), int(white[-1]) + 1
            if left < 5 or right > w - 5 or right - left < w * 0.4:
                rows.append(None)
                invalid += 1
                continue
            if abs(float(np.median(gray[:left])) - background) > 10 or abs(float(np.median(gray[right:])) - background) > 10:
                rows.append(None)
                invalid += 1
                continue
            rows.append((left, right))
    pages, warnings = [], []
    start = None
    for y in range(len(rows) + 1):
        present = y < len(rows) and rows[y] is not None
        if present and start is None:
            start = y
        if not present and start is not None:
            edges = np.array(rows[start:y])
            stable = bool(np.max(np.ptp(edges, axis=0)) <= 3)
            if y - start >= 40 and stable:
                left, right = np.median(edges, axis=0).astype(int)
                complete = start > 0 and y < len(rows)
                # Any unsupported row means we cannot certify the surrounding
                # absence of paper as a real background gap.
                complete = complete and invalid == 0
                pages.append(PageBoundary(len(pages) + 1, int(left), start, int(right), y,
                                          0.95 if complete else 0.55, complete))
            else:
                warnings.append(f"uncertain_paper_geometry:{start}:{y}")
            start = None
    if invalid:
        warnings.append(f"unsupported_background_or_clipped_edges:{invalid}_rows")
    if not pages:
        warnings.append("no_reliable_page_boundaries")
    if any(not page.complete for page in pages):
        warnings.append("incomplete_or_uncertain_pages_require_review")
    return PaginationResult(pages, warnings)
