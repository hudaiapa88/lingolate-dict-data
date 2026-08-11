"""Quick smoke test for the Lingolate v2 schema DB."""
import sqlite3
import sys
from pathlib import Path

db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    r"E:\project\mobile-apps\lingolate-dict-data\dictionaries\en-tr.sqlite3"
)

conn = sqlite3.connect(str(db_path))

print(f"=== {db_path.name} ===\n")

# 1. Metadata
print("--- _meta ---")
for key, value in conn.execute("SELECT key, value FROM _meta").fetchall():
    print(f"  {key:20s} = {value}")

# 2. Tables
print("\n--- tables ---")
tables = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
).fetchall()
for (t,) in tables:
    count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t:20s} {count:>10d} rows")

# 3. Indexes
print("\n--- indexes ---")
idxs = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
).fetchall()
for (i,) in idxs:
    print(f"  {i}")

# 4. Sample query — exact match
print("\n--- exact match: 'hello' ---")
results = conn.execute(
    """
    SELECT h.written_rep, h.normalized, t.trans_list, t.score, t.importance, t.is_verified
    FROM headword h
    JOIN translation t ON t.headword_id = h.id
    WHERE h.normalized = 'hello'
    """
).fetchall()
for row in results:
    print(f"  {row}")

# 5. Sample query — prefix match
print("\n--- prefix match: 'hello%' ---")
results = conn.execute(
    """
    SELECT h.written_rep, t.trans_list,
           CASE WHEN h.normalized = 'hello' THEN 0 ELSE 1 END as exact
    FROM headword h
    JOIN translation t ON t.headword_id = h.id
    WHERE h.normalized LIKE 'hello%'
    ORDER BY exact ASC
    LIMIT 5
    """
).fetchall()
for row in results:
    print(f"  {row}")

# 6. FTS5 test
print("\n--- FTS5 fuzzy: 'helo' (misspelled) ---")
try:
    results = conn.execute(
        """
        SELECT h.written_rep, t.trans_list, bm25(headword_fts) as rank
        FROM headword_fts
        JOIN headword h ON h.id = headword_fts.rowid
        JOIN translation t ON t.headword_id = h.id
        WHERE headword_fts MATCH 'helo'
        ORDER BY rank
        LIMIT 3
        """
    ).fetchall()
    for row in results:
        print(f"  {row}")
except sqlite3.OperationalError as e:
    print(f"  FTS5 error: {e}")

# 7. File size
print(f"\n--- file size: {db_path.stat().st_size:,} bytes ---")

conn.close()
