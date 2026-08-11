# Lingolate Dictionary DB — Custom Schema Design

## Design Goals

1. **Normalized** — headword and translations separated (1:N), no string duplication
2. **Multi-sense** — a word can have multiple meanings, each a separate translation row
3. **Compact** — INTEGER foreign keys instead of repeating source words
4. **Indexed** — `normalized` column for O(log n) case-insensitive match, FTS5 for fuzzy
5. **Metadata** — `_meta` table embeds version, source, license, pair, generated date
6. **Forms** — inflection table (go → went, gone, going) with grammatical metadata
7. **Examples** — per-sense example sentences
8. **Quality scores** — `score`, `importance`, `is_verified` for result ranking
9. **Read-optimized** — mobile read-only workload, no WAL, no journal, mmap-friendly

## Schema

```sql
-- ─────────────────────────────────────────────────────────────
-- Metadata — version, source, license, pair, build date
-- ─────────────────────────────────────────────────────────────
CREATE TABLE _meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Rows: ('schema_version', '2'), ('data_version', '2026.08.1'),
--       ('pair', 'en-tr'), ('source', 'open-dict-data/wikidict-en'),
--       ('license', 'CC0-1.0'), ('attribution', '...'),
--       ('generated', '2026-08-11'), ('entry_count', '12345')

-- ─────────────────────────────────────────────────────────────
-- Headword — the source-language word/phrase
-- ─────────────────────────────────────────────────────────────
CREATE TABLE headword (
    id            INTEGER PRIMARY KEY,
    written_rep   TEXT NOT NULL,           -- original casing: "Hello"
    normalized    TEXT NOT NULL,           -- lowercase for matching: "hello"
    pos           TEXT,                    -- noun, verb, adjective, ...
    gender        TEXT,                    -- masculine, feminine, neuter
    pronunciation TEXT,                    -- IPA: /həˈloʊ/
    frequency     INTEGER DEFAULT 0        -- 0-100, usage frequency
);

-- ─────────────────────────────────────────────────────────────
-- Translation — each headword can have multiple senses
-- ─────────────────────────────────────────────────────────────
CREATE TABLE translation (
    id           INTEGER PRIMARY KEY,
    headword_id  INTEGER NOT NULL,
    trans_list   TEXT NOT NULL,            -- "merhaba, selam"
    sense        TEXT,                     -- "a greeting" (meaning context)
    example      TEXT,                     -- "Hello, how are you?"
    score        REAL DEFAULT 0,           -- 0-100, translation quality
    importance   REAL DEFAULT 0,           -- 0-100, word importance
    is_verified  INTEGER DEFAULT 0,        -- 0 or 1 (curated/high-quality)
    FOREIGN KEY (headword_id) REFERENCES headword(id)
);

-- ─────────────────────────────────────────────────────────────
-- Form — inflected forms (go → went, gone, going)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE form (
    headword_id  INTEGER NOT NULL,
    form         TEXT NOT NULL,            -- "went"
    form_type    TEXT,                     -- "past", "past_participle", "plural"
    pos          TEXT,                     -- part of speech of this form
    tense        TEXT,                     -- present, past, perfect, future
    person       TEXT,                     -- first, second, third
    number       TEXT,                     -- singular, plural
    gram_case    TEXT,                     -- nominative, accusative, dative, genitive
    mood         TEXT,                     -- indicative, subjunctive, imperative
    FOREIGN KEY (headword_id) REFERENCES headword(id)
);

-- ─────────────────────────────────────────────────────────────
-- Synonym — same-language synonyms for a headword
-- ─────────────────────────────────────────────────────────────
CREATE TABLE synonym (
    headword_id  INTEGER NOT NULL,
    synonym      TEXT NOT NULL,
    FOREIGN KEY (headword_id) REFERENCES headword(id)
);

-- ─────────────────────────────────────────────────────────────
-- Indexes — cover all query patterns the app uses
-- ─────────────────────────────────────────────────────────────

-- Exact + prefix match (the app's primary query pattern)
CREATE INDEX idx_headword_normalized ON headword(normalized);

-- Reverse lookup: inflected form → headword
CREATE INDEX idx_form_form ON form(form COLLATE NOCASE);
CREATE INDEX idx_form_headword_id ON form(headword_id);

-- Translation joins + ranking
CREATE INDEX idx_translation_headword_id ON translation(headword_id);
CREATE INDEX idx_translation_score ON translation(is_verified DESC, score DESC, importance DESC);

-- Synonym joins
CREATE INDEX idx_synonym_headword_id ON synonym(headword_id);

-- ─────────────────────────────────────────────────────────────
-- FTS5 — full-text search for fuzzy/prefix matching (future)
-- ─────────────────────────────────────────────────────────────
-- Enabled when SQLite is compiled with FTS5 (sqflite on mobile supports it).
-- The external-content pattern keeps the FTS index in sync with headword.
CREATE VIRTUAL TABLE headword_fts USING fts5(
    written_rep,
    content='headword',
    content_rowid='id',
    tokenize='unicode61'
);
-- Triggers keep FTS in sync on INSERT (DB is read-only after build, no UPDATE/DELETE needed)
CREATE TRIGGER headword_ai AFTER INSERT ON headword BEGIN
    INSERT INTO headword_fts(rowid, written_rep) VALUES (new.id, new.written_rep);
END;
```

