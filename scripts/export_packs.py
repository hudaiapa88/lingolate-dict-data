"""Export one pair pack per language pair from lexicon.sqlite3 (SCHEMA_V3.md).

Output: packs/pack-{a}-{b}.sqlite3 (a < b) — dictionary both ways, inflected
forms, definitions, English concept glosses (for on-device AI grounding) and
Tatoeba example sentences (examples/{a}-{b}.sqlite3).

Usage:  python scripts/export_packs.py [--pairs en-tr,de-tr] [--version 2026.11.2]
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sqlite3
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import date
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import SUPPORTED_LANGS  # noqa: E402
from prune_packs import prune_pack  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / 'lexicon.sqlite3'
EXAMPLES = ROOT / 'examples'
OUT = ROOT / 'packs'

TOP_PER_LEMMA = 8        # translations kept per headword (each direction)
MIN_CONF = 0.35          # below this a translation is dropped
MAX_DEFS_PER_LEMMA = 3
MAX_USAGE_PER_LEMMA = 2

# Script every letter of a headword must belong to. PanLex in particular has
# Latin transliterations listed as Russian, Thai, Arabic... words.
SCRIPT = {
    **dict.fromkeys(['cs', 'da', 'de', 'en', 'es', 'fi', 'fr', 'id', 'it', 'lt', 'nl',
                     'no', 'pl', 'pt', 'sv', 'tr', 'vi'], ('LATIN',)),
    **dict.fromkeys(['ru', 'uk', 'bg'], ('CYRILLIC',)),
    'el': ('GREEK',), 'ar': ('ARABIC',), 'hi': ('DEVANAGARI',), 'th': ('THAI',),
    'ko': ('HANGUL',), 'zh': ('CJK',),
    'ja': ('HIRAGANA', 'KATAKANA', 'CJK'),
}


def clean_headword(lang: str, text: str) -> bool:
    """Letters in the language's script, no digits/URLs, sane length."""
    if not text or len(text) > 40 or 'http' in text or '@' in text:
        return False
    scripts = SCRIPT.get(lang)
    letters = 0
    for ch in text:
        if ch.isdigit():
            return False
        if ch.isalpha():
            letters += 1
            if scripts and not unicodedata.name(ch, '').startswith(scripts):
                return False
    return letters > 0

PACK_SCHEMA = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;
CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE source (id INTEGER PRIMARY KEY, key TEXT, name TEXT, license TEXT,
                     url TEXT, attribution TEXT);
CREATE TABLE lemma (id INTEGER PRIMARY KEY, lang TEXT, written TEXT, norm TEXT,
                    pos TEXT, gender TEXT, ipa TEXT, freq INTEGER, ipa_src TEXT);
CREATE TABLE trans (a_lemma INTEGER NOT NULL, b_lemma INTEGER NOT NULL,
                    confidence REAL NOT NULL, sources TEXT NOT NULL, concept INTEGER);
CREATE TABLE definition (lemma_id INTEGER, concept INTEGER, text TEXT, source TEXT);
CREATE TABLE form (lemma_id INTEGER, form TEXT, norm TEXT, tags TEXT);
CREATE TABLE gloss (concept INTEGER PRIMARY KEY, gloss_en TEXT);
CREATE TABLE usage (lemma_id INTEGER, concept INTEGER, text TEXT, source TEXT);
CREATE TABLE grammar (lang TEXT, dataset TEXT, param_id TEXT, param_name TEXT, value TEXT);
CREATE TABLE example (id INTEGER PRIMARY KEY, a TEXT, b TEXT, a_norm TEXT,
                      b_norm TEXT, a_src INTEGER, b_src INTEGER, score REAL,
                      via_en INTEGER);
