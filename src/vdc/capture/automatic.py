"""Automatic capture worker; all image and input work stays off the GUI thread."""
import json
import time
from datetime import datetime, timezone
from dataclasses import asdict
from PIL import Image
from PySide6.QtCore import QLockFile
from vdc.app.session import read_capture, write_json
from vdc.automation.engine import AutoEngine, AutoSettings
from vdc.automation.windows import WindowsWheel, InputInterrupted
from vdc.stitching.store import SegmentStore
from .worker import CaptureWorker


class AutomaticCaptureWorker(CaptureWorker):
    def __init__(self, *args, driver_factory=WindowsWheel, settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.driver_factory = driver_factory
        self.settings = settings or AutoSettings()

    def run(self):
        lock = QLockFile(str(self.directory / "capture.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            self.problem.emit("该会话正被另一个采集任务使用")
            return
        source = driver = engine = None
        metadata = {}
        last_snapshot = None

        def persist(state, **values):
            nonlocal last_snapshot
            metadata.update(state=state, mode="automatic", **values)
            snapshot = dict(metadata)
            if snapshot == last_snapshot:
                return
            write_json(self.directory / "capture.json", metadata)
            with (self.directory / "events.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), **snapshot},
                                     ensure_ascii=False)+"\n")
            last_snapshot = snapshot
            self.progress.emit(snapshot)

        owner = self
        class GuardedInput:
            def scroll(self, units):
                if owner.stop_event.is_set() or owner.pause_event.is_set() or owner.own_window_visible():
                    raise InputInterrupted("自动滚动已暂停")
                driver.scroll(units)

        try:
            metadata = read_capture(self.directory)
            store = SegmentStore(self.directory)
            persist("starting", notice="自动模式：请保持阅读器前台可见，Esc 或移出鼠标可暂停",
                    completion_reason="", auto_settings=asdict(self.settings))
            if self.stop_event.wait(self.delay):
                persist("cancelled" if self.cancelled else "reviewing")
                return
            source = self.source_factory(self.region)
            while not self.stop_event.is_set():
                if self.own_window_visible():
                    self.pause_event.set()
                if self.pause_event.is_set():
                    if metadata.get("state") != "needs_recovery":
                        persist("paused")
                    self.stop_event.wait(.05)
                    continue
                try:
                    if driver is None:
                        driver = self.driver_factory(self.region)
                        engine = AutoEngine(store, GuardedInput(), self.settings)
                        engine.reset(time.monotonic())
                    if self.reset_event.is_set():
                        # Keep the original window binding on resume.
                        driver.rearm()
                        engine.reset(time.monotonic())
                        self.reset_event.clear()
                    driver.check()
                    frame = source.grab()
                    driver.check()
                    if self.stop_event.is_set() or self.pause_event.is_set() or self.own_window_visible():
                        continue
                    event = engine.observe(frame, time.monotonic())
                    result = event.alignment
                    if result and result.accepted and result.status != "stationary":
                        metadata["accepted_frames"] = metadata.get("accepted_frames", 0)+1
                    persist(event.state, position=store.state["position"], height=store.state["height"],
                            quality=result.confidence if result else metadata.get("quality", 0),
                            needs_recovery=engine.outstanding or event.state == "needs_recovery",
                            error=event.reason if event.state == "needs_recovery" else "",
                            notice=event.reason or "自动滚动后正在验证画面；Esc 可暂停",
                            scroll_commands=engine.commands, wheel_units=engine.units)
                    if event.state == "auto_finished":
                        persist("reviewing", needs_recovery=False, completion_reason="visible_bottom_and_no_motion",
                                notice=event.reason)
                        return
                    if event.state == "needs_recovery":
                        self.pause_event.set()
                        diagnostics = self.directory / "diagnostics"
                        diagnostics.mkdir(exist_ok=True)
                        Image.fromarray(frame).save(diagnostics / "auto-failed.png")
                        if store.previous is not None:
                            Image.fromarray(store.previous).save(diagnostics / "reference.png")
                        write_json(diagnostics / "auto-failure.json", {"reason": event.reason,
                            "position": store.state["position"], "commands": engine.commands})
                        self.problem.emit(event.reason)
                except InputInterrupted as exc:
                    if self.stop_event.is_set():
                        break
                    self.pause_event.set()
                    persist("paused", needs_recovery=True, error=str(exc), notice=str(exc))
                    self.problem.emit(str(exc))
                self.stop_event.wait(self.settings.sample_ms/1000)
            persist("cancelled" if self.cancelled else "reviewing")
        except Exception as exc:
            try:
                persist("error", needs_recovery=True, error=str(exc))
            except OSError:
                pass
            self.problem.emit(f"自动采集失败：{exc}")
        finally:
            if source is not None:
                try:
                    source.close()
                except Exception:
                    pass
            lock.unlock()
