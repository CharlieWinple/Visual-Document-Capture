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


def detect_page_boundaries(blocks, progress=None) -> PaginationResult:
    """Consume ordered blocks; retain only row geometry, not a long pixel image."""
    rows, kinds, gap_evidence = [], [], []  # 1 paper, 0 observed background, -1 unknown
    expected_top = 0
    for top, image in blocks:
        if top != expected_top:
            raise ValueError("Blocks must be contiguous and ordered")
        h, w = image.shape[:2]
        expected_top += h
        # Vectorize side statistics in bounded chunks, even for a huge input block.
        for y in range(0, h, 256):
            if progress:
                progress(top+y)
            gray_chunk = image[y:y+256].mean(axis=2)
            sides = np.concatenate((gray_chunk[:, :8], gray_chunk[:, -8:]), axis=1)
            backgrounds = np.median(sides, axis=1)
            consistent_rows = (np.abs(np.median(gray_chunk[:, :8], axis=1) -
                                      np.median(gray_chunk[:, -8:], axis=1)) <= 12)
            consistent_rows &= np.median(np.abs(sides-backgrounds[:, None]), axis=1) <= 6
            for gray, bg, consistent in zip(gray_chunk, backgrounds, consistent_rows):
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
    if progress:
        progress(expected_top)
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


def _calibrate_from_gaps(profile, anchor, gap):
    """Match repeated separator bands without requiring visible side margins.

    The manual separator supplies appearance and phase; several observations
    must corroborate a period before any boundary is adjusted.
    """
    if gap < 3 or not len(profile):
        return None
    height = anchor.bottom-anchor.top
    period = float(height+gap)
    tolerance = max(8, height*.025)
    level, coverage = profile.T
    mask = (coverage >= .95) & (level <= 250)
    runs = np.flatnonzero(np.diff(np.r_[False, mask, False])).reshape(-1, 2)
    candidates = []
    for a, b in runs:
        if not max(3, gap*.6) <= b-a <= gap*1.4+3:
            continue
        color = float(np.median(level[a:b]))
        # Paper on both sides must be lighter; white page whitespace is not a gap.
        before = level[max(0, a-8):a]
        after = level[b:b+8]
        if not len(before) or not len(after):
            continue
        if min(float(np.median(before)), float(np.median(after))) < color+4:
            continue
        candidates.append((int(a), int(b), color))
    seeds = [c for c in candidates if abs(c[0]-anchor.bottom) <= tolerance and
             abs(c[1]-(anchor.bottom+gap)) <= tolerance]
    if len(seeds) != 1:
        return None
    seed = seeds[0]
    candidates = [c for c in candidates if abs(c[2]-seed[2]) <= 5]
    matches = {0: seed}
    # Follow in both directions, tolerating missing separators without treating
    # them as observed. Ambiguous candidates never supply a confirmed boundary.
    for direction in (-1, 1):
        last, index = seed, 0
        while True:
            options = []
            for c in candidates:
                distance = direction*(c[0]-last[0])
                steps = round(distance/period)
                if 1 <= steps <= 4 and abs(distance-steps*period) <= tolerance:
                    options.append((steps, c))
            if not options:
                break
            nearest = min(n for n, c in options)
            options = [c for n, c in options if n == nearest]
            if len(options) != 1:
                break
            last = options[0]
            index += direction*nearest
            matches[index] = last
            if len(matches) >= 3:
                xs = np.array(sorted(matches))
                ys = np.array([matches[k][0] for k in xs])
                period = float(np.polyfit(xs, ys, 1)[0])
    if len(matches) < 3:
        return None
    xs = np.array(sorted(matches))
    ys = np.array([matches[k][0] for k in xs])
    period, intercept = np.polyfit(xs, ys, 1)
    if (abs(period-(height+gap)) > tolerance or
            np.max(np.abs(ys-(intercept+xs*period))) > max(3, height*.005)):
        return None
    heights = [matches[k][0]-matches[k-1][1] for k in matches if k-1 in matches]
    if not heights or max(heights)-min(heights) > max(3, height*.005):
        return None
    page_height = float(np.median(heights))
    total = len(profile)
    pages = []
    first = int(np.floor((0-intercept)/period))
    last = int(np.ceil((total-intercept+page_height)/period))
    for k in range(first, last+1):
        upper, lower = matches.get(k-1), matches.get(k)
        top = upper[1] if upper else round(intercept+k*period-page_height)
        bottom = lower[0] if lower else round(intercept+k*period)
        if k == 0 and upper is None:
            top = anchor.top
        if bottom <= 0 or top >= total or bottom <= top:
            continue
        complete = upper is not None and lower is not None and 0 <= top < bottom <= total
        pages.append(PageBoundary(len(pages)+1, anchor.left, max(0, top), anchor.right,
            min(total, bottom), .9 if complete else .45, complete,
            "gap_verified" if complete else "calibrated"))
    warnings = ["gap_guided_horizontal_bounds_require_review"]
    if any(not p.complete for p in pages):
        warnings.append("calibrated_boundaries_require_review")
    geometry = {"height": page_height, "width": anchor.right-anchor.left,
                "gap": round(float(period-page_height), 3), "period": round(float(period), 6),
                "support": len(matches), "source": "manual_gap_refined",
                "verified_pages": sum(p.complete for p in pages),
                "unverified_pages": sum(not p.complete for p in pages)}
    return PaginationResult(pages, warnings, geometry)


