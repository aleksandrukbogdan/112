# Развёртывание: пошагово

Тренажёр и помощник оператора 112. Всё, что нужно, — в архиве.
Распаковать, положить в каталог, выполнить команды по порядку.

---

## Шаг 0. Что понадобится

| Требование | Как проверить |
|---|---|
| Docker ≥ 24.0 | `docker --version` |
| Docker Compose ≥ 2.20 | `docker compose version` |
| NVIDIA Driver ≥ 535 | `nvidia-smi` |
| NVIDIA Container Toolkit | `docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi` |
| Python 3.10+ на хосте | `python3 --version` — нужен для `make config-check` |
| Свободно на диске | ~15 ГБ (веса моделей) |
| Свободно портов | 21120–21125 |

Если последняя проверка с `--gpus all` не прошла — установите NVIDIA
Container Toolkit, иначе ASR не поднимется.

---

## Шаг 1. Куда положить

```bash
sudo mkdir -p /data/112
sudo chown $USER:$USER /data/112
cd /data/112

tar xzf ~/t112.tar.gz --strip-components=1
```

`--strip-components=1` убирает верхний каталог `t112/`, чтобы файлы легли
прямо в `/data/112`. Без него получится `/data/112/t112/...`.

Проверка:

```bash
ls
# Makefile  README.md  config  content  db  docker-compose.yml
# models  scripts  services  voicebank  web  .env.example
```

### Что где лежит

```
/data/112/
├── .env                      ← создаётся на шаге 2, НЕ в git
├── .env.example              образец
├── config/                   ВСЯ НОРМАТИВКА — правится руками
│   ├── normativy.yaml        сроки ПП 1931
│   ├── routes.yaml           13 направлений реагирования
│   ├── ukio.yaml             134 поля карточки
│   ├── rechevye_pravila.yaml правила речи МЧС
│   ├── regions/so.yaml       Свердловская область
│   ├── classifiers/*.yaml    22 классификатора
│   └── protocols/_base.yaml  алгоритм опроса, прил. № 11
├── content/scenarios/*.json  11 сценариев обучения
├── db/00{1,2,3}_*.sql        миграции, применяются по номерам
├── models/                   ПУСТО — веса скачаются сюда сами (~10 ГБ)
├── voicebank/                ПУСТО — заполнит `make prewarm`
├── services/api/             FastAPI
├── services/asr/             GigaAM
├── services/tts/             Silero
├── web/                      Next.js
└── scripts/                  проверки и утилиты
```

**Каталоги `models/` и `voicebank/` должны существовать и быть
доступны на запись** — в них пишут контейнеры.

```bash
mkdir -p models voicebank
chmod 777 models voicebank
```

---

## Шаг 2. Настроить .env

```bash
cp .env.example .env
nano .env
```

Обязательно поменять:

```ini
# --- пароль БД: сменить с примера ---
PG_PASSWORD=ваш_надёжный_пароль

# --- LLM: ГЛАВНОЕ ---
# Должно ТОЧНО совпадать с --served-model-name у vLLM.
# Это не путь к весам, а имя, под которым модель отдаётся по API.
LLM_MODEL=Qwen3-VL
LLM_BASE_URL=http://host.docker.internal:1212/v1

# --- GPU ---
# 0-я карта под LLM, 1-я под речь. На одну карту не ставить:
# конкуренция за память даёт плавающие таймауты вместо ошибки.
SPEECH_GPU=1

# --- регион ---
REGION=so
```

Порты менять не обязательно, по умолчанию:

```ini
WEB_PORT=21120
API_PORT=21121
PG_PORT=21122
REDIS_PORT=21123
ASR_PORT=21124
TTS_PORT=21125
BIND_HOST=127.0.0.1
```

---

## Шаг 3. Запустить LLM (отдельно, на хосте)

**LLM в docker-compose не входит.** Она уже была развёрнута у вас
отдельно — если работает, шаг пропустить.

```bash
curl -s http://localhost:1212/v1/models | python3 -m json.tool
```

Ответ должен содержать `"id": "Qwen3-VL"` — и это значение обязано
совпадать с `LLM_MODEL` в `.env`.

Если не запущена:

```bash
python -m vllm.entrypoints.openai.api_server \
  --model /path/to/Qwen3-VL \
  --served-model-name Qwen3-VL \
  --port 1212 \
  --gpu-memory-utilization 0.85 \
  --max-model-len 8192 \
  --enable-prefix-caching
```

`--enable-prefix-caching` обязателен: клиент в `llm.py` намеренно ставит
статичную часть промпта первой, чтобы KV-кэш переиспользовался между
ходами диалога. Без флага каждый ход считается заново.

---

## Шаг 4. Проверить до запуска

```bash
pip install pyyaml            # нужен для проверки конфигурации
make config-check
```

Ожидаемый вывод:

