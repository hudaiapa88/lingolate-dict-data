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


BACK_VOWELS = set('aıou')
FRONT_VOWELS = set('eiöü')


def fix_tr_verb_stems(p: sqlite3.Connection) -> None:
    """TUFS writes Turkish verbs as stems ('yap-', 'git-'); dictionaries and
    learners use the infinitive ('yapmak', 'gitmek'). The suffix follows
    vowel harmony: -mak after a back vowel, -mek after a front vowel."""
    rows = p.execute("SELECT id, written FROM lemma WHERE lang = 'tr' AND written LIKE '%-'").fetchall()
    fixed = []
    for lid, w in rows:
        stem = w[:-1].strip()
        last = next((ch for ch in reversed(stem.lower()) if ch in BACK_VOWELS | FRONT_VOWELS), None)
        if not stem or last is None:
            continue
        inf = stem + ('mak' if last in BACK_VOWELS else 'mek')
        fixed.append((inf, inf.replace('I', 'ı').replace('İ', 'i').lower(), lid))
    p.executemany('UPDATE lemma SET written = ?, norm = ? WHERE id = ?', fixed)
    # no vowel ('DNYT-'): not a word; its links go, the orphan sweep does the rest
    bad = [(lid,) for lid, w in rows if lid not in {f[2] for f in fixed}]
    p.executemany('DELETE FROM trans WHERE a_lemma = ? OR b_lemma = ?', ((i, i) for (i,) in bad))


IPA_PRIORITY = {'wikidata': 0, 'wordnet': 1, 'espeak': 2, 'pinyin': 2, 'kana': 2, None: 3}


def _min(x, y):
    return y if x is None else (x if y is None else min(x, y))


def merge_duplicates(p: sqlite3.Connection) -> int:
    """One headword per (language, written form): the same word arrives from
    several sources, with and without a part of speech. Attributes merge into
    the lowest id; translations, forms, definitions and usage follow it."""
    cols = [r[1] for r in p.execute('PRAGMA table_info(lemma)')]
    has_src = 'ipa_src' in cols
    rows = p.execute('SELECT id, lang, written, pos, gender, ipa, freq, '
                     f'{"ipa_src" if has_src else "NULL"} FROM lemma ORDER BY id').fetchall()
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r[1], r[2]), []).append(r)
    remap: dict[int, int] = {}
    updates = []
    for g in groups.values():
        if len(g) < 2:
            continue
        keep = g[0][0]
        pos = next((r[3] for r in g if r[3]), None)
        gender = next((r[4] for r in g if r[4]), None)
        best_ipa = min((r for r in g if r[5]), key=lambda r: IPA_PRIORITY.get(r[7], 3), default=None)
        freqs = [r[6] for r in g if r[6] is not None]
        updates.append((pos, gender, best_ipa[5] if best_ipa else None,
                        best_ipa[7] if best_ipa else None, min(freqs) if freqs else None, keep))
        for r in g[1:]:
            remap[r[0]] = keep
    if not remap:
        return 0
    if has_src:
        p.executemany('UPDATE lemma SET pos = ?, gender = ?, ipa = ?, ipa_src = ?, freq = ? WHERE id = ?', updates)
    else:
        p.executemany('UPDATE lemma SET pos = ?, gender = ?, ipa = ?, freq = ? WHERE id = ?',
                      [(u[0], u[1], u[2], u[4], u[5]) for u in updates])

    # translations: remap, then fold duplicate (a, b) links together
    tcols = [r[1] for r in p.execute('PRAGMA table_info(trans)')]
    ranked = 'a_rank' in tcols
    merged: dict[tuple, list] = {}
    for row in p.execute(f'SELECT a_lemma, b_lemma, confidence, sources, concept'
                         f'{", a_rank, b_rank" if ranked else ""} FROM trans'):
        a, b = remap.get(row[0], row[0]), remap.get(row[1], row[1])
        cur = merged.get((a, b))
        if cur is None:
            merged[(a, b)] = [row[2], set(row[3].split(';')), row[4]] + (list(row[5:7]) if ranked else [])
            continue
        if row[2] > cur[0]:
            cur[0], cur[2] = row[2], row[4]
        cur[1].update(row[3].split(';'))
        if ranked:
            cur[3], cur[4] = _min(cur[3], row[5]), _min(cur[4], row[6])
    p.execute('DELETE FROM trans')
    p.executemany(f'INSERT INTO trans VALUES ({",".join("?" * (7 if ranked else 5))})',
                  ((a, b, v[0], ';'.join(sorted(v[1])), v[2], *v[3:]) for (a, b), v in merged.items()))
    for table in ('form', 'definition', 'usage'):
        p.executemany(f'UPDATE {table} SET lemma_id = ? WHERE lemma_id = ?',
                      ((k, v) for v, k in remap.items()))
    p.executescript("""
      DELETE FROM form WHERE rowid NOT IN (SELECT min(rowid) FROM form GROUP BY lemma_id, form, tags);
      DELETE FROM definition WHERE rowid NOT IN (SELECT min(rowid) FROM definition GROUP BY lemma_id, text);
      DELETE FROM usage WHERE rowid NOT IN (SELECT min(rowid) FROM usage GROUP BY lemma_id, text);
    """)
    p.executemany('DELETE FROM lemma WHERE id = ?', ((i,) for i in remap))
    return len(remap)


def drop_case_variants(p: sqlite3.Connection) -> int:
    """'BE', 'VE', 'Bir' next to 'be', 've', 'bir': a capitalised variant
    backed only by low-confidence (PanLex) links is noise ('VE' → 'AND').
    Variants with a reliable translation stay ('Be' → beryllium)."""
    lower = {(lang, w) for lang, w in p.execute('SELECT lang, written FROM lemma')
             if w == w.lower()}
    cand = [lid for lid, lang, w in p.execute('SELECT id, lang, written FROM lemma')
            if w != w.lower() and (lang, w.lower()) in lower]
    sure = {lid for (lid,) in p.execute(
        'SELECT a_lemma FROM trans WHERE confidence >= ? UNION '
        'SELECT b_lemma FROM trans WHERE confidence >= ?', (SURE, SURE))}
    drop = [(lid, lid) for lid in cand if lid not in sure]
    p.executemany('DELETE FROM trans WHERE a_lemma = ? OR b_lemma = ?', drop)
    return len(drop)


def prune_pack(path: Path) -> tuple[int, int, int, int]:
    """Returns (lemmas before, after, translations before, after)."""
    p = sqlite3.connect(path)
    p.execute('PRAGMA journal_mode = OFF')
    lem0 = p.execute('SELECT count(*) FROM lemma').fetchone()[0]
    tr0 = p.execute('SELECT count(*) FROM trans').fetchone()[0]

    fix_tr_verb_stems(p)
    # Japanese wordnet marks the inflection boundary with '+' ('逃避+する')
    p.execute("UPDATE lemma SET written = replace(written, '+', ''), norm = replace(norm, '+', '') "
              "WHERE lang = 'ja' AND written LIKE '%+%'")
    merge_duplicates(p)
    drop_case_variants(p)
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
