"""Build the master lexicon database (schema v3, see SCHEMA_V3.md).

Merges, with provenance + confidence per row:
  - Open Multilingual Wordnet 1.4 (commercial-safe lexicons only, config.OMW_LANGS)
  - TUFS Basic Vocabulary (CC-BY 4.0)
  - PanLex (CC0) — filtered, low base confidence; only fills gaps

Output: lexicon.sqlite3 at the repo root (gitignored; packs are exported
from it by export_packs.py).

Usage:  python scripts/build_lexicon.py [--no-panlex] [--langs tr,en,...]
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import LICENSES, OMW_LANGS, SUPPORTED_LANGS  # noqa: E402
from convert_omw_to_sqlite import parse_wordnet  # noqa: E402
from download_panlex import PANLEX_LANG_MAP  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'sources_cache'
OUT = ROOT / 'lexicon.sqlite3'

BASE_CONF = {'tufs': 0.95, 'omw': 0.90, 'panlex': 0.40}

PANLEX_LANGS = {iso3: lang for lang, iso3 in PANLEX_LANG_MAP.items()}  # iso3 -> app code
PANLEX_MAX_WORDS = 4
PANLEX_MAX_CHARS = 40
PANLEX_MAX_PER_MEANING = 8  # >8 expressions of one language in one meaning = noise

# WN-LMF partOfSpeech → short tag
POS = {'n': 'n', 'v': 'v', 'a': 'a', 's': 'a', 'r': 'r', 'c': 'c', 'p': 'p',
       'x': 'x', 'u': 'u'}

SCHEMA = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;
CREATE TABLE source (
  id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  license TEXT NOT NULL, url TEXT NOT NULL, attribution TEXT NOT NULL,
  base_conf REAL NOT NULL);
CREATE TABLE concept (
  id INTEGER PRIMARY KEY, ili TEXT UNIQUE, qid TEXT, panlex INTEGER UNIQUE,
  pos TEXT, gloss_en TEXT);
CREATE TABLE lemma (
  id INTEGER PRIMARY KEY, lang TEXT NOT NULL, written TEXT NOT NULL,
  norm TEXT NOT NULL, pos TEXT, gender TEXT, ipa TEXT, freq INTEGER,
  UNIQUE (lang, written, pos));
CREATE TABLE sense (
  id INTEGER PRIMARY KEY, lemma_id INTEGER NOT NULL, concept_id INTEGER,
  source_id INTEGER NOT NULL, confidence REAL NOT NULL, definition TEXT,
  UNIQUE (lemma_id, concept_id, source_id));
CREATE TABLE form (
  lemma_id INTEGER NOT NULL, form TEXT NOT NULL, norm TEXT NOT NULL,
  tags TEXT, source_id INTEGER NOT NULL);
CREATE TABLE sense_example (
  sense_id INTEGER NOT NULL, text TEXT NOT NULL, source_id INTEGER NOT NULL);
"""

INDEXES = """
CREATE INDEX sense_concept ON sense(concept_id);
CREATE INDEX sense_lemma ON sense(lemma_id);
CREATE INDEX lemma_lang ON lemma(lang, norm);
CREATE INDEX form_lemma ON form(lemma_id);
"""


def log(msg: str) -> None:
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', file=sys.stderr, flush=True)


