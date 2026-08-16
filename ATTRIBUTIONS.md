# Attributions

This repository distributes bilingual dictionary data derived from multiple
open sources. Attribution is rendered inside the Lingolate app's
"About / Licenses" page to satisfy CC-BY-SA, CC-BY, and WordNet license
requirements.

## Primary Source: WikDict (CC-BY-SA 3.0)

14 languages (de, en, es, fr, id, it, ja, nl, pl, pt, ru, sv, tr, zh),
178 language pairs, 8.1M entries. Data extracted from Wiktionary by the
DBnary project and processed into bilingual SQLite databases by WikDict.

- Website: https://www.wikdict.com
- Download: https://download.wikdict.com/dictionaries/sqlite/
- Source code: https://github.com/karlb/wikdict-gen (MIT)
- License: CC-BY-SA 3.0 (Creative Commons Attribution-ShareAlike 3.0)
- Attribution: "WikDict by Karl Bartel — Data from Wiktionary via DBnary
  licensed under the Creative Commons Attribution-ShareAlike 3.0 License"

### CC-BY-SA License Clarification

CC-BY-SA applies to the **dictionary data** only, not the Lingolate app
code. Commercial use is permitted with proper attribution. The app code,
database schema, and application logic are NOT derivative works of the
dictionary data — only the data itself is. This interpretation is confirmed
by:
- EDRDG (JMdict): "Software using these files does NOT have to be under any
  form of open-source licence. NO restriction on commercial use."
- Major commercial dictionary apps (GoldenDict, KOReader, Dictionary
  Universal) use CC-BY-SA dictionary data with proprietary app code.

## Premium Sources (Language-Specific Gold Standard)

For Japanese, Chinese, and Korean, premium curated dictionaries replace
WikDict with higher-quality, editorial-reviewed data.

### JMdict/EDRDG (Japanese) — CC-BY-SA 4.0

Gold standard for Japanese-English. 218K entries, editorial reviewed.
Replaces WikDict for ja-en and en-ja pairs (464K + 267K entries).

- Website: https://www.edrdg.org/jmdict/j_jmdict.html
- License: CC-BY-SA 4.0 (commercial use explicitly permitted)
- Attribution: "JMdict/EDRDG — CC BY-SA 4.0"
- Note: EDRDG license explicitly states: "Software using these files does
  NOT have to be under any form of open-source licence."

### CC-CEDICT (Chinese) — CC-BY-SA 4.0

Curated Chinese-English dictionary by MDBG. 125K entries.
Replaces WikDict for zh-en and en-zh pairs (197K + 136K entries).

- Website: https://www.mdbg.net/chinese/dictionary?page=cc-cedict
- License: CC-BY-SA 4.0
- Attribution: "CC-CEDICT by MDBG — CC BY-SA 4.0"

### Kengdic (Korean) — MPL 2.0

Korean-English dictionary by Joe Speigle. 117K entries.
Provides ko-en and en-ko pairs (106K + 86K entries) — Korean was not
available in WikDict.

- Repository: https://github.com/garfieldnate/kengdic
- License: MPL 2.0 / LGPL 2.0+
- Attribution: "Kengdic by Joe Speigle — MPL 2.0"

## Kaikki.org / Wiktextract (CC-BY-SA 4.0)

Wiktionary extraction for 5 languages not available in WikDict:
Arabic, Hindi, Thai, Ukrainian, Vietnamese. 371K entries across 10 pairs.

- Website: https://kaikki.org
- Source: https://github.com/tatuylonen/wiktextract
- License: CC-BY-SA 4.0
- Attribution: "Kaikki.org (Wiktextract) by Tatu Ylonen — CC BY-SA 4.0"
- Citation: Ylonen (2022), "Wiktextract: Wiktionary as Machine-Readable
  Structured Data", LREC 2022, pp. 1317-1325.

## Pivot Dictionaries (CC-BY-SA 4.0)

Direct bilingual dictionaries built OFFLINE at build time by pivoting
through English. For example, ar-de is built from ar-en (Kaikki.org) +
en-de (WikDict). 160 pairs, 5.4M entries.

This is NOT runtime bridge translation — these are pre-computed direct
SQLite dictionary files with the same schema as WikDict. Polysemy noise
is mitigated by limiting translations to first 3 English glosses and
top 5 target translations per word.

