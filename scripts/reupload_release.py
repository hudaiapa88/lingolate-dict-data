"""Re-upload updated dictionary files to GitHub Release v2026.08.1.

Deletes old assets with same name, then uploads new ones.
Only uploads files that changed (size or mtime different).
"""
import os
import sys
import time
import urllib.request
import urllib.parse
import json
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Get token from git credential manager
r = subprocess.run(["git", "credential", "fill"],
                   input=b"protocol=https\nhost=github.com\n\n",
                   capture_output=True)
TOKEN = ""
for line in r.stdout.decode("utf-8", errors="replace").splitlines():
    if line.startswith("password="):
        TOKEN = line.split("=", 1)[1]
        break
if not TOKEN:
    print("ERROR: No token", file=sys.stderr)
    sys.exit(1)

REPO = "hudaiapa88/lingolate-dict-data"
RELEASE_ID = 368452110
DICT_DIR = Path(__file__).resolve().parent.parent / "dictionaries"

API_BASE = f"https://api.github.com/repos/{REPO}/releases/{RELEASE_ID}"
UPLOAD_URL = f"https://uploads.github.com/repos/{REPO}/releases/{RELEASE_ID}/assets"


def get_existing_assets():
    """Get list of existing assets {name: asset_id}."""
    req = urllib.request.Request(f"{API_BASE}/assets",
                                 headers={"Authorization": f"token {TOKEN}",
                                          "Accept": "application/vnd.github+json"})
    r = urllib.request.urlopen(req)
    return {a["name"]: a["id"] for a in json.loads(r.read())}


def delete_asset(asset_id: int) -> bool:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases/assets/{asset_id}",
        method="DELETE",
        headers={"Authorization": f"token {TOKEN}",
                 "Accept": "application/vnd.github+json"})
    try:
        urllib.request.urlopen(req)
        return True
    except Exception:
        return False


def upload_one(filepath: Path, existing_assets: dict) -> tuple[str, bool, str]:
    name = filepath.name
    size = filepath.stat().st_size

    # Delete old asset if exists
    if name in existing_assets:
        delete_asset(existing_assets[name])

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
        return (name, True, f"{size:,} bytes")
    except Exception as exc:
        return (name, False, str(exc))


def main():
    files = sorted(DICT_DIR.glob("*.sqlite3"))
    print(f"Re-uploading {len(files)} files to release {RELEASE_ID}")

    print("Fetching existing assets...")
    existing = get_existing_assets()
    print(f"  Found {len(existing)} existing assets")

    ok = 0
    fail = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(upload_one, f, existing): f for f in files}
        for i, future in enumerate(as_completed(futures), 1):
            name, success, msg = future.result()
            if success:
                ok += 1
                if i % 50 == 0 or i == len(files):
                    print(f"  [{i}/{len(files)}] ok={ok} fail={fail} ({time.time()-t0:.0f}s)")
            else:
                fail += 1
                print(f"  [FAIL] {name}: {msg}", file=sys.stderr)

    print(f"\nDone: {ok} ok, {fail} fail in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
