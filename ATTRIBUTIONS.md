# Attributions

This repository distributes bilingual dictionary data derived from multiple
open wordnet sources. All sources are **commercial-use-safe** — no ShareAlike
(viral) licenses are used. Attribution is rendered inside the Lingolate app's
"About / Licenses" page to satisfy CC-BY and WordNet license requirements.

## Primary Source: omw-data 1.4 (Open Multilingual Wordnet)

17 languages, large wordnets (156K entries for English). Cross-language
translations via ILI (Inter-Lingual Index).

### Princeton WordNet 3.0 (English)
- Repository: https://github.com/omwn/omw-data (omw-en)
- License: Princeton WordNet 3.0 License (OSI-approved, commercial-safe)
- URL: https://wordnet.princeton.edu/license-and-commercial-use
- Attribution: "Princeton WordNet 3.0, Copyright 2006 Princeton University"

### NICT Japanese WordNet (Japanese)
- Repository: https://github.com/omwn/omw-data (omw-ja)
- License: NICT WordNet License (same terms as Princeton, commercial-safe)
- Attribution: "Japanese WordNet, Copyright 2009 NICT"

### BulTreeBank Wordnet (Bulgarian)
- Repository: https://github.com/omwn/omw-data (omw-bg)
- License: CC-BY 3.0
- Attribution: "BulTreeBank Wordnet (BTB-WN) — CC BY 3.0"

### DanNet (Danish)
- Repository: https://github.com/omwn/omw-data (omw-da)
- License: WordNet-style (commercial-safe)
- Attribution: "DanNet"

### Greek Wordnet (Greek)
- Repository: https://github.com/omwn/omw-data (omw-el)
- License: Apache-2.0
- Attribution: "Greek Wordnet — Apache 2.0"

### Multilingual Central Repository (Spanish, Finnish, Icelandic, Italian, Swedish)
- Repository: https://github.com/omwn/omw-data (omw-es, omw-fi, omw-is, omw-it, omw-sv)
- License: CC-BY 3.0
- Attribution: "Multilingual Central Repository — CC BY 3.0"

### WOLF (French)
- Repository: https://github.com/omwn/omw-data (omw-fr)
- License: CeCILL-C (MIT-compatible, French law)
- Attribution: "WOLF (French Wordnet) — CeCILL-C"

### Wordnet Bahasa (Indonesian, Malay)
- Repository: https://github.com/omwn/omw-data (omw-id, omw-zsm)
- License: MIT
- Attribution: "Wordnet Bahasa — MIT"

### Norwegian Wordnet (Bokmål)
- Repository: https://github.com/omwn/omw-data (omw-nb)
- License: WordNet-style (commercial-safe)
- Attribution: "Norwegian Wordnet"

### plWordNet (Polish)
- Repository: https://github.com/omwn/omw-data (omw-pl)
- License: WordNet-style (commercial-safe)
- Attribution: "plWordNet"

### Thai Wordnet (Thai)
- Repository: https://github.com/omwn/omw-data (omw-th)
- License: WordNet-style (commercial-safe)
- Attribution: "Thai Wordnet"

### Chinese Open Wordnet (Mandarin Chinese)
- Repository: https://github.com/omwn/omw-data (omw-cmn)
- License: WordNet-style (commercial-safe)
- Attribution: "Chinese Open Wordnet"

## Enrichment Source: tufs (TUFS Basic Vocabulary)

23 languages, ~500-1000 entries each. Used for languages not in omw-data
(de, ko, ru, tr, ar, hi, vi) and for pronunciation audio + example sentences.

- Repository: https://github.com/omwn/tufs
- License: CC-BY 4.0
- Attribution: "TUFS Basic Vocabulary — CC BY 4.0"
- Citation: Bond, Nomoto, Morgado da Costa, Bond (LREC 2020), "Linking the
  TUFS Basic Vocabulary to the Open Multilingual Wordnet"

## Excluded Sources (Viral Licenses — NOT Used)

The following omw-data languages are **excluded** because their licenses
contain a ShareAlike clause that would require the Lingolate app to be
open-sourced:

- omw-arb (Arabic) — CC-BY-SA 3.0
- omw-lt (Lithuanian) — CC-BY-SA 3.0
- omw-nl (Dutch) — CC-BY-SA 4.0
- omw-pt (Portuguese) — CC-BY-SA
- omw-ro (Romanian) — CC-BY-SA
- omw-sk (Slovak) — CC-BY-SA 3.0
- omw-sl (Slovenian) — CC-BY-SA 3.0

These languages are filled by tufs (CC-BY 4.0) or PanLex (CC0) instead.

## How Attribution Is Surfaced

The Lingolate app reads `manifest.json` and renders an "About / Licenses"
page that lists every upstream source, its license, and a link to the
original repository. This satisfies the attribution requirement of all
CC-BY and WordNet-style licenses.