def calibrate_page_boundaries(blocks, anchor: PageBoundary, gap: int, progress=None) -> PaginationResult:
    """User-supplied complete-page anchor. Predicted boundaries stay unconfirmed."""
    if anchor.top < 0 or anchor.bottom <= anchor.top or anchor.right <= anchor.left or gap < 0:
        raise ValueError("标定页或页间距无效")
    height = anchor.bottom - anchor.top
    period = height + gap
    if gap > height // 4:
        raise ValueError("页间距过大，请检查全局像素坐标")
    total, width = 0, 0
    profiles = []
    def observed_blocks():
        nonlocal total, width
        for top, image in blocks:
            total, width = top + len(image), image.shape[1]
            if 0 <= anchor.left < anchor.right <= width and gap >= 3:
                step = max(1, (anchor.right-anchor.left)//256)
                for offset in range(0, len(image), 256):
                    gray = image[offset:offset+256, anchor.left:anchor.right:step].mean(axis=2)
                    median = np.median(gray, axis=1)
                    coverage = np.mean(np.abs(gray-median[:, None]) <= 5, axis=1)
                    profiles.append(np.column_stack((median, coverage)))
            yield top, image
    observed = detect_page_boundaries(observed_blocks(), progress)
    if anchor.bottom > total or not 0 <= anchor.left < anchor.right <= width:
        raise ValueError("标定页超出已采集内容")
    tolerance = max(4, round(height * .01))
    if any(abs(p.bottom-p.top-height) > tolerance for p in observed.pages if p.complete):
        raise ValueError("已观察到不同尺寸页面或标定页高度不符，不能统一套用；请使用逐页编辑")
    # Snap the anchor, then use repeated observed geometry to remove period bias.
    # Never promote a predicted boundary to visually confirmed.
    nearby_anchor = [p for p in observed.pages if p.complete and
                     abs(p.top-anchor.top) <= tolerance and
                     abs(p.bottom-anchor.bottom) <= tolerance and
                     abs(p.left-anchor.left) <= tolerance and
                     abs(p.right-anchor.right) <= tolerance]
    if not nearby_anchor and profiles:
        guided = _calibrate_from_gaps(np.concatenate(profiles), anchor, gap)
        if guided is not None:
            return guided
    origin = anchor.top
    refined = False
    if nearby_anchor:
        origin = nearby_anchor[0].top
        height = nearby_anchor[0].bottom-nearby_anchor[0].top
        period = height + gap
        if observed.geometry:
            learned = observed.geometry
            if abs(learned["period"]-period) <= tolerance*2:
                height, gap, period = learned["height"], learned["gap"], learned["period"]
                refined = True
    pages = []
    # Sorted candidates and a moving cursor avoid scanning all pages for each page.
    candidates = sorted(observed.pages, key=lambda p: p.top)
    cursor = 0
    top = origin - ((origin + period - 1)//period)*period
    while top < total:
        bottom = top + height
        while cursor < len(candidates) and candidates[cursor].top < top-tolerance:
            cursor += 1
        match = candidates[cursor] if cursor < len(candidates) else None
        if bottom > 0:
            if (match is not None and abs(match.top-top) <= tolerance and
                    abs(match.bottom-bottom) <= tolerance and
                    abs(match.left-anchor.left) <= tolerance and
                    abs(match.right-anchor.right) <= tolerance):
                pages.append(match)
                # Correct phase at every observed page instead of accumulating drift.
                top = match.top
                cursor += 1
            else:
                pages.append(PageBoundary(0, anchor.left, max(0, top), anchor.right, min(total, bottom),
                                          .45, False, "calibrated"))
        top += period
    pages = [replace(p, page_id=i+1) for i, p in enumerate(pages)]
    warnings = list(observed.warnings)
    if any(p.source == "calibrated" for p in pages):
        warnings.append("calibrated_boundaries_require_review")
    geometry = {"height": height, "width": anchor.right-anchor.left, "gap": gap,
                "period": period, "support": observed.geometry["support"] if refined else 1,
                "source": "manual_refined" if refined else "manual"}
    return PaginationResult(pages, warnings, geometry)
