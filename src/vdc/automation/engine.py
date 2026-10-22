"""Bounded visual feedback loop. Wheel units are never treated as pixels."""
from dataclasses import dataclass
from pathlib import Path
import json
import uuid
import numpy as np
from PIL import Image
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


def texture_rows(frame):
    """Vertical evidence only: constant paper side edges do not locate a scroll."""
    gray = frame.astype(np.float32).mean(axis=2)
    return np.mean(np.abs(np.diff(gray, axis=0)), axis=1) >= .15


def cautious_step(frame, units):
    rows = texture_rows(frame)
    # The lower half will be the next overlap. Reduce input before its features
    # disappear, without treating wheel units as physical pixel displacement.
    if np.mean(rows[len(rows)//2:]) < .08:
        return max(30, min(60, units//2))
    return units


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
    BLANK_MAX_COMMANDS = 6
    BLANK_MAX_SECONDS = 12
    def __init__(self, store, driver, settings=None):
        self.store, self.driver = store, driver
        self.settings = settings or AutoSettings()
        self.units = 120
        self.retries = 0
        self.no_motion = 0
        self.commands = 0
        self.reset(0)

    def reset(self, now):
        self.finish_probe("interrupted")
        self.stability = StableFrames()
        self.stable_since = None
        self.since = now
        self.mode = "verify"
        self.outstanding = False
        self.last_units = 0
        self.retries = self.no_motion = 0
        self.stalled_since = None
        self.motion_verified = False
        self.probe_started = None
        self.probe_count = 0
        self.probe_directory = None
        self.probe_manifest = None

    def finish_probe(self, status="interrupted"):
        manifest = getattr(self, "probe_manifest", None)
        if manifest and manifest["status"] == "probing":
            manifest["status"] = status
            self.write_probe_manifest()

    def write_probe_manifest(self):
        temporary = self.probe_directory / "manifest.json.tmp"
        temporary.write_text(json.dumps(self.probe_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.probe_directory / "manifest.json")

    def save_probe(self, frame, now, status="probing"):
        if self.probe_directory is None:
            self.probe_directory = Path(self.store.directory) / "fragments" / uuid.uuid4().hex[:12]
            self.probe_directory.mkdir(parents=True)
            self.probe_manifest = {"status": status, "connected": False,
                "trusted_position": self.store.state["position"], "trusted_height": self.store.state["height"],
                "note": "独立探测截图，位置未知；未并入长图或 PDF。", "frames": []}
            if self.store.previous is not None:
                Image.fromarray(self.store.previous).save(self.probe_directory / "trusted-reference.png")
        manifest = self.probe_manifest
        filename = f"probe-{self.probe_count:02d}.png"
        if not any(item["file"] == filename for item in manifest["frames"]):
            Image.fromarray(frame).save(self.probe_directory / filename)
            manifest["frames"].append({"file": filename, "seconds": round(now-self.probe_started, 3),
                                       "probe_commands": self.probe_count})
        manifest["status"] = status
        self.write_probe_manifest()

    def probe_blank(self, frame, now):
        if self.probe_started is None:
            self.probe_started = now
        self.save_probe(frame, now)
        has_texture = np.mean(texture_rows(frame)) >= .08
        exhausted = (self.probe_count >= self.BLANK_MAX_COMMANDS
                     or now-self.probe_started >= self.BLANK_MAX_SECONDS)
        if has_texture or exhausted:
            self.save_probe(frame, now, "unresolved")
            reason = ("已重新出现内容，但无法与最后可信画面定位连接" if has_texture else
                      "空白探测已达次数或时间上限，仍无法确定位置")
            return AutoEvent("needs_recovery", reason + "；独立片段已保留（未并入 PDF）。请回到可信位置后继续，或结束并检查已有内容。")
        event = self.command(30, now, "blank_probe")
        self.probe_count += 1
        event.state = "auto_blank_probe"
        event.reason = f"正在探测空白区域（{self.probe_count}/{self.BLANK_MAX_COMMANDS}），位置尚待确认；截图独立保留"
        return event

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
        if self.mode == "blank_probe":
            self.finish_probe("unresolved")
            return AutoEvent("needs_recovery", reason + "；空白探测片段已保留，未并入 PDF")
        if self.mode == "forward" and self.retries < 2:
            self.retries += 1
            return self.command(-self.last_units, now, "rollback")
        return AutoEvent("needs_recovery", reason)

    def observe(self, frame, now):
        if self.mode == "blank_probe" and now-self.probe_started >= self.BLANK_MAX_SECONDS:
            self.save_probe(frame, now, "unresolved")
            return AutoEvent("needs_recovery", "空白探测超过 12 秒，已暂停；独立片段已保留，未并入 PDF")
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
            return AutoEvent("auto_blank_probe", "正在探测空白区域，等待画面稳定；位置尚待确认") if self.mode == "blank_probe" else AutoEvent("auto_waiting")
        if self.mode == "rollback" and self.store.previous is not None:
            proof = estimate_scroll_offset(self.store.previous, frame)
            if proof.accepted and proof.offset != 0:
                return AutoEvent("needs_recovery", "回滚未回到可信位置，请手动调整", proof)
        # Probe frames never become the reference unless direct overlap with the
        # last trusted image is independently verified. New content alone is not proof.
        if self.mode == "blank_probe":
            proof = estimate_scroll_offset(self.store.previous, frame)
            if not proof.accepted or proof.offset <= 0:
                return self.probe_blank(frame, now)
            self.save_probe(frame, now, "overlap_verified")
            self.mode = "forward"
            self.probe_started = None
            self.probe_directory = None
            self.probe_manifest = None
            self.probe_count = 0
        result = self.store.add(frame)
        if not result.accepted:
            # Allow delayed rendering to resolve before attempting rollback.
            if now-self.since < 2:
                return AutoEvent("auto_waiting", result.reason)
            if self.mode == "forward" and result.reason == "insufficient_texture" and np.mean(texture_rows(frame)) < .08:
                return self.probe_blank(frame, now)
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
        normal_units = self.units
        self.units = cautious_step(frame, self.units)
        event = self.command(self.units, now, "forward")
        if self.units < normal_units:
            event.reason = "下方可定位特征减少，已提前减小滚动步长"
        event.alignment = result
        return event
