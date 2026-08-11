"""Download upstream CC0 dictionary data from open-dict-data/wikidict-*.

The upstream files are tab-separated `source\\ttarget` pairs, one per line.
We cache them under sources_cache/<source_id>/<src>-<tgt>_wiki.txt so the
converter can run without re-downloading.

Usage:
    python download_sources.py                    # all planned pairs
    python download_sources.py --pairs en-tr,de-tr
    python download_sources.py --dry-run          # list what would be fetched
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

from config import SOURCES, dedupe_pairs, planned_pairs

CACHE_DIR = Path(__file__).resolve().parent.parent / "sources_cache"
TIMEOUT = 60


def cache_path(source_id: str, src: str, tgt: str) -> Path:
    return CACHE_DIR / source_id / f"{src}-{tgt}_wiki.txt"


def download_one(src: str, tgt: str, source_id: str, dry_run: bool) -> Path | None:
    """Download the upstream file for src->tgt. Returns local path or None on failure.

    The wikidict-{pivot} repos store files as {other}-{pivot}_wiki.txt where
    pivot is the repo's anchor language. So for pair (src, tgt):
      - if src is the pivot, the file is {tgt}-{pivot}_wiki.txt (we swap columns later)
      - otherwise the file is {src}-{pivot}_wiki.txt (forward)
    We always cache the file under its upstream name {file_src}-{file_tgt}_wiki.txt.
    """
    source = SOURCES[source_id]
    pivot = source_id.rsplit("-", 1)[-1]
    if src == pivot:
        file_src, file_tgt = tgt, src  # file is {tgt}-{pivot}_wiki.txt
    else:
        file_src, file_tgt = src, tgt  # file is {src}-{pivot}_wiki.txt
    url = source.url_template.format(src=file_src, tgt=file_tgt)
    dest = cache_path(source_id, file_src, file_tgt)

    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [cache] {src}-{tgt} -> {dest.relative_to(CACHE_DIR.parent)}")
        return dest

    if dry_run:
        print(f"  [dry-run] {src}-{tgt} <- {url} (file: {file_src}-{file_tgt})")
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  [fetch]  {src}-{tgt} <- {url} (file: {file_src}-{file_tgt})")
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return dest
    except requests.RequestException as exc:
        print(f"  [error]  {src}-{tgt}: {exc}", file=sys.stderr)
        # 404 means the pair doesn't exist upstream — not a hard failure.
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pairs",
        help="Comma-separated list of src-tgt pairs to build (default: all planned)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Plan only, no downloads")
    args = parser.parse_args()

    pairs = planned_pairs()
    if args.pairs:
        wanted = {tuple(p.strip().split("-")) for p in args.pairs.split(",") if p.strip()}
        pairs = [(s, t, sid) for (s, t, sid) in pairs if (s, t) in wanted]
    pairs = dedupe_pairs(pairs)

    print(f"Planning {len(pairs)} pair-downloads across {len({sid for _, _, sid in pairs})} sources")
    ok = 0
    missing: list[tuple[str, str]] = []
    for src, tgt, sid in pairs:
        result = download_one(src, tgt, sid, dry_run=args.dry_run)
        if result is not None:
            ok += 1
        else:
            missing.append((src, tgt))

    print(f"\nDone: {ok} cached/fetched, {len(missing)} missing")
    if missing and not args.dry_run:
        print("Missing pairs (no upstream file — will be skipped at convert time):")
        for src, tgt in missing:
            print(f"  {src}-{tgt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
