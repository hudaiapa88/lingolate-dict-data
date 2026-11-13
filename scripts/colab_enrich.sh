#!/usr/bin/env bash
# Enrich the cached master DB and re-export the packs on a Colab VM that
# already holds lexicon.sqlite3, sources_cache/ and examples/ in /content/ldd
# (restored from Drive or left over from build_lexicon.ipynb). Drive must be
# mounted at /content/drive. Progress: tail -f /content/enrich.log
set -euo pipefail
cd /content/ldd
VERSION=${VERSION:-2026.11.1}
D=/content/drive/MyDrive/lingolate-lexicon-build
export PYTHONIOENCODING=utf-8

apt-get -qq install -y espeak-ng > /dev/null
pip install -q -r scripts/requirements.txt
python scripts/download_extras.py
rsync -a sources_cache/ "$D/sources_cache/"
python scripts/enrich_lexicon.py --steps wikidata,ipa,freq,grammar
cp lexicon.sqlite3 "$D/lexicon.sqlite3"
python scripts/export_packs.py --version "$VERSION"
python scripts/generate_pack_manifest.py --version "$VERSION" --tag "packs-v$VERSION"
mkdir -p "$D/packs"
rsync -a packs/ "$D/packs/"
cp manifest-v3.json "$D/"
du -sh packs "$D"
echo ALLDONE
