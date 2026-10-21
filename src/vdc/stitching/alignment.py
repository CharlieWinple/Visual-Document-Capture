"""Conservative integer vertical registration, independent of capture and UI."""
from dataclasses import dataclass
import cv2
import numpy as np


@dataclass(frozen=True)
class Alignment:
    status: str
    offset: int | None
    confidence: float
    reason: str

    @property
    def accepted(self) -> bool:
        return self.offset is not None


def estimate_scroll_offset(previous: np.ndarray, current: np.ndarray) -> Alignment:
    if previous.shape != current.shape:
        return Alignment("failed", None, 0.0, "frame_size_changed")
    if previous.dtype != np.uint8 or previous.ndim != 3 or previous.shape[2] != 3:
        raise ValueError("Expected RGB uint8 images")
    if current.dtype != np.uint8:
        raise ValueError("Expected RGB uint8 images")
    h, w = previous.shape[:2]
    if h < 96 or w < 64:
        return Alignment("failed", None, 0.0, "viewport_too_small")
    # Keep all rows, but reduce horizontal work. Vertical gradients suppress
    # constant side margins, which otherwise produce misleadingly good matches.
    def features(image):
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY).astype(np.float32)
        gray = cv2.resize(gray, (min(w, 320), h), interpolation=cv2.INTER_AREA)
        edge = np.zeros_like(gray)
        edge[1:] = np.abs(np.diff(gray, axis=0))
        return gray, edge
    a, ea = features(previous)
    b, eb = features(current)
    # Faint moving watermarks still carry position information. Completely
    # uniform paper (including stationary vertical margins) does not.
    if min(np.mean(ea), np.mean(eb)) < 0.04:
        return Alignment("failed", None, 0.0, "insufficient_texture")
    band = max(24, h // 10)
    overlap_min = max(64, h // 4)
    candidates = {0}
    for y in np.linspace(1, h - band, 7, dtype=int):
        template = eb[y:y + band]
        if np.mean(template) < 0.04:
            continue
        scores = cv2.matchTemplate(ea, template, cv2.TM_CCOEFF_NORMED).ravel()
        # Multiple peaks must survive proposal generation to detect repeated text.
        for _ in range(16):
            peak = int(np.argmax(scores))
            if scores[peak] < 0.75:
                break
            candidates.add(peak - int(y))
            scores[max(0, peak - 2):peak + 3] = -1
    matches = []
    for d in candidates:
        if abs(d) > h - overlap_min:
            continue
        # Ignore a few gradient border rows, which have no preceding observation.
        lo, hi = max(0, d) + 2, min(h, h + d) - 2
        pa, pb = a[lo:hi], b[lo - d:hi - d]
        ga, gb = ea[lo:hi], eb[lo - d:hi - d]
        energy = float(np.mean(ga) + np.mean(gb))
        if energy < 0.08:
            continue
        pixel_error = float(np.mean(np.abs(pa - pb)))
        edge_error = float(np.mean(np.abs(ga - gb))) / energy
        if pixel_error <= 2.0 and edge_error <= 0.08:
            matches.append((edge_error + pixel_error / 255, d))
    matches.sort()
    if not matches:
        return Alignment("failed", None, 0.0, "no_reliable_overlap_or_render_changed")
    best, d = matches[0]
    if any(abs(other - d) > 2 and error - best < 0.025 for error, other in matches[1:]):
        return Alignment("failed", None, 0.0, "ambiguous_repeated_content")
    return Alignment("stationary" if d == 0 else "forward" if d > 0 else "reverse",
                     d, round(max(0.0, 1 - best * 4), 4), "verified_overlap")
