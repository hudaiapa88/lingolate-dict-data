"""Upload pair packs + manifest-v3.json to a GitHub Release (creates it if needed).

Needs a token with `contents: write` on the repo in the GH_TOKEN env var
(in Colab: Secrets → GH_TOKEN). Existing assets with the same name are
replaced, so the script can be re-run after a partial upload.

Usage:  GH_TOKEN=... python scripts/upload_packs.py --tag v2026.11.2 --version 2026.11.2
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
REPO = 'hudaiapa88/lingolate-dict-data'
API = f'https://api.github.com/repos/{REPO}'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', required=True)
    ap.add_argument('--version', required=True)
    args = ap.parse_args()

    token = os.environ.get('GH_TOKEN')
    if not token:
        sys.exit('GH_TOKEN is not set')
    s = requests.Session()
    s.headers.update({'Authorization': f'Bearer {token}',
                      'Accept': 'application/vnd.github+json'})

    r = s.get(f'{API}/releases/tags/{args.tag}')
    if r.status_code == 404:
        r = s.post(f'{API}/releases', json={
            'tag_name': args.tag,
            'name': f'Lexicon packs {args.version}',
            'body': ('Pair packs (schema pack-v3): dictionary both ways, forms, '
                     'definitions and Tatoeba example sentences. See SCHEMA_V3.md '
                     'and ATTRIBUTIONS.md.'),
            'prerelease': False,
            'make_latest': 'false',  # keep the v2 dictionary release as "latest" for old apps
        })
    r.raise_for_status()
    release = r.json()
    existing = {a['name']: a['id'] for a in release.get('assets', [])}
    upload_url = release['upload_url'].split('{')[0]

    files = sorted((ROOT / 'packs').glob('pack-*.sqlite3'))
    files += [ROOT / 'manifest-v3.json', ROOT / 'ATTRIBUTIONS.md', ROOT / 'SCHEMA_V3.md']
    for i, path in enumerate(files, 1):
        if path.name in existing:
            s.delete(f'{API}/releases/assets/{existing[path.name]}').raise_for_status()
        ctype = 'application/json' if path.suffix == '.json' else (
            'text/markdown' if path.suffix == '.md' else 'application/octet-stream')
        with path.open('rb') as f:
            r = s.post(upload_url, params={'name': path.name}, data=f,
                       headers={'Content-Type': ctype}, timeout=600)
        r.raise_for_status()
        print(f'[{i}/{len(files)}] {path.name} ({path.stat().st_size / 1e6:.1f} MB)', flush=True)
    # Publish manifest-v3.json on main too: the app reads it via jsDelivr/raw.
    import base64
    manifest = (ROOT / 'manifest-v3.json').read_bytes()
    cur = s.get(f'{API}/contents/manifest-v3.json', params={'ref': 'main'})
    body = {'message': f'chore: manifest-v3.json {args.version}',
            'content': base64.b64encode(manifest).decode(), 'branch': 'main'}
    if cur.status_code == 200:
        body['sha'] = cur.json()['sha']
    s.put(f'{API}/contents/manifest-v3.json', json=body).raise_for_status()
    print('manifest-v3.json committed to main')
    print('done:', release['html_url'])


if __name__ == '__main__':
    main()
