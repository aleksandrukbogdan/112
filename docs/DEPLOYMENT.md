# Развёртывание

Документ описывает установку на сервер (в том числе с видеокартами), настройку, обновление и
обслуживание. Для быстрого знакомства на своём компьютере достаточно раздела
«Быстрый локальный запуск» в [README](../README.md).

## 1. Варианты состава

| Команда | Что поднимается | Когда использовать |
|---|---|---|
| `docker compose up -d --build db app` | БД и приложение | демонстрация, текстовый режим |
| `make up-cpu` | + `voice` (Vosk, Piper) | сервер без видеокарты |
| `make up` | + `asr` (GigaAM) и `tts` (F5-TTS) на GPU | рекомендуемый рабочий режим |
| `make up-llm` | + `llm` (vLLM с Qwen3-VL) | если модель не запущена на хосте |

Оценка не зависит ни от модели, ни от речевых сервисов.

## 2. Требования

| | Требование | Проверка |
|---|---|---|
| ОС | Linux x86_64 (Ubuntu 22.04 / 24.04); для минимального состава подойдут macOS и Windows с Docker Desktop | `uname -m` |
| Docker / Compose | 24.0+ / 2.20+ | `docker --version`, `docker compose version` |
| Видеокарта | для GigaAM и F5-TTS — NVIDIA, от 4 ГБ свободной памяти, желательно не та, где работает LLM | `nvidia-smi` |
| Драйвер NVIDIA | 525+, рекомендуется 550+ (образы на CUDA 12.4) | `nvidia-smi` |
| NVIDIA Container Toolkit | для GPU-контейнеров | `make gpu-check` |
| Диск | ~20 ГБ (образы PyTorch и модели речи); с `up-llm` ещё ~20 ГБ под веса | `df -h` |
| Память | от 8 ГБ | `free -h` |
| Интернет | только при первой сборке | — |

## 3. Получение кода

```bash
sudo mkdir -p /data/t112 && sudo chown $USER:$USER /data/t112
git clone <URL_РЕПОЗИТОРИЯ> /data/t112
cd /data/t112
```

## 4. Настройка `.env`

```bash
cp .env.example .env
```

`make up` создаст `.env` из образца автоматически, но пароль БД нужно сменить **до первого запуска**:
PostgreSQL запоминает пароль при инициализации каталога `state/pg`.

### Переменные, которые читает `docker-compose.yml`

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `DB_PASSWORD` | `t112pass` | пароль PostgreSQL — **смените** |
| `BIND_HOST` | `127.0.0.1` | на каком адресе хоста открыт порт приложения; `0.0.0.0` — для всей сети |
| `PORT` | `8080` | порт приложения на хосте |
| `LLM_BASE_URL` | `http://host.docker.internal:1212/v1` | OpenAI-совместимый адрес модели |
| `LLM_MODEL` | `Qwen3-VL` | должно совпадать с `--served-model-name` у vLLM |
| `LLM_ENABLED` | `true` | `false` — работать без модели |
| `ASR_ENGINE` | `auto` | `auto` (GigaAM, затем Vosk), `gigaam`, `vosk` |
| `ASR_GPU` | `1` | номер карты для GigaAM |
| `F5_GPU` | `1` | номер карты для F5-TTS |
| `F5_NFE_STEP` | `12` | шагов синтеза F5 (меньше — быстрее, грубее) |
| `F5_MODEL_DIR` | `/data/models/F5-TTS_RUSSIAN` | каталог весов F5-TTS на хосте |
| `F5_HF_HOME` | `/data/models` | кэш Hugging Face на хосте |
| `F5_SOUND_DIR` | `/data/112/voice/Sound` | звуковые фоны сцен |
| `F5_VOICES_DIR` | `/data/112/voice/Voices` | референсные голоса |
| `F5_URL` | `http://tts:8092` | адрес F5 для сервиса `voice` |
| `ADMIN_PASSWORD`, `TEACHER_PASSWORD`, `TRAINEE_PASSWORD` | пусто | пароли учётных записей первого запуска; пусто — стандартные |
| `UCHEBNY_TAYMING_SEC` | `30` | учебный тайминг по ТЗ |
| `DDS_ETAPY_SEC` | `45,25,50` | через сколько секунд после выезда бригада прибывает, начинает и завершает работы |
| `DDS_VEROYATNOST_OSHIBKI` | `0.7` | передаётся в контейнер, но в текущей версии не используется: доля ошибок задаётся профилем упражнения (0,7 для «Проверки карточки») |
| `BACKUP_PERIOD_SEC` | `86400` | период автоматического резервного копирования |
| `LLM_GPU`, `LLM_GPU_UTIL`, `LLM_HF_MODEL`, `LLM_MAX_LEN`, `LLM_PORT`, `VLLM_IMAGE`, `HF_TOKEN`, `HF_CACHE` | см. `docker-compose.yml` | только для `make up-llm` |

