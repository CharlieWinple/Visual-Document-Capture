# Visual Document Capture

[简体中文](README.md) | **English**

A local, visual-first document capture project for Windows 11. It reconstructs continuous content and original pagination from visible pixels in a user-selected region, without reading the DOM, browser internals, PDF.js, or original network resources.

**Currently an experimental release with page-height-assisted joining (0.4.2).** The workflow supports region selection → manual or automatic scrolling capture → pagination preview and correction → multipage PDF export, including crop adjustments, page boundaries, visual calibration, and undo. Features include directed scrolling on a visible secondary display, early step reduction, bounded blank-area probing, and separately retained screenshots. Page-height-assisted joining requires actual pixel overlap; inferred positions must be reviewed.

## Installation and launch

On Windows 11 x64, download the portable ZIP from the [v0.4.2 prerelease](https://github.com/CharlieWinple/Visual-Document-Capture/releases/tag/v0.4.2). Extract the entire archive and double-click `VisualDocumentCapture.exe`; Python is not required. Keep the `_internal` folder. Sessions and logs are saved in `sessions` and `logs` inside the extracted directory. The application is unsigned and has not completed acceptance testing on clean machines or across all readers. To run from source, follow the steps below.

Python 3.12+ is required. Development uses Python 3.13. Run these commands in Windows PowerShell from the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[gui,dev]"
.\.venv\Scripts\python.exe -m vdc.app.main
```

After installing dependencies, you can also double-click **`Start-VDC.cmd`** in the project root or run `.venv\Scripts\vdc.exe`. Use the Python command above to see terminal output when troubleshooting launch errors.

## First capture

The interface labels below include the Chinese text shown in the application.

1. Click “Open demo reader” (「打开演示阅读器」) to display a six-page synthetic document, or open your own document in Chrome/Edge.
2. Click “New · Select region” (「新建 · 框选区域」) and drag on any one monitor. Include the gray background on both sides of the paper and the gaps between pages; exclude toolbars, scrollbars, and overlays. Drag the edges to adjust, press Enter to confirm, or Esc to cancel. A selection cannot span monitors.
3. Position the reader at the top of the first page and click “Start capture” (「开始采集」). Wait about one second after VDC minimizes and keep the reader visible.
4. Scroll slowly with the mouse, or try steady middle-button autoscrolling; stopping after each scroll is unnecessary. Adjacent screenshots need sufficient overlap. Slow down if “Position awaiting confirmation” (「位置暂待确认」) appears. Keep the window position and page zoom unchanged. Browser middle-button compatibility still needs hands-on verification.
5. Click VDC on the taskbar to return to its window; capture pauses automatically. You can also use the system tray menu. Click “Finish and preview” (「结束并预览」) to detect page boundaries.
6. Select a thumbnail on the left to inspect a page. You can adjust its crop, split it, merge it with the next page, or delete it. Exact coordinates are collapsed by default; expand “Precise adjustments” (「精确调整」) to view them. Changes save automatically. Use the buttons below the preview to zoom in.
7. Click “Export multipage PDF” (「导出多页 PDF」) and choose a path. If there are incomplete or low-confidence pages, or capture interruption warnings, inspect each page and check the acknowledgment box before confirming export.

A brief matching failure first enters “Position awaiting confirmation,” retains the last trusted position, and continues sampling. Capture pauses if verification still fails after about 1.5 seconds or 24 observations. Return near the last successfully captured position, resume, and move forward slowly. If recovery is impossible, finish and explicitly confirm exporting only the captured portion, or start a new session. Unknown pagination is shown as a **content segment requiring correction**, without claiming that original pagination has been recovered. When matching failure causes a pause, the session's `diagnostics/` directory retains the reference frame and the first and last failed frames for local troubleshooting.

For an older session, click “Detect page boundaries again” (「重新识别分页」) to apply the new algorithm; this replaces existing pagination edits. Automatic detection learns page height and spacing from at least three complete pages of the same size. Blank areas inside a page are not directly treated as page gaps. If needed, use “Visual calibration” (「可视化标定」): mark a complete page with the blue rectangle and the top of the next page with the orange line to calculate the gap automatically. Inferred page edges remain unverified and require page-by-page review before export. Do not force a uniform page height on mixed-size documents.

## Direct manipulation editing

- **Adjust crop** (「调整裁切」): select a page and click the button below the preview. Drag the blue rectangle's edges or corners. Original content remains visible outside it, allowing you to restore incorrectly cropped areas. Click “Confirm and apply” (「确认应用」) to save; canceling leaves the session unchanged.
- **Split page** (「拆分页面」): click to place a horizontal boundary, drag to refine it, and confirm to split the page in two.
- **Visual calibration** (「可视化标定」): enclose a complete page in the blue rectangle, then drag the orange “Top of next page” (「下一页顶边」) line. The interface calculates the gap without requiring you to guess pixel values. Confirmation prompts you to replace pagination.
- The editor includes original-pixel rulers, pointer coordinates, and synchronized numeric fields. Use the wheel to zoom and drag away from boundaries to pan; in pagination mode, scrollbars also support panning. After selecting a boundary, use arrow keys for 1 px adjustments or Shift+arrow for 10 px adjustments.
- “Expand original content” (「展开更多原始内容」) loads more distant neighboring regions on demand while preserving current adjustments. Expanded context is limited to 12,000 rows to avoid loading the entire document at once.
- “Undo adjustments” (「撤销调整」), or Ctrl+Z inside the editor, undoes changes that have not yet been applied. The main window's “Undo applied changes” (「撤销已应用修改」) restores up to the last 20 edits made during the current session opening, including cropping, splitting, merging, deletion, and calibration. Undo history does not survive a restart and is cleared when switching sessions, detecting boundaries again, or resuming capture.

Editing and undo change only `review.json`; original image blocks remain intact. “Precise adjustments” in the main window is collapsed by default. Expand it to enter left/right X boundaries and top/bottom Y boundaries and see the pending dimensions immediately. Coordinates are relative to the top-left corner of the entire stitched image, not pixel amounts removed from each edge. Right and bottom boundaries are exclusive. If draggable lines are too close together, zoom in first. See `docs/VISUAL_EDITING_REPORT.md` for the stage report.

Sessions save automatically in `sessions/date-time-id/`, including PNG blocks, `session.json`, `capture.json`, `events.jsonl`, and the edited `review.json`. Use “Open existing session” (「打开已有会话」) to preview, export, or continue. Before continuing capture, restore the same monitor layout, selection, and zoom, and return near the last trusted position. Continuing capture detects page boundaries again and backs up previous edits to `review.previous.json`. Stopping or exiting does not delete captured results.

You can open an existing session directly:

```powershell
.\.venv\Scripts\python.exe -m vdc.app.main --session output/desktop-primary-20/session
```

## Automatic scrolling capture

### Large blank areas (new)

**Page-height-assisted joining:** during blank-area probing, the application learns from at least three previously captured complete pages of the same size. It requires an observed top edge of the current page, followed by an observed bottom edge and background in a screenshot. The current frame is joined only if page width is consistent, the inferred displacement preserves at least 25% actual overlap (and at least 64 rows), and the overlapping pixels pass verification. This is an inference based on size assumptions, not textured visual positioning. Affected pages show “Page-height join · Review required” (「页高连接 · 待检查」) and require acknowledgment before export.

The application does not invent blank content or bridge gaps without screenshot coverage. Mixed page heights, fewer than three sample pages, unclear paper edges, or a joining distance beyond the overlap range still cause a pause under the existing rules. The number of consecutive blank pages cannot be reliably inferred. Run `.\.venv\Scripts\python.exe examples/blank_reader.py --height-model` to open a four-page, same-size sample, then select the region normally. See `docs/HEIGHT_CONNECTION_REPORT.md` for details.

When positioning features become scarce in the lower half of the view, automatic capture reduces the input step in advance. If scrolling produces a low-texture view that cannot be matched, capture first waits for rendering, then probes the blank area with at most six additional small scroll inputs over a maximum of 12 seconds. Input counts do not represent pixel distances and are not used to insert blank content or infer page counts.

Stitching continues only after successful direct overlap matching or a join satisfying the page-height conditions above. Capture pauses if content reappears but cannot be connected, or if the probing limit is reached. During probing, an unchanged view is not treated as evidence that the document has ended.

Click “View retained fragments” (「查看保留片段」) to open the local `sessions/session/fragments/id/` directory containing the trusted reference image, probe PNGs, and `manifest.json`. Probe screenshots are not automatically added to the PDF as a batch. The final frame that satisfies the joining conditions is added to the stitched image, with inference evidence recorded in the manifest; other screenshots remain separate. Return to the last trusted position to continue capture, or finish and explicitly confirm exporting only existing content. Each probe uses a separate directory, preserved when reopening the session. Batch joining of historical standalone fragments is deferred to a future release.

An optional synthetic blank-area reader is available. Once open, select its content normally, excluding window borders:

```powershell
.\.venv\Scripts\python.exe examples/blank_reader.py --blank-height 900
```

This example demonstrates step reduction, probing, and pausing. Do not expect this version to automatically traverse the entire 900 px blank area. See `docs/BLANK_PROBE_REPORT.md` for the verification scope.

### Visible secondary-display mode (experimental)

1. Place the reader on a secondary monitor, keep it visible, and scroll to the document's beginning. Select the content region, including background at the paper edges and page gaps, while excluding fixed borders, toolbars, and scrollbars.
2. Enable “Auto scroll” (「自动滚动」). In “Auto settings” (「自动设置」), choose “Visible secondary display (without taking the mouse or focus, experimental)” (「副屏可见（不占用鼠标和焦点，试验版）」). Existing sessions still default to foreground mode.
3. After starting automatic capture, you can type, click, scroll, or switch applications on the primary monitor. Capture does not move the pointer or activate the reader.
4. Pause with **Ctrl+Shift+F8** or the tray menu. In this mode, Esc, moving the pointer away, and switching windows do not pause capture. Restore the reader's visibility and position before resuming.
5. Completion and problems produce tray notifications without opening the main window or taking focus. Click “Show window” (「显示窗口」) in the tray menu to inspect results, finish capture, or export a PDF.

The reader must not be minimized, moved, resized, or covered by another window. Capture pauses when these conditions are detected. Occlusion detection uses sampling and cannot detect every small overlay or reader-internal overlay. Readers that do not support directed wheel input cause a pause; the application does not fall back to global mouse input. Capture also pauses if scrolling is never detected initially, even if a paper bottom is visible. If all content fits on one screen, manually choose “Finish and preview.” A shortcut conflict prompts a pause.

The local synthetic reader passed six-page and 20-page tests on a secondary display at 200% scaling. During the 20-page capture, all 1,986 focus/pointer-position checks passed. **This does not establish compatibility with Chrome/Edge, PDF readers, or all websites.** Some applications may ignore messages or read modifier-key state from activity on the primary display. If scrolling fails or causes zooming, pause and switch to foreground mode. See `docs/VISIBLE_BACKGROUND_REPORT.md`.

### Foreground mode

After selecting a region, scroll the reader to the beginning, enable “Auto scroll,” and click “Start automatic capture” (「开始自动采集」). There is a 2.5-second preparation period after the main window minimizes. Keep the reader in the foreground with the selected region visible. The application moves the pointer into the reading area, scrolls, and verifies the view; manual scrolling is unnecessary. After repeated observations without displacement, at least six seconds by default, and a visible paper bottom edge, capture ends automatically and opens the pagination preview. Review the results and export the PDF manually.

“Auto settings” provides target step size (15%–45% of viewport height; default 30%), minimum wait per step (default 400 ms), screenshot observation interval (default 60 ms), and end-confirmation duration (default 6,000 ms). Step size adapts using visually measured displacement. Wheel units are not pixels; actual speed depends on reader behavior and processing time.

Esc, moving the pointer outside the region, switching to another window, or restoring VDC pauses capture. Keep the same reader and zoom, return near the last trusted position if necessary, and resume. Reselect the region after changing window position or size. Foreground mode uses the mouse; try the visible secondary-display mode above if you need to use other applications simultaneously.

On matching failure, capture first waits briefly for rendering, then attempts up to two rounds of reverse scrolling and retries with smaller steps. It continues only after visually confirming a return to a trusted position; otherwise, it preserves captured content and pauses. An unchanged view without a visible page bottom also causes a pause, rather than successful completion. Network loading longer than the confirmation period may still look like the end of the document, so check the final page. Increase the end-confirmation duration for slow-loading documents and resume capture if necessary.

Automatic settings and the completion reason are saved in `capture.json`; scroll steps are recorded locally in `events.jsonl`. Failure images are saved in the session's `diagnostics/`. Manual capture remains available, and older sessions can still be opened.

## Automated validation commands

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\vdc-validate.exe --output output/phase0
```

Activating the virtual environment is unnecessary. For repeat runs, specify a new output directory, such as `output/phase0-run2`; the validator refuses to overwrite a nonempty directory. All processing runs locally.

To reproduce the tested dependency versions, add `-c requirements-tested.txt` during installation. The Phase 0 offline pixel regression covering 32 pages still passes. See `docs/PHASE1_REPORT.md` for Phase 1 tests and real-screen capture results.

Validator outputs:

- `REPORT.md` / `metrics.json`: quantitative results, edge cases, and dependency versions.
- `sample/input/source.pdf`: test PDF generated from known original page images.
- `sample/input/frames/` / `ground_truth.json`: screenshot sequence and ground-truth positions.
- `sample/reconstructed.pdf`: multipage PDF generated solely through screenshot stitching and pagination.
- `sample/session/`: disk image blocks and session manifest that can be reopened.
- `sample/alignment.json` / `pagination.json`: displacement and pagination diagnostics.
- `fast/` / `twenty_pages/`: results for faster scrolling and the 20-page synthetic sample.

Run the installed `vdc-validate --help` for options. Linux/macOS can use `.venv/bin/python` and `.venv/bin/vdc-validate` to validate the algorithms, but Windows 11 remains the target desktop platform.

## Algorithm API

```python
from vdc.stitching.store import SegmentStore
from vdc.pagination.detector import detect_page_boundaries
from vdc.export.pdf import export_session

store = SegmentStore("sessions/demo")
for rgb_frame in frames:  # RGB uint8; keep dimensions unchanged
    alignment = store.add(rgb_frame)
    if not alignment.accepted:
        print(alignment.reason)  # Stop; return to the last trusted position
        break
else:
    pagination = detect_page_boundaries(store.iter_blocks())
    export_session(store, pagination, "output/document.pdf")
```

The capture thread uses a session lock to prevent concurrent writes. Each trusted addition is saved as a PNG block before the manifest is atomically replaced. On restart, the last trusted reference frame is reloaded. Failed frames do not update the position. Prepending content before the first frame is not implemented; scrolling backward beyond the session's starting point produces a warning.

## Current limitations

- Assumes vertical integer displacement, stable rendering, and at least 25% viewport overlap. Matching is not guaranteed with browser subpixel scrolling, mouse cursors, fixed toolbars, overlays, or dynamic loading.
- Pagination currently requires near-white paper, a distinguishable uniform background, and visible left/right paper edges; light-gray backgrounds are supported. A pure-white background or cropped paper edges prevent automatic pagination confirmation. Incomplete first/last pages are flagged and block default export. A completely textureless blank viewport still provides no scroll-distance evidence; the page-height model cannot recover missing captured content.
- Pagination confidence is a heuristic score, not a probability of success. Manual correction is supported, but automatic pagination runs only after capture ends and does not yet integrate paper-edge evidence across frames. Page counts are not displayed live during capture.
- Repeated content can be visually indistinguishable. Existing repeated-texture detection cannot guarantee detection of skipped identical pages.
- Real-screen pixel capture has been tested with the local synthetic reader: 20 pages on a primary display at 150% scaling and six pages on a secondary display with negative coordinates at 200% scaling. Chrome/Edge and complex website interfaces have not completed compatibility acceptance testing; synthetic-reader results are not browser guarantees.
- Disk-backed blocks avoid keeping the entire document image in memory, but PDF library memory usage, linear growth in pagination row geometry, and long-session resource limits have not completed full performance acceptance testing.
- Output is an image PDF preserving image proportions and pixels. The default 96 DPI is only an output-size convention. Original PDF physical dimensions, vector content, and text layers cannot be inferred.

## Further acceptance testing

For your first real-document capture, follow the desktop workflow above and start with a short document. See `docs/WINDOWS_ACCEPTANCE.md` for the complete 20-page Chrome/Edge acceptance procedure. Export currently supports lossless mode only. Configurable compression, supplementary capture of fragments across sessions, automatic cleanup, and full performance acceptance testing are deferred.

See `DEVELOPMENT_PLAN.md` for the engineering design, `docs/PHASE2_REPORT.md` for foreground automatic scrolling tests, and `docs/PHASE1_1_REPORT.md` for the previous algorithm's tests. Capture of occluded windows and Phase 4 packaging remain future plans.

The project does not upload screenshots or bypass access controls. GitHub synchronization requires the project owner's confirmation. Screenshots, sessions, generated PDFs, and virtual environments are excluded from Git.

## Technical references

- [OpenCV template matching](https://docs.opencv.org/4.x/d4/dc6/tutorial_py_template_matching.html): used to search for candidate displacements, with additional overlap verification and ambiguity rejection in this project.
- [PyMuPDF Page API](https://pymupdf.readthedocs.io/en/latest/page.html): used to set page dimensions and embed screenshot images.
- [Qt high-DPI coordinates](https://doc.qt.io/qt-6/highdpi.html): local coordinates within a monitor are converted using that monitor's scale factor; the virtual desktop origin is not scaled.


### Reading and boundary review

The preview now fits page width by default. Scroll vertically through long pages, use Ctrl + wheel to zoom, or choose Whole page / 100%. Page navigation preserves the zoom mode. F11 toggles focus mode; capture details and editing controls are collapsed until needed. The sidebar supports page-number navigation and filtering pages that need review. Boundary inspection shows continuous source pixels around adjacent pages with both crop lines overlaid.

Automatic capture settings default to visible-background mode for new settings; reopening a session preserves its saved input mode. Keep the reader visible: this mode does not move it to another monitor automatically.
