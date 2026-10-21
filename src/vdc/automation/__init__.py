"""Extension contracts; no automatic input is performed in Phase 0."""
from typing import Protocol
import numpy as np


class CaptureSource(Protocol):
    def grab(self) -> np.ndarray: ...


class ScrollController(Protocol):
    def scroll(self, pixels: int) -> None: ...
