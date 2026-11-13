#!/usr/bin/env bash
# Final pack build on a Colab VM that holds the enriched master DB in
# /content/ldd (after colab_enrich.sh): export (prune included) → manifest →
# quality report with release gates → verified backup to Drive.
# The upload runs from a notebook cell (needs the GITHUB_TOKEN secret).
# Progress: tail -f /content/final.log
set -euo pipefail
cd /content/ldd
VERSION=${VERSION:-2026.11.1}
D=/content/drive/MyDrive/lingolate-lexicon-build
export PYTHONIOENCODING=utf-8

rm -rf packs
python scripts/export_packs.py --version "$VERSION"
python scripts/generate_pack_manifest.py --version "$VERSION" --tag "packs-v$VERSION"
python scripts/quality_report.py --out quality_report.md

# Backup: compressed copies, checked by size after the write (a plain cp of
# the 9 GB master to the Drive mount once ended up truncated).
backup() {
  local src=$1 dst=$2
  cp "$src" "$dst"
  sync
  local a b
  a=$(stat -c %s "$src"); b=$(stat -c %s "$dst")
  if [ "$a" != "$b" ]; then echo "BACKUP MISMATCH $dst ($a != $b)"; return 1; fi
  echo "backup ok $dst ($a bytes)"
}
apt-get -qq install -y zstd > /dev/null
zstd -q -f -T0 -3 lexicon.sqlite3 -o /content/lexicon.sqlite3.zst
backup /content/lexicon.sqlite3.zst "$D/lexicon.sqlite3.zst"
tar -cf /content/packs.tar packs manifest-v3.json quality_report.md
backup /content/packs.tar "$D/packs.tar"
cp quality_report.md "$D/"
echo ALLDONE
