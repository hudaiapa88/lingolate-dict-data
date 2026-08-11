"""Inspect WN-LMF 1.4 XML structure — Lexicon → LexicalEntry/Synset."""
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

TUF_DIR = Path(r"E:\project\mobile-apps\lingolate-dict-data\sources_cache\tufs")

for lang in ["en", "tr", "ja"]:
    archive = TUF_DIR / f"tufs-{lang}.tar.xz"
    print(f"\n{'='*70}\n=== {lang.upper()} ===\n{'='*70}")
    with tarfile.open(archive, "r:xz") as tar:
        xml_name = next(m.name for m in tar.getmembers() if m.name.endswith(".xml"))
        f = tar.extractfile(xml_name)
        content = f.read().decode("utf-8")

    root = ET.fromstring(content)
    lexicon = root.find("Lexicon")
    print(f"Lexicon attrs: {dict(lexicon.attrib)}")

    # Lexicon children
    lex_children = list(lexicon)
    lex_tags = {}
    for c in lex_children:
        lex_tags[c.tag] = lex_tags.get(c.tag, 0) + 1
    print(f"Lexicon children: {lex_tags}")

    # First LexicalEntry
    le = lexicon.find("LexicalEntry")
    if le is not None:
        print(f"\n--- First LexicalEntry ---")
        xml_str = ET.tostring(le, encoding="unicode")
        print(xml_str[:2500])

    # First Synset
    ss = lexicon.find("Synset")
    if ss is not None:
        print(f"\n--- First Synset ---")
        xml_str = ET.tostring(ss, encoding="unicode")
        print(xml_str[:2000])
