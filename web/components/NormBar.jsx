"use client";

/**
 * Полоса 75 секунд — подпись продукта.
 *
 * Один примитив на трёх масштабах:
 *   АРМ обучаемого  — бежит в реальном времени, зелёная зона сжимается
 *   Отчёт           — застывает с отметками событий
 *   Аналитика       — десятки полос складываются в стопку
 *
 * Норматив 75 с — ПП РФ № 1931, п. 9 подп. р.
 */
export const NORM_SEC = 75;

export default function NormBar({
  nowSec = 0,
  marks = [],
  height = 26,
  showAxis = false,
  dark = false,
  onClick,
}) {
  const span = Math.max(NORM_SEC * 1.4, nowSec * 1.1, 1);
  const pct = (v) => `${Math.min(100, Math.max(0, (v / span) * 100))}%`;
  const within = nowSec <= NORM_SEC;

  const track = dark ? "#2A3644" : "#EAEFF4";
  const axis = dark ? "#7A8A9E" : "#93A2B2";

  return (
    <div className="w-full">
      <div
        onClick={onClick}
        role={onClick ? "button" : undefined}
        tabIndex={onClick ? 0 : undefined}
        onKeyDown={onClick ? (e) => e.key === "Enter" && onClick() : undefined}
        className={`relative w-full overflow-hidden rounded-sm ${onClick ? "cursor-pointer" : ""}`}
        style={{ height, background: track }}
      >
        {/* зона норматива */}
        <div
          className="absolute inset-y-0 left-0"
          style={{ width: pct(NORM_SEC), background: "rgba(78,158,126,0.16)" }}
        />
        {/* граница норматива */}
        <div
          className="absolute inset-y-0"
          style={{ left: pct(NORM_SEC), width: 2, background: "#4E9E7E", opacity: 0.8 }}
          title="Норматив 75 с"
        />
        {/* прожитое время */}
        <div
          className="absolute"
          style={{
            left: 0,
            width: pct(nowSec),
            top: height / 2 - 2,
            height: 4,
            background: within ? "#4E9E7E" : "#D6453F",
            opacity: 0.55,
          }}
        />
        {marks.map((m, i) => (
          <div
            key={i}
            className="absolute"
            style={{
              left: pct(m.t),
              width: m.big ? 3 : 2,
              top: m.big ? 2 : 5,
              bottom: m.big ? 2 : 5,
              background: m.color,
            }}
            title={`${m.label} — ${Math.round(m.t)} с`}
          />
        ))}
      </div>

      {showAxis && (
        <div className="relative mt-1 font-mono text-[10px]" style={{ height: 14, color: axis }}>
          {[0, 25, 45, NORM_SEC].map((t) => (
            <span key={t} className="absolute" style={{ left: pct(t) }}>
              {t}с
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
