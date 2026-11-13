"""Quality report over the exported packs (packs/*.sqlite3).

Per pack: size, lemmas, translations and the share of low-confidence
(PanLex-only) ones, IPA / frequency / forms / definition / usage coverage,
grammar rows. For a few showcase pairs, the translations of the most frequent
words are printed for a manual read.

Usage:  python scripts/quality_report.py [--out quality_report.md]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKS = ROOT / 'packs'
SHOWCASE = ['en-tr', 'de-tr', 'ko-tr', 'ar-tr', 'en-ja', 'es-ru', 'fr-zh']
sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_packs import clean_headword  # noqa: E402
from prune_packs import is_phrase  # noqa: E402
LOW = 0.5


def pct(a: int, b: int) -> str:
    return f'{100 * a / b:.0f}%' if b else '-'


def stats(path: Path) -> dict:
    p = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    q = lambda sql, *a: p.execute(sql, a).fetchone()[0]  # noqa: E731
    lem = q('SELECT count(*) FROM lemma')
    tr = q('SELECT count(*) FROM trans')
    r = {
        'pair': path.stem.removeprefix('pack-'),
        'mb': path.stat().st_size / 1e6,
        'lemmas': lem,
        'trans': tr,
        'low': q('SELECT count(*) FROM trans WHERE confidence < ?', LOW),
        'ipa': q('SELECT count(*) FROM lemma WHERE ipa IS NOT NULL'),
        'freq': q('SELECT count(*) FROM lemma WHERE freq IS NOT NULL'),
        'with_forms': q('SELECT count(DISTINCT lemma_id) FROM form'),
        'with_def': q('SELECT count(DISTINCT lemma_id) FROM definition'),
        'usage': q('SELECT count(*) FROM usage'),
        'examples': q('SELECT count(*) FROM example'),
        'grammar': q('SELECT count(*) FROM grammar'),
    }
    p.close()
    return r


def showcase(path: Path, n: int = 25) -> list[str]:
    a, b = path.stem.removeprefix('pack-').split('-')
    p = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    out = []
    for src, mine, other in ((a, 'a_lemma', 'b_lemma'), (b, 'b_lemma', 'a_lemma')):
        out.append(f'\n**{src} → {b if src == a else a}** (most frequent words)\n')
        rows = p.execute(f"""
          SELECT l.id, l.written, l.ipa, l.freq FROM lemma l
          WHERE l.lang = ? AND l.freq IS NOT NULL
            AND EXISTS (SELECT 1 FROM trans t WHERE t.{mine} = l.id)
          ORDER BY l.freq LIMIT ?""", (src, n)).fetchall()
        for lid, w, ipa, freq in rows:
            ts = p.execute(f"""
              SELECT o.written, t.confidence FROM trans t JOIN lemma o ON o.id = t.{other}
              WHERE t.{mine} = ? ORDER BY t.confidence DESC LIMIT 4""", (lid,)).fetchall()
            tx = ', '.join(f"{t}{'' if c >= LOW else '?'}" for t, c in ts)
            out.append(f'- {w} /{ipa or ""}/ → {tx}')
    p.close()
    return out


def gates(packs: list[Path]) -> tuple[list[str], bool]:
    """Release gates. Every pack must pass; the release is held otherwise."""
    out, ok_all = [], True
    manifest = ROOT / 'manifest-v3.json'
    listed = {}
    if manifest.exists():
        m = json.loads(manifest.read_text(encoding='utf-8'))
        items = m.get('packs', m) if isinstance(m, dict) else m
        for it in (items.values() if isinstance(items, dict) else items):
            if isinstance(it, dict) and 'file' in it:
                listed[it['file']] = it
    fails: dict[str, list[str]] = {}
    for path in packs:
        p = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        f = []
        if p.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            f.append('integrity_check')
        if p.execute("SELECT value FROM _meta WHERE key = 'schema'").fetchone() != ('pack-v3',):
            f.append('schema')
        bad = sum(1 for lang, w in p.execute('SELECT lang, written FROM lemma')
                  if not clean_headword(lang, w) and not w.endswith('-'))
        if bad:
            f.append(f'{bad} unclean headwords')
        phrases = p.execute('SELECT t.confidence, la.lang, la.written, lb.lang, lb.written FROM trans t '
                            'JOIN lemma la ON la.id = t.a_lemma JOIN lemma lb ON lb.id = t.b_lemma '
                            'WHERE t.confidence < ?', (LOW,)).fetchall()
        n_ph = sum(1 for _, a1, w1, b1, w2 in phrases if is_phrase(a1, w1) or is_phrase(b1, w2))
        if n_ph:
            f.append(f'{n_ph} low-confidence phrase links')
        if p.execute("SELECT count(*) FROM lemma WHERE lang = 'tr' AND written LIKE '%-'").fetchone()[0]:
            f.append('Turkish verb stems')
        if p.execute('SELECT count(*) FROM trans').fetchone()[0] == 0:
            f.append('empty')
        p.close()
        it = listed.get(path.name)
        if listed and it is None:
            f.append('not in manifest')
        elif it is not None:
            if it.get('bytes') != path.stat().st_size:
                f.append('manifest size mismatch')
            elif it.get('sha256') != hashlib.sha256(path.read_bytes()).hexdigest():
                f.append('manifest sha256 mismatch')
        if f:
            fails[path.name] = f
    out.append(f'- packs checked: {len(packs)}; manifest entries: {len(listed)}')
    if fails:
        ok_all = False
        out.append(f'- **FAIL** in {len(fails)} packs:')
        out += [f'  - {k}: {", ".join(v)}' for k, v in sorted(fails.items())[:40]]
    else:
        out.append('- **PASS**: integrity, schema, headword cleanliness, no low-confidence '
                   'phrase links, no Turkish stems, manifest size + sha256')
    return out, ok_all


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(ROOT / 'quality_report.md'))
    args = ap.parse_args()
    packs = sorted(PACKS.glob('pack-*.sqlite3'))
    rows = [stats(x) for x in packs]
    L = ['# Pack quality report', '',
         f'{len(rows)} packs, {sum(r["mb"] for r in rows) / 1000:.2f} GB. '
         f'"low" = translations with confidence < {LOW} (PanLex only).', '',
         '| pair | MB | lemmas | trans | low | IPA | freq | forms | defs | usage | examples | grammar |',
         '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        L.append(f'| {r["pair"]} | {r["mb"]:.1f} | {r["lemmas"]:,} | {r["trans"]:,} | '
                 f'{pct(r["low"], r["trans"])} | {pct(r["ipa"], r["lemmas"])} | '
                 f'{pct(r["freq"], r["lemmas"])} | {pct(r["with_forms"], r["lemmas"])} | '
                 f'{pct(r["with_def"], r["lemmas"])} | {r["usage"]:,} | {r["examples"]:,} | '
                 f'{r["grammar"]} |')
    g, ok = gates(packs)
    L += ['', '## Release gates', ''] + g
    L += ['', '## Showcase', '']
    for pair in SHOWCASE:
        path = PACKS / f'pack-{pair}.sqlite3'
        if path.exists():
            L.append(f'### {pair}')
            L += showcase(path)
            L.append('')
    Path(args.out).write_text('\n'.join(L), encoding='utf-8')
    print(f'wrote {args.out}; gates {"PASS" if ok else "FAIL"}')


if __name__ == '__main__':
    main()