```
config/: 29 файлов
  маршрутов: 13, со спецчастью УКИО: 6
  классификаторов: есть 22, нужно 22
  ЗАГЛУШКИ (5): classifiers/doroga.yaml, ...
ОК
```

Пять заглушек — это нормально, они помечены сознательно.

```bash
make ports
```

Если порт занят — поменять в `.env`.

---

## Шаг 5. Первый запуск

Разворачивать по частям, а не всё сразу: так видно, где сломалось.

### 5.1. Текстовый режим — быстро

```bash
make up
```

Поднимутся Postgres, Redis, API, Web. Около минуты.

```bash
make ps
make health
```

Проверка:

```bash
curl -s localhost:21121/health
curl -s localhost:21121/api/v2/config | python3 -m json.tool | head -20
```

Должно вернуться: регион, 13 маршрутов, 6 спецчастей, норматив 75.

Откройте `http://localhost:21120` — заработают оба режима, но без голоса.

### 5.2. Речевой режим — долго

```bash
make up-speech
```

**Первый запуск до 10 минут** — качаются веса GigaAM и Silero в `./models`.
У ASR в healthcheck стоит `start_period: 300s`, это не зависание.

Смотреть прогресс:

```bash
docker logs -f t112-asr
```

Ждать статус `healthy`:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
```

Проверка:

```bash
curl -s localhost:21124/health   # ASR
curl -s localhost:21125/health   # TTS
du -sh models/                   # должно быть несколько ГБ
```

### 5.3. Прогреть голоса и загрузить сценарии

```bash
make prewarm     # синтезирует реплики заранее, разгружает TTS в бою
make seed        # загружает 11 сценариев в БД
```

`make prewarm` повторять после каждого добавления сценариев.

---

## Шаг 6. Проверить, что всё работает

```bash
make test-v2
```

55 проверок без docker: конфигурация, движок протокола, правила речи,
сессии в Redis, отчёты, сценарии. Все должны пройти.

```bash
make status
```

Покажет режим доступа и адрес.

---

## Шаг 7. Открыть с другого компьютера

Выбор режима зависит от одного: **нужен ли микрофон.**

Браузер даёт доступ к микрофону только в защищённом контексте —
`https` либо `localhost`. По `http://10.109.50.250:21120` записи не будет.
SSH-туннель решает это тем, что для браузера адрес становится `localhost`.

### Вариант А. Туннель — микрофон работает

**На сервере:**

```bash
make close
make tunnel
```

**На своём компьютере.** Создайте или дополните `~/.ssh/config`:

```
Host nir
    HostName 10.109.50.250
    User ваш_логин
    IdentityFile ~/.ssh/nir_gpu1_ed25519
    IdentitiesOnly yes
    LocalForward 21120 localhost:21120
    LocalForward 21121 localhost:21121
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

Права на ключ обязательны, иначе SSH откажется работать:

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/nir_gpu1_ed25519
```

Проверить ключ до туннеля:

```bash
ssh nir 'echo ok'
```

Поднять туннель:

```bash
ssh -N nir
```

Открыть `http://localhost:21120`.

**Пробрасывать нужно оба порта.** 21120 — интерфейс, 21121 — API, куда
браузер ходит напрямую. Пробросите один — страница откроется пустой.

`IdentitiesOnly yes` нужен при нескольких ключах: без него сервер может
отвергнуть подключение, исчерпав лимит попыток.

**Windows.** Встроенный OpenSSH в PowerShell работает так же, конфиг
в `C:\Users\Имя\.ssh\config`. Права — через свойства файла: убрать всех,
кроме своей учётной записи. В PuTTY: Connection → SSH → Tunnels,
Source port 21120, Destination `localhost:21120`, Add; затем то же для 21121.

**Логин root.** Если `make tunnel` подставил root, а вход не проходит —
во многих системах `sshd` настроен как `PermitRootLogin prohibit-password`.
По ключу работает, по паролю нет.

### Вариант Б. Открытые порты — микрофона нет

```bash
make open
```

Скрипт сам определит адрес сервера, перепишет `.env` и **пересоздаст**
контейнер web.

Пересоздаст, а не перезапустит — это важно: `NEXT_PUBLIC_API_URL`
вкомпилируется в клиентский бандл при старте процесса Next, и обычный
`restart` её не подхватит. Интерфейс откроется, но останется пустым.

Дальше с любого компьютера: `http://10.109.50.250:21120`.

Вернуть безопасный режим:

```bash
make close
```

Если сервер виден извне — ограничьте подсеть:

```bash
sudo ufw allow from 10.109.0.0/16 to any port 21120
sudo ufw allow from 10.109.0.0/16 to any port 21121
```

### Вариант В. HTTPS — и микрофон, и сеть

Единственный способ получить оба. Caddy берёт сертификат сам:

```
t112.вашдомен.ru {
    reverse_proxy /api/* localhost:21121
    reverse_proxy localhost:21120
}
```

Нужны доменное имя и доступность 80/443 для Let's Encrypt.

---

## Шаг 8. Что открывать

