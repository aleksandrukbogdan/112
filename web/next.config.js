/** @type {import('next').NextConfig} */
module.exports = {
  reactStrictMode: true,

  // dev в контейнере: пересборка по опросу файлов, иначе изменения не видны.
  //
  // ignored ОБЯЗАТЕЛЕН. Каталог .next лежит внутри той же смонтированной
  // папки /app, и без исключения получается петля: Next пишет в .next →
  // watcher видит изменение → пересборка → снова пишет. В логах это выглядит
  // как «Found a change in next.config.js. Restarting the server».
  webpack: (config) => {
    config.watchOptions = {
      poll: 800,
      aggregateTimeout: 300,
      ignored: ["**/node_modules/**", "**/.next/**", "**/.git/**"],
    };
    return config;
  },
};
