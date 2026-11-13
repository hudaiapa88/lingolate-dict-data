"""Quality report over the exported packs (packs/*.sqlite3).

Per pack: size, lemmas, translations and the share of low-confidence
(PanLex-only) ones, IPA / frequency / forms / definition / usage coverage,
grammar rows. For a few showcase pairs, the translations of the most frequent
words are printed for a manual read.

Usage:  python scripts/quality_report.py [--out quality_report.md]
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKS = ROOT / 'packs'
SHOWCASE = ['en-tr', 'de-tr', 'ko-tr', 'en-ja', 'es-ru']
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
    L += ['', '## Showcase', '']
    for pair in SHOWCASE:
        path = PACKS / f'pack-{pair}.sqlite3'
        if path.exists():
            L.append(f'### {pair}')
            L += showcase(path)
            L.append('')
    Path(args.out).write_text('\n'.join(L), encoding='utf-8')
    print(f'wrote {args.out}')


if __name__ == '__main__':
    main()
