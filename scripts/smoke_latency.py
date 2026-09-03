#!/usr/bin/env python3
"""
ГЛАВНЫЙ ТЕСТ ПРОЕКТА.

Закон 2 из плана: пауза между концом реплики оператора и началом ответа
заявителя — не больше 1 секунды. Этот скрипт её измеряет по всей цепочке
и раскладывает по слагаемым, чтобы было видно, кто тормозит.

Запускать ПЕРЕД любой другой разработкой. Если не укладываемся —
режем живые вычисления, а не функциональность.

    pip install requests
    python3 scripts/smoke_latency.py
"""
import json
import time
import argparse
import statistics
import sys

import requests

import os as _os

def _p(name, default):
    v = _os.environ.get(name)
    if not v:
        from pathlib import Path
        env = Path(__file__).resolve().parent.parent / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith(f"{name}="):
                    v = line.split("=", 1)[1].strip()
                    break
    return v or default


ASR = f"http://127.0.0.1:{_p('ASR_PORT', '21124')}"
TTS = f"http://127.0.0.1:{_p('TTS_PORT', '21125')}"
LLM = "http://127.0.0.1:1212/v1"
LLM_MODEL = "Qwen3-VL"          # = --served-model-name у vllm-qwen

BUDGET_MS = 1000

# Реплика оператора, на которой гоняем цепочку
OPERATOR_LINE = "Служба сто двенадцать, назовите адрес происшествия"

# Закрытый список интентов — модель не изобретает новые
INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intents": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "greeting", "asked_address", "asked_address_detail",
                    "asked_victims", "asked_hazards", "gave_instructions",
                    "other",
                ],
            },
        },
        "tone": {"type": "string", "enum": ["calm", "tense", "harsh"]},
    },
    "required": ["intents", "tone"],
}

# Порядок важен: статичная часть промпта идёт ПЕРВОЙ, чтобы
# --enable-prefix-caching у vLLM реально кэшировал её между ходами.
CALLER_SYSTEM = (
    "Ты играешь роль заявителя, звонящего в службу 112. "
    "Женщина 34 лет, паника, рваные короткие фразы, повторы. "
    "Отвечай ОДНОЙ-ДВУМЯ короткими репликами. "
    "Используй ТОЛЬКО факты из списка ниже. Ничего не выдумывай: "
    "никаких адресов, чисел и названий, которых нет в списке.\n"
    "Раскрытые факты: улица Ленина, дом 12."
)


def step(name, fn):
    t0 = time.perf_counter()
    try:
        out = fn()
    except Exception as e:
        print(f"  [ОШИБКА] {name}: {e}")
        raise
    ms = (time.perf_counter() - t0) * 1000
    return name, ms, out


def synth(text, emotion="panic", cache=False):
    r = requests.post(
        f"{TTS}/synth",
        json={"text": text, "emotion": emotion, "telephone": True, "cache": cache},
        timeout=120,
    )
    r.raise_for_status()
    return r.content


def transcribe(wav):
    r = requests.post(
        f"{ASR}/transcribe", files={"file": ("a.wav", wav, "audio/wav")}, timeout=120
    )
    r.raise_for_status()
    return r.json()


def emotion(wav):
    r = requests.post(
        f"{ASR}/emotion", files={"file": ("a.wav", wav, "audio/wav")}, timeout=120
    )
    r.raise_for_status()
    return r.json()


def classify(text):
    r = requests.post(
        f"{LLM}/chat/completions",
        json={
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content":
                 "Определи интенты реплики оператора 112. Отвечай только JSON."},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "max_tokens": 128,
            "guided_json": INTENT_SCHEMA,     # vLLM structured output
        },
        timeout=120,
    )
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])


