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
            expected_top = 0
            for block in self.state["blocks"]:
                self._safe_filename(block["file"])
                if block["top"] != expected_top or block["height"] <= 0:
                    raise ValueError("Stored blocks are not contiguous")
                with Image.open(self.directory / block["file"]) as image:
                    image.load()
                    if image.size != (self.state["width"], block["height"]):
                        raise ValueError("Invalid stored block")
                expected_top += block["height"]
            if expected_top != self.state["height"]:
                raise ValueError("Stored height does not match block coverage")
        ref = self.state["reference"]
        self.previous = self._load(ref) if ref else None
        if self.previous is not None and (self.previous.shape[1] != self.state["width"]
                or not 0 <= self.state["position"] <= self.state["height"] - len(self.previous)):
            raise ValueError("Invalid reference frame or position")

    @staticmethod
    def _safe_filename(filename):
        if not isinstance(filename, str) or not filename or Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise ValueError("Invalid session image filename")

    def _load(self, filename):
        self._safe_filename(filename)
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
        return self._append(frame, result)

    def add_geometry(self, frame, evidence):
        """Persist an inferred position only after independently checking overlap."""
        delta = evidence["position"]-self.state["position"]
        if self.previous is None or frame.shape != self.previous.shape or frame.dtype != np.uint8:
            raise ValueError("尺寸辅助连接的图像无效")
        overlap = len(frame)-delta
        if not 0 < delta <= len(frame)-max(64, len(frame)//4):
            raise ValueError("尺寸辅助连接缺少足够重叠，不能补造未观察内容")
        error = np.abs(self.previous[delta:].astype(np.int16)-frame[:overlap].astype(np.int16))
        if np.mean(error) > 1 or np.mean(np.max(error, axis=2) > 8) > .001:
            raise ValueError("尺寸辅助连接的重叠像素不一致")
        return self._append(frame, Alignment("geometry_inferred", delta, .65, "page_height_requires_review"), evidence)

    def _append(self, frame, result, evidence=None):
        if self.previous is not None and result.offset == 0:
            return result  # Stationary observations do not rewrite PNGs or manifest.
        position = self.state["position"] + result.offset
        if position < 0:
            return Alignment("failed", None, 0, "before_session_start")
        state = dict(self.state)
        state["blocks"] = list(state["blocks"])
        if evidence is not None:
            state["geometry_joins"] = [*state.get("geometry_joins", []), evidence]
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
