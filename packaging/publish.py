"""Publish only the explicitly prepared release files using Git credentials."""
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
REPO = "CharlieWinple/Visual-Document-Capture"


def main():
    # Capture credential-helper output in memory; never print or persist secrets.
    credential = subprocess.run(["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n", text=True, capture_output=True, check=True)
    fields = dict(line.split("=", 1) for line in credential.stdout.splitlines() if "=" in line)
    token = fields["password"]
    def request(path, method="GET", data=None, content_type="application/json"):
        url = path if path.startswith("https://") else f"https://api.github.com/repos/{REPO}/{path}"
        assert urllib.parse.urlparse(url).hostname in ("api.github.com", "uploads.github.com")
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "VDC-release-builder", "Content-Type": content_type})
        with urllib.request.urlopen(req, timeout=240) as response:
            return json.load(response)
    files = [ROOT / "dist/release" / name for name in
             ("VisualDocumentCapture-v0.4.4-windows-x64.zip", "Source-v0.4.4.zip", "SHA256SUMS.txt")]
    for path in files:
        assert path.is_file()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    releases = request("releases")
    matches = [r for r in releases if r["tag_name"] == "v0.4.4"]
    if matches:
        release = matches[0]
        if not release["draft"]:
            raise RuntimeError("v0.4.4 already published; refusing to overwrite")
    else:
        release = request("releases", "POST", json.dumps({"tag_name": "v0.4.4", "target_commitish": commit,
            "name": "v0.4.4 — Windows 免安装预览版", "draft": True, "prerelease": True,
            "body": (ROOT / "packaging/RELEASE_NOTES.md").read_text(encoding="utf-8")}).encode())
    for path in files:
        digest = "sha256:" + hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
        existing = next((a for a in release["assets"] if a["name"] == path.name), None)
        if existing:
            assert existing.get("digest") == digest, "Existing draft asset differs"
            asset = existing
        else:
            asset = request(release["upload_url"].split("{")[0]+"?name="+urllib.parse.quote(path.name),
                            "POST", path.read_bytes(), "application/zip" if path.suffix == ".zip" else "text/plain")
        assert asset["size"] == path.stat().st_size
        assert asset.get("digest") == digest
        print(json.dumps({"uploaded": path.name, "bytes": asset["size"], "digest": digest}), flush=True)
    release = request(f"releases/{release['id']}", "PATCH", json.dumps({"draft": False, "prerelease": True}).encode())
    print(json.dumps({"release": release["html_url"], "prerelease": release["prerelease"], "draft": release["draft"]}))


if __name__ == "__main__":
    main()
