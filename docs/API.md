# API

Приложение — REST API на FastAPI. Базовый адрес при локальном запуске: `http://localhost:8080`.

Автоматическая спецификация OpenAPI генерируется самим приложением:

- `GET /docs` — Swagger UI со всеми маршрутами и схемами запросов;
- `GET /openapi.json` — спецификация в JSON.

Этот документ даёт обзор; точные схемы тел запросов смотрите в `/docs`.

## Общие соглашения

**Аутентификация.** `POST /api/login` возвращает токен. Остальные маршруты `/api/*` требуют заголовок:

```
Authorization: Bearer <token>
```

Токен действует 12 часов. Для `GET /api/voice/tts`, который вызывается из тега `<audio>`, токен
передаётся параметром `?token=`.

**Роли.** `admin`, `teacher`, `trainee`. Ответ `401` — нет или истёк токен, `403` — недостаточно прав
или чужой объект, `409` — конфликт состояния (например, разбор до завершения попытки),
`422` — ошибка валидации.

**Идемпотентность.** Для POST/PUT/PATCH/DELETE можно передать `Idempotency-Key` (до 128 символов).
Повтор команды с тем же ключом не выполнит её второй раз. Интерфейс использует это для команд попытки.

**Формат.** JSON в UTF-8. Имена полей — латинская транслитерация русских терминов
(`kartochka` — карточка, `sluzhby` — службы, `replika` — реплика, `otpravit` — отправить,
`razbor` — разбор, `prognoz` — прогноз, `otchet` — отчёт).

### Пример

```bash
TOKEN=$(curl -s -X POST localhost:8080/api/login \
  -H 'Content-Type: application/json' \
  -d '{"login":"trainee","password":"trainee112"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

curl -s localhost:8080/api/scenarios -H "Authorization: Bearer $TOKEN"

curl -s -X POST localhost:8080/api/session -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"scenario_id":"<id сценария>"}'
```

## Служебные

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| GET | `/health` | все | состояние БД, LLM, речевых сервисов, сводка данных, число идущих и завершённых занятий |
| GET | `/readyz` | все | готовность приложения, версия релиза и рубрики, тип БД |
| GET | `/` | все | интерфейс |
| GET | `/static/{name}` | все | JS и CSS интерфейса |

## Вход и профиль

| Метод | Путь | Тело / параметры | Описание |
|---|---|---|---|
| POST | `/api/login` | `{login, password}` | возвращает `{token, user}` |
| GET | `/api/me` | — | текущий пользователь |
| POST | `/api/me/password` | `{old, new}` | смена своего пароля |

