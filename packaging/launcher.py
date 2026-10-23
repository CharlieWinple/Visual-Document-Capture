"""Windowed portable entry point, with logs kept outside bundled libraries."""
from pathlib import Path
import sys
import traceback

if __name__ == "__main__":
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    try:
        logs = base / "logs"
        logs.mkdir(exist_ok=True)
        if sys.stdout is None:
            sys.stdout = (logs / "application.log").open("a", encoding="utf-8", buffering=1)
        if sys.stderr is None:
            sys.stderr = sys.stdout
        if "--smoke-test" in sys.argv:
            from vdc.app.package_smoke import main
            main(Path(sys.argv[sys.argv.index("--smoke-test")+1]))
        else:
            if "--sessions-dir" not in sys.argv:
                sys.argv.extend(["--sessions-dir", str(base / "sessions")])
            from vdc.app.main import main
            sys.exit(main())
    except Exception:
        detail = traceback.format_exc()
        if sys.stderr:
            sys.stderr.write(detail)
        if "--smoke-test" not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, detail, "VDC 启动失败", 0x10)
        sys.exit(1)
