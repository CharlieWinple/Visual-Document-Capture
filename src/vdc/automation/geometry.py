"""Page-height proposals; only observed pixel overlap may be connected."""
import numpy as np
from vdc.pagination.detector import detect_page_boundaries, learn_geometry


def learn_anchor(store):
    result = detect_page_boundaries(store.iter_blocks())
    joins = store.state.get("geometry_joins", [])
    observed = [p for p in result.pages if p.complete and not any(
        j["position"] < p.bottom and j["end"] > p.top for j in joins)]
    model = learn_geometry(observed)
    if model is None or not result.pages:
        return None
    # Reject mixed sizes, including minority outliers accepted by pagination.
    if any(abs(p.bottom-p.top-model["height"]) > 3 or
           abs(p.right-p.left-model["width"]) > 3 for p in observed):
        return None
    page = result.pages[-1]
    if page.complete or page.bottom != store.state["height"] or page.top < 3:
        return None
    if page.bottom-page.top >= model["height"] or abs(page.right-page.left-model["width"]) > 3:
        return None
    # The anchor top must have an observed background gap, not a clipped start.
    gap = store.read_rows(page.top-3, page.top)
    if np.max(np.ptp(gap.astype(np.int16), axis=1)) > 8:
        return None
    return {**model, "top": page.top, "left": page.left, "right": page.right}


def propose_connection(store, frame, anchor):
    if anchor is None:
        return None
    pages = detect_page_boundaries([(0, frame)]).pages
    if not pages:
        return None
    page = pages[0]
    # Need the current page's clipped top and a visible bottom with background.
    if page.top != 0 or page.bottom > len(frame)-3:
        return None
    if abs(page.left-anchor["left"]) > 2 or abs(page.right-anchor["right"]) > 2:
        return None
    gap = frame[page.bottom:page.bottom+3].astype(np.int16)
    if np.max(np.ptp(gap, axis=1)) > 8:
        return None
    position = anchor["top"] + anchor["height"] - page.bottom
    delta = position-store.state["position"]
    # At least 25% direct overlap, and no unobserved rows to fill. The next-page
    # alternative must lie outside this bounded overlap window as well.
    limit = len(frame)-max(64, len(frame)//4)
    alternatives = [delta + k*anchor["period"] for k in (-1, 0, 1)]
    if sum(0 < d <= limit for d in alternatives) != 1 or not 0 < delta <= limit:
        return None
    overlap = len(frame)-delta
    previous = store.previous[delta:]
    if previous.shape != frame[:overlap].shape:
        return None
    error = np.abs(previous.astype(np.int16)-frame[:overlap].astype(np.int16))
    if np.mean(error) > 1 or np.mean(np.max(error, axis=2) > 8) > .001:
        return None
    return {"position": position, "end": position+len(frame), "anchor": anchor,
            "method": "page_height_with_pixel_overlap", "requires_review": True}
