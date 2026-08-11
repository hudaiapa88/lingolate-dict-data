"""Download tufs basic vocabulary wordnets (CC-BY 4.0).

tufs covers 23 languages with ~500-1000 entries each — basic vocabulary.
Used for languages NOT in omw-data (de, ko, ru, tr, ar, hi, vi) or as
enrichment for omw languages (pronunciation audio, example sentences).

Caches extracted XML under sources_cache/tufs/<lang>/<xml_name>.

Usage:
    python download_tufs.py                       # all tufs langs we use
    python download_tufs.py --langs tr,de,ko
    python download_tufs.py --dry-run
"""

from __future__ import annotations

import argparse
import io
import sys
import tarfile
from pathlib import Path

import requests

from config import TUF_ONLY_LANGS, TUF_SHARED_LANGS, TUF_VERSION, tufs_url

CACHE_DIR = Path(__file__).resolve().parent.parent / "sources_cache" / "tufs"
TIMEOUT = 60


def cache_dir(lang: str) -> Path:
    return CACHE_DIR / lang


def list_tufs_langs() -> list[str]:
    return sorted(set(TUF_ONLY_LANGS.keys()) | set(TUF_SHARED_LANGS.keys()))


def download_one(lang: str, dry_run: bool) -> Path | None:
    """Download + extract tufs for a language. Returns XML path or None."""
    url = tufs_url(lang)
    if not url:
        print(f"  [skip]   {lang}: not in tufs")
        return None

    dest_dir = cache_dir(lang)
    # Find existing XML
    existing = list(dest_dir.glob("*.xml")) if dest_dir.exists() else []
    if existing:
        print(f"  [cache]  {lang} -> {existing[0].relative_to(CACHE_DIR.parent)}")
        return existing[0]

    if dry_run:
        print(f"  [dry-run] {lang} <- {url}")
        return None

    print(f"  [fetch]  {lang} <- {url}")
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  [error]  {lang}: {exc}", file=sys.stderr)
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:xz") as tar:
            xml_members = [m for m in tar.getmembers() if m.name.endswith(".xml")]
            if not xml_members:
                print(f"  [error]  {lang}: no XML in archive", file=sys.stderr)
                return None
            f = tar.extractfile(xml_members[0])
            if f is None:
                return None
            xml_path = dest_dir / Path(xml_members[0].name).name
            xml_path.write_bytes(f.read())
            print(f"  [ok]     {lang}: {xml_path.stat().st_size:,} bytes XML")
            return xml_path
    except tarfile.TarError as exc:
        print(f"  [error]  {lang}: tar extract failed: {exc}", file=sys.stderr)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--langs", help="Comma-separated lang list (default: all tufs langs we use)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.langs:
        langs = [l.strip() for l in args.langs.split(",") if l.strip()]
    else:
        langs = list_tufs_langs()

    print(f"Planning {len(langs)} tufs downloads")
    ok = 0
    for lang in langs:
        result = download_one(lang, dry_run=args.dry_run)
        if result is not None:
            ok += 1
    print(f"\nDone: {ok}/{len(langs)} cached/fetched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
