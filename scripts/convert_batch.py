"""Batch convert: parse each omw XML once, build ALL pairs in one run.

Much faster than convert_omw_to_sqlite.py for many pairs — avoids re-parsing
the same XML for every pair. For 17 langs × 16 = 272 directed pairs, this
parses 17 XMLs once instead of 544 times.

Usage:
    python convert_batch.py --data-version 2026.08.1
    python convert_batch.py --data-version 2026.08.1 --pairs en-ja,ja-es
    python convert_batch.py --data-version 2026.08.1 --pivot-only en,ja  # only pairs anchored on these pivots
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from config import (
    OMW_LANGS,
    SUPPORTED_LANGS,
    TUF_ONLY_LANGS,
    TUF_SHARED_LANGS,
    attribution_for_pair,
    dedupe_pairs,
    license_id_for_pair,
    planned_pairs,
    source_for_lang,
)
from download_omw import cache_path as omw_cache_path

OUT_DIR = Path(__file__).resolve().parent.parent / "dictionaries"
TUF_CACHE_DIR = Path(__file__).resolve().parent.parent / "sources_cache" / "tufs"
SCHEMA_VERSION = "2"


def xml_path_for_lang(lang: str) -> Path | None:
    """Find the cached XML for a language — omw or tufs."""
    info = source_for_lang(lang)
    if not info:
        return None
    if info.primary == "omw" and info.omw_id:
        p = omw_cache_path(info.omw_id)
        return p if p.exists() else None
    if info.primary == "tufs":
        tuf_dir = TUF_CACHE_DIR / lang
        if tuf_dir.exists():
            xmls = list(tuf_dir.glob("*.xml"))
            if xmls:
                return xmls[0]
    return None


# ---------------------------------------------------------------------------
# XML parsing (same as convert_omw_to_sqlite.py but cached)
# ---------------------------------------------------------------------------
def parse_wordnet(xml_path: Path) -> dict:
    content = xml_path.read_text(encoding="utf-8", errors="replace")
    root = ET.fromstring(content)
    lexicon = root.find("Lexicon")
    if lexicon is None:
        raise ValueError(f"No Lexicon in {xml_path}")

    lex_id = lexicon.attrib.get("id", "")
    language = lexicon.attrib.get("language", "")
    license_url = lexicon.attrib.get("license", "")
    version = lexicon.attrib.get("version", "")

    entries: list[dict] = []
    for le in lexicon.findall("LexicalEntry"):
        lemma = le.find("Lemma")
        if lemma is None:
            continue
        written_form = lemma.attrib.get("writtenForm", "")
        pos = lemma.attrib.get("partOfSpeech", "")
        pron = None
        pron_el = lemma.find("Pronunciation")
        if pron_el is not None:
            pron = pron_el.text or pron_el.attrib.get("audio")

        forms: list[dict] = []
        for form_el in le.findall("Form"):
            f_wf = form_el.attrib.get("writtenForm", "")
            tags = [t.text or t.attrib.get("category", "") for t in form_el.findall("Tag")]
            if f_wf:
                forms.append({"form": f_wf, "tags": tags})

        senses: list[dict] = []
        for sense_el in le.findall("Sense"):
            sense_id = sense_el.attrib.get("id", "")
            synset = sense_el.attrib.get("synset", "")
            subject = sense_el.attrib.get(
                "{https://globalwordnet.github.io/schemas/dc/}subject"
            )
            examples = [e.text for e in sense_el.findall("Example") if e.text]
            senses.append({
                "id": sense_id, "synset": synset,
                "examples": examples, "subject": subject,
            })

        entries.append({
            "id": le.attrib.get("id", ""),
            "written_form": written_form, "pos": pos,
            "pronunciation": pron, "forms": forms, "senses": senses,
        })

    synsets: list[dict] = []
    synset_to_ili: dict[str, str | None] = {}
    ili_to_synsets: dict[str, list[str]] = defaultdict(list)
    for ss in lexicon.findall("Synset"):
        ss_id = ss.attrib.get("id", "")
        ili = ss.attrib.get("ili") or None
        ss_pos = ss.attrib.get("partOfSpeech", "")
        def_el = ss.find("Definition")
        definition = def_el.text if def_el is not None else None
        examples = [e.text for e in ss.findall("Example") if e.text]
        synsets.append({
            "id": ss_id, "ili": ili, "pos": ss_pos,
            "definition": definition, "examples": examples,
        })
        synset_to_ili[ss_id] = ili
        if ili:
            ili_to_synsets[ili].append(ss_id)

    return {
        "lexicon_id": lex_id, "language": language,
        "license": license_url, "version": version,
        "entries": entries, "synsets": synsets,
        "ili_to_synsets": dict(ili_to_synsets),
        "synset_to_ili": synset_to_ili,
    }


# ---------------------------------------------------------------------------
# Pre-compute ILI → forms map for a language (once per lang)
# ---------------------------------------------------------------------------
def build_ili_to_forms(wn: dict) -> dict[str, list[str]]:
    """Map ILI → list of written_forms in this language."""
    synset_to_forms: dict[str, list[str]] = defaultdict(list)
    for entry in wn["entries"]:
        for sense in entry["senses"]:
            synset_to_forms[sense["synset"]].append(entry["written_form"])

    ili_to_forms: dict[str, list[str]] = defaultdict(list)
    for ili, synset_ids in wn["ili_to_synsets"].items():
        for sid in synset_ids:
            for form in synset_to_forms.get(sid, []):
                ili_to_forms[ili].append(form)

    # Dedupe
    for ili in ili_to_forms:
        ili_to_forms[ili] = list(dict.fromkeys(ili_to_forms[ili]))
    return dict(ili_to_forms)


def build_synset_to_definition(wn: dict) -> dict[str, str]:
    """Map synset_id → definition."""
    return {ss["id"]: ss["definition"] for ss in wn["synsets"] if ss["definition"]}


# ---------------------------------------------------------------------------
# Bilingual dictionary construction (fast — uses pre-computed maps)
# ---------------------------------------------------------------------------
def normalize(text: str) -> str:
    text = text.strip().lower()
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def build_bilingual_fast(
    src_wn: dict,
    tgt_ili_to_forms: dict[str, list[str]],
) -> list[dict]:
    """Build bilingual entries using pre-computed tgt ILI→forms map."""
    headword_map: dict[str, dict] = defaultdict(lambda: {
        "pos": None, "pronunciation": None, "forms": [],
        "translations": [], "senses": [], "examples": [], "ilis": set(),
    })

    for entry in src_wn["entries"]:
        wf = entry["written_form"]
        if not wf:
            continue
        hw = headword_map[wf]
        if not hw["pos"]:
            hw["pos"] = entry["pos"] or None
        if not hw["pronunciation"]:
            hw["pronunciation"] = entry["pronunciation"]
        if not hw["forms"]:
            hw["forms"] = entry["forms"]
        for sense in entry["senses"]:
            ili = src_wn["synset_to_ili"].get(sense["synset"])
            if ili and ili in tgt_ili_to_forms:
                for tgt_form in tgt_ili_to_forms[ili]:
                    hw["translations"].append(tgt_form)
                hw["ilis"].add(ili)
                # Definition from synset
                ss_def = src_wn.get("_synset_defs", {}).get(sense["synset"])
                if ss_def:
                    hw["senses"].append(ss_def)
                if sense["examples"]:
                    hw["examples"].extend(sense["examples"])

    result: list[dict] = []
    for wf, data in headword_map.items():
        if not data["translations"]:
            continue
        unique_trans = list(dict.fromkeys(data["translations"]))
        trans_list = ", ".join(unique_trans)
        sense = "; ".join(dict.fromkeys(data["senses"])) if data["senses"] else None
        example = data["examples"][0] if data["examples"] else None
        is_verified = 1 if data["ilis"] else 0
        importance = min(len(data["ilis"]) * 30, 100)
        result.append({
            "written_form": wf, "normalized": normalize(wf),
            "pos": data["pos"], "pronunciation": data["pronunciation"],
            "forms": data["forms"], "trans_list": trans_list,
            "sense": sense, "example": example,
            "score": 50.0, "importance": float(importance),
            "is_verified": is_verified,
        })
    return result


# ---------------------------------------------------------------------------
# SQLite writer (same schema as convert_omw_to_sqlite.py)
# ---------------------------------------------------------------------------
def write_sqlite(
    out_path: Path, entries: list[dict], pair: str,
    src_lang: str, tgt_lang: str,
    src_source_id: str, tgt_source_id: str,
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
        headword_rows: list[tuple] = []
        translation_rows: list[tuple] = []
        form_rows: list[tuple] = []

        for idx, entry in enumerate(sorted_entries, start=1):
            headword_rows.append((
                entry["written_form"], entry["normalized"], entry["pos"],
                None, entry["pronunciation"], int(entry["importance"]),
            ))
            translation_rows.append((
                idx, entry["trans_list"], entry["sense"], entry["example"],
                entry["score"], entry["importance"], entry["is_verified"],
            ))
            for form in entry.get("forms", []):
                form_rows.append((
                    idx, form["form"],
                    ",".join(form["tags"]) if form["tags"] else None,
                    None, None, None, None, None, None,
                ))

        conn.executemany(
            "INSERT INTO headword (written_rep, normalized, pos, gender, pronunciation, frequency) VALUES (?, ?, ?, ?, ?, ?)",
            headword_rows,
        )
        conn.executemany(
            "INSERT INTO translation (headword_id, trans_list, sense, example, score, importance, is_verified) VALUES (?, ?, ?, ?, ?, ?, ?)",
            translation_rows,
        )
        if form_rows:
            conn.executemany(
                "INSERT INTO form (headword_id, form, form_type) VALUES (?, ?, ?)",
                [(r[0], r[1], r[2]) for r in form_rows],
            )

        meta_rows = [
            ("schema_version", SCHEMA_VERSION),
            ("data_version", data_version),
            ("pair", pair), ("src_lang", src_lang), ("tgt_lang", tgt_lang),
            ("src_source", src_source_id), ("tgt_source", tgt_source_id),
            ("license", license_id), ("attribution", attribution),
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-version", default="2026.08.1")
    parser.add_argument("--pairs", default=None, help="Comma-separated subset")
    parser.add_argument("--pivot-only", default=None,
                        help="Comma-separated pivots (only build pairs anchored on these)")
    args = parser.parse_args()

    # Plan pairs
    pairs = planned_pairs()
    if args.pairs:
        wanted = {tuple(p.strip().split("-")) for p in args.pairs.split(",") if p.strip()}
        pairs = [(s, t, st) for (s, t, st) in pairs if (s, t) in wanted]
    if args.pivot_only:
        pivots = set(args.pivot_only.split(","))
        pairs = [(s, t, st) for (s, t, st) in pairs if s in pivots or t in pivots]
    pairs = dedupe_pairs(pairs)

    # Filter to pairs where we have XML for both sides (omw or tufs)
    buildable_pairs = []
    for src, tgt, st in pairs:
        src_info = source_for_lang(src)
        tgt_info = source_for_lang(tgt)
        if not src_info or not tgt_info:
            continue
        src_xml = xml_path_for_lang(src)
        tgt_xml = xml_path_for_lang(tgt)
        if src_xml and tgt_xml:
            buildable_pairs.append((src, tgt))
        else:
            missing = []
            if not src_xml:
                missing.append(f"{src} (no XML)")
            if not tgt_xml:
                missing.append(f"{tgt} (no XML)")
            print(f"  [skip] {src}-{tgt}: {', '.join(missing)}")

    if not buildable_pairs:
        print("No buildable pairs (need XML for both langs)")
        return 0

    # Determine which langs to parse
    langs_needed = set()
    for src, tgt in buildable_pairs:
        langs_needed.add(src)
        langs_needed.add(tgt)

    print(f"\n=== Phase 1: Parse {len(langs_needed)} XMLs (omw + tufs) ===")
    parsed: dict[str, dict] = {}  # lang -> wordnet dict
    ili_forms_cache: dict[str, dict[str, list[str]]] = {}  # lang -> ILI→forms
    synset_defs_cache: dict[str, dict[str, str]] = {}  # lang -> synset→def

    t0 = time.time()
    for lang in sorted(langs_needed):
        info = source_for_lang(lang)
        if not info:
            continue
        xml_path = xml_path_for_lang(lang)
        if not xml_path:
            print(f"  [skip] {lang}: no XML cached")
            continue
        t1 = time.time()
        src_tag = info.primary  # "omw" or "tufs"
        print(f"  [parse] {lang} ({src_tag}/{info.omw_id or 'tufs'}) {xml_path.stat().st_size:,} bytes...", end=" ", flush=True)
        wn = parse_wordnet(xml_path)
        wn["_synset_defs"] = build_synset_to_definition(wn)
        wn["_source"] = info
        parsed[lang] = wn
        ili_forms_cache[lang] = build_ili_to_forms(wn)
        synset_defs_cache[lang] = wn["_synset_defs"]
        print(f"{len(wn['entries']):,} entries, {len(wn['ili_to_synsets']):,} ILIs ({time.time()-t1:.1f}s)")

    print(f"\nParsed {len(parsed)} langs in {time.time()-t0:.1f}s")

    print(f"\n=== Phase 2: Build {len(buildable_pairs)} bilingual dictionaries ===")
    t0 = time.time()
    built = 0
    total_entries = 0
    total_size = 0
    skipped = 0

    for i, (src, tgt) in enumerate(buildable_pairs, 1):
        if src not in parsed or tgt not in parsed:
            print(f"  [{i}/{len(buildable_pairs)}] [skip] {src}-{tgt}: missing parsed lang")
            skipped += 1
            continue

        t1 = time.time()
        entries = build_bilingual_fast(parsed[src], ili_forms_cache[tgt])
        if not entries:
            print(f"  [{i}/{len(buildable_pairs)}] [empty] {src}-{tgt}: no shared ILIs")
            skipped += 1
            continue

        out_path = OUT_DIR / f"{src}-{tgt}.sqlite3"
        src_info = source_for_lang(src)
        tgt_info = source_for_lang(tgt)
        license_id = license_id_for_pair(src, tgt)
        attribution = attribution_for_pair(src, tgt)

        count = write_sqlite(
            out_path, entries, f"{src}-{tgt}", src, tgt,
            src_info.attribution if src_info else "",
            tgt_info.attribution if tgt_info else "",
            license_id, attribution, args.data_version,
        )
        size = out_path.stat().st_size
        elapsed = time.time() - t1
        built += 1
        total_entries += count
        total_size += size
        print(f"  [{i}/{len(buildable_pairs)}] [ok] {src}-{tgt}: {count:>6,} hw, {size:>10,} bytes ({elapsed:.1f}s)")

    print(f"\n=== Summary ===")
    print(f"  Built: {built}/{len(buildable_pairs)}")
    print(f"  Skipped: {skipped}")
    print(f"  Total headwords: {total_entries:,}")
    print(f"  Total size: {total_size:,} bytes ({total_size/1024/1024:.1f} MB)")
    print(f"  Elapsed: {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
