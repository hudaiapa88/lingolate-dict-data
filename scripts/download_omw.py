"""Download and extract omw-data 1.4 wordnets (tar.xz → XML).

Caches extracted XML under sources_cache/omw/<omw_id>/<omw_id>.xml so the
converter can run without re-downloading.

Usage:
    python download_omw.py                       # all omw langs
    python download_omw.py --langs en,ja,tr      # subset (tr will skip — not in omw)
    python download_omw.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
import tarfile
from pathlib import Path

import requests

from config import OMW_LANGS, SUPPORTED_LANGS

CACHE_DIR = Path(__file__).resolve().parent.parent / "sources_cache" / "omw"
TIMEOUT = 120


def cache_path(omw_id: str) -> Path:
    return CACHE_DIR / omw_id / f"{omw_id}.xml"


def download_one(lang: str, dry_run: bool) -> Path | None:
    """Download + extract omw-data for a language. Returns XML path or None."""
    if lang not in OMW_LANGS:
        print(f"  [skip]   {lang}: not in omw-data (use tufs instead)")
        return None

    omw = OMW_LANGS[lang]
    xml_dest = cache_path(omw.omw_id)

    if xml_dest.exists() and xml_dest.stat().st_size > 0:
        print(f"  [cache]  {lang} ({omw.omw_id}) -> {xml_dest.relative_to(CACHE_DIR.parent)}")
        return xml_dest

    if dry_run:
        print(f"  [dry-run] {lang} ({omw.omw_id}) <- {omw.url}")
        return None

    print(f"  [fetch]  {lang} ({omw.omw_id}) <- {omw.url}")
    try:
        resp = requests.get(omw.url, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  [error]  {lang}: {exc}", file=sys.stderr)
        return None

    # Extract tar.xz → find XML → save to cache
    import io
    xml_dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:xz") as tar:
            xml_members = [m for m in tar.getmembers() if m.name.endswith(".xml")]
            if not xml_members:
                print(f"  [error]  {lang}: no XML in archive", file=sys.stderr)
                return None
            # Extract first (and usually only) XML
            f = tar.extractfile(xml_members[0])
            if f is None:
                print(f"  [error]  {lang}: cannot read XML", file=sys.stderr)
                return None
            xml_dest.write_bytes(f.read())
            print(f"  [ok]     {lang}: {xml_dest.stat().st_size:,} bytes XML")
            return xml_dest
    except tarfile.TarError as exc:
        print(f"  [error]  {lang}: tar extract failed: {exc}", file=sys.stderr)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--langs", help="Comma-separated lang list (default: all omw langs)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.langs:
        langs = [l.strip() for l in args.langs.split(",") if l.strip()]
    else:
        langs = sorted(OMW_LANGS.keys())

    print(f"Planning {len(langs)} omw-data downloads")
    ok = 0
    for lang in langs:
        result = download_one(lang, dry_run=args.dry_run)
        if result is not None:
            ok += 1
    print(f"\nDone: {ok}/{len(langs)} cached/fetched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
