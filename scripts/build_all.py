"""Orchestrator: download omw + tufs → convert → manifest → validate.

Usage:
    python build_all.py --version 2026.08.1
    python build_all.py --version 2026.08.1 --pairs en-ja,ja-es
    python build_all.py --version 2026.08.1 --dry-run
    python build_all.py --version 2026.08.1 --skip-tufs   # omw only
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def run(script: str, *args: str) -> int:
    cmd = [sys.executable, str(SCRIPTS / script), *args]
    print(f"\n$ {' '.join(cmd)}")
    return subprocess.call(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="e.g. 2026.08.1")
    parser.add_argument("--release-tag", default=None, help="GitHub Release tag (default: v<version>)")
    parser.add_argument("--pairs", default=None, help="Comma-separated subset")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-tufs", action="store_true", help="Skip tufs (omw only)")
    parser.add_argument("--skip-omw", action="store_true", help="Skip omw (tufs only)")
    args = parser.parse_args()

    release_tag = args.release_tag or f"v{args.version}"

    # ── Determine which langs to download ────────────────────────
    # For pair subset, only download the langs involved
    from config import OMW_LANGS, TUF_ONLY_LANGS, TUF_SHARED_LANGS, planned_pairs, dedupe_pairs

    pairs = planned_pairs()
    if args.pairs:
        wanted = {tuple(p.strip().split("-")) for p in args.pairs.split(",") if p.strip()}
        pairs = [(s, t, st) for (s, t, st) in pairs if (s, t) in wanted]
    pairs = dedupe_pairs(pairs)

    omw_langs_needed = set()
    tufs_langs_needed = set()
    for src, tgt, _ in pairs:
        if src in OMW_LANGS:
            omw_langs_needed.add(src)
        elif src in TUF_ONLY_LANGS or src in TUF_SHARED_LANGS:
            tufs_langs_needed.add(src)
        if tgt in OMW_LANGS:
            omw_langs_needed.add(tgt)
        elif tgt in TUF_ONLY_LANGS or tgt in TUF_SHARED_LANGS:
            tufs_langs_needed.add(tgt)

    # ── Download phase ────────────────────────────────────────────
    if args.dry_run:
        if omw_langs_needed and not args.skip_omw:
            run("download_omw.py", "--langs", ",".join(sorted(omw_langs_needed)), "--dry-run")
        if tufs_langs_needed and not args.skip_tufs:
            run("download_tufs.py", "--langs", ",".join(sorted(tufs_langs_needed)), "--dry-run")
        print("\n[dry-run] skipping convert/manifest/validate")
        return 0

    if omw_langs_needed and not args.skip_omw:
        rc = run("download_omw.py", "--langs", ",".join(sorted(omw_langs_needed)))
        if rc:
            return rc

    if tufs_langs_needed and not args.skip_tufs:
        rc = run("download_tufs.py", "--langs", ",".join(sorted(tufs_langs_needed)))
        if rc:
            return rc

    # ── Convert phase ─────────────────────────────────────────────
    pair_arg = ["--pairs", args.pairs] if args.pairs else []
    rc = run("convert_omw_to_sqlite.py", "--data-version", args.version, *pair_arg)
    if rc:
        return rc

    # ── Manifest + validate ──────────────────────────────────────
    rc = run("generate_manifest.py", "--version", args.version, "--release-tag", release_tag)
    if rc:
        return rc

    rc = run("validate_manifest.py")
    if rc:
        return rc

    print(f"\nBuild complete — version {args.version}, release tag {release_tag}")
    print("Next: create a GitHub Release with the tag and upload dictionaries/*.sqlite3")
    return 0


if __name__ == "__main__":
    sys.exit(main())