## Справочники

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/config` | нормативы, службы, навыки, настройки для интерфейса |
| GET | `/api/tree` | дерево классификатора |
| GET | `/api/classifier/search` | поиск по классификатору |
| GET | `/api/spravka` | справочные материалы |

## Сценарии

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| GET | `/api/scenarios` | все | список сценариев; эталоны обучающемуся не выдаются |
| POST | `/api/scenarios/generate` | teacher | черновик: `{kod, slozhnost, utochnenie, kolichestvo}` |
| POST | `/api/scenarios/{sid}/validate` | teacher | утвердить или отклонить: `{status, data?, comment?}` |
| POST | `/api/scenario-tools/generate` | teacher | структурированная генерация |
| POST | `/api/scenario-tools/{sid}/correct` | teacher | правка — создаёт неопубликованную копию |
| GET | `/api/scenario-tools/{sid}/preview` | teacher | предпросмотр |

## Пользователи, группы, назначения

| Метод | Путь | Доступ | Описание |
|---|---|---|---|
| GET / POST | `/api/groups` | teacher, admin | список групп / создать `{name}` |
| GET / POST | `/api/users` | admin | список / создать `{login, name, role, password, group_id?}` |
| POST | `/api/users/{uid}` | admin | изменить `{active?, group_id?, password?, name?}` |
| GET | `/api/assignments` | все | назначения (свои или своих групп) |
| POST | `/api/assignments` | teacher | `{group_id, scenario_ids, rezhim, dds_sluzhba?, tayming_sec?, deadline?}`; `rezhim`: `ops112` или `dds` |
| DELETE | `/api/assignments/{aid}` | teacher | удалить назначение |

## Попытка в режиме «Приём вызова 112»

| Метод | Путь | Тело | Описание |
|---|---|---|---|
| POST | `/api/session` | `{scenario_id, assignment_id?, lesson_id?}` | начать попытку, возвращает `sid` и начальное состояние |
| POST | `/api/session/{sid}/replika` | `{tekst}` | реплика оператора; ответ — реплика заявителя |
| POST | `/api/session/{sid}/pole` | `{key, value, expected_version?, client_id?, client_seq?}` | изменить поле карточки с проверкой версии |
| GET | `/api/session/{sid}/prognoz` | — | прогноз успеть до норматива (`ml_status` показывает источник) |
| POST | `/api/session/{sid}/otpravit` | `{sluzhby, kartochka}` | отправить карточку и завершить; повтор возвращает тот же ответ |
| GET | `/api/session/{sid}/razbor` | — | разбор: граф доказательств, балл, зачёт, события; до завершения — 409 |
| POST | `/api/session/{sid}/review` | `{changes, reason}` | пересмотр критериев преподавателем |
| POST | `/api/session/{sid}/override` | `{ball, reason}` | ручная правка балла преподавателем (в аудит) |
| GET | `/api/my/sessions` | — | мои попытки |
| GET | `/api/sessions` | — | попытки групп (преподаватель) |
| GET | `/api/live` | — | идущие занятия в реальном времени (преподаватель) |

## Режим «Диспетчер ДДС» (`/api/dds`)

| Метод | Путь | Тело | Описание |
|---|---|---|---|
| GET | `/api/dds/sluzhby` | — | службы, доступные для обучения |
| GET | `/api/dds/nastroyki` | — | статусы, этапы, нормативы режима |
| POST | `/api/dds/session` | `{scenario_id, assignment_id?, sluzhba?, exercise_profile?, lesson_id?}` | получить карточку от «оператора 112» |
| POST | `/api/dds/{sid}/status` | `{status, kommentariy}` | поставить статус службы |
| POST | `/api/dds/{sid}/zamechanie` | `{pole, verno, kommentariy}` | отметить расхождение карточки с записью |
| POST | `/api/dds/{sid}/call` | `{kontakt}` | исходящий звонок (бригада и др.) |
| POST | `/api/dds/{sid}/call/{cid}/say` | `{tekst}` | реплика в разговоре |
| POST | `/api/dds/{sid}/call/{cid}/end` | — | положить трубку |
| GET | `/api/dds/{sid}/events` | — | входящие звонки и события по таймеру |
| POST | `/api/dds/{sid}/incoming/{eid}/answer` | — | ответить на входящий доклад |
| POST | `/api/dds/{sid}/finish` | — | закрыть карточку и получить оценку |

## Групповые занятия, обучение, помощник

| Метод | Путь | Описание |
|---|---|---|
| POST / GET | `/api/lessons` | создать занятие / список |
| POST | `/api/lessons/{lid}/start`, `/api/lessons/{lid}/stop` | начать / остановить занятие |
| GET | `/api/lessons/queue/mine` | очередь карточек обучающегося |
| GET | `/api/training/{sid}/resume` | возобновить попытку |
| GET | `/api/training/{sid}/observations` | наблюдения по попытке |
| POST | `/api/training/{sid}/intervene` | вмешательство преподавателя |
| POST | `/api/training/{sid}/replay` | изолированный повтор |
| POST | `/api/training/{sid}/analyze`, `/review-preview`, `/stop` | анализ, предпросмотр пересмотра, остановка |
| GET | `/api/helper/{sid}` | состояние помощника |
| POST | `/api/helper/{sid}/toggle` | включить / выключить помощника |
| POST | `/api/helper/{sid}/suggest` | получить предложения |
| POST | `/api/helper/{sid}/proposals/{pid}` | принять / отклонить предложение |
| POST | `/api/helper/{sid}/proposals/{pid}/review` | оценка предложения преподавателем |
| POST / GET | `/api/materials`, `GET /api/materials/{mid}` | учебные материалы |

## Аналитика и отчёты

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/analytics/me` | мой прогресс |
| GET | `/api/analytics/user/{uid}` | прогресс обучающегося |
| GET | `/api/analytics/group/{gid}` | группа, тепловая карта |
| GET | `/api/analytics/drill` | проваливание до конкретных вызовов |
| GET | `/api/insights/{uid}` | персональный план и рекомендации |
| GET | `/api/analysis/snapshot` | неизменяемый снимок данных для графиков |
| GET | `/api/analysis/export/{sid}.{fmt}` | выгрузка снимка: `pdf`, `xlsx` или `json`; токен — параметром `?token=` |
| GET | `/api/analysis/live` | оперативные данные |
| GET | `/api/otchet/forma1`, `/api/otchet/forma2` | формы 1/112 и 2/112 приказа МЧС № 192 |
| GET | `/api/otchet/zanyatie/{sid}.pdf` | отчёт о занятии |
| GET | `/api/otchet/sertifikat/{uid}.pdf` | сертификат |
| GET | `/api/otchet/export.xlsx` | выгрузка в Excel |

