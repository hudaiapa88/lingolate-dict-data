"""Download the sources used by enrich_lexicon.py into sources_cache/.

  wikidata/latest-lexemes.json.gz  Wikidata Lexemes dump (CC0) — forms, IPA,
                                   senses, translations, usage examples
  cili/ili-map-pwn31.tab           Collaborative ILI ↔ WordNet 3.1 map (CC BY 4.0)
                                   — links Wikidata senses (P8814) to OMW concepts
  grambank/*.csv, wals/*.csv       Grambank and WALS CLDF (CC BY 4.0) — grammar
                                   features per language

Usage:  python scripts/download_extras.py [--skip-wikidata]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'sources_cache'
UA = {'User-Agent': 'lingolate-dict-data build (https://github.com/hudaiapa88/lingolate-dict-data)'}

WIKIDATA = 'https://dumps.wikimedia.org/wikidatawiki/entities/latest-lexemes.json.gz'
CILI = 'https://raw.githubusercontent.com/globalwordnet/cili/master/ili-map-pwn31.tab'
CLDF = {
    'grambank': 'https://raw.githubusercontent.com/grambank/grambank/master/cldf/',
    'wals': 'https://raw.githubusercontent.com/cldf-datasets/wals/master/cldf/',
}
CLDF_FILES = ('languages.csv', 'parameters.csv', 'codes.csv', 'values.csv')


def fetch(url: str, dest: Path) -> None:
    """Resumable download (a .part file is continued with a Range request)."""
    if dest.exists():
        print(f'  cached {dest.relative_to(ROOT)}', file=sys.stderr)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + '.part')
    done = part.stat().st_size if part.exists() else 0
    headers = dict(UA, **({'Range': f'bytes={done}-'} if done else {}))
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        if r.status_code == 416:  # already complete
            part.rename(dest)
            return
        r.raise_for_status()
        mode = 'ab' if done and r.status_code == 206 else 'wb'
        with part.open(mode) as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    part.rename(dest)
    print(f'  {dest.relative_to(ROOT)}: {dest.stat().st_size / 1e6:.1f} MB', file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--skip-wikidata', action='store_true')
    args = ap.parse_args()
    fetch(CILI, CACHE / 'cili' / 'ili-map-pwn31.tab')
    for name, base in CLDF.items():
        for f in CLDF_FILES:
            fetch(base + f, CACHE / name / f)
    if not args.skip_wikidata:
        fetch(WIKIDATA, CACHE / 'wikidata' / 'latest-lexemes.json.gz')
    print('done', file=sys.stderr)


if __name__ == '__main__':
    main()
