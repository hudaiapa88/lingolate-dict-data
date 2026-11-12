# Lingolate Lexicon — schema v3

One **master database** (`lexicon.sqlite3`, built locally / in CI, never shipped)
merges every source. From it, one **pair pack** per language pair
(`pack-{a}-{b}.sqlite3`, a < b) is exported and published as a GitHub Release
asset. The app downloads only the pack for its language pair and works fully
offline afterwards.

Every row carries a `source_id` (→ `source`) and, where it matters, a
`confidence` in 0..1, so the app can rank results, mark low-confidence ones,
and the build can drop a whole source if its license ever becomes a problem.

## Master: `lexicon.sqlite3`

```sql
CREATE TABLE source (
  id          INTEGER PRIMARY KEY,
  key         TEXT UNIQUE NOT NULL,   -- 'omw-en', 'tufs-tr', 'panlex', 'tatoeba'
  name        TEXT NOT NULL,
  license     TEXT NOT NULL,          -- SPDX-like id
  url         TEXT NOT NULL,
  attribution TEXT NOT NULL,          -- rendered on the in-app Licenses page
  base_conf   REAL NOT NULL           -- default confidence of its senses
);

-- Language-independent meaning. Linked to Open Multilingual Wordnet's
-- Inter-Lingual Index (ili) and/or Wikidata (qid); PanLex-only meanings get
-- a 'panlex' key until they can be matched.
CREATE TABLE concept (
  id      INTEGER PRIMARY KEY,
  ili     TEXT UNIQUE,                -- 'i35545'
  qid     TEXT,                       -- 'Q123' (Wikidata)
  panlex  INTEGER UNIQUE,             -- PanLex meaning id
  pos     TEXT,                       -- n, v, a, r, ...
  gloss_en TEXT                       -- English definition (AI grounding only)
);

CREATE TABLE lemma (
  id       INTEGER PRIMARY KEY,
  lang     TEXT NOT NULL,             -- app code: 'tr', 'en', ...
  written  TEXT NOT NULL,
  norm     TEXT NOT NULL,             -- casefolded
  pos      TEXT,
  gender   TEXT,
  ipa      TEXT,
  freq     INTEGER,                   -- rank (1 = most frequent), NULL = unknown
  UNIQUE (lang, written, pos)
);

CREATE TABLE sense (
  id          INTEGER PRIMARY KEY,
  lemma_id    INTEGER NOT NULL REFERENCES lemma(id),
  concept_id  INTEGER REFERENCES concept(id),
  source_id   INTEGER NOT NULL REFERENCES source(id),
  confidence  REAL NOT NULL,
  definition  TEXT,                   -- in the lemma's language when available
  UNIQUE (lemma_id, concept_id, source_id)
);

CREATE TABLE form (                   -- inflected forms (went, gitti, ...)
  lemma_id  INTEGER NOT NULL REFERENCES lemma(id),
  form      TEXT NOT NULL,
  norm      TEXT NOT NULL,
  tags      TEXT,                     -- 'past', 'pl;gen', ...
  source_id INTEGER NOT NULL REFERENCES source(id)
);

CREATE TABLE sense_example (          -- usage examples attached to a sense
  sense_id  INTEGER NOT NULL REFERENCES sense(id),
  text      TEXT NOT NULL,
  source_id INTEGER NOT NULL REFERENCES source(id)
);
```

Translation between two languages = two senses (one per language) sharing a
concept. Confidence of a translation = noisy-OR over the concepts/sources that
support it: `1 - Π(1 - min(conf_a, conf_b))`. A PanLex-only translation stays
low (≈ 0.4); the same pair also found in a wordnet or TUFS rises above 0.9.

Base confidence per source: TUFS 0.95, OMW / Open English WordNet 0.90,
Wikidata Lexemes 0.85, PanLex 0.40.

Example sentences are built separately (`scripts/build_tatoeba_examples.py`,
`examples/{a}-{b}.sqlite3`) and copied into the packs.

## Pair pack: `pack-{a}-{b}.sqlite3`

Read-only, mmap-friendly. Both directions are served by one file.

```sql
CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- schema='pack-v3', pair, data_version, generated, counts, licenses (JSON)

CREATE TABLE source (id INTEGER PRIMARY KEY, key TEXT, name TEXT,
                     license TEXT, url TEXT, attribution TEXT);

CREATE TABLE lemma (id INTEGER PRIMARY KEY, lang TEXT, written TEXT,
                    norm TEXT, pos TEXT, gender TEXT, ipa TEXT, freq INTEGER);
CREATE INDEX lemma_lookup ON lemma(lang, norm);

-- Precomputed translations, one row per direction-independent link.
CREATE TABLE trans (
  a_lemma    INTEGER NOT NULL,        -- lemma in language a
  b_lemma    INTEGER NOT NULL,        -- lemma in language b
  confidence REAL NOT NULL,
  sources    TEXT NOT NULL,           -- source keys, ';'-joined
  concept    INTEGER                  -- one supporting concept (for glosses)
);
CREATE INDEX trans_a ON trans(a_lemma);
CREATE INDEX trans_b ON trans(b_lemma);

CREATE TABLE definition (lemma_id INTEGER, concept INTEGER, text TEXT, source TEXT);
CREATE TABLE form (lemma_id INTEGER, form TEXT, norm TEXT, tags TEXT);
CREATE INDEX form_lookup ON form(norm);
CREATE TABLE gloss (concept INTEGER PRIMARY KEY, gloss_en TEXT);

-- Tatoeba (CC BY 2.0 FR): see build_tatoeba_examples.py
CREATE TABLE example (id INTEGER PRIMARY KEY, a TEXT, b TEXT, a_norm TEXT,
                      b_norm TEXT, a_src INTEGER, b_src INTEGER,
                      score REAL, via_en INTEGER);
```

Lookups the app performs:
1. word → `lemma` (by `norm`, then via `form.norm` for inflected input);
2. lemma → `trans` (ordered by confidence) → other-language lemmas;
3. lemma → `definition`, `form`;
4. word → `example` rows whose `a_norm`/`b_norm` contain the word, best score
   first (the app re-checks word boundaries / stems);
5. AI grounding: the same rows, formatted as a short `DATA:` block.
