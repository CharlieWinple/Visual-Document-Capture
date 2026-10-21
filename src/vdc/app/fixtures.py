"""Synthetic documents and reader screenshots. Ground truth stays in this module."""
from dataclasses import dataclass
from pathlib import Path
import json
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from vdc.export.pdf import export_multipage_pdf


@dataclass
class Fixture:
    canvas: np.ndarray
    pages: list[np.ndarray]
    bounds: list[tuple[int, int, int, int]]
    positions: list[int]
    viewport: int

    def frames(self):
        for top in self.positions:
            yield self.canvas[top:top + self.viewport].copy()


def make_fixture(seed=42, page_count=6, step=115, viewport=360) -> Fixture:
    rng = np.random.default_rng(seed)
    pages = []
    font = ImageFont.load_default(size=12)
    title = ImageFont.load_default(size=19)
    vocabulary = "visual document capture pixels boundary research method image evidence robust local sample overlap signal paper analysis".split()
    for index in range(page_count):
        w, h = [(440, 620), (500, 700), (560, 430)][index % 3]
        image = Image.new("RGB", (w, h), "white")
        draw = ImageDraw.Draw(image)
        draw.text((26, 18), f"VDC research sample {index + 1}", fill="#182e45", font=title)
        draw.line((26, 48, w - 26, 48), fill="#8a9bad", width=1)
        columns = 2 if index % 2 else 1
        cw = (w - 64) // columns
        for column in range(columns):
            x = 28 + column * (cw + 8)
            for y in range(65, h - 44, 18):
                # Deliberate internal blank areas must not become page breaks.
                if index % 3 == 1 and h // 3 < y < h // 3 + 125:
                    continue
                if index % 3 == 2 and 140 < y < 335:
                    continue
                words = []
                for word in rng.choice(vocabulary, size=10):
                    if draw.textlength(" ".join(words + [word]), font=font) > cw - 8:
                        break
                    words.append(word)
                draw.text((x, y), " ".join(words), fill="#26333d", font=font)
        if index % 3 == 1:
            draw.text((32, h // 3 + 35), "E = mc^2    f(x) = sum(a_n * x^n)", fill="#172f49", font=font)
        if index % 3 == 2:
            for x in range(32, 280, 62):
                draw.line((x, 158, x, 240), fill="#657c92")
            for y in range(158, 241, 27):
                draw.line((32, y, 280, y), fill="#657c92")
            draw.text((40, 165), "A         B         C", fill="#172f49", font=font)
            for k in range(6):
                bar = int(rng.integers(20, 115))
                draw.rectangle((325 + k * 29, 300 - bar, 343 + k * 29, 300), fill=(40, 100 + k * 15, 155))
            draw.text((325, 310), "Figure: observed signal", fill="#172f49", font=font)
        draw.text((w // 2 - 24, h - 25), f"Page {index + 1}", fill="#374c60", font=font)
        pages.append(np.array(image))
    gaps = [5, 12, 30, 18]
    total = 14 + sum(p.shape[0] for p in pages) + sum(gaps[i % 4] for i in range(page_count))
    canvas = np.full((total, 640, 3), 172, np.uint8)
    bounds, top = [], 14
    for i, page in enumerate(pages):
        h, w = page.shape[:2]
        left = (640 - w) // 2
        canvas[top:top + h, left:left + w] = page
        bounds.append((left, top, left + w, top + h))
        top += h + gaps[i % 4]
    positions = list(range(0, total - viewport + 1, step))
    if positions[-1] != total - viewport:
        positions.append(total - viewport)
    # Stop, reverse one step, revisit, then continue.
    positions[3:3] = [positions[2], positions[1], positions[2]]
    return Fixture(canvas, pages, bounds, positions, viewport)


def save_fixture(fixture: Fixture, directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    frames = directory / "frames"
    frames.mkdir(exist_ok=True)
    for i, frame in enumerate(fixture.frames()):
        Image.fromarray(frame).save(frames / f"{i:04d}.png")
    for i, page in enumerate(fixture.pages):
        Image.fromarray(page).save(directory / f"page-{i + 1:02d}.png")
    export_multipage_pdf(fixture.pages, directory / "source.pdf")
    (directory / "ground_truth.json").write_text(json.dumps({
        "bounds": fixture.bounds, "positions": fixture.positions,
        "viewport": fixture.viewport, "page_count": len(fixture.pages),
    }, indent=2), encoding="utf-8")
