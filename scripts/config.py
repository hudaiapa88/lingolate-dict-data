"""Configuration for lingolate-dict-data build pipeline.

Defines supported languages, upstream source registries (omw-data 1.4, tufs),
license metadata, and the language pair planning logic.

Strategy (3-tier):
  TIER 1: omw-data 1.4 — 17 langs, large wordnets (156K en, 94K ja), WordNet/CC-BY/Apache/MIT/CeCILL-C
  TIER 2: tufs — 23 langs, basic vocabulary (~500 entry/lang), CC-BY 4.0
  TIER 3: PanLex — gap filler for remaining langs, CC0 (future)

Cross-language translation uses ILI (Inter-Lingual Index) — synsets with the
same ILI across languages denote the same concept.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# ---------------------------------------------------------------------------
# Supported languages — must match lib/config/dynamic_dictionary_sources.dart
# `supportedLanguages` in the Lingolate app (27 langs).
# ---------------------------------------------------------------------------
SUPPORTED_LANGS: frozenset[str] = frozenset(
    {
        # Tier 1 — MLKit + UI (20)
        "ar", "de", "en", "es", "fr", "hi", "id", "it", "ja",
        "ko", "nl", "pl", "pt", "ru", "sv", "th", "tr", "uk", "vi", "zh",
        # Tier 2 — WikDict extras (7)
        "bg", "cs", "da", "el", "fi", "lt", "no",
    }
)


# ---------------------------------------------------------------------------
# License registry — SPDX-like identifiers + human-readable attribution
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class License:
    id: str  # SPDX-like: WordNet-3.0, CC-BY-3.0, CC-BY-4.0, Apache-2.0, MIT, CeCILL-C, CC0-1.0
    name: str
    url: str
    commercial: bool  # True = safe for closed-source commercial use
    attribution_required: bool


LICENSES: dict[str, License] = {
    "WordNet-3.0": License(
        id="WordNet-3.0",
        name="Princeton WordNet 3.0 License",
        url="https://wordnet.princeton.edu/license-and-commercial-use",
        commercial=True,
        attribution_required=True,
    ),
    "wordnet": License(  # Generic "wordnet" license (NICT, DanNet, etc. — same terms as Princeton)
        id="wordnet",
        name="WordNet-style License (NICT/DanNet/etc.)",
        url="https://wordnet.princeton.edu/license-and-commercial-use",
        commercial=True,
        attribution_required=True,
    ),
    "CC-BY-3.0": License(
        id="CC-BY-3.0",
        name="Creative Commons Attribution 3.0",
        url="https://creativecommons.org/licenses/by/3.0/",
        commercial=True,
        attribution_required=True,
    ),
    "CC-BY-4.0": License(
        id="CC-BY-4.0",
        name="Creative Commons Attribution 4.0",
        url="https://creativecommons.org/licenses/by/4.0/",
        commercial=True,
        attribution_required=True,
    ),
    "Apache-2.0": License(
        id="Apache-2.0",
        name="Apache License 2.0",
        url="https://www.apache.org/licenses/LICENSE-2.0",
        commercial=True,
        attribution_required=True,
    ),
    "MIT": License(
        id="MIT",
        name="MIT License",
        url="https://opensource.org/licenses/MIT",
        commercial=True,
        attribution_required=True,
    ),
    "CeCILL-C": License(
        id="CeCILL-C",
        name="CeCILL-C License (MIT-compatible, French)",
        url="http://www.cecill.info/licenses/Licence_CeCILL-C_V1-en.html",
        commercial=True,
        attribution_required=True,
    ),
    "CC0-1.0": License(
        id="CC0-1.0",
        name="Creative Commons CC0 1.0 (Public Domain)",
        url="https://creativecommons.org/publicdomain/zero/1.0/",
        commercial=True,
        attribution_required=False,
    ),
    # Viral licenses — listed for documentation but NEVER used
    "CC-BY-SA-3.0": License(
        id="CC-BY-SA-3.0",
        name="Creative Commons Attribution-ShareAlike 3.0 (VIRAL — DO NOT USE)",
        url="https://creativecommons.org/licenses/by-sa/3.0/",
        commercial=False,  # Not safe for closed-source
        attribution_required=True,
    ),
    "CC-BY-SA-4.0": License(
        id="CC-BY-SA-4.0",
        name="Creative Commons Attribution-ShareAlike 4.0 (VIRAL — DO NOT USE)",
        url="https://creativecommons.org/licenses/by-sa/4.0/",
        commercial=False,
        attribution_required=True,
    ),
}


# ---------------------------------------------------------------------------
# omw-data 1.4 language registry
# Source: https://github.com/omwn/omw-data/releases/tag/v1.4 (index.toml)
# Only languages with commercial-safe licenses are included.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OmwLang:
    omw_id: str  # e.g. "omw-en"
    lang_code: str  # e.g. "en" (our code)
    omw_lang: str  # e.g. "en", "cmn-Hans", "zsm"
    label: str
    license_id: str  # key in LICENSES
    url: str  # tar.xz download URL


OMW_LANGS: dict[str, OmwLang] = {
    # Our lang_code -> OmwLang
    "bg": OmwLang("omw-bg", "bg", "bg", "BulTreeBank Wordnet (BTB-WN)", "CC-BY-3.0",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-bg-1.4.tar.xz"),
    "da": OmwLang("omw-da", "da", "da", "DanNet", "wordnet",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-da-1.4.tar.xz"),
    "el": OmwLang("omw-el", "el", "el", "Greek Wordnet", "Apache-2.0",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-el-1.4.tar.xz"),
    "en": OmwLang("omw-en", "en", "en", "OMW English Wordnet (WordNet 3.0)", "WordNet-3.0",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-en-1.4.tar.xz"),
    "es": OmwLang("omw-es", "es", "es", "Multilingual Central Repository (Spanish)", "CC-BY-3.0",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-es-1.4.tar.xz"),
    "fi": OmwLang("omw-fi", "fi", "fi", "Multilingual Central Repository (Finnish)", "CC-BY-3.0",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-fi-1.4.tar.xz"),
    "fr": OmwLang("omw-fr", "fr", "fr", "WOLF (French Wordnet)", "CeCILL-C",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-fr-1.4.tar.xz"),
    "id": OmwLang("omw-id", "id", "id", "Wordnet Bahasa (Indonesia)", "MIT",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-id-1.4.tar.xz"),
    "is": OmwLang("omw-is", "is", "is", "Multilingual Central Repository (Icelandic)", "CC-BY-3.0",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-is-1.4.tar.xz"),
    "it": OmwLang("omw-it", "it", "it", "Multilingual Central Repository (Italian)", "CC-BY-3.0",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-it-1.4.tar.xz"),
    "ja": OmwLang("omw-ja", "ja", "ja", "Japanese WordNet (NICT)", "wordnet",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-ja-1.4.tar.xz"),
    "ms": OmwLang("omw-zsm", "ms", "zsm", "Wordnet Bahasa (Malay)", "MIT",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-zsm-1.4.tar.xz"),
    "nb": OmwLang("omw-nb", "nb", "nb", "Norwegian Wordnet (Bokmål)", "wordnet",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-nb-1.4.tar.xz"),
    "no": OmwLang("omw-nb", "no", "nb", "Norwegian Wordnet (Bokmål = no)", "wordnet",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-nb-1.4.tar.xz"),
    "pl": OmwLang("omw-pl", "pl", "pl", "Polish Wordnet (plWordNet)", "wordnet",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-pl-1.4.tar.xz"),
    "sv": OmwLang("omw-sv", "sv", "sv", "Multilingual Central Repository (Swedish)", "CC-BY-3.0",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-sv-1.4.tar.xz"),
    "th": OmwLang("omw-th", "th", "th", "Thai Wordnet", "wordnet",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-th-1.4.tar.xz"),
    "zh": OmwLang("omw-cmn", "zh", "cmn-Hans", "Chinese Open Wordnet (Mandarin)", "wordnet",
                  "https://github.com/omwn/omw-data/releases/download/v1.4/omw-cmn-1.4.tar.xz"),
}

# omw-data langs we SKIP (viral CC-BY-SA — not commercial-safe):
#   arb (ar) — CC-BY-SA 3.0
#   lt      — CC-BY-SA 3.0
#   nl      — CC-BY-SA 4.0
#   pt      — CC-BY-SA
#   ro      — CC-BY-SA
#   sk      — CC-BY-SA 3.0
#   sl      — CC-BY-SA 3.0


# ---------------------------------------------------------------------------
# tufs language registry (CC-BY 4.0 — basic vocabulary, ~500 entry/lang)
# Source: https://github.com/omwn/tufs/releases/latest
# Used for languages NOT in omw-data, or as enrichment.
# ---------------------------------------------------------------------------
TUF_VERSION = "2026.04.06"
TUF_RELEASE_TAG = "v2026.04.06"
TUF_BASE_URL = (
    f"https://github.com/omwn/tufs/releases/download/{TUF_RELEASE_TAG}"
    f"/tufs-{{lang}}-{TUF_VERSION}.tar.xz"
)

# tufs covers 23 languages; we use it for langs not in omw-data
TUF_ONLY_LANGS: dict[str, str] = {
    # our lang_code -> tufs lang_code
    "ar": "ar",
    "de": "de",
    "hi": "hi",
    "ko": "ko",
    "ru": "ru",
    "tr": "tr",
    "vi": "vi",
}

# tufs also has these (in omw-data too, but tufs adds pronunciation audio + examples)
TUF_SHARED_LANGS: dict[str, str] = {
    "en": "en", "es": "es", "fr": "fr", "id": "id", "ja": "ja",
    "ms": "ms", "pt": "pt", "th": "th", "zh": "zh",
}


def tufs_url(lang: str) -> str:
    tufs_lang = TUF_ONLY_LANGS.get(lang) or TUF_SHARED_LANGS.get(lang)
    if not tufs_lang:
        return ""
    return TUF_BASE_URL.format(lang=tufs_lang)


# ---------------------------------------------------------------------------
# Source resolution — which source provides a given language?
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LangSource:
    lang: str
    primary: str  # "omw" or "tufs"
    license_id: str
    attribution: str
    url: str
    omw_id: str | None = None  # for omw


def source_for_lang(lang: str) -> LangSource | None:
    """Return the best commercial-safe source for a language."""
    if lang in OMW_LANGS:
        omw = OMW_LANGS[lang]
        lic = LICENSES[omw.license_id]
        return LangSource(
            lang=lang,
            primary="omw",
            license_id=omw.license_id,
            attribution=f"{omw.label} — {lic.name}",
            url=omw.url,
            omw_id=omw.omw_id,
        )
    if lang in TUF_ONLY_LANGS:
        url = tufs_url(lang)
        if url:
            return LangSource(
                lang=lang,
                primary="tufs",
                license_id="CC-BY-4.0",
                attribution="TUFS Basic Vocabulary — CC BY 4.0",
                url=url,
            )
    return None


# ---------------------------------------------------------------------------
# Pair planning
# ---------------------------------------------------------------------------
# For each (src, tgt) pair, we build a bilingual dictionary by:
#   1. Loading both lang wordnets
#   2. Finding synsets that share the same ILI
#   3. For each shared ILI, collecting all written_forms in src → trans_list in tgt
#
# We build pairs where AT LEAST ONE lang is a "pivot" (en, de, es, fr, ja, ru, tr, zh)
# to keep the matrix tractable. Cross-pairs (e.g. de-tr) are built via shared ILI
# without needing a pivot, but we prioritize pivot-anchored pairs first.

PIVOT_LANGS: tuple[str, ...] = ("en", "de", "es", "fr", "ja", "ru", "tr", "zh")


def planned_pairs() -> list[tuple[str, str, str]]:
    """Return list of (src, tgt, source_type) tuples to build.

    source_type is "omw" if both langs are in omw-data, "tufs" if either is
    tufs-only, "mixed" if one is omw and the other is tufs.
    """
    pairs: list[tuple[str, str, str]] = []
    for pivot in PIVOT_LANGS:
        if not source_for_lang(pivot):
            continue
        for lang in SUPPORTED_LANGS:
            if lang == pivot:
                continue
            if not source_for_lang(lang):
                continue
            src_source = source_for_lang(pivot)
            tgt_source = source_for_lang(lang)
            if src_source and tgt_source:
                # Both directions
                pairs.append((pivot, lang, "bili"))
                pairs.append((lang, pivot, "bili"))
    return pairs


def dedupe_pairs(pairs: Iterable[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, str]] = []
    for src, tgt, st in pairs:
        if (src, tgt) in seen:
            continue
        seen.add((src, tgt))
        out.append((src, tgt, st))
    return out


# ---------------------------------------------------------------------------
# Attribution helpers
# ---------------------------------------------------------------------------
def attribution_for_pair(src: str, tgt: str) -> str:
    """Build attribution string for a bilingual dictionary."""
    src_info = source_for_lang(src)
    tgt_info = source_for_lang(tgt)
    parts: list[str] = []
    if src_info:
        parts.append(src_info.attribution)
    if tgt_info and tgt_info.attribution != (src_info.attribution if src_info else ""):
        parts.append(tgt_info.attribution)
    return " + ".join(parts) if parts else "Unknown"


def license_id_for_pair(src: str, tgt: str) -> str:
    """Return the most restrictive license for the pair (both must be satisfied)."""
    src_info = source_for_lang(src)
    tgt_info = source_for_lang(tgt)
    ids: list[str] = []
    if src_info:
        ids.append(src_info.license_id)
    if tgt_info:
        ids.append(tgt_info.license_id)
    if not ids:
        return "unknown"
    # If any is CC-BY, return CC-BY (attribution required)
    # If all are CC0, return CC0
    if all(i == "CC0-1.0" for i in ids):
        return "CC0-1.0"
    # Return the first non-CC0 (they all require attribution anyway)
    return next((i for i in ids if i != "CC0-1.0"), ids[0])
