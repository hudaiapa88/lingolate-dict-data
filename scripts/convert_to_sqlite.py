"""Convert cached upstream TSV files into the Lingolate v2 SQLite schema.

Schema (see SCHEMA.md):
    _meta        — version, source, license, pair, generated date
    headword     — source-language word, pos, gender, pronunciation, frequency
    translation  — trans_list, sense, example, score, importance, is_verified
    form         — inflected forms with grammatical metadata
    synonym      — same-language synonyms

The upstream open-dict-data files are tab-separated `source\\ttarget` pairs with
no POS/gender/pronunciation metadata. We populate headword + translation; the
form/synonym tables are left empty (enriched later from Kaikki.org/PanLex).

For pair (src, tgt):
  - If src is the pivot (e.g. en-tr, pivot=en), the file is {tgt}-en_wiki.txt
    and we swap columns to get en->tr.
  - Otherwise the file is {src}-{pivot}_wiki.txt and we use it forward.

Usage:
    python convert_to_sqlite.py                         # all cached pairs
    python convert_to_sqlite.py --pairs en-tr,de-tr
    python convert_to_sqlite.py --pair tr-en
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

from config import SOURCES, dedupe_pairs, planned_pairs
from download_sources import cache_path

OUT_DIR = Path(__file__).resolve().parent.parent / "dictionaries"
SCHEMA_VERSION = "2"


def normalize(text: str) -> str:
    """Lowercase + strip diacritics for case-insensitive matching.

    'Hello' -> 'hello', 'café' -> 'cafe', 'München' -> 'munchen'.
    This matches the app's `LOWER(written_rep) COLLATE NOCASE` behavior but
    also handles non-ASCII diacritics that NOCASE misses.
    """
    text = text.strip().lower()
    # NFD decomposition then strip combining marks (diacritics)
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def read_tsv(path: Path) -> list[tuple[str, str]]:
    """Read a tab-separated source\\ttarget file."""
    rows: list[tuple[str, str]] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if not line or "\t" not in line:
            continue
        src, tgt = line.split("\t", 1)
        rows.append((src, tgt))
    return rows


def group_translations(
    rows: list[tuple[str, str]],
) -> dict[str, list[str]]:
    """Group (src, tgt) pairs by source word; dedupe targets preserving order."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for src, tgt in rows:
        src = src.strip()
        tgt = tgt.strip()
        if not src or not tgt:
            continue
        grouped[src].append(tgt)
    # Dedupe each list
    return {src: list(dict.fromkeys(tgts)) for src, tgts in grouped.items()}