### Переменные приложения вне Compose

Используются при запуске без Docker или добавляются в `environment` сервиса `app`.

| Переменная | Назначение |
|---|---|
| `DATABASE_URL` | строка подключения PostgreSQL; если не задана — SQLite |
| `DB_PATH` | файл SQLite; рядом создаётся `secret.key` |
| `BACKUP_DIR` | каталог резервных копий |
| `DATA_DIR` | каталог `data/` |
| `VOICE_URL`, `ASR_URL` | адреса речевых сервисов |
| `SECRET_KEY` | ключ подписи токенов (иначе генерируется в `secret.key`) |
| `TOKEN_TTL_SEC` | срок жизни токена, по умолчанию 43200 |
| `SCHEDULER_ENABLED` | планировщик групповых занятий, по умолчанию `true` |
| `ML_FORECAST_ENABLED`, `ML_FORECAST_MODEL` | опциональная ML-модель прогноза (раздел 11) |

Нормативы, статусы ДДС и типы ошибок, которые вносит «оператор 112», настраиваются в `api/config.py`
без изменения логики.

## 5. Языковая модель

**Вариант А — модель уже работает на хосте (рекомендуется).**

```bash
curl -s http://localhost:1212/v1/models | python3 -m json.tool
```

Значение `id` из ответа впишите в `LLM_MODEL`. vLLM должен слушать `0.0.0.0`, иначе контейнер его не
увидит (`--host 0.0.0.0`).

**Вариант Б — модель в контейнере.** `make up-llm` поднимет `t112-llm`, скачает `LLM_HF_MODEL` в
`state/hf/` и отдаст её под именем `LLM_MODEL`. Первая загрузка — десятки гигабайт. Если контейнер
не стартует, смотрите `docker logs t112-llm`; чаще всего помогает зафиксировать версию `VLLM_IMAGE`.

**Без модели:** `LLM_ENABLED=false`. Заявитель отвечает по правилам, генерация сценариев недоступна.

## 6. Видеокарты

```bash
make gpu-check
```

Команда покажет карты и проверит, видит ли их Docker. Если Docker их не видит, установите
NVIDIA Container Toolkit:

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Распределение карт: в `nvidia-smi` найдите карту, занятую моделью, и укажите **другую** в `ASR_GPU`
и `F5_GPU`. Если свободной карты нет, речевые модели можно разместить на карте модели при остатке
от 4 ГБ, уменьшив у vLLM `--gpu-memory-utilization` (например, до 0.80). Если GigaAM не загрузился,
распознавание автоматически переходит на Vosk — причина видна в `make health` («Примечание»).

## 7. Модели речи

| Модель | Размер | Где берётся | Контейнер |
|---|---|---|---|
| GigaAM v3 e2e-RNNT (MD5 проверяется) | ≈1 ГБ | скачивается при сборке с CDN SberDevices | `t112-asr` |
| Vosk small-ru 0.22 | ~45 МБ | каталог `voice/models/vosk` | `t112-voice` |
| Piper ru_RU-irina-medium | ~63 МБ | каталог `voice/models/piper` | `t112-voice` |
| F5-TTS Russian | несколько ГБ | размещается на хосте в `F5_MODEL_DIR`, в репозиторий не входит | `t112-f5` |
| Qwen3-VL (только `up-llm`) | 15–20 ГБ | Hugging Face | `t112-llm` |

Контейнер `tts` работает в офлайн-режиме (`HF_HUB_OFFLINE=1`): веса F5-TTS должны лежать на диске
до запуска.

## 8. Запуск

```bash
make up          # или make up-cpu / make up-llm
```

Первый запуск занимает 10–20 минут: собираются образы и скачиваются модели. Команда дождётся
готовности и выведет сводку:

