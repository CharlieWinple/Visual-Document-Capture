"""Adaptive paper/background segmentation with evidence-based page geometry."""
from dataclasses import dataclass, replace
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
    source: str = "observed"


@dataclass
class PaginationResult:
    pages: list[PageBoundary]
    warnings: list[str]
    geometry: dict | None = None


def learn_geometry(pages):
    complete = [p for p in pages if p.complete]
    if len(complete) < 3:
        return None
    heights = np.array([p.bottom - p.top for p in complete])
    widths = np.array([p.right - p.left for p in complete])
    height, width = float(np.median(heights)), float(np.median(widths))
    same = (abs(heights - height) <= max(3, height * .01)) & (abs(widths - width) <= max(3, width * .01))
    if np.mean(same) < .8 or np.count_nonzero(same) < 3:
        return None
    gaps = [b.top - a.bottom for a, b in zip(complete, complete[1:])
            if 0 < b.top - a.bottom < height * .1]
    if len(gaps) < 2:
        return None
    gap = float(np.median(gaps))
    if max(abs(np.array(gaps) - gap)) > max(3, gap * .2):
        return None
    return {"height": round(height), "width": round(width), "gap": round(gap),
            "period": round(height + gap), "support": int(np.count_nonzero(same)), "source": "learned"}


def detect_page_boundaries(blocks) -> PaginationResult:
    """Consume ordered blocks; retain only row geometry, not a long pixel image."""
    rows, kinds, gap_evidence = [], [], []  # 1 paper, 0 observed background, -1 unknown
    expected_top = 0
    for top, image in blocks:
        if top != expected_top:
            raise ValueError("Blocks must be contiguous and ordered")
        h, w = image.shape[:2]
        expected_top += h
        for row in image:
            gray = row.mean(axis=1)
            sides = np.r_[gray[:8], gray[-8:]]
            bg = float(np.median(sides))
            consistent = abs(float(np.median(gray[:8])) - float(np.median(gray[-8:]))) <= 12
            consistent &= np.median(np.abs(sides - bg)) <= 6
            gap_evidence.append(bool(consistent and bg <= 249 and
                                    np.mean(np.abs(gray[4:-4] - bg) <= 5) >= .90))
            if not consistent or bg > 249:
                rows.append(None)
                kinds.append(-1)
                continue
            threshold = bg + max(5, (255 - bg) * .55)
            white = np.flatnonzero(gray[4:-4] >= threshold) + 4
            if not len(white):
                # A full-width dark figure is not a background gap.
                background = np.mean(np.abs(gray[4:-4] - bg) <= 5) >= .98
                rows.append(None)
                kinds.append(0 if background else -1)
                continue
            left, right = int(white[0]), int(white[-1]) + 1
            valid = left >= 5 and right <= w - 5 and right - left >= w * .4
            if valid:
                valid = abs(float(np.median(gray[:left])) - bg) <= 8 and abs(float(np.median(gray[right:])) - bg) <= 8
            rows.append((left, right) if valid else None)
            kinds.append(1 if valid else -1)
    # Thin shadow/antialias transitions bounded by visible paper and observed
    # background are edge uncertainty, not a missing region inside a page.
    i = 0
    while i < len(kinds):
        if kinds[i] != -1:
            i += 1
            continue
        end = i
        while end < len(kinds) and kinds[end] == -1:
            end += 1
        if end-i <= 3 and i > 0 and end < len(kinds) and {kinds[i-1], kinds[end]} == {0, 1}:
            kinds[i:end] = [0] * (end-i)
        i = end
    pages, warnings = [], []
    start = None
    for y in range(len(rows) + 1):
        present = y < len(rows) and rows[y] is not None
        if present and start is None:
            start = y
        if not present and start is not None:
            edges = np.array(rows[start:y])
            left, right = np.median(edges, axis=0).astype(int)
            stable = np.mean(np.max(np.abs(edges - [left, right]), axis=1) <= 4) >= .95
            if y - start >= 40 and stable:
                complete = start >= 3 and y + 3 <= len(rows)
                complete = complete and all(k == 0 for k in kinds[start-3:start] + kinds[y:y+3])
                pages.append(PageBoundary(len(pages) + 1, int(left), start, int(right), y,
                                          .94 if complete else .55, complete))
            else:
                warnings.append(f"uncertain_paper_geometry:{start}:{y}")
            start = None
    invalid = kinds.count(-1)
    if invalid:
        warnings.append(f"unsupported_background_or_clipped_edges:{invalid}_rows")
    if not pages:
        warnings.append("no_reliable_page_boundaries")
    geometry = learn_geometry(pages)
    if geometry:
        # Corroborate observed pages, never invent a boundary from a period.
        pages = [replace(p, confidence=.98) if p.complete and
                 abs(p.bottom-p.top-geometry["height"]) <= max(3, geometry["height"]*.01)
                 else p for p in pages]
        # Search near the learned period, but split only where an actual run of
        # background-like rows exists. Weak evidence remains review-required.
        anchor = next(p for p in pages if p.complete)
        transitions = np.flatnonzero(np.diff(np.r_[False, gap_evidence, False]))
        candidates = [(int(a), int(b)) for a, b in transitions.reshape(-1, 2)
                      if 3 <= b-a <= max(6, geometry["gap"]*2)]
        refined = []
        for page in pages:
            internal = []
            for a, b in candidates:
                center = (a+b)/2
                k = round((center - anchor.bottom - geometry["gap"]/2) / geometry["period"])
                predicted = anchor.bottom + geometry["gap"]/2 + k*geometry["period"]
                if page.top+20 < a < b < page.bottom-20 and abs(center-predicted) <= max(4, geometry["height"]*.01):
                    internal.append((a, b))
            top = page.top
            for a, b in internal:
                refined.append(replace(page, top=top, bottom=a, confidence=.75, complete=False, source="geometry_verified"))
                top = b
            refined.append(replace(page, top=top, confidence=.75, complete=False, source="geometry_verified") if internal else page)
        if len(refined) != len(pages):
            warnings.append("geometry_assisted_boundaries_require_review")
        pages = [replace(p, page_id=i+1) for i, p in enumerate(refined)]
        if any(p.bottom-p.top > geometry["height"] * 1.1 for p in pages):
            warnings.append("page_geometry_conflict_requires_review")
    if any(not p.complete for p in pages):
        warnings.append("incomplete_or_uncertain_pages_require_review")
    return PaginationResult(pages, warnings, geometry)


