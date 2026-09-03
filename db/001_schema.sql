-- =====================================================================
--  ИИ-тренажёр оператора Системы-112 — схема БД
--
--  ГЛАВНОЕ АРХИТЕКТУРНОЕ РЕШЕНИЕ:
--  всё пишется в одну append-only таблицу session_events.
--  Метрики не хранятся — они выводятся из событий.
--
--  Почему так: требование «смотреть и на макро, и на микро уровне»
--  выполнимо только если микро-данные не теряются. Если сразу
--  агрегировать, разобрать конкретный звонок будет уже нечем.
--  Один звонок = лента событий. Когорта = та же лента с GROUP BY.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------------------------------------------------------------------
--  Справочники и люди
-- ---------------------------------------------------------------------

CREATE TABLE org_group (            -- учебная группа
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        text NOT NULL,
    started_at  date,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app_user (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    full_name   text NOT NULL,
    role        text NOT NULL CHECK (role IN ('trainee', 'instructor', 'admin')),
    group_id    uuid REFERENCES org_group(id),
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Классификаторы из ПП РФ 1931. Наполняются из документации заказчика.
-- Структура задана нормативом, содержание — заказчиком: не выдумывать значения.
CREATE TABLE classifier (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    kind        text NOT NULL,      -- 'tip_proisshestviya', 'povod_smp', 'yazyk_obshcheniya', ...
    code        text NOT NULL,
    label       text NOT NULL,
    parent_code text,
    UNIQUE (kind, code)
);

-- ---------------------------------------------------------------------
--  Сценарии
-- ---------------------------------------------------------------------

CREATE TABLE scenario (
    id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    slug          text UNIQUE NOT NULL,
    title         text NOT NULL,
    difficulty    smallint NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
    services      text[] NOT NULL,          -- {'01','03'}
    incident_type text,                     -- код из classifier(tip_proisshestviya)
    body          jsonb NOT NULL,           -- facts, checklist, timing, forbidden, injections
    version       int NOT NULL DEFAULT 1,
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- Пороговые значения вынесены из кода. Демо-значения — декомпозиция
-- норматива ПП 1931 (75 секунд на опрос и заполнение обязательных полей).
CREATE TABLE threshold_profile (
    id         uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    name       text NOT NULL,
    is_default boolean NOT NULL DEFAULT false,
    values     jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO threshold_profile (name, is_default, values) VALUES
('Норматив ПП 1931', true, '{
  "time_to_ask_address_sec": 25,
  "time_to_ask_victims_sec": 45,
  "time_to_dispatch_sec": 75,
  "answer_wait_sec": 20,
  "callback_initiate_sec": 10,
  "callback_attempts_min": 3,
  "pause_penalty_sec": 2.0,
  "speech_rate_min_wpm": 90,
  "speech_rate_max_wpm": 190,
  "intent_confidence_min": 0.7,
  "weights": {"protocol": 0.60, "speed": 0.25, "composure": 0.15}
}'::jsonb);

-- ---------------------------------------------------------------------
--  Сессия = один учебный вызов
-- ---------------------------------------------------------------------

CREATE TABLE session (
    id             uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    trainee_id     uuid NOT NULL REFERENCES app_user(id),
    instructor_id  uuid REFERENCES app_user(id),
    scenario_id    uuid NOT NULL REFERENCES scenario(id),
    threshold_id   uuid NOT NULL REFERENCES threshold_profile(id),
    mode           text NOT NULL DEFAULT 'training'
                   CHECK (mode IN ('training', 'exam', 'demo')),
    attempt_no     int NOT NULL DEFAULT 1,   -- какая попытка этого сценария у обучаемого
    started_at     timestamptz NOT NULL DEFAULT now(),
    ended_at       timestamptz,
    audio_path     text,
    UNIQUE (trainee_id, scenario_id, attempt_no)
);

-- ---------------------------------------------------------------------
--  ЯДРО: лента событий
-- ---------------------------------------------------------------------

CREATE TYPE event_kind AS ENUM (
    'call_start',
    'operator_utterance',    -- payload: text, words[], wpm, fillers[], emotion_probs
    'caller_utterance',      -- payload: text, node_id, emotion, source (cache|live)
    'intent_detected',       -- payload: intent, confidence, method (rules|llm), overridden
    'fact_revealed',         -- payload: fact_id, field, weight
    'card_field_filled',     -- payload: field, value, is_correct
    'service_selected',      -- payload: service, is_correct
    'card_dispatched',
    'injection_applied',     -- payload: injection, by_user_id
    'pause_detected',        -- payload: duration_sec, is_operator_turn
    'forbidden_phrase',      -- payload: pattern, matched_text, penalty
    'leak_blocked',          -- payload: entity, action  ← перехваченная галлюцинация LLM
    'line_dropped',
    'callback_initiated',    -- payload: attempt_no, delay_sec
    'call_end'               -- payload: ended_by
);

CREATE TABLE session_event (
    id         bigserial PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    -- смещение от начала вызова в миллисекундах.
    -- Именно оно, а не wall clock: таймлайны разных звонков должны накладываться.
    t_ms       int NOT NULL,
    kind       event_kind NOT NULL,
    payload    jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX ON session_event (session_id, t_ms);
CREATE INDEX ON session_event (kind);
CREATE INDEX ON session_event USING gin (payload);

-- ---------------------------------------------------------------------
--  Оценка. Хранится отдельно, потому что пересчитывается
--  при смене профиля порогов — а события неизменны.
-- ---------------------------------------------------------------------

CREATE TABLE session_score (
    session_id      uuid PRIMARY KEY REFERENCES session(id) ON DELETE CASCADE,
    threshold_id    uuid NOT NULL REFERENCES threshold_profile(id),
    protocol_score  numeric(5,2) NOT NULL,
    speed_score     numeric(5,2) NOT NULL,
    composure_score numeric(5,2) NOT NULL,
    total_score     numeric(5,2) NOT NULL,
    stop_factors    text[] NOT NULL DEFAULT '{}',
    passed          boolean NOT NULL,
    breakdown       jsonb NOT NULL,     -- пункт чек-листа -> баллы, для разбора
    computed_at     timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
--  ПРОИЗВОДНЫЕ ПРЕДСТАВЛЕНИЯ
--  Всё ниже выводится из session_event. Ничего не дублируется.
-- ---------------------------------------------------------------------

-- Ключевые моменты вызова: когда оператор ЗАДАЛ вопрос.
-- Важно: оцениваем момент вопроса, а не момент получения ответа —
-- иначе оператор наказывается за панику заявителя.
CREATE VIEW v_session_timing AS
SELECT
    s.id AS session_id,
    MIN(e.t_ms) FILTER (WHERE e.kind = 'intent_detected'
        AND e.payload->>'intent' = 'asked_address')      AS ask_address_ms,
    MIN(e.t_ms) FILTER (WHERE e.kind = 'intent_detected'
        AND e.payload->>'intent' = 'asked_victims')      AS ask_victims_ms,
    MIN(e.t_ms) FILTER (WHERE e.kind = 'intent_detected'
        AND e.payload->>'intent' = 'asked_hazards')      AS ask_hazards_ms,
    MIN(e.t_ms) FILTER (WHERE e.kind = 'card_dispatched') AS dispatch_ms,
    MAX(e.t_ms) FILTER (WHERE e.kind = 'call_end')        AS call_end_ms,
    -- разрыв «спросил -> получил»: показывает сопротивление заявителя,
    -- показывается преподавателю, но НЕ штрафуется
    MIN(e.t_ms) FILTER (WHERE e.kind = 'fact_revealed'
        AND e.payload->>'fact_id' = 'f_street')          AS got_address_ms
FROM session s
LEFT JOIN session_event e ON e.session_id = s.id
GROUP BY s.id;

-- Информационная эффективность: фактов получено на реплику оператора.
-- Ловит и того, кто спрашивает тремя предложениями, и того, кто путает порядок.
CREATE VIEW v_session_efficiency AS
SELECT
    session_id,
    COUNT(*) FILTER (WHERE kind = 'fact_revealed')                      AS facts_revealed,
    COUNT(*) FILTER (WHERE kind = 'operator_utterance')                 AS operator_turns,
    ROUND(
        COUNT(*) FILTER (WHERE kind = 'fact_revealed')::numeric
        / NULLIF(COUNT(*) FILTER (WHERE kind = 'operator_utterance'), 0),
    3)                                                                  AS info_efficiency,
    COUNT(*) FILTER (WHERE kind = 'pause_detected'
        AND (payload->>'is_operator_turn')::boolean)                    AS operator_pauses,
    COUNT(*) FILTER (WHERE kind = 'forbidden_phrase')                   AS forbidden_count,
    COUNT(*) FILTER (WHERE kind = 'injection_applied')                  AS injections_count,
    COUNT(*) FILTER (WHERE kind = 'leak_blocked')                       AS leaks_blocked
FROM session_event
GROUP BY session_id;

-- Тепловая карта: кто какой пункт чек-листа систематически пропускает.
-- Самое полезное представление для преподавателя.
CREATE VIEW v_checklist_coverage AS
SELECT
    s.trainee_id,
    u.full_name,
    u.group_id,
    sc.slug            AS scenario,
    i.intent,
    COUNT(*)                                            AS attempts,
    COUNT(*) FILTER (WHERE i.hit)                       AS hits,
    ROUND(100.0 * COUNT(*) FILTER (WHERE i.hit) / COUNT(*), 1) AS hit_rate
FROM session s
JOIN app_user u  ON u.id = s.trainee_id
JOIN scenario sc ON sc.id = s.scenario_id
CROSS JOIN LATERAL (
    SELECT
        c->>'id' AS intent,
        EXISTS (
            SELECT 1 FROM session_event e
            WHERE e.session_id = s.id
              AND e.kind = 'intent_detected'
              AND e.payload->>'intent' = c->>'id'
        ) AS hit
    FROM jsonb_array_elements(sc.body->'checklist') AS c
) i
GROUP BY s.trainee_id, u.full_name, u.group_id, sc.slug, i.intent;

-- Кривая обучения: как меняется балл от попытки к попытке.
CREATE VIEW v_learning_curve AS
SELECT
    s.trainee_id,
    u.full_name,
    s.attempt_no,
    AVG(sco.total_score)  AS avg_score,
    AVG(t.dispatch_ms / 1000.0) AS avg_dispatch_sec
FROM session s
JOIN app_user u          ON u.id = s.trainee_id
JOIN session_score sco   ON sco.session_id = s.id
JOIN v_session_timing t  ON t.session_id = s.id
GROUP BY s.trainee_id, u.full_name, s.attempt_no;

-- Соблюдение норматива 75 секунд — главный макро-показатель.
CREATE VIEW v_norm_compliance AS
SELECT
    s.id AS session_id,
    s.trainee_id,
    u.group_id,
    s.scenario_id,
    s.started_at,
    t.dispatch_ms / 1000.0                       AS dispatch_sec,
    (t.dispatch_ms <= 75000)                     AS within_norm,
    sco.total_score,
    sco.passed,
    cardinality(sco.stop_factors) > 0            AS has_stop_factor
FROM session s
JOIN app_user u         ON u.id = s.trainee_id
JOIN v_session_timing t ON t.session_id = s.id
LEFT JOIN session_score sco ON sco.session_id = s.id
WHERE t.dispatch_ms IS NOT NULL;
