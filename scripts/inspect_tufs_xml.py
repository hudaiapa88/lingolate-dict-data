"""Inspect tufs XML structure — verify WN-LMF 1.4 compatibility with omw."""
import xml.etree.ElementTree as ET
from pathlib import Path

TUF_DIR = Path(r"E:\project\mobile-apps\lingolate-dict-data\sources_cache\tufs")

for lang in ["de", "tr", "ar"]:
    xml_files = list((TUF_DIR / lang).glob("*.xml"))
    if not xml_files:
        print(f"\n=== {lang}: no XML ===")
        continue
    xml_path = xml_files[0]
    print(f"\n=== {lang} ({xml_path.name}, {xml_path.stat().st_size:,} bytes) ===")
    content = xml_path.read_text(encoding="utf-8", errors="replace")
    root = ET.fromstring(content)
    lexicon = root.find("Lexicon")
    if lexicon is None:
        print("  No Lexicon!")
        continue
    print(f"  id={lexicon.attrib.get('id')} language={lexicon.attrib.get('language')}")
    print(f"  license={lexicon.attrib.get('license')}")
    le_count = len(lexicon.findall("LexicalEntry"))
    ss_count = len(lexicon.findall("Synset"))
    print(f"  LexicalEntry: {le_count}")
    print(f"  Synset: {ss_count}")
    # ILI coverage
    ilis = set()
    for ss in lexicon.findall("Synset"):
        ili = ss.attrib.get("ili")
        if ili:
            ilis.add(ili)
    print(f"  Unique ILIs: {len(ilis)}")
    # Sample
    for le in lexicon.findall("LexicalEntry")[:2]:
        xml_str = ET.tostring(le, encoding="unicode")
        print(f"\n  --- LexicalEntry ---")
        print(xml_str[:800])
    for ss in lexicon.findall("Synset")[:2]:
        xml_str = ET.tostring(ss, encoding="unicode")
        print(f"\n  --- Synset ---")
        print(xml_str[:800])
