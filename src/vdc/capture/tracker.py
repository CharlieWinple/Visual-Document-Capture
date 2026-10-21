"""Continuous observations; uncertain frames never move the trusted reference."""
from dataclasses import dataclass
import time
import numpy as np
from vdc.stitching.alignment import Alignment


@dataclass(frozen=True)
class TrackingResult:
    state: str
    alignment: Alignment
    recovered: bool = False


class ContinuousTracker:
    def __init__(self, store, pending_seconds=1.5, max_pending_frames=24):
        self.store = store
        self.pending_seconds = pending_seconds
        self.max_pending_frames = max_pending_frames
        self.pending_since = None
        self.pending_count = 0
        self.first_failed = None
        self.last_failed = None

    def reset(self):
        self.pending_since = None
        self.pending_count = 0
        self.first_failed = self.last_failed = None

    def observe(self, frame: np.ndarray, now=None):
        now = time.monotonic() if now is None else now
        result = self.store.add(frame)
        if result.accepted:
            recovered = self.pending_since is not None
            self.reset()
            return TrackingResult("capturing", result, recovered)
        if self.pending_since is None:
            self.pending_since = now
            self.first_failed = frame.copy()
        self.last_failed = frame.copy()
        self.pending_count += 1
        expired = now-self.pending_since >= self.pending_seconds or self.pending_count >= self.max_pending_frames
        fatal = result.reason in ("frame_size_changed", "before_session_start", "viewport_too_small")
        return TrackingResult("needs_recovery" if expired or fatal else "pending", result)
