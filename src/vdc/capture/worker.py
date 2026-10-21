"""Screen I/O and stitching live exclusively on this worker thread."""
from pathlib import Path
import threading
import time
import sys
import json
from datetime import datetime, timezone
import numpy as np
from PySide6.QtCore import QThread, Signal, QLockFile
from vdc.app.session import read_capture, write_json
from vdc.capture.source import ScreenSource
from vdc.capture.stability import StableFrames
from vdc.stitching.store import SegmentStore
from vdc.stitching.alignment import Alignment


class CaptureWorker(QThread):
    progress = Signal(object)
    problem = Signal(str)

    def __init__(self, directory, region, source_factory=ScreenSource, delay=1.2, parent=None, blocked_window=None):
        super().__init__(parent)
        self.directory, self.region = Path(directory), region
        self.source_factory, self.delay = source_factory, delay
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.reset_event = threading.Event()
        self.cancelled = False
        self.blocked_window = blocked_window

    def own_window_visible(self):
        if sys.platform != "win32" or self.blocked_window is None:
            return False
        import ctypes
        user32 = ctypes.windll.user32
        handle = ctypes.c_void_p(self.blocked_window)
        return bool(user32.IsWindowVisible(handle) and not user32.IsIconic(handle))

    def pause(self):
        self.pause_event.set()

    def resume(self):
        self.reset_event.set()
        self.pause_event.clear()

    def stop(self, cancelled=False):
        self.cancelled = cancelled
        self.stop_event.set()

    def run(self):
        lock = QLockFile(str(self.directory / "capture.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            self.problem.emit("该会话正被另一个采集任务使用，请先停止原任务。")
            return
        source = None
        metadata = {}
        stable = StableFrames()

        def persist(state=None, **extra):
            if state:
                metadata["state"] = state
            metadata.update(extra)
            write_json(self.directory / "capture.json", metadata)
            with (self.directory / "events.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                    **{key: metadata.get(key) for key in ("state", "position", "height", "quality", "error")}},
                    ensure_ascii=False) + "\n")
            self.progress.emit(dict(metadata))

        try:
            metadata = read_capture(self.directory)
            store = SegmentStore(self.directory)
            persist("starting")
            if self.stop_event.wait(self.delay):
                persist("cancelled" if self.cancelled else "reviewing")
                return
            source = self.source_factory(self.region)
            persist("capturing")
            last_notice = time.monotonic()
            while not self.stop_event.is_set():
                if self.own_window_visible():
                    self.pause_event.set()
                if self.pause_event.is_set():
                    if metadata["state"] not in ("paused", "needs_recovery"):
                        persist("paused")
                    self.stop_event.wait(0.10)
                    continue
                if self.reset_event.is_set():
                    stable.reset()
                    self.reset_event.clear()
                    persist("capturing")
                frame = source.grab()
                if self.stop_event.is_set() or self.pause_event.is_set() or self.own_window_visible():
                    continue
                if not stable.observe(frame):
                    if time.monotonic() - last_notice > 2:
                        self.progress.emit({**metadata, "notice": "等待画面稳定，请每次滚动后稍作停顿"})
                        last_notice = time.monotonic()
                    self.stop_event.wait(0.12)
                    continue
                if store.previous is not None and np.array_equal(frame, store.previous):
                    result = Alignment("stationary", 0, 1.0, "unchanged_observation")
                else:
                    result = store.add(frame)
                if not result.accepted:
                    self.pause_event.set()
                    persist("needs_recovery", needs_recovery=True, error=result.reason)
                    self.problem.emit(result.reason)
                    continue
                changed = result.status not in ("stationary",)
                if changed or metadata.get("needs_recovery"):
                    persist("capturing", needs_recovery=False, error="", height=store.state["height"],
                            position=store.state["position"], quality=result.confidence,
                            accepted_frames=metadata.get("accepted_frames", 0) + int(changed))
                self.stop_event.wait(0.20 if result.status == "stationary" else 0.10)
            persist("cancelled" if self.cancelled else "reviewing")
        except Exception as exc:
            try:
                persist("error", needs_recovery=True, error=str(exc))
            except OSError:
                pass  # Never replace the original disk/capture failure with a logging failure.
            self.problem.emit(f"采集失败：{exc}")
        finally:
            if source is not None:
                try:
                    source.close()
                except Exception:
                    pass
            lock.unlock()
