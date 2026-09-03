"use client";

export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

async function req(path, opts = {}) {
  const r = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`${r.status} ${path}: ${body.slice(0, 200)}`);
  }
  return r.json();
}

export const api = {
  health: () => req("/health"),
  scenarios: () => req("/api/scenarios"),
  startSession: (payload) =>
    req("/api/sessions", { method: "POST", body: JSON.stringify(payload) }),
  say: (sid, text, durationMs) =>
    req(`/api/sessions/${sid}/say`, {
      method: "POST",
      body: JSON.stringify({ text, duration_ms: durationMs ?? null }),
    }),
  card: (sid, field, value) =>
    req(`/api/sessions/${sid}/card`, {
      method: "POST",
      body: JSON.stringify({ field, value }),
    }),
  dispatch: (sid, services) =>
    req(`/api/sessions/${sid}/dispatch`, {
      method: "POST",
      body: JSON.stringify({ services }),
    }),
  inject: (sid, injectionId) =>
    req(`/api/sessions/${sid}/inject`, {
      method: "POST",
      body: JSON.stringify({ injection_id: injectionId }),
    }),
  finish: (sid) => req(`/api/sessions/${sid}/finish`, { method: "POST" }),
  report: (sid) => req(`/api/sessions/${sid}/report`),
  reportPdfUrl: (sid) => `${API}/api/sessions/${sid}/report.pdf`,
  utterance: async (sid, blob, { durationMs, silenceMs } = {}) => {
    const fd = new FormData();
    fd.append("file", blob, "chunk.wav");
    const qs = new URLSearchParams();
    if (durationMs) qs.set("duration_ms", String(durationMs));
    if (silenceMs) qs.set("silence_ms", String(silenceMs));
    const r = await fetch(`${API}/api/sessions/${sid}/utterance?${qs}`, {
      method: "POST",
      body: fd,
    });
    if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
    return r.json();
  },
  analytics: () => req("/api/analytics"),
  ttsUrl: (text, emotion = "neutral") =>
    `${API}/api/tts?${new URLSearchParams({ text, emotion })}`,
};

// Демо-идентификаторы из db/002_seed.sql
export const DEMO = {
  trainees: [
    { id: "aaaaaaaa-0000-0000-0000-000000000001", name: "Абрамова Л. В." },
    { id: "aaaaaaaa-0000-0000-0000-000000000002", name: "Валеев Р. И." },
    { id: "aaaaaaaa-0000-0000-0000-000000000003", name: "Гущина М. С." },
    { id: "aaaaaaaa-0000-0000-0000-000000000004", name: "Дорохов С. П." },
    { id: "aaaaaaaa-0000-0000-0000-000000000005", name: "Ерёмина О. А." },
    { id: "aaaaaaaa-0000-0000-0000-000000000006", name: "Жарков П. Н." },
  ],
  instructor: "bbbbbbbb-0000-0000-0000-000000000001",
};
