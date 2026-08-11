"""Convert omw-data WN-LMF XML → Lingolate v2 SQLite (bilingual via ILI).

Strategy:
  1. Load source-language wordnet (e.g. omw-en) — extract LexicalEntry + Synset
  2. Load target-language wordnet (e.g. omw-ja) — same
  3. Build ILI → synset_id map for each language
  4. For each shared ILI:
     - Collect all written_forms in src lang (headwords)
     - Collect all written_forms in tgt lang (translations)
     - Create headword + translation rows in the v2 schema
  5. Also extract: POS, pronunciation, definitions, examples, forms

The result is a bilingual dictionary <src>-<tgt>.sqlite3 where every
headword is a source-language word and trans_list contains all
target-language equivalents that share at least one ILI.

Usage:
    python convert_omw_to_sqlite.py --src en --tgt ja
    python convert_omw_to_sqlite.py --pairs en-ja,en-es,ja-en
    python convert_omw_to_sqlite.py                  # all planned pairs
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from config import (
    LICENSES,
    OMW_LANGS,
    SUPPORTED_LANGS,
    attribution_for_pair,
    dedupe_pairs,
    license_id_for_pair,
    planned_pairs,
    source_for_lang,
)
from download_omw import cache_path as omw_cache_path

OUT_DIR = Path(__file__).resolve().parent.parent / "dictionaries"
SCHEMA_VERSION = "2"


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------
def parse_wordnet(xml_path: Path) -> dict:
    """Parse a WN-LMF XML file into in-memory structures.

    Returns:
        {
            "lexicon_id": str,
            "language": str,
            "license": str,
            "version": str,
            "entries": [
                {
                    "id": str,
                    "written_form": str,
                    "pos": str,
                    "pronunciation": str | None,
                    "forms": [{"form": str, "tags": [str]}],
                    "senses": [
                        {"id": str, "synset": str, "examples": [str], "subject": str | None}
                    ],
                }
            ],
            "synsets": [
                {
                    "id": str,
                    "ili": str | None,
                    "pos": str,
                    "definition": str | None,
                    "examples": [str],
                    "relations": [{"target": str, "rel_type": str}],
                }
            ],
            "ili_to_synsets": {ili: [synset_id, ...]},
            "synset_to_ili": {synset_id: ili},
        }
    """
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
                "id": sense_id,
                "synset": synset,
                "examples": examples,
                "subject": subject,
            })

        entries.append({
            "id": le.attrib.get("id", ""),
            "written_form": written_form,
            "pos": pos,
            "pronunciation": pron,
            "forms": forms,
            "senses": senses,
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
        relations = [
            {"target": r.attrib.get("target", ""), "rel_type": r.attrib.get("relType", "")}
            for r in ss.findall("SynsetRelation")
        ]
        synsets.append({
            "id": ss_id,
            "ili": ili,
            "pos": ss_pos,
            "definition": definition,
            "examples": examples,
            "relations": relations,
        })
        synset_to_ili[ss_id] = ili
        if ili:
            ili_to_synsets[ili].append(ss_id)

    return {
        "lexicon_id": lex_id,
        "language": language,
        "license": license_url,
        "version": version,
        "entries": entries,
        "synsets": synsets,
        "ili_to_synsets": dict(ili_to_synsets),
        "synset_to_ili": synset_to_ili,
    }


# ---------------------------------------------------------------------------
# Bilingual dictionary construction via ILI
# ---------------------------------------------------------------------------
def build_bilingual(
    src_wn: dict, tgt_wn: dict
) -> list[dict]:
    """Build bilingual entries: src headword → tgt translations via shared ILI.

    Returns list of:
        {
            "written_form": str,
            "normalized": str,
            "pos": str | None,
            "pronunciation": str | None,
            "forms": [{"form": str, "tags": [str]}],
            "trans_list": str,        # comma-joined tgt translations
            "sense": str | None,      # definition from synset
            "example": str | None,    # example from src sense
            "score": float,           # 50 default (no quality data in omw)
            "importance": float,      # based on ILI frequency
            "is_verified": int,       # 1 if ILI-mapped (high confidence)
        }
    """
    # Map tgt synset_id → list of written_forms
    tgt_synset_to_forms: dict[str, list[str]] = defaultdict(list)
    for entry in tgt_wn["entries"]:
        for sense in entry["senses"]:
            tgt_synset_to_forms[sense["synset"]].append(entry["written_form"])

    # Map tgt ILI → all forms (via synsets)
    tgt_ili_to_forms: dict[str, list[str]] = defaultdict(list)
    for ili, synset_ids in tgt_wn["ili_to_synsets"].items():
        for sid in synset_ids:
            for form in tgt_synset_to_forms.get(sid, []):
                tgt_ili_to_forms[ili].append(form)

    # Dedupe tgt forms per ILI
    for ili in tgt_ili_to_forms:
        tgt_ili_to_forms[ili] = list(dict.fromkeys(tgt_ili_to_forms[ili]))

    # Build src headword → translations
    # Group by written_form (a word may have multiple senses/entries)
    headword_map: dict[str, dict] = defaultdict(lambda: {
        "pos": None,
        "pronunciation": None,
        "forms": [],
        "translations": [],
        "senses": [],
        "examples": [],
        "ilis": set(),
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
                # Use synset definition as sense
                for ss in src_wn["synsets"]:
                    if ss["id"] == sense["synset"]:
                        if ss["definition"]:
                            hw["senses"].append(ss["definition"])
                        # Synset examples (OMW stores examples at Synset level, not Sense level)
                        if ss.get("examples"):
                            hw["examples"].extend(ss["examples"])
                        break
                # Also check Sense-level examples (some WordNests may have them)
                if sense["examples"]:
                    hw["examples"].extend(sense["examples"])

    # Convert to list of entry dicts
    result: list[dict] = []
    for wf, data in headword_map.items():
        if not data["translations"]:
            continue  # no target equivalent
        unique_trans = list(dict.fromkeys(data["translations"]))
        trans_list = ", ".join(unique_trans)
        sense = "; ".join(dict.fromkeys(data["senses"])) if data["senses"] else None
        example = data["examples"][0] if data["examples"] else None
        # Score: ILI-mapped = verified (high confidence)
        is_verified = 1 if data["ilis"] else 0
        # Importance: based on number of shared ILIs (more ILIs = more central word)
        importance = min(len(data["ilis"]) * 30, 100)
        result.append({
            "written_form": wf,
            "normalized": normalize(wf),
            "pos": data["pos"],
            "pronunciation": data["pronunciation"],
            "forms": data["forms"],
            "trans_list": trans_list,
            "sense": sense,
            "example": example,
            "score": 50.0,
            "importance": float(importance),
            "is_verified": is_verified,
        })

    return result


# ---------------------------------------------------------------------------
# SQLite writer (Lingolate v2 schema)
# ---------------------------------------------------------------------------
def normalize(text: str) -> str:
    text = text.strip().lower()
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def write_sqlite(
    out_path: Path,
    entries: list[dict],
    pair: str,
    src_lang: str,
    tgt_lang: str,
    src_source_id: str,
    tgt_source_id: str,
    license_id: str,
    attribution: str,
    data_version: str,
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    conn = sqlite3.connect(str(out_path))
    try:
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA page_size = 4096")
        conn.execute("PRAGMA synchronous = OFF")

        conn.executescript(
            """
            CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

            CREATE TABLE headword (
                id INTEGER PRIMARY KEY,
                written_rep TEXT NOT NULL,
                normalized TEXT NOT NULL,
                pos TEXT,
                gender TEXT,
                pronunciation TEXT,
                frequency INTEGER DEFAULT 0
            );

            CREATE TABLE translation (
                id INTEGER PRIMARY KEY,
                headword_id INTEGER NOT NULL,
                trans_list TEXT NOT NULL,
                sense TEXT,
                example TEXT,
                score REAL DEFAULT 0,
                importance REAL DEFAULT 0,
                is_verified INTEGER DEFAULT 0,
                FOREIGN KEY (headword_id) REFERENCES headword(id)
            );

            CREATE TABLE form (
                headword_id INTEGER NOT NULL,
                form TEXT NOT NULL,
                form_type TEXT,
                pos TEXT,
                tense TEXT, person TEXT, number TEXT, gram_case TEXT, mood TEXT,
                FOREIGN KEY (headword_id) REFERENCES headword(id)
            );

            CREATE TABLE synonym (
                headword_id INTEGER NOT NULL,
                synonym TEXT NOT NULL,
                FOREIGN KEY (headword_id) REFERENCES headword(id)
            );

            CREATE VIRTUAL TABLE headword_fts USING fts5(
                written_rep, content='headword', content_rowid='id',
                tokenize='unicode61'
            );

            CREATE TRIGGER headword_ai AFTER INSERT ON headword BEGIN
                INSERT INTO headword_fts(rowid, written_rep) VALUES (new.id, new.written_rep);
            END;
            """
        )

        # Sort by normalized for cache locality
        sorted_entries = sorted(entries, key=lambda e: e["normalized"])

        headword_rows: list[tuple] = []
        translation_rows: list[tuple] = []
        form_rows: list[tuple] = []

        for idx, entry in enumerate(sorted_entries, start=1):
            headword_rows.append((
                entry["written_form"],
                entry["normalized"],
                entry["pos"],
                None,  # gender — omw doesn't have this
                entry["pronunciation"],
                int(entry["importance"]),
            ))
            translation_rows.append((
                idx,
                entry["trans_list"],
                entry["sense"],
                entry["example"],
                entry["score"],
                entry["importance"],
                entry["is_verified"],
            ))
            for form in entry.get("forms", []):
                form_rows.append((idx, form["form"], ",".join(form["tags"]) if form["tags"] else None,
                                  None, None, None, None, None, None))

        conn.executemany(
            "INSERT INTO headword (written_rep, normalized, pos, gender, pronunciation, frequency) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            headword_rows,
        )
        conn.executemany(
            "INSERT INTO translation (headword_id, trans_list, sense, example, score, importance, is_verified) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
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
            ("pair", pair),
            ("src_lang", src_lang),
            ("tgt_lang", tgt_lang),
            ("src_source", src_source_id),
            ("tgt_source", tgt_source_id),
            ("license", license_id),
            ("attribution", attribution),
            ("generated", dt.date.today().isoformat()),
            ("headword_count", str(len(sorted_entries))),
        ]
        conn.executemany("INSERT INTO _meta (key, value) VALUES (?, ?)", meta_rows)

        conn.executescript(
            """
            CREATE INDEX idx_headword_normalized ON headword(normalized);
            CREATE INDEX idx_form_form ON form(form COLLATE NOCASE);
            CREATE INDEX idx_form_headword_id ON form(headword_id);
            CREATE INDEX idx_translation_headword_id ON translation(headword_id);
            CREATE INDEX idx_translation_score ON translation(is_verified DESC, score DESC, importance DESC);
            CREATE INDEX idx_synonym_headword_id ON synonym(headword_id);
            """
        )

        conn.execute("VACUUM")
        conn.commit()
        return len(sorted_entries)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Pair conversion
# ---------------------------------------------------------------------------
def convert_pair(src: str, tgt: str, data_version: str) -> tuple[Path, int] | None:
    """Build <src>-<tgt>.sqlite3 from omw-data via ILI matching."""
    src_info = source_for_lang(src)
    tgt_info = source_for_lang(tgt)
    if not src_info or not tgt_info:
        print(f"  [skip]   {src}-{tgt}: no source for one or both langs")
        return None

    # For omw, we need the XML. For tufs, defer to tufs converter (future).
    if src_info.primary != "omw" or tgt_info.primary != "omw":
        print(f"  [skip]   {src}-{tgt}: non-omw source (defer to tufs converter)")
        return None

    src_xml = omw_cache_path(src_info.omw_id)
    tgt_xml = omw_cache_path(tgt_info.omw_id)
    if not src_xml.exists() or not tgt_xml.exists():
        print(f"  [skip]   {src}-{tgt}: XML not cached (run download_omw.py first)")
        return None

    print(f"  [parse]  {src}-{tgt}: parsing {src_info.omw_id} + {tgt_info.omw_id}")
    src_wn = parse_wordnet(src_xml)
    tgt_wn = parse_wordnet(tgt_xml)

    print(f"  [build]  {src}-{tgt}: {len(src_wn['entries'])} src entries, "
          f"{len(tgt_wn['entries'])} tgt entries, "
          f"{len(src_wn['ili_to_synsets'])} src ILIs, "
          f"{len(tgt_wn['ili_to_synsets'])} tgt ILIs")

    entries = build_bilingual(src_wn, tgt_wn)
    if not entries:
        print(f"  [empty]  {src}-{tgt}: no shared ILIs")
        return None

    out_path = OUT_DIR / f"{src}-{tgt}.sqlite3"
    pair = f"{src}-{tgt}"
    license_id = license_id_for_pair(src, tgt)
    attribution = attribution_for_pair(src, tgt)
    count = write_sqlite(
        out_path, entries, pair, src, tgt,
        src_info.attribution, tgt_info.attribution,
        license_id, attribution, data_version,
    )
    size = out_path.stat().st_size
    print(f"  [ok]     {src}-{tgt}: {count} headwords, {size:,} bytes")
    return out_path, count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", help="Comma-separated src-tgt list")
    parser.add_argument("--src", help="Single source lang (use with --tgt)")
    parser.add_argument("--tgt", help="Single target lang (use with --src)")
    parser.add_argument("--data-version", default="2026.08.1")
    args = parser.parse_args()

    if args.src and args.tgt:
        pairs = [(args.src, args.tgt, "bili")]
    elif args.pairs:
        wanted = {tuple(p.strip().split("-")) for p in args.pairs.split(",") if p.strip()}
        pairs = [(s, t, "bili") for (s, t, _) in planned_pairs() if (s, t) in wanted]
    else:
        pairs = planned_pairs()
    pairs = dedupe_pairs(pairs)

    print(f"Converting {len(pairs)} pairs -> {OUT_DIR.name}/ (schema v{SCHEMA_VERSION})")
    built = 0
    for src, tgt, _ in pairs:
        result = convert_pair(src, tgt, args.data_version)
        if result is not None:
            built += 1
    print(f"\nBuilt {built}/{len(pairs)} dictionaries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
