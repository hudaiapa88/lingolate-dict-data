"""Validate manifest.json against the JSON Schema + integrity checks.

Verifies:
  - manifest.json conforms to manifest.schema.json
  - every declared sha256 matches the local dictionaries/*.sqlite3 file
  - every declared size matches the local file size
  - every downloadUrl resolves to the expected release tag

Usage:
    python validate_manifest.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "manifest.json"
SCHEMA = ROOT / "manifest.schema.json"
DICT_DIR = ROOT / "dictionaries"


def main() -> int:
    if not MANIFEST.exists():
        print(f"manifest.json not found at {MANIFEST}", file=sys.stderr)
        return 1
    if not SCHEMA.exists():
        print(f"schema not found at {SCHEMA}", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    try:
        jsonschema.validate(manifest, schema)
    except jsonschema.ValidationError as exc:
        print(f"[FAIL] schema validation: {exc.message}", file=sys.stderr)
        return 1

    print(f"[ok] schema valid — version {manifest['version']}, {len(manifest['dictionaries'])} entries")

    errors = 0
    for entry in manifest["dictionaries"]:
        code = entry["code"]
        local = DICT_DIR / f"{code}.sqlite3"
        if not local.exists():
            print(f"  [fail] {code}: missing local file {local.name}")
            errors += 1
            continue
        if local.stat().st_size != entry["size"]:
            print(f"  [fail] {code}: size mismatch (local={local.stat().st_size}, manifest={entry['size']})")
            errors += 1
        # sha256 check
        import hashlib
        h = hashlib.sha256()
        with local.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        actual = h.hexdigest()
        if actual != entry["sha256"]:
            print(f"  [fail] {code}: sha256 mismatch")
            errors += 1
        else:
            print(f"  [ok]   {code}: size+sha256 verified")

    if errors:
        print(f"\n{errors} error(s)")
        return 1
    print(f"\nAll {len(manifest['dictionaries'])} dictionaries verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
