"""MSS is constructed and used on the same capture thread."""
import numpy as np
from .region import validate_monitor


class ScreenSource:
    def __init__(self, region):
        import mss
        self.region = region
        self.screen = mss.mss(with_cursor=False)
        validate_monitor(region, self.screen.monitors[1:])

    def grab(self):
        shot = self.screen.grab(self.region.as_dict())
        return np.array(shot, dtype=np.uint8)[:, :, [2, 1, 0]].copy()

    def close(self):
        self.screen.close()
