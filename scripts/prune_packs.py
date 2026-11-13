"""Prune exported packs in place (also called at the end of export_packs.py).

Low-confidence translations (PanLex only, < SURE) are what make packs big and
noisy: in en-tr two thirds of the Turkish headwords were multi-word phrases
backed by PanLex alone. Rules, applied to translations below SURE only:
  1. drop it when either side is a long phrase (>= 3 words; > 8 characters
     in ja/zh/th) — reliable phrases from wordnets / Wikidata stay;
  2. drop it when both sides already have a reliable translation (the app
     hides low-confidence ones when a reliable one exists).
Then headwords left without translations, and their forms, definitions and
usage examples, are removed and the file is vacuumed. _meta counts follow.

Usage:  python scripts/prune_packs.py [--pairs en-tr,de-tr]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKS = ROOT / 'packs'
SURE = 0.5
NO_SPACE = {'ja', 'zh', 'th'}


def is_phrase(lang: str, text: str) -> bool:
    if lang in NO_SPACE:
        return len(text) > 8
    return len(text.split()) >= 3


def prune_pack(path: Path) -> tuple[int, int, int, int]:
    """Returns (lemmas before, after, translations before, after)."""
    p = sqlite3.connect(path)
    p.execute('PRAGMA journal_mode = OFF')
    lem0 = p.execute('SELECT count(*) FROM lemma').fetchone()[0]
    tr0 = p.execute('SELECT count(*) FROM trans').fetchone()[0]

    info = {lid: (lang, w) for lid, lang, w in p.execute('SELECT id, lang, written FROM lemma')}
    sure = set()
    for a, b in p.execute('SELECT a_lemma, b_lemma FROM trans WHERE confidence >= ?', (SURE,)):
        sure.add(a)
        sure.add(b)
    drop = []
    for rid, a, b in p.execute('SELECT rowid, a_lemma, b_lemma FROM trans WHERE confidence < ?', (SURE,)):
        if (is_phrase(*info[a]) or is_phrase(*info[b])) or (a in sure and b in sure):
            drop.append((rid,))
    p.executemany('DELETE FROM trans WHERE rowid = ?', drop)

    p.executescript("""
      CREATE TEMP TABLE keep AS
        SELECT a_lemma AS id FROM trans UNION SELECT b_lemma FROM trans;
      DELETE FROM lemma WHERE id NOT IN (SELECT id FROM keep);
      DELETE FROM form WHERE lemma_id NOT IN (SELECT id FROM keep);
      DELETE FROM definition WHERE lemma_id NOT IN (SELECT id FROM keep);
      DELETE FROM usage WHERE lemma_id NOT IN (SELECT id FROM keep);
      DELETE FROM gloss WHERE concept NOT IN (SELECT concept FROM trans);
    """)
    lem1 = p.execute('SELECT count(*) FROM lemma').fetchone()[0]
    tr1 = p.execute('SELECT count(*) FROM trans').fetchone()[0]
    counts = {
        'lemmas': lem1,
        'translations': tr1,
        'forms': p.execute('SELECT count(*) FROM form').fetchone()[0],
        'usage': p.execute('SELECT count(*) FROM usage').fetchone()[0],
    }
    p.executemany('UPDATE _meta SET value = ? WHERE key = ?',
                  ((str(v), k) for k, v in counts.items()))
    p.commit()
    p.execute('VACUUM')
    p.close()
    return lem0, lem1, tr0, tr1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--pairs', help='comma list like en-tr,de-tr (default: all packs)')
    args = ap.parse_args()
    paths = ([PACKS / f'pack-{"-".join(sorted(x.split("-")))}.sqlite3' for x in args.pairs.split(',')]
             if args.pairs else sorted(PACKS.glob('pack-*.sqlite3')))
    t0 = time.time()
    tot = [0, 0, 0, 0]
    b0 = b1 = 0
    for path in paths:
        b0 += path.stat().st_size
        r = prune_pack(path)
        b1 += path.stat().st_size
        tot = [x + y for x, y in zip(tot, r)]
        print(f'{path.stem}: lemmas {r[0]:,} -> {r[1]:,}, trans {r[2]:,} -> {r[3]:,}, '
              f'{path.stat().st_size / 1e6:.1f} MB', file=sys.stderr, flush=True)
    print(f'pruned {len(paths)} packs in {time.time() - t0:.0f} s: lemmas {tot[0]:,} -> {tot[1]:,}, '
          f'trans {tot[2]:,} -> {tot[3]:,}, {b0 / 1e9:.2f} GB -> {b1 / 1e9:.2f} GB', file=sys.stderr)


if __name__ == '__main__':
    main()
