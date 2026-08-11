"""Generate manifest.json for the built dictionaries.

Reads every .sqlite3 in dictionaries/, computes sha256 + size + headword count,
pulls license/attribution from the DB's _meta table, and writes a manifest the
Lingolate app fetches via jsDelivr/raw.

Usage:
    python generate_manifest.py --version 2026.08.1 --release-tag v2026.08.1
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from config import SUPPORTED_LANGS, source_for_lang

ROOT = Path(__file__).resolve().parent.parent
DICT_DIR = ROOT / "dictionaries"
MANIFEST_PATH = ROOT / "manifest.json"

DEFAULT_REPO = "hudaiapa88/lingolate-dict-data"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def get_meta(db_path: Path) -> dict[str, str]:
    """Read _meta table from a v2 schema DB."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT key, value FROM _meta").fetchall()
        return dict(rows)
    except sqlite3.OperationalError:
        # v1 schema (no _meta) — return empty
        return {}
    finally:
        conn.close()


def count_headwords(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "headword" in tables:
            return conn.execute("SELECT COUNT(*) FROM headword").fetchone()[0]
        if "entries" in tables:
            return conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        return 0
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Manifest version, e.g. 2026.08.1")
    parser.add_argument("--release-tag", required=True, help="GitHub Release tag, e.g. v2026.08.1")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repo (owner/name)")
    args = parser.parse_args()

    if not DICT_DIR.exists():
        print(f"No dictionaries/ directory at {DICT_DIR}", file=sys.stderr)
        return 1

    release_base = f"https://github.com/{args.repo}/releases/download/{args.release_tag}"
    today = dt.date.today().isoformat()

    entries: list[dict] = []
    for db_path in sorted(DICT_DIR.glob("*.sqlite3")):
        code = db_path.stem  # e.g. "en-ja"
        src, tgt = code.split("-", 1)
        if src not in SUPPORTED_LANGS or tgt not in SUPPORTED_LANGS:
            print(f"  [skip] {code}: language not in SUPPORTED_LANGS")
            continue

        meta = get_meta(db_path)
        license_id = meta.get("license", "unknown")
        attribution = meta.get("attribution")
        source_info = meta.get("src_source", "")
        headword_count = count_headwords(db_path)

        entry = {
            "code": code,
            "size": db_path.stat().st_size,
            "sha256": sha256_of(db_path),
            "source": source_info,
            "license": license_id,
            "entry_count": headword_count,
            "downloadUrl": f"{release_base}/{db_path.name}",
        }
        if attribution:
            entry["attribution"] = attribution
        entries.append(entry)
        print(f"  [ok] {code}: {entry['size']:,} bytes, {headword_count:,} headwords, {license_id}")

    manifest = {
        "version": args.version,
        "updated": today,
        "license": "Mixed: WordNet, CC-BY-3.0/4.0, Apache-2.0, MIT, CeCILL-C (all commercial-safe)",
        "source_repo": args.repo,
        "release_tag": args.release_tag,
        "dictionaries": entries,
    }

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {MANIFEST_PATH.relative_to(ROOT)} — {len(entries)} dictionaries, version {args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