| Адрес | Что |
|---|---|
| `/` | выбор режима |
| `/trainee` | **обучение** — заявитель от LLM, оценка после вызова |
| `/assistant` | **поддержка** — боевой режим, подсказки без оценок |
| `/instructor` | преподаватель: усложнять обстановку по ходу |
| `/analytics` | отчёты по формам приказа МЧС № 192 |

---

## Шаг 9. Что править, когда придут документы

Из 22 классификаторов **пять помечены заглушками**. Их количество
видно в шапке интерфейса — чтобы на демонстрации не выдать заглушку
за утверждённый норматив.

| Файл | Чем заменить | Приоритет |
|---|---|---|
| `config/classifiers/tip_proisshestviya.yaml` | «Алгоритм опроса по видам происшествий» от ГКУ СО ТЦМ | **высокий** |
| `config/classifiers/ulica.yaml` | справочник ФИАС | средний |
| `config/classifiers/mesto.yaml` | полный ОКАТО региона | средний |
| `config/classifiers/obekt.yaml` | справочник ОКПО | низкий |
| `config/classifiers/doroga.yaml` | реестр дорог региона | низкий |

### Порядок

```bash
nano config/classifiers/tip_proisshestviya.yaml
# 1. заменить список items
# 2. удалить строку  status: ZAGLUSHKA
# 3. указать istochnik

make config-check      # проверить
make config-reload     # применить БЕЗ перезапуска контейнеров
```

**Код при этом не трогается.**

### Новый регион

```bash
cp config/regions/_template.yaml config/regions/chel.yaml
nano config/regions/chel.yaml
sed -i 's/^REGION=.*/REGION=chel/' .env
docker compose up -d --force-recreate api
```

### Новый протокол по виду происшествия

Создать `config/protocols/pozhar.yaml` по образцу `_base.yaml`
и указать имя файла в поле `protokol` соответствующего типа
происшествия в `tip_proisshestviya.yaml`.

### Изменить норматив

```bash
nano config/normativy.yaml     # например, opros_i_kartochka_sec
make config-reload
```

Таймер во фронте, пороги подсказок и оценка скорости подхватят
новое значение автоматически.

---

## Шаг 10. Эксплуатация

```bash
make ps                    # состояние контейнеров
make logs                  # логи всех
docker logs -f t112-api    # логи одного
make health                # проверка эндпоинтов
make status                # режим доступа и адрес

make down                  # остановить
make reset                 # СНЕСТИ БД и поднять заново
```

### Бэкап

Сессии и результаты обучения восстановить неоткуда:

```bash
mkdir -p /data/backup
docker exec t112-postgres pg_dump -U t112 trainer112 \
  | gzip > /data/backup/t112-$(date +%F).sql.gz
```

В cron еженедельно.

**Не бэкапить:** `models/` (скачается заново) и `voicebank/`
(пересоздаётся через `make prewarm`).

### Redis

Сессии и итоги вызовов лежат в Redis, `appendonly yes` включён —
переживают перезапуск контейнера.

```bash
docker exec t112-redis redis-cli DBSIZE
docker exec t112-redis redis-cli KEYS 't112:*' | head
```

TTL: сессии 6 часов, итоги 30 дней. Меняется в `.env`:
`SESSION_TTL_SEC`, `ITOG_TTL_SEC`.

---

## Если не поднялось

| Симптом | Причина и что делать |
|---|---|
| ASR в `starting` больше 10 минут | качает веса. `docker logs t112-asr` покажет прогресс |
| ASR упал с CUDA OOM | LLM занял всю карту. Развести: `SPEECH_GPU=1` |
| Заявитель молчит, API жив | `LLM_MODEL` не совпадает с `--served-model-name` |
| `host.docker.internal` не резолвится | проверить `extra_hosts` у сервиса api в compose |
| Web перезапускается по кругу | `.next` внутри bind-mount без `ignored` в `watchOptions` |
| Страница открылась пустой | не проброшен порт API (21121) |
| «сессия не найдена» через час | истёк `SESSION_TTL_SEC` — это норма |
| Redis недоступен | `docker logs t112-redis`; сессии не создадутся вовсе |
| `make config-check` ругается | опечатка в YAML; сообщение укажет файл |
| Микрофон не работает | вы по `http` с чужого адреса. Нужен туннель или HTTPS |

### Полный сброс

```bash
make down
docker compose down -v      # снесёт тома с БД и Redis
rm -rf models/* voicebank/*
make up-speech && make prewarm && make seed
```

---

## Быстрая шпаргалка

```bash
# развернуть
cd /data/112
cp .env.example .env && nano .env
mkdir -p models voicebank && chmod 777 models voicebank
make config-check && make ports
make up-speech && make prewarm && make seed
make test-v2 && make health

# доступ с ноутбука (после настройки ~/.ssh/config)
ssh -N nir          # затем http://localhost:21120

# показать коллегам без микрофона
make open
make close          # вернуть обратно
```
