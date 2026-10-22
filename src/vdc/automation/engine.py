"""Bounded visual feedback loop. Wheel units are never treated as pixels."""
from dataclasses import dataclass
import numpy as np
from vdc.capture.stability import StableFrames
from vdc.pagination.detector import detect_page_boundaries
from vdc.stitching.alignment import estimate_scroll_offset


@dataclass(frozen=True)
class AutoSettings:
    step_percent: int = 30
    settle_ms: int = 400
    sample_ms: int = 60
    end_wait_ms: int = 6000
    input_mode: str = "foreground"

    def __post_init__(self):
        if self.input_mode not in ("foreground", "visible_background"):
            raise ValueError("未知自动滚动方式")
        for key, low, high in (("step_percent", 15, 45), ("settle_ms", 300, 2000),
                               ("sample_ms", 50, 500), ("end_wait_ms", 4000, 30000)):
            if not low <= getattr(self, key) <= high:
                raise ValueError(f"自动设置超出范围：{key}")


@dataclass
class AutoEvent:
    state: str
    reason: str = ""
    alignment: object = None


def visible_bottom(frame):
    result = detect_page_boundaries([(0, frame)])
    if not result.pages:
        return False
    bottom = result.pages[-1].bottom
    if not 5 <= len(frame)-bottom <= len(frame)*.7:
        return False
    # Fractional desktop origins can include a one-pixel viewport border.
    # Ignore only the outer two rows, retaining at least three background rows.
    tail = frame[bottom:-2].mean(axis=2)
    if len(tail) < 3:
        return False
    background = np.median(np.r_[tail[:, :8].ravel(), tail[:, -8:].ravel()])
    background_rows = np.mean(np.abs(tail-background) <= 5, axis=1) >= .98
    starts = np.flatnonzero(background_rows)
    if not len(starts):
        return False
    first = int(starts[0])
    return bool(background < 249 and first <= 3 and len(tail)-first >= 3
                and np.all(background_rows[first:]))


class AutoEngine:
    def __init__(self, store, driver, settings=None):
        self.store, self.driver = store, driver
        self.settings = settings or AutoSettings()
        self.units = 120
        self.retries = 0
        self.no_motion = 0
        self.commands = 0
        self.reset(0)

    def reset(self, now):
        self.stability = StableFrames()
        self.stable_since = None
        self.since = now
        self.mode = "verify"
        self.outstanding = False
        self.last_units = 0
        self.retries = self.no_motion = 0
        self.stalled_since = None
        self.motion_verified = False

    def command(self, units, now, mode):
        self.driver.scroll(units)
        self.commands += 1
        self.last_units = units
        self.mode, self.since = mode, now
        self.outstanding = True
        self.stability.reset()
        self.stable_since = None
        return AutoEvent("auto_rollback" if mode == "rollback" else "auto_waiting")

    def failure(self, reason, now):
        if self.mode == "forward" and self.retries < 2:
            self.retries += 1
            return self.command(-self.last_units, now, "rollback")
        return AutoEvent("needs_recovery", reason)

    def observe(self, frame, now):
        if self.commands >= 10000:
            return AutoEvent("needs_recovery", "达到自动滚动次数上限，请检查后继续")
        stable = self.stability.observe(frame)
        if stable:
            if self.stable_since is None:
                self.stable_since = now
        else:
            self.stable_since = None
        wait = max(1.2 if self.no_motion else 0, self.settings.settle_ms/1000)
        ready = (stable and self.stable_since is not None and now-self.stable_since >= .2
                 and now-self.since >= wait)
        if not ready:
            if now-self.since >= 4:
                return self.failure("页面持续变化，无法确认稳定画面", now)
            return AutoEvent("auto_waiting")
        if self.mode == "rollback" and self.store.previous is not None:
            proof = estimate_scroll_offset(self.store.previous, frame)
            if proof.accepted and proof.offset != 0:
                return AutoEvent("needs_recovery", "回滚未回到可信位置，请手动调整", proof)
        result = self.store.add(frame)
        if not result.accepted:
            # Allow delayed rendering to resolve before attempting rollback.
            if now-self.since < 2:
                return AutoEvent("auto_waiting", result.reason)
            return self.failure(result.reason, now)
        self.outstanding = False
        if self.mode == "rollback":
            # Reversing wheel input is only an attempt: require visual proof
            # of exactly the trusted position before retrying at a smaller step.
            if result.offset != 0:
                return AutoEvent("needs_recovery", "回滚未回到可信位置，请手动调整", result)
            self.units = max(30, self.units//2)
        elif self.mode == "forward":
            if result.offset < 0:
                return AutoEvent("needs_recovery", "滚动方向异常，请检查阅读器", result)
            if result.offset == 0:
                self.no_motion += 1
                if self.stalled_since is None:
                    self.stalled_since = now
                if self.no_motion >= 4 and now-self.stalled_since >= self.settings.end_wait_ms/1000:
                    if self.settings.input_mode == "visible_background" and not self.motion_verified:
                        return AutoEvent("needs_recovery", "未观察到定向滚动产生位移：阅读器可能不兼容；若文档仅一屏，请手动结束并预览", result)
                    if visible_bottom(frame):
                        return AutoEvent("auto_finished", "多次滚动未前进且可见页底，请检查末页", result)
                    return AutoEvent("needs_recovery", "连续滚动未前进，且无法确认页底；请检查焦点或加载状态", result)
            else:
                self.no_motion = self.retries = 0
                self.motion_verified = True
                self.stalled_since = None
                # Adapt to actual observed movement, retaining generous overlap.
                target = len(frame)*self.settings.step_percent/100
                self.units = max(30, min(240, round(self.units*target/result.offset)))
        self.last_alignment = result
        event = self.command(self.units, now, "forward")
        event.alignment = result
        return event
