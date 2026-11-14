"""Enrich an existing lexicon.sqlite3 in place (SCHEMA_V3.md) with:

  wikidata  Wikidata Lexemes (CC0): lemmas, inflected forms with grammatical
            tags, IPA, gender, sense definitions, cross-language sense links
            (P5972 translation, P5137 item, P8814 WordNet 3.1 → ILI) and usage
            examples. Senses that share a meaning join the same concept, so
            they become translations of every other wordnet/PanLex lemma there.
  ipa       Pronunciation for lemmas still without one: espeak-ng (via
            phonemizer) for alphabetic languages, pinyin for Chinese and a
            hiragana reading for Japanese words written with kanji, for every
            lemma that can reach a pack (a sense with confidence >= 0.35).
  freq      Frequency rank per language from Tatoeba sentences (CC BY 2.0 FR),
            counting inflected forms towards their lemma (not abbreviations,
            and not forms that are dictionary headwords of their own).
  closed    Closed-class words (articles, adpositions, pronouns, conjunctions,
            particles) from Wikidata Lexemes. Wordnets have no such words, so
            a wordnet sense on one is noise ('à' → indium, 'I' → iodine).
  grammar   Grammar features per language from Grambank and WALS (CC BY 4.0).

Run download_extras.py first. Each step can run on its own:
    python scripts/enrich_lexicon.py --steps wikidata,ipa,freq,grammar,closed
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_tatoeba_examples import ISO3_TO_APP, NO_SPACE_SCRIPTS, read_tsv  # noqa: E402
from config import SUPPORTED_LANGS  # noqa: E402
from download_panlex import PANLEX_LANG_MAP  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'sources_cache'
MASTER = ROOT / 'lexicon.sqlite3'
LANGS = set(SUPPORTED_LANGS)
UA = {'User-Agent': 'lingolate-dict-data build (https://github.com/hudaiapa88/lingolate-dict-data)'}

WIKIDATA_CONF = 0.85
IPA_MIN_CONF = 0.35      # lemmas with a sense at or above this get a generated IPA
                         # (= the export threshold: every lemma that can reach a pack)
MAX_EXAMPLES_PER_SENSE = 3


def log(msg: str) -> None:
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', file=sys.stderr, flush=True)


def ensure_schema(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(lemma)')}
    if 'ipa_src' not in cols:
        db.execute('ALTER TABLE lemma ADD COLUMN ipa_src TEXT')
        db.execute("UPDATE lemma SET ipa_src = 'wordnet' WHERE ipa IS NOT NULL")
    # TUFS stores an audio URL in the pronunciation field — not IPA.
    db.execute("UPDATE lemma SET ipa = NULL, ipa_src = NULL WHERE ipa LIKE 'http%'")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS grammar (
        lang TEXT NOT NULL, dataset TEXT NOT NULL, param_id TEXT NOT NULL,
        param_name TEXT NOT NULL, value TEXT NOT NULL,
        PRIMARY KEY (lang, dataset, param_id));
      CREATE INDEX IF NOT EXISTS form_lemma ON form(lemma_id);
      CREATE INDEX IF NOT EXISTS concept_qid ON concept(qid);
      CREATE INDEX IF NOT EXISTS sense_lemma ON sense(lemma_id);
    """)
    db.commit()


def add_source(db, key, name, license_id, url, conf) -> int:
    row = db.execute('SELECT id FROM source WHERE key = ?', (key,)).fetchone()
    if row:
        return row[0]
    return db.execute(
        'INSERT INTO source (key, name, license, url, attribution, base_conf) VALUES (?,?,?,?,?,?)',
        (key, name, license_id, url, f'{name} — {license_id} ({url})', conf)).lastrowid


# ════════════════════════════════════════════════════════════════════════
# Wikidata Lexemes
# ════════════════════════════════════════════════════════════════════════

# Lexical category → the short POS tags used by the wordnets.
POS_Q = {
    'Q1084': 'n', 'Q147276': 'n',            # noun, proper noun
    'Q24905': 'v', 'Q34698': 'a', 'Q380057': 'r',
    'Q36224': 'p', 'Q36484': 'c',            # pronoun, conjunction
}
GENDER_Q = {'Q499327': 'm', 'Q1775415': 'f', 'Q1775461': 'n', 'Q1305037': 'c'}


class UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        p = self.parent
        root = x
        while p.get(root, root) != root:
            root = p[root]
        while p.get(x, x) != root:  # path compression
            p[x], x = root, p[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def lexemes(path: Path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        for line in f:
            line = line.strip().rstrip(',')
            if len(line) > 2:
                yield json.loads(line)


def pick_lang(reps: dict) -> tuple[str, str, str] | None:
    """(app lang, written form, representation code) of a lemma, if supported."""
    best = None
    for code, rep in reps.items():
        base = code.split('-')[0]
        base = 'no' if base == 'nb' else base
        if base not in LANGS:
            continue
        rank = 0 if code == base else (1 if code == 'zh-hans' else 2)
        if best is None or rank < best[0]:
            best = (rank, base, rep['value'], code)
    return best[1:] if best else None


def values(claims: dict, prop: str) -> list:
    out = []
    for c in claims.get(prop, ()):
        dv = c.get('mainsnak', {}).get('datavalue')
        if dv:
            out.append(dv['value'])
    return out


def qlabels(qids: set[str]) -> dict[str, str]:
    """English labels for grammatical-feature items (a few thousand ids)."""
    out: dict[str, str] = {}
    ids = sorted(qids)
    for i in range(0, len(ids), 50):
        r = requests.get('https://www.wikidata.org/w/api.php', headers=UA, timeout=60, params={
            'action': 'wbgetentities', 'ids': '|'.join(ids[i:i + 50]), 'props': 'labels',
            'languages': 'en', 'format': 'json'})
        r.raise_for_status()
        for q, e in r.json().get('entities', {}).items():
            label = e.get('labels', {}).get('en', {}).get('value')
            if label:
                out[q] = re.sub(r' (case|tense|mood|number|person)$', '', label)
    return out


def step_wikidata(db: sqlite3.Connection, force: bool) -> None:
    dump = CACHE / 'wikidata' / 'latest-lexemes.json.gz'
    if not dump.exists():
        raise SystemExit(f'missing {dump} (run download_extras.py)')
    if db.execute("SELECT 1 FROM source WHERE key = 'wikidata-lexemes'").fetchone():
        if not force:
            log('wikidata: already merged (use --force to redo)')
            return
        sid_old = db.execute("SELECT id FROM source WHERE key = 'wikidata-lexemes'").fetchone()[0]
        db.execute('DELETE FROM sense_example WHERE source_id = ?', (sid_old,))
        db.execute('DELETE FROM sense WHERE source_id = ?', (sid_old,))
        db.execute('DELETE FROM form WHERE source_id = ?', (sid_old,))
        db.commit()

    src = add_source(db, 'wikidata-lexemes', 'Wikidata Lexemes', 'CC0-1.0',
                     'https://www.wikidata.org/wiki/Wikidata:Lexicographical_data', WIKIDATA_CONF)
    ili_of_wn31: dict[str, str] = {}
    cili = CACHE / 'cili' / 'ili-map-pwn31.tab'
    if cili.exists():
        for line in cili.read_text(encoding='utf-8').splitlines():
            parts = line.split('\t')
            if len(parts) == 2:
                ili_of_wn31[parts[1].strip()] = parts[0].strip()

    uf = UnionFind()
    # (sense id, lang, lemma, definition, gloss_en, own item, own ILI)
    senses: list[tuple] = []
    examples: dict[str, list[str]] = defaultdict(list)
    forms: list[tuple] = []
    feats: set[str] = set()
    n_lex = n_new = 0

    def lemma_id(lang, written, pos, gender, ipa) -> int:
        nonlocal n_new
        norm = written.casefold()
        row = db.execute('SELECT id FROM lemma WHERE lang = ? AND norm = ? AND written = ? AND pos IS ?',
                         (lang, norm, written, pos)).fetchone()
        if row is None and pos is not None:  # PanLex lemmas carry no POS
            row = db.execute('SELECT id FROM lemma WHERE lang = ? AND norm = ? AND written = ? '
                             'AND pos IS NULL', (lang, norm, written)).fetchone()
            if row:
                db.execute('UPDATE lemma SET pos = ? WHERE id = ?', (pos, row[0]))
        if row is None:
            n_new += 1
            return db.execute(
                'INSERT INTO lemma (lang, written, norm, pos, gender, ipa, ipa_src) VALUES (?,?,?,?,?,?,?)',
                (lang, written, norm, pos, gender, ipa, 'wikidata' if ipa else None)).lastrowid
        lid = row[0]
        if gender or ipa:
            db.execute(
                "UPDATE lemma SET gender = coalesce(gender, ?), "
                "ipa_src = CASE WHEN ipa IS NULL AND ? IS NOT NULL THEN 'wikidata' ELSE ipa_src END, "
                "ipa = coalesce(ipa, ?) WHERE id = ?", (gender, ipa, ipa, lid))
        return lid

    log('wikidata: reading lexemes')
    for e in lexemes(dump):
        # Sense links count for every language: a Basque sense can still
        # bridge a Turkish and a Korean one.
        for s in e.get('senses', ()):
            sid = s['id']
            for v in values(s.get('claims', {}), 'P5972'):
                uf.union(sid, v['id'])
            # P5137 ('item for this sense') names a thing: it joins nouns
            # meaning that thing. On a verb or adjective it is only an
            # association ('have' → possession, 'senior' → old age) and
            # would make 'have' a translation of '性質'.
            if e.get('lexicalCategory') in ('Q1084', 'Q147276'):
                for v in values(s.get('claims', {}), 'P5137'):
                    uf.union(sid, v['id'])
            for v in values(s.get('claims', {}), 'P8814'):
                key = ili_of_wn31.get(v) or ili_of_wn31.get(v.replace('-s', '-a'))
                if key:
                    uf.union(sid, 'ILI:' + key)

        picked = pick_lang(e.get('lemmas', {}))
        if picked is None:
            continue
        lang, written, code = picked
        if not written or len(written) > 60:
            continue
        n_lex += 1
        claims = e.get('claims', {})
        pos = POS_Q.get(e.get('lexicalCategory'))
        gender = next((GENDER_Q[v['id']] for v in values(claims, 'P5185') if v['id'] in GENDER_Q), None)

        lemma_ipa = first_ipa = None
        form_rows = []
        for f in e.get('forms', ()):
            reps = f.get('representations', {})
            rep = reps.get(code) or next((r for c, r in reps.items() if c.split('-')[0] == code.split('-')[0]), None)
            if rep is None:
                continue
            ipa = next(iter(values(f.get('claims', {}), 'P898')), None)
            if ipa:
                first_ipa = first_ipa or ipa
                if rep['value'] == written and lemma_ipa is None:
                    lemma_ipa = ipa
            fq = f.get('grammaticalFeatures', [])
            feats.update(fq)
            form_rows.append((rep['value'], ';'.join(fq)))

        lid = lemma_id(lang, written, pos, gender, lemma_ipa or first_ipa)
        seen = set()
        for form, tags in form_rows:
            if (form, tags) not in seen:
                seen.add((form, tags))
                forms.append((lid, form, form.casefold(), tags, src))
        if len(forms) > 200_000:
            db.executemany('INSERT INTO form (lemma_id, form, norm, tags, source_id) VALUES (?,?,?,?,?)', forms)
            forms.clear()

        for s in e.get('senses', ()):
            gl = s.get('glosses', {})
            own = next((g['value'] for c, g in gl.items() if c.split('-')[0] == code.split('-')[0]), None)
            sc = s.get('claims', {})
            own_q = next((v['id'] for v in values(sc, 'P5137')), None)
            own_ili = next((ili_of_wn31.get(v) or ili_of_wn31.get(v.replace('-s', '-a'))
                            for v in values(sc, 'P8814')), None)
            senses.append((s['id'], lang, lid, own, gl.get('en', {}).get('value'), own_q, own_ili))

        for v, c in ((c['mainsnak'].get('datavalue', {}).get('value'), c) for c in claims.get('P5831', ())):
            if not isinstance(v, dict) or v.get('language', '').split('-')[0] != code.split('-')[0]:
                continue
            text = v.get('text', '').strip()
            if not text or len(text) > 200:
                continue
            for q in c.get('qualifiers', {}).get('P6072', ()):
                target = q.get('datavalue', {}).get('value', {}).get('id')
                if target and len(examples[target]) < MAX_EXAMPLES_PER_SENSE:
                    examples[target].append(text)

        if n_lex % 100_000 == 0:
            log(f'  {n_lex:,} lexemes ({n_new:,} new lemmas)')
            db.commit()
    if forms:
        db.executemany('INSERT INTO form (lemma_id, form, norm, tags, source_id) VALUES (?,?,?,?,?)', forms)
    log(f'wikidata: {n_lex:,} lexemes, {n_new:,} new lemmas, {len(senses):,} senses')

    # ── grammatical tags: item ids → English labels ──
    log(f'wikidata: labelling {len(feats):,} grammatical features')
    labels = qlabels(feats)
    rows = db.execute('SELECT rowid, tags FROM form WHERE source_id = ? AND tags != ""', (src,)).fetchall()
    db.executemany('UPDATE form SET tags = ? WHERE rowid = ?', (
        (';'.join(labels.get(q, q) for q in tags.split(';')), rid) for rid, tags in rows))
    db.commit()

    # ── senses → concepts ──
    log('wikidata: grouping senses into concepts')
    root_ili: dict[str, set] = defaultdict(set)
    root_qid: dict[str, str] = {}
    for node in list(uf.parent.keys()):
        if node.startswith('ILI:'):
            root_ili[uf.find(node)].add(node[4:])
        elif node.startswith('Q'):
            r = uf.find(node)
            if r not in root_qid or int(node[1:]) < int(root_qid[r][1:]):
                root_qid[r] = node
    per_lang: dict[str, Counter] = defaultdict(Counter)
    for s in senses:
        per_lang[uf.find(s[0])][s[1]] += 1
    # A wrong translation link chains unrelated meanings together. Such
    # components are recognisable (many senses of one language, or several
    # WordNet synsets); their senses fall back to their own item/synset.
    suspicious = {r for r, c in per_lang.items() if max(c.values()) > 15}
    suspicious |= {r for r, s in root_ili.items() if len(s) > 2}
    log(f'wikidata: {len(per_lang):,} meaning groups, {len(suspicious):,} split as suspicious')

    cache: dict[tuple, int | None] = {}

    def concept(ili: str | None, qid: str | None, anon_key: str | None) -> int | None:
        key = ('ili', ili) if ili else (('q', qid) if qid else ('anon', anon_key))
        if key in cache:
            return cache[key]
        cid = None
        if ili:
            row = db.execute('SELECT id FROM concept WHERE ili = ?', (ili,)).fetchone()
            cid = row[0] if row else db.execute(
                'INSERT INTO concept (ili, qid) VALUES (?, ?)', (ili, qid)).lastrowid
            if row and qid:
                db.execute('UPDATE concept SET qid = coalesce(qid, ?) WHERE id = ?', (qid, cid))
        elif qid:
            # qid only: 'ili IS NULL' would make SQLite pick the ili index and
            # walk all ~27M PanLex concepts.
            rows = db.execute('SELECT id, ili FROM concept WHERE qid = ?', (qid,)).fetchall()
            row = next((r for r in rows if r[1] is None), None)
            cid = row[0] if row else db.execute('INSERT INTO concept (qid) VALUES (?)', (qid,)).lastrowid
        elif anon_key is not None:
            cid = db.execute('INSERT INTO concept (qid) VALUES (NULL)').lastrowid
        cache[key] = cid
        return cid

    n_linked = n_ex = 0
    for sense_id, lang, lid, definition, gloss_en, own_q, own_ili in senses:
        root = uf.find(sense_id)
        if root in suspicious:
            cid = concept(own_ili, own_q, None)
        else:
            ilis = root_ili.get(root)
            anon = root if sum(per_lang[root].values()) >= 2 else None
            cid = concept(min(ilis) if ilis else None, root_qid.get(root), anon)
        cur = db.execute(
            'INSERT OR IGNORE INTO sense (lemma_id, concept_id, source_id, confidence, definition) '
            'VALUES (?,?,?,?,?)', (lid, cid, src, WIKIDATA_CONF, definition))
        if cid is not None:
            n_linked += 1
            if gloss_en:
                db.execute('UPDATE concept SET gloss_en = coalesce(gloss_en, ?) WHERE id = ?', (gloss_en, cid))
        if cur.rowcount and sense_id in examples:
            rid = cur.lastrowid
            db.executemany('INSERT INTO sense_example (sense_id, text, source_id) VALUES (?,?,?)',
                           ((rid, t, src) for t in examples[sense_id]))
            n_ex += len(examples[sense_id])
    db.commit()
    log(f'wikidata: {n_linked:,} senses linked to concepts, {n_ex:,} usage examples')


# ════════════════════════════════════════════════════════════════════════
# IPA
# ════════════════════════════════════════════════════════════════════════

ESPEAK_VOICE = {'en': 'en-us', 'pt': 'pt-br', 'no': 'nb'}
KANJI = re.compile(r'[一-鿿]')
WORDISH = re.compile(r'^[^\d\W][\w\s\'’.-]*$')


def step_ipa(db: sqlite3.Connection, jobs: int, min_conf: float = IPA_MIN_CONF) -> None:
    from phonemizer.backend import EspeakBackend
    from phonemizer.separator import Separator

    supported = set(EspeakBackend.supported_languages())
    sep = Separator(phone='', syllable='', word=' ')
    for lang in sorted(LANGS):
        rows = db.execute("""
          SELECT l.id, l.written FROM lemma l
          WHERE l.lang = ? AND l.ipa IS NULL AND length(l.written) <= 40
            AND EXISTS (SELECT 1 FROM sense s WHERE s.lemma_id = l.id AND s.confidence >= ?)
        """, (lang, min_conf)).fetchall()
        rows = [(i, w) for i, w in rows if len(w.split()) <= 3 and WORDISH.match(w)]
        if not rows:
            continue
        t0 = time.time()
        if lang == 'zh':
            from pypinyin import Style, lazy_pinyin
            out = [(' '.join(lazy_pinyin(w, style=Style.TONE)), 'pinyin', i) for i, w in rows]
        elif lang == 'ja':
            import pykakasi
            kks = pykakasi.kakasi()
            out = [(''.join(x['hira'] for x in kks.convert(w)), 'kana', i)
                   for i, w in rows if KANJI.search(w)]
        else:
            voice = ESPEAK_VOICE.get(lang, lang)
            if voice not in supported:
                log(f'ipa {lang}: espeak-ng has no voice "{voice}", skipped')
                continue
            backend = EspeakBackend(voice, with_stress=True, language_switch='remove-flags')
            out = []
            for k in range(0, len(rows), 50_000):
                chunk = rows[k:k + 50_000]
                ipa = backend.phonemize([w for _, w in chunk], separator=sep, strip=True, njobs=jobs)
                out.extend((p, 'espeak', i) for (i, _), p in zip(chunk, ipa))
        out = [r for r in out if r[0]]
        db.executemany('UPDATE lemma SET ipa = ?, ipa_src = ? WHERE id = ?', out)
        db.commit()
        log(f'ipa {lang}: {len(out):,} of {len(rows):,} lemmas ({time.time() - t0:.0f} s)')


# ════════════════════════════════════════════════════════════════════════
# Frequency (Tatoeba)
# ════════════════════════════════════════════════════════════════════════

TOKEN = re.compile(r"\w+(?:['’]\w+)*")
# Tatoeba's stock characters would otherwise rank among the top words.
TATOEBA_NAMES = {'tom', 'mary', 'tomu', 'tom’s', "tom's", 'mary’s', "mary's"}


def step_freq(db: sqlite3.Connection) -> None:
    log('freq: counting Tatoeba tokens')
    counts: dict[str, Counter] = defaultdict(Counter)
    for row in read_tsv('sentences_detailed'):
        if len(row) < 3:
            continue
        lang = ISO3_TO_APP.get(row[1])
        if lang is None or lang in NO_SPACE_SCRIPTS:
            continue
        counts[lang].update(t for t in TOKEN.findall(row[2].casefold()) if t not in TATOEBA_NAMES)
    for lang, c in sorted(counts.items()):
        score: Counter = Counter()
        for lid, norm in db.execute('SELECT id, norm FROM lemma WHERE lang = ?', (lang,)):
            if norm in c:
                score[lid] += c[norm]
        # Headwords a real dictionary lists (any non-PanLex sense, or a
        # function word from Wikidata): a token equal to one of them is that
        # word, not an inflected form of another ('var' is not 'varmak',
        # 'para' is not 'parar').
        real = {w for (w,) in db.execute(
            'SELECT DISTINCT l.written FROM lemma l JOIN sense s ON s.lemma_id = l.id '
            'JOIN source x ON x.id = s.source_id WHERE l.lang = ? AND x.key != ?', (lang, 'panlex'))}
        if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'closed_class'").fetchone():
            real |= {w for (w,) in db.execute('SELECT written FROM closed_class WHERE lang = ?', (lang,))}
        owners: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for lid, form, norm, tags, lwritten, lnorm in db.execute(
                'SELECT f.lemma_id, f.form, f.norm, f.tags, l.written, l.norm FROM form f '
                'JOIN lemma l ON l.id = f.lemma_id WHERE l.lang = ?', (lang,)):
            if norm == lnorm or norm not in c or form in real:
                continue
            # 'IS' (USPS abbreviation of island) is not the token 'is'
            if 'abbreviation' in (tags or '').lower() or (form != form.lower() and lwritten == lwritten.lower()):
                continue
            owners[norm].append((lid, lnorm))
        # A form shared by several words ('suis': être, suivre) goes to the
        # most frequent of them only.
        for norm, cands in owners.items():
            top = max(c.get(ln, 0) for _, ln in cands)
            for lid in {lid for lid, ln in cands if c.get(ln, 0) == top}:
                score[lid] += c[norm]
        # Rank written forms, not rows: 'o' (pronoun) and 'o' (that) share a rank.
        norm_of = dict(db.execute('SELECT id, norm FROM lemma WHERE lang = ?', (lang,)).fetchall())             if score else {}
        best: dict[str, int] = {}
        for lid, n in score.items():
            k = norm_of[lid]
            best[k] = max(best.get(k, 0), n)
        rank_of = {k: r for r, (k, n) in enumerate(
            sorted(best.items(), key=lambda kv: -kv[1]), 1) if n >= 2}
        ranked = [(rank_of[norm_of[lid]], lid) for lid in score if norm_of[lid] in rank_of]
        db.execute('UPDATE lemma SET freq = NULL WHERE lang = ?', (lang,))
        db.executemany('UPDATE lemma SET freq = ? WHERE id = ?', ranked)
        db.commit()
        log(f'freq {lang}: {len(ranked):,} ranked lemmas from {sum(c.values()):,} tokens')
    add_source(db, 'tatoeba-freq', 'Tatoeba (frequency counts)', 'CC-BY-2.0-FR',
               'https://tatoeba.org', 1.0)
    db.commit()


# ════════════════════════════════════════════════════════════════════════
# Grammar features (Grambank, WALS)
# ════════════════════════════════════════════════════════════════════════

ISO_CANDIDATES = {lang: [iso] for lang, iso in PANLEX_LANG_MAP.items()}
ISO_CANDIDATES.update({
    'ar': ['arb', 'arz', 'apc', 'ary', 'acm'],
    'zh': ['cmn'],
    'no': ['nob', 'nor', 'nno'],
    'el': ['ell'],
})


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def step_grammar(db: sqlite3.Connection) -> None:
    datasets = (('grambank', 'https://grambank.clld.org'), ('wals', 'https://wals.info'))
    # ISO 639-3 → Glottocodes, pooled from both datasets (Grambank leaves the
    # ISO column empty for many languages, e.g. Turkish).
    glotto_of_iso: dict[str, set[str]] = defaultdict(set)
    for dataset, _ in datasets:
        f = CACHE / dataset / 'languages.csv'
        if f.exists():
            for r in read_csv(f):
                isos = set((r.get('ISO_codes') or '').split()) | {r.get('ISO639P3code') or ''}
                for iso in isos - {''}:
                    if r.get('Glottocode'):
                        glotto_of_iso[iso].add(r['Glottocode'])
    for dataset, url in datasets:
        d = CACHE / dataset
        if not (d / 'values.csv').exists():
            log(f'grammar: missing {d} (run download_extras.py)')
            continue
        by_iso: dict[str, list[str]] = defaultdict(list)
        langs = read_csv(d / 'languages.csv')
        by_glotto: dict[str, list[str]] = defaultdict(list)
        for r in langs:
            if r.get('Glottocode'):
                by_glotto[r['Glottocode']].append(r['ID'])
        for r in langs:
            isos = set((r.get('ISO_codes') or '').split()) | {r.get('ISO639P3code') or ''}
            for iso in isos - {''}:
                by_iso[iso].append(r['ID'])
        for iso, glottos in glotto_of_iso.items():
            for g in glottos:
                by_iso[iso].extend(i for i in by_glotto.get(g, ()) if i not in by_iso[iso])
        params = {r['ID']: r['Name'] for r in read_csv(d / 'parameters.csv')}
        codes = {}
        for r in read_csv(d / 'codes.csv'):
            name = r.get('Name') or ''
            codes[r['ID']] = (r.get('Description') or name) if name.isdigit() else name
        vals: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for r in read_csv(d / 'values.csv'):
            if r.get('Code_ID') and r['Code_ID'] in codes:
                vals[r['Language_ID']].append((r['Parameter_ID'], codes[r['Code_ID']]))

        db.execute('DELETE FROM grammar WHERE dataset = ?', (dataset,))
        n = 0
        for lang in sorted(LANGS):
            cands = [lid for iso in ISO_CANDIDATES.get(lang, []) for lid in by_iso.get(iso, [])]
            if not cands:
                continue
            best = max(cands, key=lambda lid: len(vals.get(lid, ())))
            rows = [(lang, dataset, p, params.get(p, p), v) for p, v in vals.get(best, ())]
            db.executemany('INSERT OR REPLACE INTO grammar VALUES (?,?,?,?,?)', rows)
            n += len(rows)
        name = 'Grambank' if dataset == 'grambank' else 'WALS Online'
        add_source(db, dataset, name, 'CC-BY-4.0', url, 1.0)
        db.commit()
        log(f'grammar {dataset}: {n:,} feature values')


# ════════════════════════════════════════════════════════════════════════
# Closed-class words
# ════════════════════════════════════════════════════════════════════════

OPEN_CLASS = {'Q1084', 'Q147276', 'Q24905', 'Q34698', 'Q380057'}  # noun, proper noun, verb, adj, adv
CLOSED_LABEL = re.compile(r'\b(article|preposition|postposition|adposition|determiner|pronoun|'
                          r'conjunction|particle|circumposition|clitic)\b', re.I)


def step_closed(db: sqlite3.Connection) -> None:
    """closed_class(lang, written): headwords Wikidata only knows as function
    words. Category labels come from the API, so no hard-coded item ids."""
    dump = CACHE / 'wikidata' / 'latest-lexemes.json.gz'
    if not dump.exists():
        raise SystemExit(f'missing {dump} (run download_extras.py)')
    cats: dict[tuple[str, str], set[str]] = defaultdict(set)
    log('closed: reading lexical categories')
    for e in lexemes(dump):
        picked = pick_lang(e.get('lemmas', {}))
        if picked and e.get('lexicalCategory'):
            cats[(picked[0], picked[1])].add(e['lexicalCategory'])
    other = {q for qs in cats.values() if not qs & OPEN_CLASS for q in qs}
    labels = qlabels(other)
    closed_q = {q for q, name in labels.items() if CLOSED_LABEL.search(name)}
    log('closed categories: ' + ', '.join(sorted(labels[q] for q in closed_q)))
    rows = [k for k, qs in cats.items() if not qs & OPEN_CLASS and qs & closed_q]
    db.executescript("""
      DROP TABLE IF EXISTS closed_class;
      CREATE TABLE closed_class (lang TEXT NOT NULL, written TEXT NOT NULL,
                                 PRIMARY KEY (lang, written));""")
    db.executemany('INSERT OR IGNORE INTO closed_class VALUES (?, ?)', rows)
    db.commit()
    log(f'closed: {len(rows):,} closed-class headwords')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--steps', default='wikidata,ipa,closed,freq,grammar')
    ap.add_argument('--force', action='store_true', help='redo the wikidata merge')
    ap.add_argument('--jobs', type=int, default=os.cpu_count() or 1)
    ap.add_argument('--db', default=str(MASTER))
    ap.add_argument('--ipa-min-conf', type=float, default=IPA_MIN_CONF)
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    db.executescript('PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF; PRAGMA cache_size = -1000000;')
    ensure_schema(db)
    for step in args.steps.split(','):
        t0 = time.time()
        if step == 'wikidata':
            step_wikidata(db, args.force)
        elif step == 'ipa':
            step_ipa(db, args.jobs, args.ipa_min_conf)
        elif step == 'freq':
            step_freq(db)
        elif step == 'grammar':
            step_grammar(db)
        elif step == 'closed':
            step_closed(db)
        else:
            raise SystemExit(f'unknown step {step}')
        log(f'step {step} done in {(time.time() - t0) / 60:.1f} min')
    db.close()


if __name__ == '__main__':
    main()
