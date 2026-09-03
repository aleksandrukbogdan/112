"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Headphones, SlidersHorizontal, BarChart3, Radio, LifeBuoy } from "lucide-react";
import { api } from "../lib/api";

const CARDS = [
  { href: "/trainee",    icon: Headphones,        title: "Обучение",
    text: "Заявитель генерируется моделью. Принять вызов, провести опрос, заполнить карточку, получить разбор." },
  { href: "/assistant",  icon: LifeBuoy,          title: "Поддержка",
    text: "Боевой режим: заявитель настоящий. Подсказки по протоколу, таймер норматива, полная УКИО. Оценок в эфире нет." },
  { href: "/instructor", icon: SlidersHorizontal, title: "Рабочее место преподавателя",
    text: "Вести занятие: усложнять обстановку, следить за ходом, засчитывать вопросы вручную." },
  { href: "/analytics",  icon: BarChart3,         title: "Аналитика подготовки",
    text: "Сводка по потоку и разбор каждого вызова: где теряют секунды и что пропускают." },
];

export default function Home() {
  const [health, setHealth] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setErr(e.message));
  }, []);

  const chip = (label, state) => {
    const color =
      state === true ? "#4E9E7E" : state === false ? "#D6453F" : "#93A2B2";
    const text =
      state === true ? "готов" : state === false ? "недоступен" : "выключен";
    return (
      <span key={label} className="flex items-center gap-1.5 text-xs text-muted">
        <i className="inline-block h-2 w-2 rounded-full" style={{ background: color }} />
        {label} — {text}
      </span>
    );
  };

  return (
    <main className="min-h-screen bg-paper">
      <header className="border-b border-line bg-surface px-6 py-4">
        <div className="mx-auto flex max-w-4xl items-center gap-2.5">
          <Radio size={17} className="text-amber" />
          <div>
            <div className="text-sm font-semibold tracking-tight text-ink">
              Тренажёр оператора Системы-112
            </div>
            <div className="text-xs text-faint">
              Норматив опроса — <span className="font-mono text-ink">75 с</span> · ПП РФ № 1931
            </div>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-4xl px-6 py-8">
        <div className="mb-6 rounded-md border border-line bg-surface px-4 py-3">
          <div className="mb-2 text-xs font-medium text-muted">Состояние системы</div>
          {err && <div className="text-xs text-signal">API недоступен: {err}</div>}
          {health && (
            <div className="flex flex-wrap gap-x-5 gap-y-1.5">
              {chip("База данных", health.checks.db)}
              {chip("Языковая модель", health.checks.llm?.ok)}
              {chip("Распознавание речи", health.checks.asr?.ok)}
              {chip("Синтез речи", health.checks.tts?.ok)}
            </div>
          )}
          {!health && !err && <div className="text-xs text-faint">Проверяю…</div>}
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          {CARDS.map(({ href, icon: Icon, title, text }) => (
            <Link
              key={href}
              href={href}
              className="rounded-md border border-line bg-surface p-5 transition-colors hover:border-amber"
            >
              <Icon size={18} className="mb-3 text-amber" />
              <div className="mb-1.5 text-sm font-semibold text-ink">{title}</div>
              <p className="text-xs leading-relaxed text-muted">{text}</p>
            </Link>
          ))}
        </div>
      </div>
    </main>
  );
}