def calibrate_page_boundaries(blocks, anchor: PageBoundary, gap: int) -> PaginationResult:
    """User-supplied complete-page anchor. Predicted boundaries stay unconfirmed."""
    if anchor.top < 0 or anchor.bottom <= anchor.top or anchor.right <= anchor.left or gap < 0:
        raise ValueError("标定页或页间距无效")
    height = anchor.bottom - anchor.top
    period = height + gap
    if gap > height // 4:
        raise ValueError("页间距过大，请检查全局像素坐标")
    total, width = 0, 0
    def observed_blocks():
        nonlocal total, width
        for top, image in blocks:
            total, width = top + len(image), image.shape[1]
            yield top, image
    observed = detect_page_boundaries(observed_blocks())
    if anchor.bottom > total or not 0 <= anchor.left < anchor.right <= width:
        raise ValueError("标定页超出已采集内容")
    tolerance = max(4, round(height * .01))
    if any(abs(p.bottom-p.top-height) > tolerance for p in observed.pages if p.complete):
        raise ValueError("已观察到不同尺寸页面或标定页高度不符，不能统一套用；请使用逐页编辑")
    pages = []
    top = anchor.top
    while top > 0:
        top -= period
    while top < total:
        bottom = top + height
        if bottom > 0:
            nearby = [p for p in observed.pages if abs(p.top-top) <= tolerance and abs(p.bottom-bottom) <= tolerance]
            if nearby:
                pages.append(nearby[0])
            else:
                pages.append(PageBoundary(0, anchor.left, max(0, top), anchor.right, min(total, bottom),
                                          .45, False, "calibrated"))
        top += period
    pages = [replace(p, page_id=i+1) for i, p in enumerate(pages)]
    warnings = list(observed.warnings)
    if any(p.source == "calibrated" for p in pages):
        warnings.append("calibrated_boundaries_require_review")
    geometry = {"height": height, "width": anchor.right-anchor.left, "gap": gap,
                "period": period, "support": 1, "source": "manual"}
    return PaginationResult(pages, warnings, geometry)
