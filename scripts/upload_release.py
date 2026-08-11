"""Upload all dictionary .sqlite3 files to GitHub Release.

Requires env var GITHUB_TOKEN with repo write permission.
Usage: set GITHUB_TOKEN=gho_xxx && python upload_release.py
"""
import os
import sys
import time
import urllib.request
import urllib.parse
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

TOKEN = os.environ.get("GITHUB_TOKEN", "")
if not TOKEN:
    # Try git credential manager
    import subprocess
    r = subprocess.run(["git", "credential", "fill"],
                       input=b"protocol=https\nhost=github.com\n\n",
                       capture_output=True)
    for line in r.stdout.decode("utf-8", errors="replace").splitlines():
        if line.startswith("password="):
            TOKEN = line.split("=", 1)[1]
            break
if not TOKEN:
    print("ERROR: Set GITHUB_TOKEN env var or configure git credential manager", file=sys.stderr)
    sys.exit(1)
REPO = "hudaiapa88/lingolate-dict-data"
RELEASE_ID = 368452110
DICT_DIR = Path(__file__).resolve().parent.parent / "dictionaries"

UPLOAD_URL = f"https://uploads.github.com/repos/{REPO}/releases/{RELEASE_ID}/assets"


def upload_one(filepath: Path) -> tuple[str, bool, str]:
    name = filepath.name
    size = filepath.stat().st_size
    url = UPLOAD_URL + "?" + urllib.parse.urlencode({"name": name})
    with filepath.open("rb") as f:
        data = f.read()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"token {TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/octet-stream",
    })
    try:
        r = urllib.request.urlopen(req, timeout=300)
        resp = json.loads(r.read())
        return (name, True, f"{size:,} bytes -> {resp['browser_download_url']}")
    except Exception as exc:
        return (name, False, str(exc))


def main():
    files = sorted(DICT_DIR.glob("*.sqlite3"))
    print(f"Uploading {len(files)} files to release {RELEASE_ID}")
    total_size = sum(f.stat().st_size for f in files)
    print(f"Total size: {total_size:,} bytes ({total_size/1024/1024/1024:.2f} GB)")

    ok = 0
    fail = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(upload_one, f): f for f in files}
        for i, future in enumerate(as_completed(futures), 1):
            name, success, msg = future.result()
            if success:
                ok += 1
                print(f"  [{i}/{len(files)}] [ok] {name}: {msg}")
            else:
                fail += 1
                print(f"  [{i}/{len(files)}] [FAIL] {name}: {msg}", file=sys.stderr)

    elapsed = time.time() - t0
    print(f"\nDone: {ok} ok, {fail} fail in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
