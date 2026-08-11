"""Smoke test with language-appropriate queries."""
import sqlite3
import sys
from pathlib import Path

db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    r"E:\project\mobile-apps\lingolate-dict-data\dictionaries\ja-es.sqlite3"
)

conn = sqlite3.connect(str(db_path))
pair = conn.execute("SELECT value FROM _meta WHERE key='pair'").fetchone()[0]
src_lang = conn.execute("SELECT value FROM _meta WHERE key='src_lang'").fetchone()[0]
print(f"=== {db_path.name} ({pair}) ===\n")

# Sample 10 headwords
print("--- 10 sample headwords ---")
rows = conn.execute("SELECT written_rep, trans_list, pos, sense, is_verified FROM headword h JOIN translation t ON t.headword_id=h.id LIMIT 10").fetchall()
for r in rows:
    print(f"  {r[0]:15s} [{r[2] or '?'}] -> {r[1][:60]}")
    if r[3]:
        print(f"    sense: {r[3][:80]}")

# Try common words based on src_lang
test_words = {
    "ja": ["勉強", "学校", "本", "水", "食べる", "行く", "良い"],
    "es": ["hola", "casa", "comer", "ir", "bueno"],
    "en": ["hello", "house", "eat", "go", "good", "study", "school", "book", "water"],
}.get(src_lang, ["hello", "house"])

print(f"\n--- exact match tests ({src_lang}) ---")
for w in test_words:
    norm = w.lower()
    result = conn.execute(
        "SELECT h.written_rep, t.trans_list, t.sense, t.is_verified FROM headword h "
        "JOIN translation t ON t.headword_id=h.id WHERE h.normalized = ?",
        (norm,)
    ).fetchall()
    if result:
        for r in result:
            print(f"  {r[0]:15s} -> {r[1][:80]}")
            if r[2]:
                print(f"    sense: {r[2][:100]}")
    else:
        print(f"  {w:15s} -> (not found)")

# FTS5 fuzzy test
print(f"\n--- FTS5 fuzzy: '{test_words[0]}' ---")
try:
    results = conn.execute(
        "SELECT h.written_rep, t.trans_list, bm25(headword_fts) as rank "
        "FROM headword_fts JOIN headword h ON h.id = headword_fts.rowid "
        "JOIN translation t ON t.headword_id = h.id "
        "WHERE headword_fts MATCH ? ORDER BY rank LIMIT 3",
        (test_words[0],)
    ).fetchall()
    for r in results:
        print(f"  {r[0]:15s} -> {r[1][:60]} (rank={r[2]:.2f})")
except Exception as e:
    print(f"  FTS5 error: {e}")

# Stats
print(f"\n--- stats ---")
verified = conn.execute("SELECT COUNT(*) FROM translation WHERE is_verified=1").fetchone()[0]
with_sense = conn.execute("SELECT COUNT(*) FROM translation WHERE sense IS NOT NULL").fetchone()[0]
with_pos = conn.execute("SELECT COUNT(*) FROM headword WHERE pos IS NOT NULL").fetchone()[0]
total = conn.execute("SELECT COUNT(*) FROM headword").fetchone()[0]
print(f"  total headwords: {total:,}")
print(f"  verified (ILI-mapped): {verified:,} ({100*verified/total:.1f}%)")
print(f"  with sense/definition: {with_sense:,} ({100*with_sense/total:.1f}%)")
print(f"  with POS: {with_pos:,} ({100*with_pos/total:.1f}%)")

conn.close()
