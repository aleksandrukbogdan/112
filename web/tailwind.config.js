/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,jsx}", "./components/**/*.{js,jsx}", "./lib/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        // регистр «Протокол» — аналитика и отчёты
        paper:   "#F1F4F7",
        surface: "#FFFFFF",
        line:    "#DCE3EA",
        lineSoft:"#EAEFF4",
        ink:     "#16202B",
        muted:   "#5E6E7F",
        faint:   "#93A2B2",
        // регистр «Пульт» — АРМ обучаемого
        pult:     "#0F141B",
        pultUp:   "#171F2A",
        pultLine: "#2A3644",
        pultText: "#C9D4E2",
        pultMuted:"#7A8A9E",
        // сигнальные
        amber:  "#E0913C",
        signal: "#D6453F",
        norm:   "#4E9E7E",
        act:    "#3E6BB5",
        second: "#7A5EA8",
      },
      fontFamily: {
        sans: ["'Golos Text'", "system-ui", "sans-serif"],
        mono: ["'IBM Plex Mono'", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