"""
PACK_INDEXES = """
CREATE INDEX lemma_lookup ON lemma(lang, norm);
CREATE INDEX trans_a ON trans(a_lemma);
CREATE INDEX trans_b ON trans(b_lemma);
CREATE INDEX def_lemma ON definition(lemma_id);
CREATE INDEX form_lookup ON form(norm);
CREATE INDEX form_lemma ON form(lemma_id);
CREATE INDEX usage_lemma ON usage(lemma_id);
CREATE INDEX grammar_lang ON grammar(lang);
"""


def log(msg: str) -> None:
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', file=sys.stderr, flush=True)


def export_pair(m: sqlite3.Connection, a: str, b: str, version: str) -> dict:
    keys = {sid: key for sid, key in m.execute('SELECT id, key FROM source')}

    # ── translations: senses of a and b sharing a concept, noisy-OR ──
    agg: dict[tuple[int, int], dict] = {}
    # sl = sense + lemma.lang, indexed by (lang, concept): reads only the two
    # languages' senses instead of scanning all ~55M senses for every pair.
    q = """
      SELECT sa.lemma_id, sb.lemma_id, sa.concept_id,
             min(sa.confidence, sb.confidence), sa.source_id, sb.source_id
      FROM aux.sl sa
      JOIN aux.sl sb ON sb.lang = ? AND sb.concept_id = sa.concept_id
      WHERE sa.lang = ?
    """
    for la, lb, cid, conf, sa, sb in m.execute(q, (b, a)):
        r = agg.get((la, lb))
        if r is None:
            r = agg[(la, lb)] = {'miss': 1.0, 'src': set(), 'concept': cid, 'best': 0.0}
        r['miss'] *= 1.0 - conf
        r['src'].update((keys[sa], keys[sb]))
        if conf > r['best']:
            r['best'], r['concept'] = conf, cid

    rows = [(la, lb, 1.0 - r['miss'], r) for (la, lb), r in agg.items()
            if 1.0 - r['miss'] >= MIN_CONF]
    # drop headwords in the wrong script, with digits, URLs... (mostly PanLex)
    cand = {r[0] for r in rows} | {r[1] for r in rows}
    m.execute('CREATE TEMP TABLE IF NOT EXISTS want (id INTEGER PRIMARY KEY)')
    m.execute('DELETE FROM want')
    m.executemany('INSERT INTO want VALUES (?)', ((i,) for i in cand))
    good = {lid for lid, lang, w in m.execute(
        'SELECT l.id, l.lang, l.written FROM lemma l JOIN want w ON w.id = l.id')
        if clean_headword(lang, w)}
    rows = [r for r in rows if r[0] in good and r[1] in good]
    # keep the best TOP_PER_LEMMA links per headword on either side
    by_a, by_b = defaultdict(list), defaultdict(list)
    for row in rows:
        by_a[row[0]].append(row)
        by_b[row[1]].append(row)
    keep = set()
    for group in (by_a, by_b):
        for lst in group.values():
            lst.sort(key=lambda r: -r[2])
            keep.update((r[0], r[1]) for r in lst[:TOP_PER_LEMMA])
    trans = [r for r in rows if (r[0], r[1]) in keep]
    if not trans:
        return {}

    lemma_ids = {r[0] for r in trans} | {r[1] for r in trans}
    concepts = {r[3]['concept'] for r in trans}

    OUT.mkdir(exist_ok=True)
    path = OUT / f'pack-{a}-{b}.sqlite3'
    if path.exists():
        path.unlink()
    p = sqlite3.connect(path)
    p.executescript(PACK_SCHEMA)

    m.execute('CREATE TEMP TABLE IF NOT EXISTS want (id INTEGER PRIMARY KEY)')
    m.execute('DELETE FROM want')
    m.executemany('INSERT INTO want VALUES (?)', ((i,) for i in lemma_ids))

    has_ipa_src = any(r[1] == 'ipa_src' for r in m.execute('PRAGMA table_info(lemma)'))
    p.executemany('INSERT INTO lemma VALUES (?,?,?,?,?,?,?,?,?)', m.execute(
        'SELECT l.id, l.lang, l.written, l.norm, l.pos, l.gender, l.ipa, l.freq, '
        f"{'l.ipa_src' if has_ipa_src else 'NULL'} FROM lemma l JOIN want w ON w.id = l.id"))
    used: set[str] = set()
    p.executemany('INSERT INTO trans VALUES (?,?,?,?,?)', (
        (la, lb, round(c, 3), ';'.join(sorted(r['src'])), r['concept'])
        for la, lb, c, r in trans))

    defs: dict[int, list] = defaultdict(list)
    for lid, cid, text, sid in m.execute(
            'SELECT s.lemma_id, s.concept_id, s.definition, s.source_id FROM sense s '
            'JOIN want w ON w.id = s.lemma_id WHERE s.definition IS NOT NULL '
            'ORDER BY s.confidence DESC'):
        lst = defs[lid]
        if len(lst) < MAX_DEFS_PER_LEMMA and all(text != d[2] for d in lst):
            lst.append((lid, cid, text, keys[sid]))
    p.executemany('INSERT INTO definition VALUES (?,?,?,?)',
                  (d for lst in defs.values() for d in lst))
    used.update(d[3] for lst in defs.values() for d in lst)
    forms = m.execute('SELECT f.lemma_id, f.form, f.norm, f.tags, f.source_id FROM form f '
                      'JOIN want w ON w.id = f.lemma_id').fetchall()
    p.executemany('INSERT INTO form VALUES (?,?,?,?)', (f[:4] for f in forms))
    used.update(keys[f[4]] for f in forms)

    usage: dict[int, list] = defaultdict(list)
    lang_of = {lid: lang for lid, lang in p.execute('SELECT id, lang FROM lemma')}
    for lid, cid, text, sid in m.execute(
            'SELECT s.lemma_id, s.concept_id, e.text, e.source_id FROM sense_example e '
            'JOIN sense s ON s.id = e.sense_id JOIN want w ON w.id = s.lemma_id '
            'ORDER BY s.confidence DESC, length(e.text)'):
        # a usage example is a sentence, not a bare phrase ("Siyah.")
        if (len(text) < 8 if lang_of.get(lid) in ('ja', 'zh', 'th') else len(text.split()) < 3):
            continue
        lst = usage[lid]
        if len(lst) < MAX_USAGE_PER_LEMMA and all(text != u[2] for u in lst):
            lst.append((lid, cid, text, keys[sid]))
    p.executemany('INSERT INTO usage VALUES (?,?,?,?)', (u for lst in usage.values() for u in lst))
    used.update(u[3] for lst in usage.values() for u in lst)
    if p.execute('SELECT 1 FROM lemma WHERE freq IS NOT NULL LIMIT 1').fetchone():
        used.add('tatoeba-freq')

    if m.execute("SELECT 1 FROM sqlite_master WHERE name = 'grammar'").fetchone():
        g = m.execute('SELECT lang, dataset, param_id, param_name, value FROM grammar '
                      'WHERE lang IN (?, ?)', (a, b)).fetchall()
        p.executemany('INSERT INTO grammar VALUES (?,?,?,?,?)', g)
        used.update(r[1] for r in g)

    m.execute('DELETE FROM want')
    m.executemany('INSERT INTO want VALUES (?)', ((c,) for c in concepts))
    p.executemany('INSERT INTO gloss VALUES (?,?)', m.execute(
        'SELECT c.id, c.gloss_en FROM concept c JOIN want w ON w.id = c.id '
        'WHERE c.gloss_en IS NOT NULL'))

    for _, _, _, r in trans:
        used.update(r['src'])
    n_ex = 0
    ex_path = EXAMPLES / f'{a}-{b}.sqlite3'
    if ex_path.exists():
        e = sqlite3.connect(ex_path)
        rows_ex = e.execute('SELECT id, a, b, a_norm, b_norm, a_src, b_src, score, via_en FROM pair').fetchall()
        e.close()
        p.executemany('INSERT INTO example VALUES (?,?,?,?,?,?,?,?,?)', rows_ex)
        n_ex = len(rows_ex)
        if n_ex:
            used.add('tatoeba')

    src_rows = [r for r in m.execute('SELECT id, key, name, license, url, attribution FROM source')
                if r[1] in used]
    if 'tatoeba' in used:
        src_rows.append((10_000, 'tatoeba', 'Tatoeba', 'CC-BY-2.0-FR', 'https://tatoeba.org',
                         'Example sentences from Tatoeba (https://tatoeba.org) — CC BY 2.0 FR '
                         '(some sentences CC0).'))
    p.executemany('INSERT INTO source VALUES (?,?,?,?,?,?)', src_rows)

    meta = {
        'schema': 'pack-v3',
        'pair': f'{a}-{b}',
        'data_version': version,
        'generated': date.today().isoformat(),
        'lemmas': str(len(lemma_ids)),
        'translations': str(len(trans)),
        'examples': str(n_ex),
        'forms': str(len(forms)),
        'usage': str(sum(len(v) for v in usage.values())),
        'licenses': json.dumps(sorted({r[3] for r in src_rows})),
    }
    p.executemany('INSERT INTO _meta VALUES (?,?)', meta.items())
    p.executescript(PACK_INDEXES)
    p.commit()
    p.execute('VACUUM')
    p.close()
    return {'pair': f'{a}-{b}', 'lemmas': len(lemma_ids), 'translations': len(trans),
            'examples': n_ex, 'bytes': path.stat().st_size}


AUX = ROOT / 'lexicon-aux.sqlite3'


def build_aux() -> None:
    """One-time helper table: every sense with its lemma's language, indexed
    by (lang, concept). Built once per master DB (a few minutes)."""
    if AUX.exists() and AUX.stat().st_mtime > MASTER.stat().st_mtime:
        return
    if AUX.exists():
        AUX.unlink()
    log('building aux sense/lang index')
    db = sqlite3.connect(f'file:{AUX.as_posix()}', uri=True)  # uri: ATTACH below uses ?mode=ro
    db.executescript('PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;')
    db.execute(f"ATTACH DATABASE 'file:{MASTER.as_posix()}?mode=ro' AS m")
    db.executescript("""
      CREATE TABLE sl AS
        SELECT l.lang AS lang, s.concept_id AS concept_id, s.lemma_id AS lemma_id,
               s.confidence AS confidence, s.source_id AS source_id
        FROM m.sense s JOIN m.lemma l ON l.id = s.lemma_id
        WHERE s.concept_id IS NOT NULL;
      CREATE INDEX sl_lang_concept ON sl(lang, concept_id);
    """)
    db.commit()
    db.close()
    log('aux index ready')


def worker(args: tuple) -> list:
    pairs, version = args
    m = sqlite3.connect(f'file:{MASTER}?mode=ro', uri=True)
    m.execute('PRAGMA temp_store = MEMORY')
    m.execute(f"ATTACH DATABASE 'file:{AUX.as_posix()}?mode=ro' AS aux")
    report = []
    for a, b in pairs:
        r = export_pair(m, a, b, version)
        if r:
            path = OUT / f'pack-{a}-{b}.sqlite3'
            _, r['lemmas'], _, r['translations'] = prune_pack(path)
            r['bytes'] = path.stat().st_size
            report.append(r)
            log(f"{r['pair']}: {r['lemmas']:,} lemmas, {r['translations']:,} trans, "
                f"{r['examples']:,} examples, {r['bytes'] / 1e6:.1f} MB")
        else:
            log(f'{a}-{b}: no translations, skipped')
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--pairs', help='comma list like en-tr,de-tr (default: all)')
    ap.add_argument('--version', default=date.today().strftime('%Y.%m.1'))
    ap.add_argument('--jobs', type=int, default=os.cpu_count() or 1)
    ap.add_argument('--skip-existing', action='store_true',
                    help='keep packs already in packs/ (resume an interrupted run)')
    args = ap.parse_args()
    pairs = ([tuple(sorted(p.split('-'))) for p in args.pairs.split(',')] if args.pairs
             else list(combinations(sorted(SUPPORTED_LANGS), 2)))
    if args.skip_existing:
        pairs = [(a, b) for a, b in pairs if not (OUT / f'pack-{a}-{b}.sqlite3').exists()]

    build_aux()
    OUT.mkdir(exist_ok=True)
    chunks = [(pairs[i::args.jobs], args.version) for i in range(args.jobs)]
    with multiprocessing.Pool(args.jobs) as pool:
        report = [r for part in pool.map(worker, chunks) for r in part]
    (OUT / 'report.json').write_text(json.dumps(report, indent=1))
    log(f'exported {len(report)} packs, {sum(r["bytes"] for r in report) / 1e9:.2f} GB')


if __name__ == '__main__':
    main()