- Source: Built from Kaikki.org + WikDict + JMdict + CC-CEDICT + Kengdic
- License: CC-BY-SA 4.0 (inherits from source data)
- Attribution: "Pivot dictionary built from Kaikki.org + WikDict/JMdict/
  CEDICT/Kengdic data — CC BY-SA 4.0"

## Fallback Sources (for remaining pairs)

### omw-data 1.4 (Open Multilingual Wordnet)

17 languages, large wordnets (156K entries for English). Cross-language
translations via ILI (Inter-Lingual Index).

#### Princeton WordNet 3.0 (English)
- Repository: https://github.com/omwn/omw-data (omw-en)
- License: Princeton WordNet 3.0 License (OSI-approved, commercial-safe)
- URL: https://wordnet.princeton.edu/license-and-commercial-use
- Attribution: "Princeton WordNet 3.0, Copyright 2006 Princeton University"

#### NICT Japanese WordNet (Japanese)
- Repository: https://github.com/omwn/omw-data (omw-ja)
- License: NICT WordNet License (same terms as Princeton, commercial-safe)
- Attribution: "Japanese WordNet, Copyright 2009 NICT"

#### BulTreeBank Wordnet (Bulgarian)
- Repository: https://github.com/omwn/omw-data (omw-bg)
- License: CC-BY 3.0
- Attribution: "BulTreeBank Wordnet (BTB-WN) — CC BY 3.0"

#### DanNet (Danish)
- Repository: https://github.com/omwn/omw-data (omw-da)
- License: WordNet-style (commercial-safe)
- Attribution: "DanNet"

#### Greek Wordnet (Greek)
- Repository: https://github.com/omwn/omw-data (omw-el)
- License: Apache-2.0
- Attribution: "Greek Wordnet — Apache 2.0"

#### Multilingual Central Repository (Spanish, Finnish, Icelandic, Italian, Swedish)
- Repository: https://github.com/omwn/omw-data (omw-es, omw-fi, omw-is, omw-it, omw-sv)
- License: CC-BY 3.0
- Attribution: "Multilingual Central Repository — CC BY 3.0"

#### WOLF (French)
- Repository: https://github.com/omwn/omw-data (omw-fr)
- License: CeCILL-C (MIT-compatible, French law)
- Attribution: "WOLF (French Wordnet) — CeCILL-C"

#### Wordnet Bahasa (Indonesian, Malay)
- Repository: https://github.com/omwn/omw-data (omw-id, omw-zsm)
- License: MIT
- Attribution: "Wordnet Bahasa — MIT"

#### Norwegian Wordnet (Bokmal)
- Repository: https://github.com/omwn/omw-data (omw-nb)
- License: WordNet-style (commercial-safe)
- Attribution: "Norwegian Wordnet"

#### plWordNet (Polish)
- Repository: https://github.com/omwn/omw-data (omw-pl)
- License: WordNet-style (commercial-safe)
- Attribution: "plWordNet"

#### Thai Wordnet (Thai)
- Repository: https://github.com/omwn/omw-data (omw-th)
- License: WordNet-style (commercial-safe)
- Attribution: "Thai Wordnet"

#### Chinese Open Wordnet (Mandarin Chinese)
- Repository: https://github.com/omwn/omw-data (omw-cmn)
- License: WordNet-style (commercial-safe)
- Attribution: "Chinese Open Wordnet"

### TUFS Basic Vocabulary (CC-BY 4.0)

23 languages, ~500-1000 entries each. Used for languages not in omw-data
and for pronunciation audio + example sentences.

- Repository: https://github.com/omwn/tufs
- License: CC-BY 4.0
- Attribution: "TUFS Basic Vocabulary — CC BY 4.0"
- Citation: Bond, Nomoto, Morgado da Costa, Bond (LREC 2020), "Linking the
  TUFS Basic Vocabulary to the Open Multilingual Wordnet"

### PanLex (CC0)

Used for language pairs not covered by other sources.

- Website: https://panlex.org
- License: CC0 1.0 (Public Domain)
- Attribution: "PanLex — CC0 1.0"

## How Attribution Is Surfaced

The Lingolate app reads `manifest.json` and renders an "About / Licenses"
page that lists every upstream source, its license, and a link to the
original repository. This satisfies the attribution requirement of all
CC-BY-SA, CC-BY, and WordNet-style licenses.