```
  Приложение ..... работает
  База ........... PostgreSQL — работает
  Модель ......... работает
  Распознавание .. GigaAM на cuda · NVIDIA …
  Синтез речи .... F5-TTS
  Данные ......... классификатор v0.46.24 (1283 поз.), 96 вызовов, 193 служб
```

Логины первого запуска: `make passwords`.

## 9. Доступ к приложению

По умолчанию порт открыт только на `127.0.0.1` сервера. Варианты доступа:

**SSH-туннель (рекомендуется).** На своём компьютере:

```bash
ssh -i ~/.ssh/ваш_ключ -N -L 8080:localhost:8080 пользователь@адрес_сервера
```

Затем откройте http://localhost:8080. `make tunnel` напечатает готовую команду. Для постоянной работы
удобно описать туннель в `~/.ssh/config`:

```
Host t112
    HostName адрес_сервера
    User пользователь
    IdentityFile ~/.ssh/ваш_ключ
    IdentitiesOnly yes
    LocalForward 8080 localhost:8080
    ServerAliveInterval 30
```

и запускать `ssh -N t112`. В Windows PowerShell команды те же; в PuTTY туннель задаётся в
Connection → SSH → Tunnels.

**Открыть порт в сеть** (для показа): `make open`, вернуть — `make close`. Микрофон в браузере по
обычному HTTP с чужого адреса работать не будет.

**HTTPS для учебных АРМ:**

```bash
python3 deploy/enable_tls.py --target /data/t112 --hostname адрес_или_имя --port 8443
docker compose -f docker-compose.yml -f deploy/runtime.compose.json -f deploy/tls.compose.json up -d --no-deps tls
```

Скрипт создаёт самоподписанный сертификат в `state/tls/` и конфигурацию nginx. Установите
`state/tls/server.crt` как доверенный на рабочих местах или замените парой от своего УЦ.

## 10. Данные и резервные копии

Всё состояние хранится в `state/` и в репозиторий не попадает:

```
state/pg/      файлы PostgreSQL
state/app/     ключ подписи сессий, резервные копии (backup/), ML-модель (ml/)
state/hf/      кэш весов модели (make up-llm)
```

- Автоматическая копия всех таблиц — раз в сутки, хранятся 14: `state/app/backup/t112-ДАТА.json.gz`.
- Вручную: `make backup` или кнопка в разделе администратора.
- Восстановление: «Администрирование → Резервные копии → Восстановить»; перед этим система
  сама делает страховочную копию.
- Полная копия сервера — архив каталога `state/` при остановленных контейнерах (`make down`).

## 11. Опциональный ML-прогноз

По умолчанию выключен, используется подписанная эвристика. Порядок включения после накопления
реальных попыток (минимум 200):

```bash
python -m scripts.ml_export /безопасная/папка/dataset.json
python -m scripts.ml_train /безопасная/папка/dataset.json /безопасная/папка/candidate.json
# после изучения отчёта:
python -m scripts.ml_train ... --approve
```

Проверенный файл разместите в `state/app/ml/call-model.json` и добавьте в `environment` сервиса `app`:

```yaml
ML_FORECAST_ENABLED: "true"
ML_FORECAST_MODEL: /app/state/ml/call-model.json
```

При отсутствующем, повреждённом или неодобренном файле приложение возвращается к эвристике.
Экспорт содержит псевдонимы обучающихся — ограничивайте к нему доступ.

## 12. Обновление

**Из Git:**

```bash
make backup
git pull
docker compose build app
docker compose up -d --no-deps app
```

Новые таблицы создаются миграциями при старте; старые строки не удаляются. Каталог `state/` не
затрагивается. Перед обновлением лучше завершить активные занятия.

**Пакетом обновления** (`deploy/installer.py`): проверяет контрольные суммы, делает резервную копию
кода и БД, собирает образ, проверяет `/readyz` и при ошибке автоматически откатывается.

```bash
python3 deploy/installer.py --target /data/t112 --check     # только проверка
python3 deploy/installer.py --target /data/t112             # установка
python3 deploy/installer.py --target /data/t112 --rollback state/upgrade-backups/<каталог>
python3 deploy/doctor.py --url http://127.0.0.1:8080        # диагностика после установки
```

## 13. Сервер без интернета

На машине с интернетом: `make up`, затем `make save` → `t112-images.tar`. Перенесите репозиторий и
архив образов. На изолированной машине:

