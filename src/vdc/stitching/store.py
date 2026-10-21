"""Append-only PNG blocks with an atomically replaced session manifest."""
from dataclasses import asdict
import json
from pathlib import Path
import uuid
import numpy as np
from PIL import Image
from .alignment import Alignment, estimate_scroll_offset


class SegmentStore:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.directory / "session.json"
        self.state = {"version": 1, "height": 0, "width": 0, "position": 0,
                      "reference": None, "blocks": [], "last_alignment": None}
        if self.manifest_path.exists():
            self.state = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if self.state["version"] != 1:
                raise ValueError("Unsupported session version")
            for block in self.state["blocks"]:
                with Image.open(self.directory / block["file"]) as image:
                    image.load()
                    if image.size != (self.state["width"], block["height"]):
                        raise ValueError("Invalid stored block")
        ref = self.state["reference"]
        self.previous = self._load(ref) if ref else None

    def _load(self, filename):
        with Image.open(self.directory / filename) as image:
            return np.array(image.convert("RGB"))

    def _save_image(self, image, prefix):
        filename = f"{prefix}-{uuid.uuid4().hex}.png"
        Image.fromarray(image).save(self.directory / filename)
        return filename

    def add(self, frame: np.ndarray) -> Alignment:
        if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("Expected RGB uint8 frame")
        result = (Alignment("initial", 0, 1.0, "first_observation") if self.previous is None
                  else estimate_scroll_offset(self.previous, frame))
        if not result.accepted:
            return result  # Reference and position remain at last trusted frame.
        position = self.state["position"] + result.offset
        if position < 0:
            return Alignment("failed", None, 0, "before_session_start")
        state = dict(self.state)
        state["blocks"] = list(state["blocks"])
        h, w = frame.shape[:2]
        new_end = position + h
        if new_end > state["height"]:
            start = max(0, state["height"] - position)
            filename = self._save_image(frame[start:], "block")
            state["blocks"].append({"file": filename, "top": state["height"], "height": h - start})
            state["height"] = new_end
        old_ref = state["reference"]
        state.update(position=position, width=w, reference=self._save_image(frame, "reference"),
                     last_alignment=asdict(result))
        temporary = self.directory / "session.json.tmp"
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temporary.replace(self.manifest_path)
        self.state, self.previous = state, frame.copy()
        # Old reference is no longer needed after the manifest commit. Crash-created
        # orphan files are left intact for diagnosis; cleanup UI is Phase 1 work.
        if old_ref:
            try:
                (self.directory / old_ref).unlink(missing_ok=True)
            except OSError:
                pass
        return result

    def iter_blocks(self):
        for block in self.state["blocks"]:
            yield block["top"], self._load(block["file"])

    def read_rows(self, top: int, bottom: int) -> np.ndarray:
        if not 0 <= top < bottom <= self.state["height"]:
            raise ValueError("Requested rows outside captured segment")
        result = np.empty((bottom - top, self.state["width"], 3), dtype=np.uint8)
        for block in self.state["blocks"]:
            lo, hi = max(top, block["top"]), min(bottom, block["top"] + block["height"])
            if lo < hi:
                pixels = self._load(block["file"])
                result[lo - top:hi - top] = pixels[lo - block["top"]:hi - block["top"]]
        return result