## Query Patterns → SQL

### 1. Exact + prefix match (primary — replaces app's current 4-branch logic)

```sql
SELECT
    h.written_rep,
    h.pos,
    h.gender,
    h.pronunciation,
    h.frequency,
    t.trans_list,
    t.sense,
    t.example,
    t.score,
    t.importance,
    t.is_verified,
    CASE WHEN h.normalized = ? THEN 0 ELSE 1 END as exact_match
FROM headword h
JOIN translation t ON t.headword_id = h.id
WHERE h.normalized LIKE ?    -- 'hello' for exact, 'hello%' for prefix
ORDER BY
    exact_match ASC,
    t.is_verified DESC,
    t.score DESC,
    t.importance DESC
LIMIT ?;
```

### 2. Inflected form lookup ("went" → find "go")

```sql
SELECT h.*, t.trans_list, t.sense, t.score, t.importance, t.is_verified
FROM form f
JOIN headword h ON h.id = f.headword_id
JOIN translation t ON t.headword_id = h.id
WHERE f.form = ? COLLATE NOCASE;
```

### 3. Fuzzy search (FTS5 — future)

```sql
SELECT h.written_rep, t.trans_list, bm25(headword_fts) as rank
FROM headword_fts
JOIN headword h ON h.id = headword_fts.rowid
JOIN translation t ON t.headword_id = h.id
WHERE headword_fts MATCH ?
ORDER BY rank
LIMIT ?;
```

## Why This Is Better Than WikDict/Kaikki Schemas

| Aspect | WikDict (entry+translation) | Kaikki (entries) | **Lingolate v2** |
|--------|-----------------------------|-------------------|-------------------|
| Normalization | Partial (entry ↔ translation JOIN) | Flat (1 row = 1 entry) | Full (headword ↔ translation ↔ form ↔ synonym) |
| Multi-sense | ✅ (multiple translation rows per entry) | ❌ (one trans_list per entry) | ✅ (multiple translation rows per headword) |
| Forms | Separate `form` table | ❌ Not supported | ✅ `form` table with 8 grammatical fields |
| Examples | ❌ Not in WikDict | ✅ (one example field) | ✅ (per-sense example) |
| Synonyms | ❌ | ❌ | ✅ (separate table) |
| Matching | `LIKE` on `written_rep` | `LIKE` on `written_rep` | `normalized` column + FTS5 |
| Quality ranking | `is_good`, `score`, `importance` | ❌ None | `is_verified`, `score`, `importance` |
| Metadata | ❌ No version/source in DB | ❌ No version/source in DB | ✅ `_meta` table |
| File size | ~28MB (en-fr) | ~28MB | Smaller (INTEGER FKs vs repeated strings) |
| App code | 4 branches, ~2000 lines | 1 branch, ~20 lines | 1 branch, ~50 lines (clean) |

## Compatibility Strategy

The app's `_getMainTableName()` detects schemas by checking `sqlite_master`. We add
a new detection branch for our schema:

```dart
// Check for Lingolate v2 schema (our custom one)
final hasHeadword = await _hasTable(db, 'headword');
if (hasHeadword) return 'headword';  // new branch, highest priority
```

Then a single clean query replaces the current 4-branch mess. The old schemas
remain supported for backward compatibility with already-downloaded dictionaries.

## Build-Time Optimizations

1. **PRAGMA settings** (applied at build time, persisted in DB file):
   - `PRAGMA journal_mode = OFF` — read-only, no WAL needed
   - `PRAGMA page_size = 4096` — optimal for mobile flash storage
   - `VACUUM` at end — defragment, minimize file size

2. **Batch INSERT** — `executemany()` with 500-row batches for speed

3. **Index after data** — create indexes after all data inserted (faster than
   incremental index updates)

4. **FTS5 populate** — triggers auto-populate during INSERT, no manual sync needed
