"""Reproducible Phase 0 benchmark; never feeds truth into the algorithms."""
import argparse
from dataclasses import asdict
import importlib.metadata
import json
from pathlib import Path
import platform
import time
import threading
import numpy as np
from PIL import Image, ImageDraw
import pymupdf
from vdc.app.fixtures import make_fixture, save_fixture
from vdc.stitching.alignment import estimate_scroll_offset
from vdc.stitching.store import SegmentStore
from vdc.pagination.detector import detect_page_boundaries
from vdc.export.pdf import export_session


def evaluate(directory, seed, page_count, step, save_inputs=False):
    # This measures the WHOLE benchmark, including fixture creation and PDF
    # checking, not only the reconstruction engine. Sampling may miss spikes.
    try:
        import psutil
    except ImportError:
        result = _evaluate(directory, seed, page_count, step, save_inputs)
        result["sampled_process_peak_mib"] = None
        return result
    process = psutil.Process()
    samples = [process.memory_info().rss]
    stop = threading.Event()

    def sample():
        while not stop.wait(0.01):
            samples.append(process.memory_info().rss)

    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    try:
        result = _evaluate(directory, seed, page_count, step, save_inputs)
    finally:
        stop.set()
        monitor.join()
    result["sampled_process_peak_mib"] = round(max(samples) / 1024**2, 2)
    return result


