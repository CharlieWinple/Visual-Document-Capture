"""Build/assemble the portable distribution; never includes user sessions."""
import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.4.3"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--assemble-only", action="store_true")
    args = parser.parse_args()
    if not args.assemble_only:
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), sys.base_prefix,
                                      str(Path(os.environ["SystemRoot"]) / "System32")])
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--workpath", "build/portable", "packaging/VisualDocumentCapture.spec"], cwd=ROOT, env=env, check=True)
    bundle = ROOT / "dist/VisualDocumentCapture"
    assert (bundle / "VisualDocumentCapture.exe").is_file()
    shutil.copy2(ROOT / "packaging/PORTABLE_README.txt", bundle / "README.txt")
    license_root = bundle / "THIRD_PARTY_LICENSES"
    license_root.mkdir(exist_ok=True)
    versions = {}
    for name in ("numpy", "opencv-python-headless", "pillow", "pymupdf", "mss", "psutil", "PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "pyinstaller"):
        dist = metadata.distribution(name)
        versions[name] = dist.version
        destination = license_root / name
        destination.mkdir(exist_ok=True)
        (destination / "METADATA.txt").write_text(dist.read_text("METADATA") or "", encoding="utf-8")
        for item in dist.files or []:
            lower = str(item).lower()
            if any(word in lower for word in ("license", "copying", "copyright", "notice")) and not lower.endswith((".py", ".pyc", ".pyd")):
                source = Path(dist.locate_file(item))
                if source.is_file():
                    target = destination / str(item).replace("../", "parent/")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.exists():
        shutil.copy2(python_license, license_root / "PYTHON-LICENSE.txt")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    (bundle / "BUILD_INFO.json").write_text(json.dumps({"version": VERSION, "commit": commit,
        "python": sys.version, "platform": "Windows x64", "dependencies": versions}, indent=2), encoding="utf-8")
    release = ROOT / "dist/release"
    release.mkdir(exist_ok=True)
    archive = release / f"VisualDocumentCapture-v{VERSION}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in sorted(bundle.rglob("*")):
            if path.is_file() and path.relative_to(bundle).parts[0] not in ("logs", "sessions"):
                output.write(path, Path(bundle.name) / path.relative_to(bundle))
    source = release / f"Source-v{VERSION}.zip"
    subprocess.run(["git", "archive", "--format=zip", f"--output={source}", "HEAD"], cwd=ROOT, check=True)
    checksums = []
    for path in (archive, source):
        digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
        checksums.append(f"{digest}  {path.name}")
    (release / "SHA256SUMS.txt").write_text("\n".join(checksums)+"\n", encoding="utf-8")
    print(json.dumps({"commit": commit, "zip_bytes": archive.stat().st_size, "release": str(release)}))


if __name__ == "__main__":
    main()
