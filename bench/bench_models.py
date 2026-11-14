"""Benchmark the app's on-device models (GGUF) on short translation, the task
the app gives them most. 5 target languages x 50 Tatoeba sentence pairs taken
from the published pair packs; references are human translations.

Per model and language: chrF++ against the reference, the share of answers in
the wrong language (language confusion) and seconds per answer.

Runs on a Colab VM (GPU used when present, e.g. L4/T4):
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
    # candidates (phone-sized, < 3 GB)
    'ternary-bonsai-2-9b-q2-0':
        'https://huggingface.co/prism-ml/Ternary-Bonsai-2-9B-gguf/resolve/main/Ternary-Bonsai-2-9B-Q2_0.gguf',
    'hy-mt2-1.8b-q8-0':
        'https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF/resolve/main/Hy-MT2-1.8B-Q8_0.gguf',
    'hy-mt2-1.8b-q4-k-m':
        'https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF/resolve/main/Hy-MT2-1.8B-Q4_K_M.gguf',
    'translategemma-4b-q4-k-m':
        'https://huggingface.co/bullerwins/translategemma-4b-it-GGUF/resolve/main/translategemma-4b-it-Q4_K_M.gguf',
    'tiny-aya-global-q4-k-m':
        'https://huggingface.co/CohereLabs/tiny-aya-global-GGUF/resolve/main/tiny-aya-global-q4_k_m.gguf',
    'qwen3.5-4b-q4-k-m':
        'https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf',
}
WORK = Path('/content/bench')
GPU = subprocess.run('nvidia-smi', shell=True, capture_output=True).returncode == 0
LLAMA = Path('/content/llama.cpp')
PORT = 8089
PAR = 8  # parallel requests (server slots)


def sh(cmd: str) -> None:
    subprocess.run(cmd, shell=True, check=True)


def build_llama() -> Path:
    server = LLAMA / 'build' / 'bin' / 'llama-server'
    if not server.exists():
        sh(f'git clone -q --depth 1 https://github.com/ggml-org/llama.cpp {LLAMA}')
        cuda = '-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=native' if GPU else ''
        sh(f'cmake -S {LLAMA} -B {LLAMA}/build -DLLAMA_CURL=OFF -DGGML_NATIVE=ON {cuda} > /dev/null')
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


# Held-out test sets (never in OPUS / Tatoeba training data): WMT news and
# IWSLT talks, fetched by sacrebleu. Tatoeba sentences are in the training
# data of Opus-MT, so only this set compares Opus-MT fairly.
HELDOUT = {'tr': 'wmt18', 'de': 'wmt20', 'ja': 'wmt20', 'ru': 'wmt20', 'ar': 'iwslt17'}


def sentences_heldout(lang: str, n: int, seed: int = 7) -> list[tuple[str, str]]:
    """n (English, lang) pairs, 5-30 words, from the held-out test set."""
    def echo(side: str) -> list[str]:
        r = subprocess.run(['sacrebleu', '-t', HELDOUT[lang], '-l', f'en-{lang}', '--echo', side],
                           capture_output=True, encoding='utf-8', errors='replace', check=True,
                           env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        return r.stdout.rstrip('\n').split('\n')
    pairs = [(e, t) for e, t in zip(echo('src'), echo('ref')) if 5 <= len(e.split()) <= 30]
    random.Random(seed).shuffle(pairs)
    return pairs[:n]


def score(pairs: list, hyps: list, secs: list, lang: str) -> dict:
    import sacrebleu
    refs = [t for _, t in pairs]
    ok = sum(script_ok(lang, h) for h in hyps)
    return {'chrf': round(sacrebleu.corpus_chrf(hyps, [refs], word_order=2).score, 1),
            'right_lang': round(100 * ok / len(pairs)),
            'sec': round(sum(secs) / max(len(secs), 1), 2),
            'samples': [[e, r, h] for (e, r), h in list(zip(pairs, hyps))[:3]]}


# Dedicated translation models (Marian, CC BY 4.0), 30-300 MB per direction.
OPUS = {'tr': 'Helsinki-NLP/opus-mt-tc-big-en-tr', 'de': 'Helsinki-NLP/opus-mt-en-de',
        'ja': 'Helsinki-NLP/opus-tatoeba-en-ja', 'ar': 'Helsinki-NLP/opus-mt-tc-big-en-ar',
        'ru': 'Helsinki-NLP/opus-mt-en-ru'}
OPUS_PREFIX = {'ar': '>>arb<< '}  # multi-target model: pick Standard Arabic


def run_opus(data: dict) -> dict:
    from transformers import MarianMTModel, MarianTokenizer
    import torch
    dev = 'cuda' if torch.cuda.is_available() else 'cpu'
    out = {}
    for lang, name in OPUS.items():
        try:
            tok = MarianTokenizer.from_pretrained(name)
            model = MarianMTModel.from_pretrained(name).to(dev)
        except Exception as e:  # noqa: BLE001
            print('opus', lang, 'FAILED', e, flush=True)
            continue
        for key, pairs in data.items():
            if key.split('/')[1] != lang:
                continue
            t0 = time.time()
            src = [OPUS_PREFIX.get(lang, '') + e for e, _ in pairs]
            hyps = []
            for i in range(0, len(src), 16):
                batch = tok(src[i:i + 16], return_tensors='pt', padding=True, truncation=True).to(dev)
                gen = model.generate(**batch, num_beams=4, max_new_tokens=256)
                hyps += tok.batch_decode(gen, skip_special_tokens=True)
            per = (time.time() - t0) / len(src)
            out[key] = score(pairs, hyps, [per] * len(src), lang)
            print('opus-mt', key, out[key]['chrf'], out[key]['right_lang'], flush=True)
    return out


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
    """One answer; a failed request (e.g. a chat template that rejects the
    message) counts as an empty answer instead of stopping the run."""
    try:
        return _ask(prompt)
    except Exception:  # noqa: BLE001
        return '', 0.0


def _ask(prompt: str) -> tuple[str, float]:
    t0 = time.time()
    r = requests.post(f'http://127.0.0.1:{PORT}/v1/chat/completions', timeout=300, json={
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0, 'max_tokens': 256,
        'chat_template_kwargs': {'enable_thinking': False},
    })
    r.raise_for_status()
    text = r.json()['choices'][0]['message']['content'] or ''
    return text.strip().split('\n')[0].strip(' "'), time.time() - t0


def run_model(server: Path, model: str, url: str, data: dict) -> dict:
    gguf = WORK / f'{model}.gguf'
    if not gguf.exists():
        sh(f'curl -sSL --retry 5 -o {gguf} {url}')
    proc = subprocess.Popen([str(server), '-m', str(gguf), '--port', str(PORT), '-c', str(1024 * PAR),
                             '-t', str(os.cpu_count()), '--jinja', '-np', str(PAR),
                             *(['-ngl', '99'] if GPU else [])],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(180):
            try:
                if requests.get(f'http://127.0.0.1:{PORT}/health', timeout=2).ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(1)
        out = {}
        for key, pairs in data.items():
            lang = key.split('/')[1]
            from concurrent.futures import ThreadPoolExecutor
            prompts = [f'Translate into {NAMES[lang]}. Reply with the translation only.\n\n{en}'
                       for en, _ in pairs]
            with ThreadPoolExecutor(PAR) as ex:
                answers = list(ex.map(ask, prompts))  # keeps input order
            hyps = [h for h, _ in answers]
            secs = [t for _, t in answers]
            out[key] = score(pairs, hyps, secs, lang)
            print(model, key, out[key]['chrf'], out[key]['right_lang'], out[key]['sec'], flush=True)
        return out
    finally:
        proc.terminate()
        proc.wait()
        gguf.unlink(missing_ok=True)


def table(results: dict, dataset: str) -> list[str]:
    L = ['| model | ' + ' | '.join(LANGS) + ' |', '|---|' + '---|' * len(LANGS)]
    for m, r in results.items():
        cells = []
        for x in LANGS:
            v = r.get(f'{dataset}/{x}')
            cells.append(f'{v["chrf"]} / {v["right_lang"]}% / {v["sec"]}s' if v else '-')
        L.append(f'| {m} | ' + ' | '.join(cells) + ' |')
    return L


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=50)
    ap.add_argument('--models', default=','.join(MODELS))
    ap.add_argument('--opus', action='store_true', help='also run the Opus-MT models')
    ap.add_argument('--out', default='/content/bench.md')
    args = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    data = {}
    for lang in LANGS:
        data[f'tatoeba/{lang}'] = sentences(lang, args.n)
        data[f'heldout/{lang}'] = sentences_heldout(lang, args.n)
    results = {}
    if args.opus:
        results['opus-mt (Marian)'] = run_opus(data)
    server = build_llama() if args.models else None
    for m in filter(None, args.models.split(',')):
        try:
            results[m] = run_model(server, m, MODELS[m], data)
        except Exception as e:  # noqa: BLE001 — one broken model must not stop the others
            print(m, 'FAILED', e, flush=True)
    (WORK / 'bench.json').write_text(json.dumps(results, ensure_ascii=False, indent=1))
    L = ['# On-device model benchmark', '',
         f'en → {", ".join(LANGS)}; {args.n} sentences per language and test set; '
         'LLMs greedy with thinking off, Opus-MT beam 4.',
         'Cells: chrF++ vs human reference (higher is better) / % answers in the right '
         'language / s per answer (GPU, 8 parallel; not phone speed).', '',
         '## Held-out test set (WMT18 tr, WMT20 de/ja/ru, IWSLT17 ar) — fair for every model', '']
    L += table(results, 'heldout')
    L += ['', '## Tatoeba (short everyday sentences; in Opus-MT training data)', '']
    L += table(results, 'tatoeba')
    L += ['', '## Samples (source / reference / model)', '']
    for m, r in results.items():
        L.append(f'### {m}')
        for key in sorted(r):
            for e, ref, h in r[key]['samples'][:2]:
                L.append(f'- [{key}] {e} / {ref} / {h}')
        L.append('')
    Path(args.out).write_text('\n'.join(L), encoding='utf-8')
    print('\n'.join(L[:30]))


if __name__ == '__main__':
    main()