```bash
docker load -i t112-images.tar
cp .env.example .env && mkdir -p state/pg state/app
docker compose --profile gpu up -d      # без --build
make health
```

Только образ приложения можно подготовить скриптом `deploy/prepare_offline.py`.

## 14. Тестирование

```bash
python3 scripts/verify_patch.py                   # модульные тесты, JS-проверки (нужен Node.js)
python3 scripts/verify_patch.py --integration     # + API-тесты
```

Внутри образа приложения (без Node.js):

```bash
docker compose run --rm --no-deps -v "$PWD:/work:ro" -w /work app \
  python scripts/verify_patch.py --integration --skip-js
```

`make test` и `scripts/test.py` — прогон предыдущей версии: он ожидает ошибку оператора 112 в
стандартной карточке ДДС, которой в текущей версии нет, и останавливается в разделе 8. Актуальные
проверки — `scripts/verify_patch.py`.

Проверка на PostgreSQL — на **отдельной тестовой** базе; тест создаёт и затем удаляет свою схему
`t112_patch_test_*`:

```bash
export TEST_DATABASE_URL=postgresql://user:pass@host:5432/testdb RUN_POSTGRES_TESTS=1
python3 scripts/verify_patch.py --postgres
```

## 15. Команды Make

| Команда | Назначение |
|---|---|
| `make up` / `up-cpu` / `up-llm` | запуск |
| `make health` · `ps` · `logs` | состояние |
| `make passwords` · `tunnel` | учётные записи и команда туннеля |
| `make open` / `close` | открыть / закрыть порт в сеть |
| `make backup` · `save` | резервная копия, упаковка образов |
| `make restart` · `down` | перезапуск, остановка |
| `make gpu-check` | проверка видеокарт |
| `make test` | старый приёмочный прогон `scripts/test.py` (см. примечание в разделе 14) |

## 16. Если не работает

| Симптом | Что делать |
|---|---|
| «Docker НЕ видит GPU» | установить NVIDIA Container Toolkit (раздел 6) или `make up-cpu` |
| сборка падает на загрузке моделей | нет доступа к сайтам моделей — раздел 13 |
| `База … ОШИБКА` | `docker logs t112-db`; если `DB_PASSWORD` изменён после первого запуска — верните прежний или удалите `state/pg` (данные пропадут) |
| Модель «НЕДОСТУПНА» | вариант А: `curl localhost:1212/v1/models`; вариант Б: `docker logs t112-llm` |
| «не совпадает с served-model-name» | исправить `LLM_MODEL`, `make restart` |
| распознаёт Vosk вместо GigaAM | `make health` → «Примечание»; `docker logs t112-asr` |
| страница не открывается | не поднят туннель; проверить `curl localhost:8080/health` |
| «Нет доступа к микрофону» | открывать через `localhost` (туннель) или HTTPS |
| старший бригады не звонит | бригаде не передан адрес — позвонить и назвать адрес |

Сброс пароля администратора:

```bash
docker exec -it t112 python3 -c "from api import db, auth; db.ex('UPDATE users SET pw_hash=?, active=1 WHERE login=?', (auth.hash_pw('НовыйПароль123'), 'admin')); print('готово')"
```

## Приложение: версии компонентов

```
t112-db     postgres:16-alpine
t112        python:3.12-slim · fastapi 0.141.1 · uvicorn 0.54.0 · httpx 0.28.1 · pydantic 2.13.5
            psycopg[binary] 3.3.6 · python-multipart 0.0.32 · reportlab 4.4.9 · openpyxl 3.1.5
            python-docx 1.2.0 · pypdf 6.10.0            (requirements-release.txt)
t112-asr    pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime · GigaAM коммит 7447938 (MIT), v3_e2e_rnnt
            onnxruntime 1.23.2 · numpy 2.2.6          (asr/requirements.txt)
t112-voice  python:3.12-slim · vosk 0.3.45 + vosk-model-small-ru-0.22 · piper-tts 1.3.0 + ru_RU-irina-medium
t112-f5     pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime · f5-tts 1.1.22 (--no-deps)
            + tts/requirements-docker.txt
t112-llm    vllm/vllm-openai (VLLM_IMAGE) · Qwen/Qwen3-VL-8B-Instruct (LLM_HF_MODEL)
```
