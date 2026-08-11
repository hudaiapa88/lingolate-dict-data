"""Summary report — dictionary count, total size, top pairs, missing langs."""
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DICT_DIR = ROOT / "dictionaries"
MANIFEST = ROOT / "manifest.json"

manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
dicts = manifest["dictionaries"]

print(f"=== Lingolate Dictionary Data — Summary ===\n")
print(f"  Version: {manifest['version']}")
print(f"  Updated: {manifest['updated']}")
print(f"  Total dictionaries: {len(dicts)}")
print(f"  Total size: {sum(d['size'] for d in dicts):,} bytes ({sum(d['size'] for d in dicts)/1024/1024:.1f} MB)")
print(f"  Total headwords: {sum(d['entry_count'] for d in dicts):,}")

# Languages covered
langs = set()
for d in dicts:
    src, tgt = d["code"].split("-")
    langs.add(src)
    langs.add(tgt)
print(f"  Languages covered: {len(langs)} ({', '.join(sorted(langs))})")

# License distribution
license_counts = defaultdict(lambda: {"count": 0, "size": 0, "entries": 0})
for d in dicts:
    lic = d["license"]
    license_counts[lic]["count"] += 1
    license_counts[lic]["size"] += d["size"]
    license_counts[lic]["entries"] += d["entry_count"]

print(f"\n--- License distribution ---")
for lic, stats in sorted(license_counts.items(), key=lambda x: -x[1]["count"]):
    print(f"  {lic:20s}: {stats['count']:>3} dicts, {stats['size']/1024/1024:>8.1f} MB, {stats['entries']:>10,} hw")

# Top 20 largest dictionaries
print(f"\n--- Top 20 largest dictionaries ---")
for d in sorted(dicts, key=lambda x: -x["size"])[:20]:
    print(f"  {d['code']:8s}: {d['size']/1024/1024:>7.1f} MB, {d['entry_count']:>7,} hw, {d['license']}")

# Smallest 10
print(f"\n--- Smallest 10 dictionaries ---")
for d in sorted(dicts, key=lambda x: x["size"])[:10]:
    print(f"  {d['code']:8s}: {d['size']/1024:>7.0f} KB, {d['entry_count']:>7,} hw, {d['license']}")

# Missing pairs (supported langs with no dictionary)
SUPPORTED = {
    "ar", "de", "en", "es", "fr", "hi", "id", "it", "ja",
    "ko", "nl", "pl", "pt", "ru", "sv", "th", "tr", "uk", "vi", "zh",
    "bg", "cs", "da", "el", "fi", "lt", "no",
}
existing_codes = {d["code"] for d in dicts}
missing = []
for src in sorted(SUPPORTED):
    for tgt in sorted(SUPPORTED):
        if src == tgt:
            continue
        if f"{src}-{tgt}" not in existing_codes:
            missing.append(f"{src}-{tgt}")
print(f"\n--- Missing pairs ({len(missing)}) ---")
# Group by missing language
missing_langs = defaultdict(list)
for code in missing:
    src, tgt = code.split("-")
    if src not in langs:
        missing_langs[src].append(code)
    if tgt not in langs:
        missing_langs[tgt].append(code)
for lang in sorted(missing_langs):
    print(f"  {lang}: {len(missing_langs[lang])} pairs missing (lang not in any dict)")
    # Show first 3
    for c in missing_langs[lang][:3]:
        print(f"    e.g. {c}")
