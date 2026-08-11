"""Convert PanLex TSV → Lingolate v2 SQLite (bilingual via meaning ID).

PanLex uses `meaning` as a concept identifier (like ILI in omw). Words with
the same `meaning` across languages are translations of each other.

This script builds bilingual dictionaries where:
  - src language is a PanLex gap-filler (cs, hi, lt, nl, pt, uk)
  - tgt language is a pivot (en, de, es, fr, ja, ru, tr, zh) OR another gap-filler

Strategy:
  1. Load src TSV → build meaning → [words] map
  2. Load tgt TSV → build meaning → [words] map
  3. For each shared meaning: src words (headwords) → tgt words (translations)
  4. Write v2 SQLite

Usage:
    python convert_panlex_to_sqlite.py --data-version 2026.08.1
    python convert_panlex_to_sqlite.py --pairs cs-en,nl-de
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sqlite3
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

from config import SUPPORTED_LANGS, attribution_for_pair, license_id_for_pair
from download_panlex import PANLEX_LANG_MAP, cache_path as panlex_cache_path

OUT_DIR = Path(__file__).resolve().parent.parent / "dictionaries"
SCHEMA_VERSION = "2"

# Pairs to build: gap-filler langs × all supported langs with PanLex data
GAP_FILLER_LANGS = ("cs", "hi", "lt", "nl", "pt", "uk")
PIVOT_LANGS = ("en", "de", "es", "fr", "ja", "ru", "tr", "zh",
               "ar", "ko", "vi", "it", "pl", "sv", "fi", "da",
               "no", "bg", "el", "th", "id", "ms", "is")


def normalize(text: str) -> str:
    text = text.strip().lower()
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def load_panlex(lang: str) -> dict[str, list[str]]:
    """Load PanLex TSV for a language → {meaning_id: [txt, ...]}."""
    path = panlex_cache_path(lang)
    if not path.exists():
        return {}

    meaning_to_words: dict[str, list[str]] = defaultdict(list)
    with path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            meaning = row.get("meaning", "")
            txt = row.get("txt", "")
            if meaning and txt:
                meaning_to_words[meaning].append(txt)

    # Dedupe
    for m in meaning_to_words:
        meaning_to_words[m] = list(dict.fromkeys(meaning_to_words[m]))

    return dict(meaning_to_words)


def build_bilingual(
    src_meanings: dict[str, list[str]],
    tgt_meanings: dict[str, list[str]],
) -> list[dict]:
    """Build bilingual entries via shared meaning IDs."""
    headword_map: dict[str, dict] = defaultdict(lambda: {
        "translations": [],
        "meanings": set(),
    })

    for meaning, src_words in src_meanings.items():
        tgt_words = tgt_meanings.get(meaning)
        if not tgt_words:
            continue
        for src_word in src_words:
            hw = headword_map[src_word]
            for tgt_word in tgt_words:
                hw["translations"].append(tgt_word)
            hw["meanings"].add(meaning)

    result: list[dict] = []
    for wf, data in headword_map.items():
        if not data["translations"]:
            continue
        unique_trans = list(dict.fromkeys(data["translations"]))
        # Cap at 50 translations to avoid huge rows
        if len(unique_trans) > 50:
            unique_trans = unique_trans[:50]
        trans_list = ", ".join(unique_trans)
        is_verified = 1 if data["meanings"] else 0
        importance = min(len(data["meanings"]) * 20, 100)
        result.append({
            "written_form": wf,
            "normalized": normalize(wf),
            "pos": None,
            "pronunciation": None,
            "forms": [],
            "trans_list": trans_list,
            "sense": None,
            "example": None,
            "score": 30.0,  # PanLex is lower quality than omw (no POS/sense)
            "importance": float(importance),
            "is_verified": is_verified,
        })
    return result


def write_sqlite(
    out_path: Path, entries: list[dict], pair: str,
    src_lang: str, tgt_lang: str,
    license_id: str, attribution: str, data_version: str,
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    conn = sqlite3.connect(str(out_path))
    try:
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA page_size = 4096")
        conn.execute("PRAGMA synchronous = OFF")

        conn.executescript("""
            CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE headword (
                id INTEGER PRIMARY KEY, written_rep TEXT NOT NULL,
                normalized TEXT NOT NULL, pos TEXT, gender TEXT,
                pronunciation TEXT, frequency INTEGER DEFAULT 0
            );
            CREATE TABLE translation (
                id INTEGER PRIMARY KEY, headword_id INTEGER NOT NULL,
                trans_list TEXT NOT NULL, sense TEXT, example TEXT,
                score REAL DEFAULT 0, importance REAL DEFAULT 0,
                is_verified INTEGER DEFAULT 0,
                FOREIGN KEY (headword_id) REFERENCES headword(id)
            );
            CREATE TABLE form (
                headword_id INTEGER NOT NULL, form TEXT NOT NULL,
                form_type TEXT, pos TEXT, tense TEXT, person TEXT,
                number TEXT, gram_case TEXT, mood TEXT,
                FOREIGN KEY (headword_id) REFERENCES headword(id)
            );
            CREATE TABLE synonym (
                headword_id INTEGER NOT NULL, synonym TEXT NOT NULL,
                FOREIGN KEY (headword_id) REFERENCES headword(id)
            );
            CREATE VIRTUAL TABLE headword_fts USING fts5(
                written_rep, content='headword', content_rowid='id',
                tokenize='unicode61'
            );
            CREATE TRIGGER headword_ai AFTER INSERT ON headword BEGIN
                INSERT INTO headword_fts(rowid, written_rep) VALUES (new.id, new.written_rep);
            END;
        """)

        sorted_entries = sorted(entries, key=lambda e: e["normalized"])
        headword_rows = [
            (e["written_form"], e["normalized"], e["pos"], None,
             e["pronunciation"], int(e["importance"]))
            for e in sorted_entries
        ]
        translation_rows = [
            (i + 1, e["trans_list"], e["sense"], e["example"],
             e["score"], e["importance"], e["is_verified"])
            for i, e in enumerate(sorted_entries)
        ]

        conn.executemany(
            "INSERT INTO headword (written_rep, normalized, pos, gender, pronunciation, frequency) VALUES (?, ?, ?, ?, ?, ?)",
            headword_rows,
        )
        conn.executemany(
            "INSERT INTO translation (headword_id, trans_list, sense, example, score, importance, is_verified) VALUES (?, ?, ?, ?, ?, ?, ?)",
            translation_rows,
        )

        meta_rows = [
            ("schema_version", SCHEMA_VERSION),
            ("data_version", data_version),
            ("pair", pair), ("src_lang", src_lang), ("tgt_lang", tgt_lang),
            ("src_source", "PanLex (CC0)"),
            ("tgt_source", "PanLex (CC0)"),
            ("license", license_id),
            ("attribution", attribution),
            ("generated", dt.date.today().isoformat()),
            ("headword_count", str(len(sorted_entries))),
        ]
        conn.executemany("INSERT INTO _meta (key, value) VALUES (?, ?)", meta_rows)

        conn.executescript("""
            CREATE INDEX idx_headword_normalized ON headword(normalized);
            CREATE INDEX idx_form_form ON form(form COLLATE NOCASE);
            CREATE INDEX idx_form_headword_id ON form(headword_id);
            CREATE INDEX idx_translation_headword_id ON translation(headword_id);
            CREATE INDEX idx_translation_score ON translation(is_verified DESC, score DESC, importance DESC);
            CREATE INDEX idx_synonym_headword_id ON synonym(headword_id);
        """)

        conn.execute("VACUUM")
        conn.commit()
        return len(sorted_entries)
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-version", default="2026.08.1")
    parser.add_argument("--pairs", default=None, help="Comma-separated src-tgt list")
    args = parser.parse_args()

    # Plan pairs
    if args.pairs:
        pairs = [tuple(p.strip().split("-")) for p in args.pairs.split(",") if p.strip()]
    else:
        pairs = []
        for gap in GAP_FILLER_LANGS:
            for pivot in PIVOT_LANGS:
                if gap == pivot:
                    continue
                pairs.append((gap, pivot))
                pairs.append((pivot, gap))
            # Also gap-to-gap
            for other in GAP_FILLER_LANGS:
                if gap == other:
                    continue
                pairs.append((gap, other))

    # Dedupe
    seen = set()
    unique_pairs = []
    for src, tgt in pairs:
        if (src, tgt) in seen:
            continue
        seen.add((src, tgt))
        unique_pairs.append((src, tgt))

    # Filter to pairs where both langs have PanLex data
    buildable = [(s, t) for s, t in unique_pairs
                 if s in PANLEX_LANG_MAP and t in PANLEX_LANG_MAP]

    print(f"Planning {len(buildable)} PanLex pairs")
    if not buildable:
        print("No buildable pairs — download PanLex TSVs first")
        return 1

    # Phase 1: Load gap-filler langs (small) — keep in memory
    print(f"\n=== Phase 1: Load {len(GAP_FILLER_LANGS)} gap-filler PanLex TSVs ===")
    t0 = time.time()
    gap_loaded: dict[str, dict[str, list[str]]] = {}
    for lang in GAP_FILLER_LANGS:
        path = panlex_cache_path(lang)
        if not path.exists():
            print(f"  [skip] {lang}: TSV not cached")
            continue
        t1 = time.time()
        meanings = load_panlex(lang)
        gap_loaded[lang] = meanings
        print(f"  [load] {lang} ({PANLEX_LANG_MAP[lang]}): {len(meanings):,} meanings ({time.time()-t1:.1f}s)")

    print(f"\nLoaded {len(gap_loaded)} gap-filler langs in {time.time()-t0:.1f}s")

    # Phase 2: For each pivot lang, load once and build all gap×pivot pairs
    pivots_needed = set()
    for src, tgt in buildable:
        if src in GAP_FILLER_LANGS and tgt not in GAP_FILLER_LANGS:
            pivots_needed.add(tgt)
        elif tgt in GAP_FILLER_LANGS and src not in GAP_FILLER_LANGS:
            pivots_needed.add(src)

    print(f"\n=== Phase 2: Build dictionaries (load each pivot once) ===")
    t0 = time.time()
    built = 0
    total_entries = 0
    total_size = 0

    # Build gap×gap pairs first (no pivot loading needed)
    gap_pairs = [(s, t) for s, t in buildable
                 if s in GAP_FILLER_LANGS and t in GAP_FILLER_LANGS]
    for i, (src, tgt) in enumerate(gap_pairs, 1):
        if src not in gap_loaded or tgt not in gap_loaded:
            continue
        t1 = time.time()
        entries = build_bilingual(gap_loaded[src], gap_loaded[tgt])
        if not entries:
            print(f"  [gap-gap] {src}-{tgt}: no shared meanings")
            continue
        out_path = OUT_DIR / f"{src}-{tgt}.sqlite3"
        count = write_sqlite(out_path, entries, f"{src}-{tgt}", src, tgt,
                             "CC0-1.0", "PanLex Database (CC0 1.0) — The Long Now Foundation",
                             args.data_version)
        size = out_path.stat().st_size
        built += 1
        total_entries += count
        total_size += size
        print(f"  [gap-gap] {src}-{tgt}: {count:>6,} hw, {size:>10,} bytes ({time.time()-t1:.1f}s)")

    # Build gap×pivot pairs (load each pivot once, build all gap pairs with it)
    for pivot in sorted(pivots_needed):
        path = panlex_cache_path(pivot)
        if not path.exists():
            print(f"  [skip] pivot {pivot}: TSV not cached")
            continue
        t1 = time.time()
        pivot_meanings = load_panlex(pivot)
        print(f"\n  [load pivot] {pivot} ({PANLEX_LANG_MAP[pivot]}): {len(pivot_meanings):,} meanings ({time.time()-t1:.1f}s)")

        for gap in GAP_FILLER_LANGS:
            if gap not in gap_loaded:
                continue
            # gap → pivot
            if (gap, pivot) in seen:
                t2 = time.time()
                entries = build_bilingual(gap_loaded[gap], pivot_meanings)
                if entries:
                    out_path = OUT_DIR / f"{gap}-{pivot}.sqlite3"
                    count = write_sqlite(out_path, entries, f"{gap}-{pivot}", gap, pivot,
                                         "CC0-1.0", "PanLex Database (CC0 1.0) — The Long Now Foundation",
                                         args.data_version)
                    size = out_path.stat().st_size
                    built += 1
                    total_entries += count
                    total_size += size
                    print(f"  [gap-pivot] {gap}-{pivot}: {count:>6,} hw, {size:>10,} bytes ({time.time()-t2:.1f}s)")
            # pivot → gap
            if (pivot, gap) in seen:
                t2 = time.time()
                entries = build_bilingual(pivot_meanings, gap_loaded[gap])
                if entries:
                    out_path = OUT_DIR / f"{pivot}-{gap}.sqlite3"
                    count = write_sqlite(out_path, entries, f"{pivot}-{gap}", pivot, gap,
                                         "CC0-1.0", "PanLex Database (CC0 1.0) — The Long Now Foundation",
                                         args.data_version)
                    size = out_path.stat().st_size
                    built += 1
                    total_entries += count
                    total_size += size
                    print(f"  [pivot-gap] {pivot}-{gap}: {count:>6,} hw, {size:>10,} bytes ({time.time()-t2:.1f}s)")

        # Free pivot memory
        del pivot_meanings

    print(f"\n=== Summary ===")
    print(f"  Built: {built}/{len(buildable)}")
    print(f"  Total headwords: {total_entries:,}")
    print(f"  Total size: {total_size:,} bytes ({total_size/1024/1024:.1f} MB)")
    print(f"  Elapsed: {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
