#!/usr/bin/env bash
# Quality-fix rebuild on a fresh Colab VM (Drive mounted at /content/drive,
# repo cloned to /content/ldd). Restores the verified master backup, redoes
# the frequency ranks, adds the closed-class table, then:
#   phase A  showcase packs + quality report  -> /content/preview.md
#   phase B  every pack + manifest + quality report + verified Drive backup
# The upload runs from a notebook cell (GITHUB_TOKEN secret) and only when
# the release gates pass. Progress: tail -f /content/fix.log
set -euo pipefail
cd /content/ldd
VERSION=${VERSION:-2026.11.2}
D=/content/drive/MyDrive/lingolate-lexicon-build
export PYTHONIOENCODING=utf-8
SHOW=en-tr,de-tr,ko-tr,ar-tr,en-ja,es-ru,fr-zh

apt-get -qq install -y zstd > /dev/null
pip install -q -r scripts/requirements.txt
mkdir -p sources_cache
for s in wikidata tatoeba cili; do rsync -a "$D/sources_cache/$s" sources_cache/; done
rsync -a "$D/examples/" examples/
echo "[$(date +%T)] restoring master"
zstd -q -d -f -T0 "$D/lexicon.sqlite3.zst" -o lexicon.sqlite3
python - <<'EOF'
import sqlite3
assert sqlite3.connect('lexicon.sqlite3').execute('PRAGMA quick_check').fetchone()[0] == 'ok'
print('master ok')
EOF

python scripts/enrich_lexicon.py --steps freq,closed
python - <<'EOF'
import sqlite3
m = sqlite3.connect('lexicon.sqlite3')
for lang in ('en', 'tr', 'de', 'fr', 'es'):
    top = [w for (w,) in m.execute(
        'SELECT written FROM lemma WHERE lang = ? AND freq IS NOT NULL GROUP BY freq ORDER BY freq LIMIT 30', (lang,))]
    print(lang, ' '.join(top))
EOF

# phase A
rm -rf packs
python scripts/export_packs.py --version "$VERSION" --pairs "$SHOW"
python scripts/quality_report.py --out /content/preview.md
echo PHASE_A_DONE

# phase B
zstd -q -f -T0 -3 lexicon.sqlite3 -o /content/lexicon.sqlite3.zst
backup() {
  local src=$1 dst=$2
  cp "$src" "$dst"
  sync
  local a b
  a=$(stat -c %s "$src"); b=$(stat -c %s "$dst")
  if [ "$a" != "$b" ]; then echo "BACKUP MISMATCH $dst ($a != $b)"; return 1; fi
  echo "backup ok $dst ($a bytes)"
}
backup /content/lexicon.sqlite3.zst "$D/lexicon-$VERSION.sqlite3.zst"
python scripts/export_packs.py --version "$VERSION" --skip-existing
python scripts/generate_pack_manifest.py --version "$VERSION" --tag "packs-v$VERSION"
python scripts/quality_report.py --out quality_report.md
tar -cf /content/packs.tar packs manifest-v3.json quality_report.md
backup /content/packs.tar "$D/packs-$VERSION.tar"
cp quality_report.md "$D/quality_report-$VERSION.md"
echo ALLDONE
