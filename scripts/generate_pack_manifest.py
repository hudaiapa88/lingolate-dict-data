"""Write manifest-v3.json describing the pair packs in packs/ (SCHEMA_V3.md).

The app reads this list (via jsDelivr / raw.githubusercontent) to find the
pack for its language pair. manifest.json (v2 dictionaries) is left untouched
so older app versions keep working.

Usage:  python scripts/generate_pack_manifest.py --version 2026.11.2 --tag v2026.11.2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKS = ROOT / 'packs'
REPO = 'hudaiapa88/lingolate-dict-data'


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--version', required=True)
    ap.add_argument('--tag', required=True)
    args = ap.parse_args()

    packs = []
    for path in sorted(PACKS.glob('pack-*.sqlite3')):
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        meta = dict(db.execute('SELECT key, value FROM _meta'))
        db.close()
        a, b = meta['pair'].split('-')
        packs.append({
            'pair': meta['pair'],
            'langs': [a, b],
            'file': path.name,
            'url': f'https://github.com/{REPO}/releases/download/{args.tag}/{path.name}',
            'bytes': path.stat().st_size,
            'sha256': sha256(path),
            'lemmas': int(meta['lemmas']),
            'translations': int(meta['translations']),
            'examples': int(meta['examples']),
            'licenses': json.loads(meta['licenses']),
        })

    manifest = {
        'schema': 'pack-v3',
        'version': args.version,
        'release_tag': args.tag,
        'updated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'source_repo': f'https://github.com/{REPO}',
        'attributions': f'https://github.com/{REPO}/blob/main/ATTRIBUTIONS.md',
        'packs': packs,
    }
    out = ROOT / 'manifest-v3.json'
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    total = sum(p['bytes'] for p in packs)
    print(f'{out.name}: {len(packs)} packs, {total / 1e9:.2f} GB')


if __name__ == '__main__':
    main()