def write_sqlite(
    out_path: Path,
    grouped: dict[str, list[str]],
    pair: str,
    source_id: str,
    data_version: str,
) -> int:
    """Write the Lingolate v2 schema DB. Returns headword count."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    source = SOURCES.get(source_id)
    license_id = source.license if source else "unknown"
    attribution = source.attribution if source else ""

    conn = sqlite3.connect(str(out_path))
    try:
        # Build-time pragmas — read-only DB, no journal, compact page size
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA page_size = 4096")
        conn.execute("PRAGMA synchronous = OFF")

        # ── Schema ──────────────────────────────────────────────
        conn.executescript(
            """
            CREATE TABLE _meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE headword (
                id            INTEGER PRIMARY KEY,
                written_rep   TEXT NOT NULL,
                normalized    TEXT NOT NULL,
                pos           TEXT,
                gender        TEXT,
                pronunciation TEXT,
                frequency     INTEGER DEFAULT 0
            );

            CREATE TABLE translation (
                id           INTEGER PRIMARY KEY,
                headword_id  INTEGER NOT NULL,
                trans_list   TEXT NOT NULL,
                sense        TEXT,
                example      TEXT,
                score        REAL DEFAULT 0,
                importance   REAL DEFAULT 0,
                is_verified  INTEGER DEFAULT 0,
                FOREIGN KEY (headword_id) REFERENCES headword(id)
            );

            CREATE TABLE form (
                headword_id  INTEGER NOT NULL,
                form         TEXT NOT NULL,
                form_type    TEXT,
                pos          TEXT,
                tense        TEXT,
                person       TEXT,
                number       TEXT,
                gram_case    TEXT,
                mood         TEXT,
                FOREIGN KEY (headword_id) REFERENCES headword(id)
            );

            CREATE TABLE synonym (
                headword_id  INTEGER NOT NULL,
                synonym      TEXT NOT NULL,
                FOREIGN KEY (headword_id) REFERENCES headword(id)
            );

            -- FTS5 for fuzzy search (sqflite supports it on mobile)
            CREATE VIRTUAL TABLE headword_fts USING fts5(
                written_rep,
                content='headword',
                content_rowid='id',
                tokenize='unicode61'
            );

            CREATE TRIGGER headword_ai AFTER INSERT ON headword BEGIN
                INSERT INTO headword_fts(rowid, written_rep)
                VALUES (new.id, new.written_rep);
            END;
            """
        )

        # ── Data insert ─────────────────────────────────────────
        # Batch insert headwords + translations
        headword_rows: list[tuple[str, str]] = []
        translation_rows: list[tuple[int, str]] = []

        # Sort by word for predictable rowid assignment + better cache locality
        sorted_words = sorted(grouped.keys(), key=lambda w: normalize(w))

        for idx, word in enumerate(sorted_words, start=1):
            norm = normalize(word)
            targets = grouped[word]
            trans_list = ", ".join(targets)
            headword_rows.append((word, norm))
            translation_rows.append((idx, trans_list))

        # Insert headwords (triggers auto-populate FTS)
        conn.executemany(
            "INSERT INTO headword (written_rep, normalized) VALUES (?, ?)",
            headword_rows,
        )

        # Insert translations (one per headword — open-dict-data has no sense info)
        conn.executemany(
            "INSERT INTO translation (headword_id, trans_list) VALUES (?, ?)",
            translation_rows,
        )

        # ── Metadata ────────────────────────────────────────────
        meta_rows = [
            ("schema_version", SCHEMA_VERSION),
            ("data_version", data_version),
            ("pair", pair),
            ("source", source_id),
            ("license", license_id),
            ("attribution", attribution),
            ("generated", dt.date.today().isoformat()),
            ("headword_count", str(len(sorted_words))),
        ]
        conn.executemany(
            "INSERT INTO _meta (key, value) VALUES (?, ?)",
            meta_rows,
        )

        # ── Indexes (after data for faster build) ───────────────
        conn.executescript(
            """
            CREATE INDEX idx_headword_normalized ON headword(normalized);
            CREATE INDEX idx_form_form ON form(form COLLATE NOCASE);
            CREATE INDEX idx_form_headword_id ON form(headword_id);
            CREATE INDEX idx_translation_headword_id ON translation(headword_id);
            CREATE INDEX idx_translation_score
                ON translation(is_verified DESC, score DESC, importance DESC);
            CREATE INDEX idx_synonym_headword_id ON synonym(headword_id);
            """
        )

        # ── Optimize file size ──────────────────────────────────
        conn.execute("VACUUM")
        conn.commit()

        return len(sorted_words)
    finally:
        conn.close()


def convert_pair(
    src: str, tgt: str, source_id: str, data_version: str
) -> tuple[Path, int] | None:
    """Build <src>-<tgt>.sqlite3 from the cached upstream file."""
    pivot = source_id.rsplit("-", 1)[-1]
    if src == pivot:
        file_src, file_tgt = tgt, src  # file is {tgt}-{pivot}_wiki.txt
        swap = True
    else:
        file_src, file_tgt = src, tgt  # file is {src}-{pivot}_wiki.txt
        swap = False

    cached = cache_path(source_id, file_src, file_tgt)
    if not cached.exists() or cached.stat().st_size == 0:
        print(f"  [skip]   {src}-{tgt}: no cached file at {cached.name}")
        return None

    print(f"  [build]  {src}-{tgt} <- {cached.name}{' (swapped)' if swap else ''}")
    raw = read_tsv(cached)
    if swap:
        raw = [(t, s) for (s, t) in raw]
    grouped = group_translations(raw)
    if not grouped:
        print(f"  [empty]  {src}-{tgt}: no entries after grouping")
        return None

    out_path = OUT_DIR / f"{src}-{tgt}.sqlite3"
    pair = f"{src}-{tgt}"
    count = write_sqlite(out_path, grouped, pair, source_id, data_version)
    size = out_path.stat().st_size
    print(f"  [ok]     {src}-{tgt}: {count} headwords, {size:,} bytes")
    return out_path, count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", help="Comma-separated src-tgt list (default: all planned)")
    parser.add_argument("--pair", help="Single src-tgt pair")
    parser.add_argument(
        "--data-version",
        default="2026.08.1",
        help="Data version to embed in _meta (default: 2026.08.1)",
    )
    args = parser.parse_args()

    pairs = planned_pairs()
    if args.pair:
        s, t = args.pair.split("-")
        pairs = [
            (s, t, sid)
            for (ss, tt, sid) in planned_pairs()
            if ss == s and tt == t
        ]
    if args.pairs:
        wanted = {tuple(p.strip().split("-")) for p in args.pairs.split(",") if p.strip()}
        pairs = [(s, t, sid) for (s, t, sid) in pairs if (s, t) in wanted]
    pairs = dedupe_pairs(pairs)

    print(f"Converting {len(pairs)} pairs -> {OUT_DIR.name}/ (schema v{SCHEMA_VERSION})")
    built = 0
    for src, tgt, sid in pairs:
        result = convert_pair(src, tgt, sid, args.data_version)
        if result is not None:
            built += 1
    print(f"\nBuilt {built}/{len(pairs)} dictionaries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
