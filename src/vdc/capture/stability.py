"""Bounded two-frame stability filter; no stored screenshot history."""
import numpy as np


class StableFrames:
    def __init__(self):
        self.previous = None

    def reset(self):
        self.previous = None

    def observe(self, frame):
        previous, self.previous = self.previous, frame
        if previous is None or previous.shape != frame.shape:
            return False
        # Sparse RGB samples keep this inexpensive at high screen resolutions.
        delta = np.abs(previous[::3, ::3].astype(np.int16) - frame[::3, ::3].astype(np.int16))
        return float(delta.mean()) < 0.25 and float(np.mean(delta > 8)) < 0.001
