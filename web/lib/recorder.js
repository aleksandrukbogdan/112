"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Запись реплик оператора с микрофона.
 *
 * Почему не MediaRecorder: он отдаёт webm/opus, а сервис распознавания ждёт WAV.
 * Поэтому берём сырой PCM через Web Audio и кодируем WAV на клиенте —
 * заодно получаем контроль над громкостью и определением тишины.
 *
 * Частота 16 кГц запрашивается прямо у AudioContext: браузер сам пересчитает
 * поток с микрофона, и ручной ресемплинг не нужен.
 *
 * Два режима:
 *   auto — реплика нарезается сама по паузе (как в настоящем разговоре)
 *   push — удержание кнопки, для шумных помещений и демо
 */

const SAMPLE_RATE = 16000;
const SILENCE_RMS = 0.012;      // ниже этого считаем тишиной
const SILENCE_TAIL_MS = 900;    // столько тишины = конец реплики
const MIN_SPEECH_MS = 400;      // короче — шум, не отправляем
const MAX_UTTERANCE_MS = 24000; // GigaAM принимает до 25 с за раз

function encodeWav(chunks, sampleRate) {
  let total = 0;
  for (const c of chunks) total += c.length;

  const buffer = new ArrayBuffer(44 + total * 2);
  const view = new DataView(buffer);
  const str = (off, s) => {
    for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i));
  };

  str(0, "RIFF");
  view.setUint32(4, 36 + total * 2, true);
  str(8, "WAVE");
  str(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);            // PCM
  view.setUint16(22, 1, true);            // моно
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  str(36, "data");
  view.setUint32(40, total * 2, true);

  let off = 44;
  for (const c of chunks) {
    for (let i = 0; i < c.length; i++) {
      const s = Math.max(-1, Math.min(1, c[i]));
      view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      off += 2;
    }
  }
  return new Blob([buffer], { type: "audio/wav" });
}

export default function useRecorder({ onUtterance, mode = "auto" }) {
  const [active, setActive] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState(null);

  const ctxRef = useRef(null);
  const streamRef = useRef(null);
  const nodeRef = useRef(null);

  const chunks = useRef([]);
  const speechStart = useRef(null);
  const lastVoice = useRef(0);
  const lastEnd = useRef(null);     // когда закончилась прошлая реплика — для пауз
  const modeRef = useRef(mode);
  const cbRef = useRef(onUtterance);
  const holding = useRef(false);

  useEffect(() => { modeRef.current = mode; }, [mode]);
  useEffect(() => { cbRef.current = onUtterance; }, [onUtterance]);

  const flush = useCallback(() => {
    const data = chunks.current;
    chunks.current = [];
    const startedAt = speechStart.current;
    speechStart.current = null;
    setSpeaking(false);

    if (!data.length || !startedAt) return;
    const durationMs = (data.reduce((a, c) => a + c.length, 0) / SAMPLE_RATE) * 1000;
    if (durationMs < MIN_SPEECH_MS) return;

    const silenceMs = lastEnd.current ? Math.max(0, startedAt - lastEnd.current) : 0;
    lastEnd.current = Date.now();

    cbRef.current?.(encodeWav(data, SAMPLE_RATE), {
      durationMs: Math.round(durationMs),
      silenceMs: Math.round(silenceMs),
    });
  }, []);

  const start = useCallback(async () => {
    if (ctxRef.current) return;
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;

      const ctx = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: SAMPLE_RATE,
      });
      ctxRef.current = ctx;
      if (ctx.state === "suspended") await ctx.resume();

      const src = ctx.createMediaStreamSource(stream);
      // ScriptProcessorNode устарел, но работает во всех браузерах без отдельного
      // файла ворклета. Для тренажёра надёжность важнее современности API.
      const node = ctx.createScriptProcessor(2048, 1, 1);
      nodeRef.current = node;

      node.onaudioprocess = (e) => {
        const input = e.inputBuffer.getChannelData(0);

        let sum = 0;
        for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
        const rms = Math.sqrt(sum / input.length);
        setLevel(Math.min(1, rms * 12));

        const now = Date.now();
        const push = modeRef.current === "push";
        const voiced = push ? holding.current : rms > SILENCE_RMS;

        if (voiced) {
          if (!speechStart.current) {
            speechStart.current = now;
            setSpeaking(true);
          }
          lastVoice.current = now;
          chunks.current.push(new Float32Array(input));
        } else if (speechStart.current) {
          // небольшой хвост тишины пишем тоже: обрезанные окончания слов
          // портят распознавание сильнее, чем лишние полсекунды
          chunks.current.push(new Float32Array(input));
          if (!push && now - lastVoice.current > SILENCE_TAIL_MS) flush();
        }

        if (speechStart.current && now - speechStart.current > MAX_UTTERANCE_MS) {
          flush();
        }
      };

      src.connect(node);
      node.connect(ctx.destination);
      setActive(true);
      lastEnd.current = Date.now();
    } catch (e) {
      setError(
        e.name === "NotAllowedError"
          ? "Доступ к микрофону запрещён. Разрешите его в настройках браузера."
          : `Микрофон недоступен: ${e.message}`
      );
    }
  }, [flush]);

  const stop = useCallback(() => {
    if (speechStart.current) flush();
    nodeRef.current?.disconnect();
    nodeRef.current = null;
    ctxRef.current?.close();
    ctxRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setActive(false);
    setSpeaking(false);
    setLevel(0);
  }, [flush]);

  const holdStart = useCallback(() => { holding.current = true; }, []);
  const holdEnd = useCallback(() => { holding.current = false; }, []);

  useEffect(() => () => stop(), [stop]);

  return { active, speaking, level, error, start, stop, holdStart, holdEnd };
}
