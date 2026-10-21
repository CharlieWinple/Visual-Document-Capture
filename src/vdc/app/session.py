"""Persist capture metadata and review edits independently of GUI widgets."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import time
from pathlib import Path
from vdc.pagination.detector import PageBoundary, PaginationResult


def write_json(path: Path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows readers/antivirus can momentarily hold a non-delete-sharing handle.
    # Keep atomic replacement, retry briefly, then surface the real write error.
    for attempt in range(6):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.02 * (attempt + 1))


def read_capture(directory):
    path = Path(directory) / "capture.json"
    for attempt in range(6):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.02 * (attempt + 1))


def new_capture(directory, region):
    state = {"version": 1, "created": datetime.now(timezone.utc).isoformat(),
             "region": region.as_dict(), "state": "ready", "needs_recovery": False,
             "error": "", "accepted_frames": 0}
    write_json(Path(directory) / "capture.json", state)
    return state


class Review:
    def __init__(self, pages, warnings, width, height):
        self.pages = list(pages)
        self.warnings = list(warnings)
        self.width, self.height = width, height
        for page in self.pages:
            self.validate(page)

    def validate(self, page):
        if not (0 <= page.left < page.right <= self.width and 0 <= page.top < page.bottom <= self.height):
            raise ValueError("页面裁切边界超出已采集内容，或页面尺寸为零")

    def update(self, index, left, top, right, bottom):
        old = self.pages[index]
        page = replace(old, left=left, top=top, right=right, bottom=bottom,
                       confidence=min(old.confidence, 0.7), complete=False)
        self.validate(page)
        self.pages[index] = page

    def split(self, index, at):
        page = self.pages[index]
        if not page.top < at < page.bottom:
            raise ValueError("拆分位置必须位于页面内部")
        self.pages[index:index + 1] = [replace(page, bottom=at, complete=False, confidence=0.5),
                                     replace(page, top=at, complete=False, confidence=0.5)]
        self.renumber()

    def merge(self, index):
        if index + 1 >= len(self.pages):
            raise ValueError("没有下一页可合并")
        a, b = self.pages[index:index + 2]
        self.pages[index:index + 2] = [PageBoundary(a.page_id, min(a.left, b.left), min(a.top, b.top),
            max(a.right, b.right), max(a.bottom, b.bottom), 0.5, False)]
        self.renumber()

    def delete(self, index):
        del self.pages[index]
        self.renumber()

    def renumber(self):
        self.pages = [replace(p, page_id=i + 1) for i, p in enumerate(self.pages)]

    def save(self, directory):
        write_json(Path(directory) / "review.json", {"version": 1, "width": self.width,
                   "height": self.height, "pages": [asdict(p) for p in self.pages], "warnings": self.warnings})

    @classmethod
    def load(cls, directory, width, height):
        path = Path(directory) / "review.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or data["width"] != width or data["height"] != height:
            return None
        return cls([PageBoundary(**p) for p in data["pages"]], data["warnings"], width, height)

    def pagination(self):
        return PaginationResult(self.pages, self.warnings)

    def export_warnings(self, capture):
        warnings = list(self.warnings)
        if capture.get("needs_recovery"):
            warnings.append("采集曾中断且尚未恢复，只能导出已确认的前段内容")
        if any(not p.complete or p.confidence < 0.8 for p in self.pages):
            warnings.append("存在不完整、人工修改或低置信度页面，请逐页检查")
        ordered = sorted(self.pages, key=lambda p: p.top)
        if any(a.bottom > b.top for a, b in zip(ordered, ordered[1:])):
            warnings.append("页面范围存在重叠，可能重复导出正文")
        return list(dict.fromkeys(warnings))
