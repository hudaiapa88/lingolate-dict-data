"""Build per-pair example-sentence databases from Tatoeba.

Input  (sources_cache/tatoeba/*.tar.bz2, from https://downloads.tatoeba.org/exports/):
  sentences_detailed  id, lang (ISO 639-3), text, username, added, modified
  links               sentence_id, translation_id (both directions listed)
  tags                sentence_id, tag
  user_languages      lang, level (0-5, 5 = native), username, details
  sentences_CC0       ids of sentences released under CC0 (rest: CC BY 2.0 FR)

Output: examples/{a}-{b}.sqlite3 (a < b alphabetically, one file serves both
directions) with table `pair` and `_meta` (license + attribution).

Quality rules:
  - drop sentences tagged as needing review (@needs native check, @change, ...)
  - length 4..120 chars; 2..18 words for space-delimited scripts
  - no URLs / markup
  - score: author is a native speaker of that language (+2 each side),
    shorter sentences preferred (learner-friendly), CC0 side bonus (+0.1)
  - at most MAX_PAIRS per language pair, highest score first
  - one translation per source sentence (best scored) to keep variety
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sqlite3
import sys
import tarfile
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'sources_cache' / 'tatoeba'
OUT = ROOT / 'examples'

ISO3_TO_APP = {
    'ara': 'ar', 'deu': 'de', 'eng': 'en', 'spa': 'es', 'fra': 'fr',
    'hin': 'hi', 'ind': 'id', 'ita': 'it', 'jpn': 'ja', 'kor': 'ko',
    'nld': 'nl', 'pol': 'pl', 'por': 'pt', 'rus': 'ru', 'swe': 'sv',
    'tha': 'th', 'tur': 'tr', 'ukr': 'uk', 'vie': 'vi', 'cmn': 'zh',
    'bul': 'bg', 'ces': 'cs', 'dan': 'da', 'ell': 'el', 'fin': 'fi',
    'lit': 'lt', 'nob': 'no',
}
NO_SPACE_SCRIPTS = {'ja', 'zh', 'th'}
BAD_TAGS = {
    '@needs native check', '@change', '@check', '@delete', '@fixme',
    '@translation check', 'needs native check', '@needs review',
}
MAX_PAIRS = 20000
MIN_PAIRS = 300  # skip pairs too small to be useful
PIVOT_BELOW = 5000  # add English-pivot pairs only where direct data is thin
PIVOT_PENALTY = 0.5
URL_RE = re.compile(r'https?://|www\.|<[^>]+>')

LICENSE = 'CC-BY-2.0-FR'
ATTRIBUTION = (
    'Example sentences from Tatoeba (https://tatoeba.org), licensed under '
    'CC BY 2.0 FR (some sentences CC0). Sentence ids are kept for attribution.'
)

csv.field_size_limit(10**7)


def read_tsv(name: str):
    path = CACHE / f'{name}.tar.bz2'
    with tarfile.open(path, 'r:bz2') as tar:
        member = next(m for m in tar.getmembers() if m.isfile())
        f = tar.extractfile(member)
        assert f is not None
        reader = csv.reader(io.TextIOWrapper(f, encoding='utf-8'), delimiter='\t',
                            quoting=csv.QUOTE_NONE)
        yield from reader


def good_text(lang: str, text: str) -> bool:
    if not 4 <= len(text) <= 120 or URL_RE.search(text):
        return False
    if lang not in NO_SPACE_SCRIPTS:
        n = len(text.split())
        if not 2 <= n <= 18:
            return False
    return True


NAME_RE = re.compile(r'\b(?:Tom|Mary)\b')


def length_score(lang: str, text: str) -> float:
    """Peaks at ~7 words (~14 chars for CJK/Thai): long enough to show the
    word in context, short enough for a flashcard. 2-word sentences such as
    "He ran." used to win on brevity alone."""
    if lang in NO_SPACE_SCRIPTS:
        n, ideal, span = len(text), 14, 20
    else:
        n, ideal, span = len(text.split()), 7, 10
    return max(0.0, 1.0 - abs(n - ideal) / span)


def norm(text: str) -> str:
    return text.casefold()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-pairs', type=int, default=MAX_PAIRS)
    ap.add_argument('--only', help='comma list of pairs like en-tr,de-tr')
    args = ap.parse_args()

    print('reading native speakers...', file=sys.stderr)
    native: dict[str, set[str]] = defaultdict(set)  # app lang -> usernames
    for row in read_tsv('user_languages'):
        if len(row) >= 3 and row[0] in ISO3_TO_APP and row[1] == '5':
            native[ISO3_TO_APP[row[0]]].add(row[2])

    print('reading tags...', file=sys.stderr)
    bad: set[int] = set()
    for row in read_tsv('tags'):
        if len(row) >= 2 and row[1].strip().lower() in BAD_TAGS:
            bad.add(int(row[0]))

    print('reading CC0 ids...', file=sys.stderr)
    cc0: set[int] = set()
    for row in read_tsv('sentences_CC0'):
        if row and row[0].isdigit():
            cc0.add(int(row[0]))

    print('reading sentences...', file=sys.stderr)
    sent: dict[int, tuple[str, str, float]] = {}  # id -> (lang, text, score)
    for row in read_tsv('sentences_detailed'):
        if len(row) < 4:
            continue
        lang = ISO3_TO_APP.get(row[1])
        if lang is None:
            continue
        sid = int(row[0])
        text = row[2].strip()
        if sid in bad or not good_text(lang, text):
            continue
        score = 2.0 if row[3] in native[lang] else 0.0
        score += length_score(lang, text)
        if NAME_RE.search(text):  # Tatoeba's ubiquitous Tom/Mary sentences
            score -= 0.3
        if sid in cc0:
            score += 0.1
        sent[sid] = (lang, text, score)
    print(f'  kept {len(sent):,} sentences', file=sys.stderr)

    only = set(args.only.split(',')) if args.only else None
    print('reading links...', file=sys.stderr)
    # pair key -> best candidate per source id
    best: dict[str, dict[int, tuple[float, int, int]]] = defaultdict(dict)
    for row in read_tsv('links'):
        if len(row) < 2:
            continue
        a_id, b_id = int(row[0]), int(row[1])
        sa, sb = sent.get(a_id), sent.get(b_id)
        if sa is None or sb is None or sa[0] == sb[0]:
            continue
        la, lb = sa[0], sb[0]
        if la > lb:  # canonical order; the reverse link is also listed
            continue
        key = f'{la}-{lb}'
        if only and key not in only:
            continue
        score = sa[2] + sb[2]
        cur = best[key].get(a_id)
        if cur is None or score > cur[0]:
            best[key][a_id] = (score, a_id, b_id)

    # ── English pivot for weak pairs: X ↔ en ↔ Y ("indirect translations" in
    # Tatoeba's own UI). Only fills pairs with < PIVOT_BELOW direct pairs. ──
    print('building English pivot...', file=sys.stderr)
    en_links: dict[int, list[int]] = defaultdict(list)  # en id -> non-en ids
    for row in read_tsv('links'):
        if len(row) < 2:
            continue
        a_id, b_id = int(row[0]), int(row[1])
        sa, sb = sent.get(a_id), sent.get(b_id)
        if sa and sb and sa[0] == 'en' and sb[0] != 'en':
            en_links[a_id].append(b_id)
    langs = sorted(set(ISO3_TO_APP.values()) - {'en'})
    weak = {
        f'{x}-{y}' for i, x in enumerate(langs) for y in langs[i + 1:]
        if len(best.get(f'{x}-{y}', {})) < PIVOT_BELOW
        and (not only or f'{x}-{y}' in only)
    }
    indirect: set[tuple[int, int]] = set()
    for en_id, others in en_links.items():
        by_lang: dict[str, int] = {}
        for oid in others:  # first (any) translation per language
            by_lang.setdefault(sent[oid][0], oid)
        items = sorted(by_lang.items())
        for i, (la, a_id) in enumerate(items):
            for lb, b_id in items[i + 1:]:
                key = f'{la}-{lb}'
                if key not in weak:
                    continue
                score = sent[a_id][2] + sent[b_id][2] - PIVOT_PENALTY
                cur = best[key].get(a_id)
                if cur is None or score > cur[0]:
                    best[key][a_id] = (score, a_id, b_id)
                    indirect.add((a_id, b_id))

    OUT.mkdir(exist_ok=True)
    built = 0
    for key, cands in sorted(best.items()):
        rows = sorted(cands.values(), reverse=True)
        # one use per target sentence too
        seen_b: set[int] = set()
        picked = []
        for score, a_id, b_id in rows:
            if b_id in seen_b:
                continue
            seen_b.add(b_id)
            picked.append((score, a_id, b_id))
            if len(picked) >= args.max_pairs:
                break
        if len(picked) < MIN_PAIRS:
            continue
        la, lb = key.split('-')
        path = OUT / f'{key}.sqlite3'
        if path.exists():
            path.unlink()
        db = sqlite3.connect(path)
        db.executescript(
            """
            PRAGMA journal_mode = OFF;
            CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE pair (
                id      INTEGER PRIMARY KEY,
                a       TEXT NOT NULL,   -- sentence in language A (file name order)
                b       TEXT NOT NULL,   -- its translation in language B
                a_norm  TEXT NOT NULL,   -- casefolded, for LIKE lookups
                b_norm  TEXT NOT NULL,
                a_src   INTEGER NOT NULL, -- Tatoeba sentence ids (attribution)
                b_src   INTEGER NOT NULL,
                score   REAL NOT NULL,
                via_en  INTEGER NOT NULL DEFAULT 0  -- 1 = indirect (X↔en↔Y)
            );
            """
        )
        db.executemany(
            'INSERT INTO pair (a, b, a_norm, b_norm, a_src, b_src, score, via_en) '
            'VALUES (?,?,?,?,?,?,?,?)',
            [
                (sent[a][1], sent[b][1], norm(sent[a][1]), norm(sent[b][1]), a, b,
                 round(s, 3), 1 if (a, b) in indirect else 0)
                for s, a, b in picked
            ],
        )
        meta = {
            'schema': 'examples-v1',
            'pair': key,
            'source': 'Tatoeba',
            'license': LICENSE,
            'attribution': ATTRIBUTION,
            'generated': date.today().isoformat(),
            'entry_count': str(len(picked)),
            'indirect_count': str(sum(1 for _, a, b in picked if (a, b) in indirect)),
        }
        db.executemany('INSERT INTO _meta VALUES (?, ?)', meta.items())
        db.commit()
        db.execute('VACUUM')
        db.close()
        built += 1
        print(f'{key}: {len(picked):,} pairs, {path.stat().st_size / 1e6:.1f} MB', file=sys.stderr)
    print(f'built {built} pair files', file=sys.stderr)


if __name__ == '__main__':
    main()
