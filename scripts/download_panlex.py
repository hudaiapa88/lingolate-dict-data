"""Download PanLex data from HuggingFace (cointegrated/panlex-meanings).

PanLex is CC0 (public domain). Dataset is per-language parquet files.
We use it as a gap-filler for languages not covered by omw-data or tufs:
  cs, hi, lt, nl, pt, uk

Strategy:
  - Download parquet for each needed language
  - Each row: meaning (concept id), variety (lang code), txt (word)
  - Build bilingual pairs by joining on meaning (same as ILI in omw)

Usage:
    python download_panlex.py                       # all 6 missing langs
    python download_panlex.py --langs cs,nl,pt      # subset
    python download_panlex.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

CACHE_DIR = Path(__file__).resolve().parent.parent / "sources_cache" / "panlex"

# PanLex uses ISO 639-3 codes (3-letter). Map our codes to PanLex.
PANLEX_LANG_MAP: dict[str, str] = {
    "cs": "ces",
    "hi": "hin",
    "lt": "lit",
    "nl": "nld",
    "pt": "por",
    "uk": "ukr",
    # Also useful for pivot-based pairs (already in omw, but PanLex adds coverage)
    "en": "eng",
    "de": "deu",
    "es": "spa",
    "fr": "fra",
    "ja": "jpn",
    "ru": "rus",
    "tr": "tur",
    "zh": "cmn",
    "ar": "arb",
    "ko": "kor",
    "vi": "vie",
    "it": "ita",
    "pl": "pol",
    "sv": "swe",
    "fi": "fin",
    "da": "dan",
    "no": "nob",
    "bg": "bul",
    "el": "ell",
    "th": "tha",
    "id": "ind",
    "ms": "zsm",
    "is": "isl",
}

HF_BASE = "https://huggingface.co/datasets/cointegrated/panlex-meanings/resolve/main"


def parquet_url(lang: str) -> str:
    """Get TSV URL for a language (our code)."""
    pl_code = PANLEX_LANG_MAP.get(lang)
    if not pl_code:
        return ""
    return f"{HF_BASE}/data/{pl_code}.tsv"


def cache_path(lang: str) -> Path:
    pl_code = PANLEX_LANG_MAP.get(lang, lang)
    return CACHE_DIR / pl_code / "data.tsv"


def download_one(lang: str, dry_run: bool) -> Path | None:
    """Download PanLex parquet for a language."""
    if lang not in PANLEX_LANG_MAP:
        print(f"  [skip]   {lang}: not in PanLex map")
        return None

    dest = cache_path(lang)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [cache]  {lang} ({PANLEX_LANG_MAP[lang]}) -> {dest.relative_to(CACHE_DIR.parent)}")
        return dest

    url = parquet_url(lang)
    if not url:
        return None

    if dry_run:
        print(f"  [dry-run] {lang} ({PANLEX_LANG_MAP[lang]}) <- {url}")
        return None

    print(f"  [fetch]  {lang} ({PANLEX_LANG_MAP[lang]}) <- {url}")
    try:
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  [error]  {lang}: {exc}", file=sys.stderr)
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    print(f"  [ok]     {lang}: {dest.stat().st_size:,} bytes TSV")
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--langs", default="cs,hi,lt,nl,pt,uk",
                        help="Comma-separated lang list (default: 6 missing langs)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    langs = [l.strip() for l in args.langs.split(",") if l.strip()]
    print(f"Planning {len(langs)} PanLex downloads")
    ok = 0
    for lang in langs:
        result = download_one(lang, dry_run=args.dry_run)
        if result is not None:
            ok += 1
    print(f"\nDone: {ok}/{len(langs)} cached/fetched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
