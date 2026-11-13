#!/usr/bin/env bash
# Second pass of colab_fix.sh on the same VM (master already restored and
# enriched): closed-class table first, then frequency (uses it), then the
# preview + full export as in colab_fix.sh. Logs to /content/fix.log.
set -euo pipefail
cd /content/ldd
git pull -q --ff-only
VERSION=${VERSION:-2026.11.2}
D=/content/drive/MyDrive/lingolate-lexicon-build
export PYTHONIOENCODING=utf-8
SHOW=en-tr,de-tr,ko-tr,ar-tr,en-ja,es-ru,fr-zh

python scripts/enrich_lexicon.py --steps closed,freq
python - <<'PY'
import sqlite3
m = sqlite3.connect('lexicon.sqlite3')
for lang in ('en', 'tr', 'de', 'fr', 'es'):
    top = [w for (w,) in m.execute(
        'SELECT written FROM lemma WHERE lang = ? AND freq IS NOT NULL GROUP BY freq ORDER BY freq LIMIT 30', (lang,))]
    print(lang, ' '.join(top))
PY
rm -f lexicon-aux.sqlite3
rm -rf packs
python scripts/export_packs.py --version "$VERSION" --pairs "$SHOW"
python scripts/quality_report.py --out /content/preview2.md
cp /content/preview2.md "$D/preview-$VERSION.md"
echo PHASE_A_DONE

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
