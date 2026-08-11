# lingolate-dict-data

Commercial-use-friendly bilingual dictionary data for the Lingolate
translation app. Built from **omw-data 1.4** (Open Multilingual Wordnet) and
**tufs** (TUFS Basic Vocabulary) — all licenses are commercial-safe (WordNet,
CC-BY, Apache-2.0, MIT, CeCILL-C). No viral ShareAlike licenses.

Distributed via GitHub Releases (large `.sqlite3` files) + a small
`manifest.json` index served via jsDelivr/raw.

## License

All data is **commercial-use-safe** — see [ATTRIBUTIONS.md](ATTRIBUTIONS.md).

| License | Languages | ShareAlike? |
|---------|-----------|-------------|
| Princeton WordNet 3.0 | en | No |
| WordNet-style (NICT, DanNet, etc.) | ja, da, nb, pl, th, zh | No |
| CC-BY 3.0 | bg, es, fi, is, it, sv | No |
| CC-BY 4.0 (tufs) | de, ko, ru, tr, ar, hi, vi | No |
| Apache-2.0 | el | No |
| MIT | id, ms | No |
| CeCILL-C | fr | No |

Attribution is rendered inside the Lingolate app's "About / Licenses" page.

## Sources

| Source | License | Role | Coverage |
|--------|---------|------|----------|
| omw-data 1.4 | WordNet/CC-BY/Apache/MIT/CeCILL-C | Primary | 17 langs, 156K en, 94K ja |
| tufs | CC-BY 4.0 | Enrichment | 23 langs, ~500-1000 entry/lang |
| PanLex | CC0 (future) | Gap filler | Cross-pairs, remaining langs |

Cross-language translation uses **ILI (Inter-Lingual Index)** — synsets
with the same ILI across languages denote the same concept.

## Layout

```
lingolate-dict-data/
├── LICENSE                    # CC0 (for our build scripts)
├── ATTRIBUTIONS.md            # omw-data + tufs attributions
├── SCHEMA.md                  # Lingolate v2 SQLite schema design
├── README.md
├── manifest.json              # generated index (served via jsDelivr)
├── manifest.schema.json       # JSON Schema for manifest validation
├── scripts/
│   ├── config.py              # languages, sources, licenses, pair planning
│   ├── download_omw.py        # fetch omw-data tar.xz → extract XML
│   ├── download_tufs.py       # fetch tufs tar.xz → extract XML
│   ├── convert_omw_to_sqlite.py  # WN-LMF XML → v2 SQLite (ILI matching)
│   ├── generate_manifest.py   # produce manifest.json + sha256 + sizes
│   ├── validate_manifest.py   # schema + integrity check
│   ├── build_all.py           # orchestrator: download → convert → manifest
│   ├── smoke_test.py          # DB query verification
│   └── requirements.txt
├── .github/workflows/
│   └── build-release.yml      # monthly build + GitHub Releases upload
├── dictionaries/              # generated .sqlite3 files (gitignored)
└── sources_cache/             # downloaded upstream data (gitignored)
```

## SQLite Schema (Lingolate v2)

See [SCHEMA.md](SCHEMA.md) for full design. Summary:

```sql
CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE headword (
    id INTEGER PRIMARY KEY,
    written_rep TEXT NOT NULL,    -- "hello"
    normalized TEXT NOT NULL,     -- "hello" (lowercase + diacritics stripped)
    pos TEXT,                     -- noun, verb, adjective, ...
    gender TEXT,                  -- masculine, feminine, neuter
    pronunciation TEXT,           -- IPA or audio URL
    frequency INTEGER DEFAULT 0   -- 0-100
);
CREATE TABLE translation (
    id INTEGER PRIMARY KEY,
    headword_id INTEGER NOT NULL,
    trans_list TEXT NOT NULL,     -- "こんにちは, やあ"
    sense TEXT,                   -- "an expression of greeting"
    example TEXT,                 -- example sentence
    score REAL DEFAULT 0,         -- 0-100, translation quality
    importance REAL DEFAULT 0,    -- 0-100, word importance
    is_verified INTEGER DEFAULT 0 -- 0 or 1 (ILI-mapped = high confidence)
);
CREATE TABLE form (               -- inflected forms (go → went, gone)
    headword_id INTEGER NOT NULL,
    form TEXT NOT NULL,
    form_type TEXT, pos TEXT, tense TEXT, person TEXT,
    number TEXT, gram_case TEXT, mood TEXT
);
CREATE TABLE synonym (headword_id INTEGER NOT NULL, synonym TEXT NOT NULL);
CREATE VIRTUAL TABLE headword_fts USING fts5(written_rep, ...);  -- fuzzy search
```

## Usage

### Build all dictionaries locally

```bash
cd scripts
pip install -r requirements.txt
python build_all.py --version 2026.08.1                    # full build
python build_all.py --version 2026.08.1 --pairs en-ja,ja-es  # subset
python build_all.py --version 2026.08.1 --dry-run          # plan only
```

### Consume from the app

1. Fetch the manifest (small, cached):
   ```
   https://cdn.jsdelivr.net/gh/hudaiapa88/lingolate-dict-data@main/manifest.json
   ```
   Fallback: `https://raw.githubusercontent.com/hudaiapa88/lingolate-dict-data/main/manifest.json`

2. Download a dictionary (large, CDN-backed, unlimited bandwidth):
   ```
   https://github.com/hudaiapa88/lingolate-dict-data/releases/download/v2026.08.1/en-ja.sqlite3
   ```

3. Verify `sha256` against `manifest.json` before opening the database.

## PoC Results

| Pair | Headwords | Size | Verified | Sense | POS |
|------|-----------|------|----------|-------|-----|
| en-ja | 81,045 | 22.5 MB | 100% | 100% | 100% |
| ja-es | 56,007 | 13.5 MB | 100% | 100% | 100% |

Sample queries:
- `hello` → こんにちは, やあ (sense: "an expression of greeting")
- `house` → 宿, 家屋, 館, 戸, 住宅, 家, 屋... (20+ translations)
- `勉強` → estudio (sense: "ある問題を学び理解するために頭を使うこと")
- `学校` → colegio, escuela, instituto

## Versioning

- Manifest `version` follows `YYYY.MM.N` (e.g. `2026.08.1`).
- Each build creates a GitHub Release tag `vYYYY.MM.N`.
- The app detects updates by comparing `manifest.version` against cached value.