## Администрирование (роль `admin`)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/admin/system` | состояние компонентов |
| GET | `/api/admin/audit` | журнал аудита |
| GET | `/api/admin/backups` | список резервных копий |
| POST | `/api/admin/backup` | создать резервную копию |
| GET | `/api/admin/backup/{name}` | скачать копию |
| POST | `/api/admin/restore/{name}` | восстановить (перед этим создаётся страховочная копия) |
| POST | `/api/admin/settings` | `{uchebny_tayming_sec}` |
| GET / POST | `/api/admin/scopes` | доступ преподавателей к группам `{teacher_id, group_id, allowed}` |
| GET / POST | `/api/admin/voices` | голоса заявителей: список / загрузить |
| POST | `/api/admin/voices/select` | выбрать голос `{voice_id}` |
| GET | `/api/admin/voices/{voice_id}/audio` | образец голоса |
| PATCH / DELETE | `/api/admin/voices/{voice_id}` | переименовать / удалить |
| GET | `/api/admin/voices/board` | привязки голосов к сценариям |
| POST | `/api/admin/voices/binds` | сохранить привязки |

## Речь

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/voice/asr` | `multipart/form-data`, поле `audio` (webm/ogg/wav). Сначала GigaAM, при недоступности Vosk. Ответ: `{text, engine, ...}`; 503 — распознавание недоступно |
| GET | `/api/voice/tts` | `?text=&token=&sid=&voice=&plain=` → `audio/wav` |

## Внутренние речевые сервисы

Доступны только внутри сети Compose; приложение обращается к ним по `ASR_URL`, `VOICE_URL`, `F5_URL`.

| Сервис | Метод | Путь | Описание |
|---|---|---|---|
| `asr:8091` (GigaAM) | GET | `/health` | модель, устройство, GPU |
| | POST | `/asr` | аудио → `{text, words, latency_ms, engine}` |
| `voice:8090` (Vosk + Piper) | GET | `/health` | `{asr, tts, tts_engine}` |
| | POST | `/asr` | аудио → `{text}` |
| | GET | `/tts` | `?text=` → `audio/wav` (F5, при отказе Piper) |
| | GET / POST | `/voices`, `GET /voices/{id}/audio` | голоса |
| | GET | `/sounds` | звуковые сцены |
| | POST | `/classify` | классификация звуковой сцены |
| `tts:8092` (F5-TTS) | GET | `/health`, `/tts`, `/voices`, `/voices/{id}/audio`, `/sounds` | тот же контракт синтеза |
| | POST | `/voices`, `/classify` | |

Языковая модель подключается по OpenAI-совместимому протоколу (`LLM_BASE_URL`, `/chat/completions`);
имя модели в `LLM_MODEL` должно совпадать с `--served-model-name` у vLLM.