def caller_reply(operator_text):
    r = requests.post(
        f"{LLM}/chat/completions",
        json={
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": CALLER_SYSTEM},
                {"role": "user", "content": operator_text},
            ],
            "temperature": 0.9,
            "max_tokens": 80,
        },
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def health():
    ok = True
    for name, url in (("asr", f"{ASR}/health"), ("tts", f"{TTS}/health")):
        try:
            j = requests.get(url, timeout=10).json()
            print(f"  {name}: {'ok' if j.get('ok') else 'МОДЕЛЬ НЕ ЗАГРУЖЕНА'} {j}")
            ok &= bool(j.get("ok"))
        except Exception as e:
            print(f"  {name}: НЕДОСТУПЕН ({e})")
            ok = False
    try:
        models = requests.get(f"{LLM}/models", timeout=10).json()
        names = [m["id"] for m in models.get("data", [])]
        print(f"  llm: ok, модели={names}")
        if LLM_MODEL not in names:
            print(f"  ВНИМАНИЕ: '{LLM_MODEL}' нет в списке — поправьте LLM_MODEL")
            ok = False
    except Exception as e:
        print(f"  llm: НЕДОСТУПЕН ({e})")
        ok = False
    return ok


def run_once(verbose=True):
    """Один полный цикл: голос оператора -> ответ заявителя в аудио."""
    # Готовим входное аудио заранее, вне замера — это имитация речи оператора
    op_wav = synth(OPERATOR_LINE, emotion="neutral", cache=True)

    marks = []
    t_start = time.perf_counter()

    marks.append(step("ASR (распознавание реплики)", lambda: transcribe(op_wav)))
    text = marks[-1][2].get("text") or OPERATOR_LINE

    marks.append(step("LLM: классификация интента", lambda: classify(text)))
    marks.append(step("LLM: реплика заявителя", lambda: caller_reply(text)))
    reply = marks[-1][2]

    marks.append(step("TTS (живой синтез)", lambda: synth(reply, "panic", cache=False)))

    total = (time.perf_counter() - t_start) * 1000

    # Аналитика эмоций идёт параллельно и в бюджет ответа НЕ входит
    _, emo_ms, emo = step("эмоции (вне бюджета, параллельно)", lambda: emotion(op_wav))

    if verbose:
        print(f"\n  распознано: {text!r}")
        print(f"  интенты:    {marks[1][2]}")
        print(f"  заявитель:  {reply!r}")
        print(f"  эмоции:     {emo.get('probs')}\n")
        for name, ms, _ in marks:
            bar = "#" * min(int(ms / 20), 50)
            print(f"  {name:<32} {ms:7.0f} мс  {bar}")
        print(f"  {'эмоции (параллельно)':<32} {emo_ms:7.0f} мс")

    return total, {n: ms for n, ms, _ in marks}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=5, help="число прогонов")
    args = ap.parse_args()

    print("=" * 66)
    print("ПРОВЕРКА ЗДОРОВЬЯ")
    print("=" * 66)
    if not health():
        print("\nНе все сервисы готовы. Чиним и повторяем.")
        sys.exit(1)

    print("\n" + "=" * 66)
    print("ПРОГРЕВ (первый вызов всегда медленный — в статистику не идёт)")
    print("=" * 66)
    run_once(verbose=False)

    totals = []
    for i in range(args.n):
        print("\n" + "=" * 66)
        print(f"ПРОГОН {i + 1} / {args.n}")
        print("=" * 66)
        total, _ = run_once()
        totals.append(total)
        print(f"\n  ИТОГО ДО ПЕРВОГО ЗВУКА: {total:.0f} мс")

    print("\n" + "=" * 66)
    med = statistics.median(totals)
    print(f"МЕДИАНА: {med:.0f} мс   МИН: {min(totals):.0f}   МАКС: {max(totals):.0f}")
    print(f"БЮДЖЕТ:  {BUDGET_MS} мс")
    if med <= BUDGET_MS:
        print("ВЕРДИКТ: укладываемся. Можно строить логику.")
    else:
        print("ВЕРДИКТ: НЕ УКЛАДЫВАЕМСЯ.")
        print("  1. Предсинтез: критические реплики играть из кэша (cache=true).")
        print("  2. Классификацию интента слить с генерацией реплики в один вызов.")
        print("  3. Стриминг TTS: отдавать первый чанк, не дожидаясь конца.")
        print("  4. Филлеры: вдох/всхлип из банка сразу, пока считается ответ.")
    print("=" * 66)


if __name__ == "__main__":
    main()