class Builder:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.ili_concept: dict[str, int] = {}
        self.panlex_concept: dict[int, int] = {}
        self.lemma_ids: dict[tuple[str, str, str | None], int] = {}

    # ── sources ───────────────────────────────────────────────────────────
    def source(self, key: str, name: str, license_id: str, url: str, kind: str) -> int:
        lic = LICENSES.get(license_id)
        if lic is not None and not lic.commercial:
            raise SystemExit(f'refusing non-commercial license {license_id} for {key}')
        attribution = f'{name} — {lic.name if lic else license_id} ({url})'
        cur = self.db.execute(
            'INSERT INTO source (key, name, license, url, attribution, base_conf) '
            'VALUES (?,?,?,?,?,?)',
            (key, name, license_id, url, attribution, BASE_CONF[kind]))
        return cur.lastrowid

    # ── helpers ───────────────────────────────────────────────────────────
    def concept_for_ili(self, ili: str, pos: str | None) -> int:
        cid = self.ili_concept.get(ili)
        if cid is None:
            cid = self.db.execute(
                'INSERT INTO concept (ili, pos) VALUES (?, ?)', (ili, pos)).lastrowid
            self.ili_concept[ili] = cid
        return cid

    def lemma(self, lang: str, written: str, pos: str | None, ipa: str | None = None) -> int:
        key = (lang, written, pos)
        lid = self.lemma_ids.get(key)
        if lid is None:
            lid = self.db.execute(
                'INSERT INTO lemma (lang, written, norm, pos, ipa) VALUES (?,?,?,?,?)',
                (lang, written, written.casefold(), pos, ipa)).lastrowid
            self.lemma_ids[key] = lid
        elif ipa:
            self.db.execute('UPDATE lemma SET ipa = coalesce(ipa, ?) WHERE id = ?', (ipa, lid))
        return lid

    # ── wordnets (OMW, TUFS) ──────────────────────────────────────────────
    def add_wordnet(self, xml: Path, lang: str, source_id: int, conf: float) -> None:
        d = parse_wordnet(xml)
        synsets = {s['id']: s for s in d['synsets']}
        n_sense = 0
        for s in d['synsets']:
            if s['ili'] and lang == 'en' and s['definition']:
                cid = self.concept_for_ili(s['ili'], POS.get(s['pos']))
                self.db.execute(
                    'UPDATE concept SET gloss_en = coalesce(gloss_en, ?) WHERE id = ?',
                    (s['definition'], cid))
        for e in d['entries']:
            wf = e['written_form'].strip()
            if not wf:
                continue
            pos = POS.get(e['pos'], e['pos'] or None)
            lid = self.lemma(lang, wf, pos, e['pronunciation'])
            for f in e['forms']:
                self.db.execute(
                    'INSERT INTO form (lemma_id, form, norm, tags, source_id) VALUES (?,?,?,?,?)',
                    (lid, f['form'], f['form'].casefold(), ';'.join(t for t in f['tags'] if t) or None,
                     source_id))
            for sn in e['senses']:
                ss = synsets.get(sn['synset'])
                if ss is None or not ss['ili']:
                    continue  # no inter-lingual link → cannot translate
                cid = self.concept_for_ili(ss['ili'], POS.get(ss['pos']))
                cur = self.db.execute(
                    'INSERT OR IGNORE INTO sense (lemma_id, concept_id, source_id, confidence, definition) '
                    'VALUES (?,?,?,?,?)', (lid, cid, source_id, conf, ss['definition']))
                if cur.rowcount:
                    n_sense += 1
                    sid = cur.lastrowid
                    for ex in (sn['examples'] or ss['examples'])[:2]:
                        self.db.execute(
                            'INSERT INTO sense_example (sense_id, text, source_id) VALUES (?,?,?)',
                            (sid, ex, source_id))
        log(f'  {xml.name}: {len(d["entries"]):,} entries, {n_sense:,} senses')

    # ── PanLex ────────────────────────────────────────────────────────────
    def add_panlex(self, iso3: str, lang: str, source_id: int) -> None:
        path = CACHE / 'panlex' / iso3 / 'data.tsv'
        by_meaning: dict[int, list[str]] = defaultdict(list)
        with path.open(encoding='utf-8', errors='replace') as f:
            for row in csv.DictReader(f, delimiter='\t'):
                txt = (row.get('txt') or '').strip()
                m = row.get('meaning')
                if not txt or not m or len(txt) > PANLEX_MAX_CHARS:
                    continue
                if len(txt.split()) > PANLEX_MAX_WORDS:
                    continue
                by_meaning[int(m)].append(txt)
        kept = 0
        rows = []
        for m, words in by_meaning.items():
            words = list(dict.fromkeys(words))
            if len(words) > PANLEX_MAX_PER_MEANING:
                continue
            cid = self.panlex_concept.get(m)
            if cid is None:
                cid = self.db.execute('INSERT INTO concept (panlex) VALUES (?)', (m,)).lastrowid
                self.panlex_concept[m] = cid
            for w in words:
                rows.append((self.lemma(lang, w, None), cid, source_id, BASE_CONF['panlex']))
            kept += 1
        self.db.executemany(
            'INSERT OR IGNORE INTO sense (lemma_id, concept_id, source_id, confidence) VALUES (?,?,?,?)',
            rows)
        log(f'  panlex {iso3}: {kept:,} meanings, {len(rows):,} senses')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-panlex', action='store_true')
    ap.add_argument('--langs', help='comma list (default: all supported)')
    args = ap.parse_args()
    langs = set(args.langs.split(',')) if args.langs else set(SUPPORTED_LANGS)

    if OUT.exists():
        OUT.unlink()
    db = sqlite3.connect(OUT)
    db.executescript(SCHEMA)
    b = Builder(db)

    # English first: it carries the gloss_en for every ILI concept.
    order = sorted(langs, key=lambda l: (l != 'en', l))

    log('OMW wordnets')
    for lang in order:
        omw = OMW_LANGS.get(lang)
        if omw is None:
            continue
        xml = CACHE / 'omw' / omw.omw_id / f'{omw.omw_id}.xml'
        if not xml.exists():
            log(f'  missing {xml} (run download_omw.py)')
            continue
        sid = b.source(omw.omw_id, omw.label, omw.license_id, omw.url, 'omw')
        b.add_wordnet(xml, lang, sid, BASE_CONF['omw'])

    log('TUFS basic vocabulary')
    for lang in order:
        d = CACHE / 'tufs' / lang
        xmls = sorted(d.glob('tufs-*.xml')) if d.exists() else []
        if not xmls:
            continue
        sid = b.source(f'tufs-{lang}', f'TUFS Basic Vocabulary ({lang})', 'CC-BY-4.0',
                       'https://github.com/omwn/tufs', 'tufs')
        b.add_wordnet(xmls[-1], lang, sid, BASE_CONF['tufs'])

    if not args.no_panlex:
        log('PanLex (filtered)')
        sid = b.source('panlex', 'PanLex', 'CC0-1.0',
                       'https://huggingface.co/datasets/cointegrated/panlex-meanings', 'panlex')
        for iso3, lang in sorted(PANLEX_LANGS.items()):
            if lang in langs and (CACHE / 'panlex' / iso3 / 'data.tsv').exists():
                b.add_panlex(iso3, lang, sid)
                db.commit()

    log('indexing')
    db.executescript(INDEXES)
    db.commit()
    for t in ('concept', 'lemma', 'sense', 'form', 'sense_example'):
        log(f'  {t}: {db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]:,}')
    db.execute('VACUUM')
    db.close()
    log(f'done: {OUT} ({OUT.stat().st_size / 1e6:.0f} MB)')


if __name__ == '__main__':
    main()
