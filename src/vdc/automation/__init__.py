"""Capture contracts and foreground visual-feedback automation."""
from typing import Protocol
import numpy as np


class CaptureSource(Protocol):
    def grab(self) -> np.ndarray: ...


class ScrollController(Protocol):
    def scroll(self, units: int) -> None:
        """Positive wheel units scroll down; displacement must be measured visually."""
        ...
