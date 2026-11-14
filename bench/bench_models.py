"""Benchmark the app's on-device models (GGUF) on short translation, the task
the app gives them most. 5 target languages x 50 Tatoeba sentence pairs taken
from the published pair packs; references are human translations.

Per model and language: chrF++ against the reference, the share of answers in
the wrong language (language confusion) and seconds per answer.

Runs on a Colab CPU VM:
    apt-get install -y cmake && pip install sacrebleu langid requests
    python bench/bench_models.py --out /content/bench.md

llama.cpp (mainline: Q2_0 is merged since b9913) is built once into
/content/llama.cpp; models are downloaded from Hugging Face one at a time and
deleted after their run to keep the disk small.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import subprocess
import time
import unicodedata
from pathlib import Path

import requests

RELEASE = 'https://github.com/hudaiapa88/lingolate-dict-data/releases/download/packs-v2026.11.2'
LANGS = ['tr', 'de', 'ja', 'ar', 'ru']
NAMES = {'tr': 'Turkish', 'de': 'German', 'ja': 'Japanese', 'ar': 'Arabic', 'ru': 'Russian'}
MODELS = {  # same files the app downloads (lib/app/services/ai/model_store.dart)
    'ternary-bonsai-2-4b-q2-0':
        'https://huggingface.co/prism-ml/Ternary-Bonsai-2-4B-gguf/resolve/main/Ternary-Bonsai-2-4B-Q2_0.gguf',
    'ternary-bonsai-2-2b-q2-0':
        'https://huggingface.co/prism-ml/Ternary-Bonsai-2-2B-gguf/resolve/main/Ternary-Bonsai-2-2B-Q2_0.gguf',
    'qwen3.5-2b-q4-k-m':
        'https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf',
    'gemma-4-e2b-q3-k-m':
        'https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q3_K_M.gguf',
    'qwen3.5-0.8b-q4-k-m':
        'https://huggingface.co/mradermacher/Qwen3.5-0.8B-GGUF/resolve/main/Qwen3.5-0.8B.Q4_K_M.gguf',
}
WORK = Path('/content/bench')
LLAMA = Path('/content/llama.cpp')
PORT = 8089


def sh(cmd: str) -> None:
    subprocess.run(cmd, shell=True, check=True)


def build_llama() -> Path:
    server = LLAMA / 'build' / 'bin' / 'llama-server'
    if not server.exists():
        sh(f'git clone -q --depth 1 https://github.com/ggml-org/llama.cpp {LLAMA}')
        sh(f'cmake -S {LLAMA} -B {LLAMA}/build -DLLAMA_CURL=OFF -DGGML_NATIVE=ON > /dev/null')
        sh(f'cmake --build {LLAMA}/build --target llama-server -j {os.cpu_count()} > /dev/null')
    return server


def sentences(lang: str, n: int, seed: int = 7) -> list[tuple[str, str]]:
    """n (English, lang) pairs from the en-lang pack: 4-14 words, no names."""
    a, b = sorted(['en', lang])
    path = WORK / f'pack-{a}-{b}.sqlite3'
    if not path.exists():
        with requests.get(f'{RELEASE}/{path.name}', stream=True, timeout=120) as r:
            r.raise_for_status()
            with path.open('wb') as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
    db = sqlite3.connect(path)
    rows = db.execute('SELECT a, b FROM example WHERE score >= 0.5').fetchall()
    db.close()
    pairs = [(x, y) if a == 'en' else (y, x) for x, y in rows]
    pairs = [(e, t) for e, t in pairs
             if 4 <= len(e.split()) <= 14 and 'Tom' not in e and 'Mary' not in e]
    random.Random(seed).shuffle(pairs)
    return pairs[:n]


def script_ok(lang: str, text: str) -> bool:
    """Answer is in the target language (script check, then langid)."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    want = {'ja': ('CJK', 'HIRAGANA', 'KATAKANA'), 'ar': ('ARABIC',), 'ru': ('CYRILLIC',)}.get(lang)
    if want:
        good = sum(1 for c in letters if unicodedata.name(c, '').startswith(want))
        return good / len(letters) >= 0.6
    import langid
    langid.set_languages(['en', 'tr', 'de', 'fr', 'es', 'it', 'nl', 'az'])
    return langid.classify(text)[0] == lang


def ask(prompt: str) -> tuple[str, float]:
    t0 = time.time()
    r = requests.post(f'http://127.0.0.1:{PORT}/v1/chat/completions', timeout=300, json={
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0, 'max_tokens': 128,
        'chat_template_kwargs': {'enable_thinking': False},
    })
    r.raise_for_status()
    text = r.json()['choices'][0]['message']['content'] or ''
    return text.strip().split('\n')[0].strip(' "'), time.time() - t0


def run_model(server: Path, model: str, url: str, data: dict) -> dict:
    gguf = WORK / f'{model}.gguf'
    if not gguf.exists():
        sh(f'curl -sSL --retry 5 -o {gguf} {url}')
    proc = subprocess.Popen([str(server), '-m', str(gguf), '--port', str(PORT), '-c', '2048',
                             '-t', str(os.cpu_count()), '--jinja'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(180):
            try:
                if requests.get(f'http://127.0.0.1:{PORT}/health', timeout=2).ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(1)
        import sacrebleu
        out = {}
        for lang, pairs in data.items():
            hyps, secs, ok = [], [], 0
            for en, _ in pairs:
                hyp, s = ask(f'Translate into {NAMES[lang]}. Reply with the translation only.\n\n{en}')
                hyps.append(hyp)
                secs.append(s)
                ok += script_ok(lang, hyp)
            refs = [t for _, t in pairs]
            chrf = sacrebleu.corpus_chrf(hyps, [refs], word_order=2).score
            out[lang] = {'chrf': round(chrf, 1), 'right_lang': round(100 * ok / len(pairs)),
                         'sec': round(sum(secs) / len(secs), 2),
                         'samples': [[e, r, h] for (e, r), h in list(zip(pairs, hyps))[:3]]}
            print(model, lang, out[lang]['chrf'], out[lang]['right_lang'], out[lang]['sec'], flush=True)
        return out
    finally:
        proc.terminate()
        proc.wait()
        gguf.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=50)
    ap.add_argument('--models', default=','.join(MODELS))
    ap.add_argument('--out', default='/content/bench.md')
    args = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    server = build_llama()
    data = {lang: sentences(lang, args.n) for lang in LANGS}
    results = {m: run_model(server, m, MODELS[m], data) for m in args.models.split(',')}
    (WORK / 'bench.json').write_text(json.dumps(results, ensure_ascii=False, indent=1))
    L = ['# On-device model benchmark', '',
         f'en → {", ".join(LANGS)}; {args.n} Tatoeba pairs per language; greedy, thinking off.',
         'chrF++ vs human reference (higher is better) / % answers in the right language / s per answer.', '',
         '| model | ' + ' | '.join(LANGS) + ' |', '|---|' + '---|' * len(LANGS)]
    for m, r in results.items():
        L.append(f'| {m} | ' + ' | '.join(
            f'{r[x]["chrf"]} / {r[x]["right_lang"]}% / {r[x]["sec"]}s' for x in LANGS) + ' |')
    L += ['', '## Samples (source / reference / model)', '']
    for m, r in results.items():
        L.append(f'### {m}')
        for x in LANGS:
            for e, ref, h in r[x]['samples']:
                L.append(f'- [{x}] {e} / {ref} / {h}')
        L.append('')
    Path(args.out).write_text('\n'.join(L), encoding='utf-8')
    print('\n'.join(L[:12]))


if __name__ == '__main__':
    main()
