import "./globals.css";

export const metadata = {
  title: "Тренажёр оператора 112",
  description: "Отработка приёма экстренного вызова",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ru">
      <body className="font-sans">{children}</body>
    </html>
  );
}
