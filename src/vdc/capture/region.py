"""Physical screen coordinates. No dependency on Qt or browser internals."""
from dataclasses import dataclass, asdict
import math


@dataclass(frozen=True)
class Region:
    left: int
    top: int
    width: int
    height: int

    def __post_init__(self):
        if self.width < 64 or self.height < 96:
            raise ValueError("选区至少需要 64×96 个物理像素")

    def as_dict(self):
        return asdict(self)


def logical_to_physical(rect, logical_size, monitor, scale=None) -> Region:
    """Map screen-local logical coordinates, never scale a desktop origin."""
    x, y, width, height = rect
    lw, lh = logical_size
    if lw <= 0 or lh <= 0 or width <= 0 or height <= 0:
        raise ValueError("无效的显示器或选区尺寸")
    if x < 0 or y < 0 or x + width > lw or y + height > lh:
        raise ValueError("选区必须位于同一显示器内")
    # Qt rounds the logical screen size (2560 / 1.5 -> 1707). Dividing the
    # physical size by that rounded number introduces a one-pixel border drift.
    sx = scale if scale is not None else monitor["width"] / lw
    sy = scale if scale is not None else monitor["height"] / lh
    left, top = math.floor(x * sx + .5), math.floor(y * sy + .5)
    right = min(monitor["width"], math.floor((x + width) * sx + .5))
    bottom = min(monitor["height"], math.floor((y + height) * sy + .5))
    return Region(monitor["left"] + left, monitor["top"] + top, right - left, bottom - top)


def validate_monitor(region: Region, monitors):
    if not any(region.left >= m["left"] and region.top >= m["top"]
               and region.left + region.width <= m["left"] + m["width"]
               and region.top + region.height <= m["top"] + m["height"] for m in monitors):
        raise ValueError("显示器布局已变化或选区越界，请重新框选")