def _evaluate(directory, seed, page_count, step, save_inputs=False):
    fixture = make_fixture(seed=seed, page_count=page_count, step=step)
    if save_inputs:
        save_fixture(fixture, directory / "input")
    store = SegmentStore(directory / "session")
    started = time.perf_counter()
    events, errors = [], []
    rejected = 0
    last_trusted_truth = 0
    for index, (position, frame) in enumerate(zip(fixture.positions, fixture.frames(), strict=True)):
        expected = position - last_trusted_truth
        result = store.add(frame)
        events.append({"frame": index, "expected_offset": expected, **asdict(result)})
        if result.accepted:
            errors.append(abs(result.offset - expected))
            last_trusted_truth = position
        else:
            rejected += 1
            break  # In production, user must recover; never bridge a failed gap.
    pagination = detect_page_boundaries(store.iter_blocks())
    actual_bounds = [(p.left, p.top, p.right, p.bottom) for p in pagination.pages]
    matches = sum(bound in fixture.bounds for bound in actual_bounds)
    mismatched_pixels = 0
    for top, pixels in store.iter_blocks():
        expected_pixels = fixture.canvas[top:top + len(pixels)]
        if pixels.shape != expected_pixels.shape:
            mismatched_pixels += int(pixels.shape[0] * pixels.shape[1])
        else:
            mismatched_pixels += int(np.count_nonzero(np.any(pixels != expected_pixels, axis=2)))
    pdf_count, pdf_exact = 0, False
    if not rejected and not pagination.warnings:
        pdf_count = export_session(store, pagination, directory / "reconstructed.pdf")
        pdf_exact = True
        with pymupdf.open(directory / "reconstructed.pdf") as document:
            if len(document) != len(fixture.pages):
                pdf_exact = False
            else:
                for page, expected in zip(document, fixture.pages, strict=True):
                    pixmap = pymupdf.Pixmap(document, page.get_images()[0][0])
                    pixels = np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
                    pdf_exact &= np.array_equal(pixels, expected)
    metrics = {
        "seed": seed, "step": step, "expected_pages": page_count,
        "frames_checked": len(events), "accepted": len(errors), "false_rejections": rejected,
        "incorrect_acceptances": sum(error != 0 for error in errors),
        "max_offset_error_px": max(errors, default=None),
        "missing_rows": max(0, len(fixture.canvas) - store.state["height"]),
        "extra_rows": max(0, store.state["height"] - len(fixture.canvas)),
        "mismatched_pixels": mismatched_pixels,
        "detected_pages": len(actual_bounds), "missed_pages": page_count - matches,
        "false_pages": len(actual_bounds) - matches,
        "page_precision": matches / len(actual_bounds) if actual_bounds else 0,
        "page_recall": matches / page_count,
        "complete_pages": sum(p.complete for p in pagination.pages),
        "pdf_page_count": pdf_count, "pdf_pixels_and_order_exact": bool(pdf_exact),
        "seconds": round(time.perf_counter() - started, 3),
        "warnings": pagination.warnings,
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "alignment.json").write_text(json.dumps(events, indent=2), encoding="utf-8")
    (directory / "pagination.json").write_text(json.dumps(asdict(pagination), indent=2), encoding="utf-8")
    if save_inputs:
        preview = Image.new("RGB", (900, 600), "#dce3eb")
        draw = ImageDraw.Draw(preview)
        for i, page in enumerate(fixture.pages[:6]):
            thumb = Image.fromarray(page)
            thumb.thumbnail((270, 260))
            x, y = (i % 3) * 300 + 15, (i // 3) * 300 + 30
            preview.paste(thumb, (x, y))
            draw.text((x, y - 20), f"Page {i+1} - {page.shape[1]} x {page.shape[0]} px", fill="#19344e")
        preview.save(directory / "preview.png")
    return metrics


def failure_cases():
    fixture = make_fixture()
    first = fixture.canvas[:360]
    rng = np.random.default_rng(7)
    repeated = np.tile(rng.integers(0, 255, (24, 640, 3), dtype=np.uint8), (15, 1, 1))
    blank = np.full_like(first, 255)
    import cv2
    pairs = {
        "no_overlap": (first, fixture.canvas[1300:1660]),
        "blank_ambiguous_motion": (blank, blank),
        "periodic_rows": (repeated, np.roll(repeated, 24, axis=0)),
        "zoom_changed": (first, cv2.resize(first, None, fx=1.1, fy=1.1)[:360, :640]),
        "viewport_changed": (first, first[:300]),
    }
    results = [{"case": name, "expected": "reject", "correct": not (result := estimate_scroll_offset(a, b)).accepted,
                **asdict(result)} for name, (a, b) in pairs.items()]
    unknown = detect_page_boundaries([(0, blank)])
    results.append({"case": "white_reader_background", "expected": "warn_no_pages",
                    "correct": not unknown.pages and bool(unknown.warnings), "reason": "; ".join(unknown.warnings)})
    partial = detect_page_boundaries([(0, fixture.canvas[150:-150])])
    results.append({"case": "partial_first_last_pages", "expected": "mark_incomplete",
                    "correct": bool(partial.pages) and not partial.pages[0].complete and not partial.pages[-1].complete,
                    "reason": "; ".join(partial.warnings)})
    return results


def main():
    parser = argparse.ArgumentParser(description="Run local pixel-only reconstruction benchmark")
    parser.add_argument("--output", type=Path, default=Path("output/phase0"))
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Output directory is not empty; select a new --output path")
    args.output.mkdir(parents=True, exist_ok=True)
    metrics = []
    for name, seed, pages, step in [("sample", 42, 6, 115), ("fast", 17, 6, 205), ("twenty_pages", 91, 20, 149)]:
        print(f"Validating {name}...", flush=True)
        metrics.append({"name": name, **evaluate(args.output / name, seed, pages, step, name == "sample")})
    failures = failure_cases()
    results = {"python": platform.python_version(), "platform": platform.platform(),
               "dependencies": {name: importlib.metadata.version(name) for name in
                                ["numpy", "opencv-python-headless", "PyMuPDF", "Pillow"]},
               "sequences": metrics, "boundary_cases": failures}
    (args.output / "metrics.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    lines = ["# Phase 0 自动验证结果", "", "合成像素测试；不是 Chrome/Edge 或 GUI 验收。", "",
             "|场景|帧数|拒绝|错误接受|位移最大误差|缺失行|多余行|错误像素|分页精确率/召回率|PDF 页数|耗时秒|",
             "|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|"]
    for m in metrics:
        lines.append(f"|{m['name']}|{m['frames_checked']}|{m['false_rejections']}|{m['incorrect_acceptances']}|{m['max_offset_error_px']}|{m['missing_rows']}|{m['extra_rows']}|{m['mismatched_pixels']}|{m['page_precision']:.3f}/{m['page_recall']:.3f}|{m['pdf_page_count']}|{m['seconds']}|")
    lines += ["", "## 边界案例", "", "|案例|按预期报警/标记|原因|", "|---|---|---|"]
    lines += [f"|{r['case']}|{r['correct']}|{r['reason']}|" for r in failures]
    lines += ["", "完整事件、版本和逐页矩形见 metrics.json、alignment.json、pagination.json。",
              "逐像素对比检查丢失/重复/裁切；仅对这些已知样本有效。未测试真实屏幕渲染与 DPI。",
              "metrics.json 中进程峰值内存按 10 ms 采样，包含夹具整图及 PDF 验证开销，可能漏掉短暂尖峰，不代表真实采集内存。",
              "检测器运行时只读取截图；源 PDF 仅用于生成与人工对照。"]
    (args.output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))
    success = all(m["false_rejections"] == m["incorrect_acceptances"] == m["missing_rows"] == m["extra_rows"] == m["mismatched_pixels"] == m["missed_pages"] == m["false_pages"] == 0 and m["pdf_pixels_and_order_exact"] for m in metrics)
    raise SystemExit(0 if success and all(r["correct"] for r in failures) else 1)


if __name__ == "__main__":
    main()
