-- Полная модель УЧОЛ + УКИО по ПП РФ от 12.11.2021 № 1931 (ред. 28.07.2025 № 1115).
--
-- Две сущности, а не одна:
--   обращение (УЧОЛ)  — сведения о заявителе и канале обращения
--   карточка (УКИО)   — сведения о происшествии
-- Связь: одно обращение -> одна карточка; одна карточка <- много обращений.

-- ---------------------------------------------------------------- типы

DO $$ BEGIN
    CREATE TYPE obrashchenie_kind AS ENUM ('call', 'sms', 'era_glonass', 'manual');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE card_state AS ENUM (
        'sozdana', 'naznachena', 'obrabatyvaetsya',
        'reagirovanie', 'zavershena', 'zakryta', 'otmenena'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE dds_state AS ENUM (
        'novaya', 'obrabatyvaetsya', 'reagirovanie', 'zavershena', 'otmenena'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    -- этапы реагирования бригады (у всех ДДС кроме 01)
    CREATE TYPE brigada_state AS ENUM (
        'uvedomlenie', 'vyezd', 'pribytie', 'likvidirovano', 'otmeneno'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------- УЧОЛ

CREATE TABLE IF NOT EXISTS obrashchenie (
    id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id    uuid REFERENCES session(id) ON DELETE CASCADE,
    card_id       uuid,                       -- FK ставим ниже, после создания card
    kind          obrashchenie_kind NOT NULL DEFAULT 'call',

    -- сведения об абонентском устройстве (в бою — автозаполнение от оператора связи)
    ab_telefon    text,
    ab_familiya   text,
    ab_imya       text,
    ab_otchestvo  text,
    ab_adres      text,
    shirota       double precision,
    dolgota       double precision,
    tochnost_m    double precision,

    -- сведения о заявителе (заполняет оператор)
    familiya      text,
    imya          text,
    otchestvo     text,
    telefon       text,
    yazyk         text DEFAULT 'русский',
    adres         text,

    -- опрос начинается отсюда (см. руководство АРМ: «опрос заявителя
    -- начинается с заполнения данного поля»)
    povod         text,
    utochnenie    text,

    -- служебное
    fias_ok       boolean,                    -- адрес подтверждён ФИАС
    lozhnyh_s_nomera int NOT NULL DEFAULT 0,  -- чёрный список
    sms_text      text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- УКИО, общая часть

CREATE TABLE IF NOT EXISTS card (
    id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id    uuid REFERENCES session(id) ON DELETE CASCADE,
    identifikator text,                       -- «авг26_ПОЖАР_003»
    state         card_state NOT NULL DEFAULT 'sozdana',

    -- служебное
    data_proish   date,
    vremya_proish time,

    -- место происшествия (ПП 1931, прил. 1)
    mesto_okato   text,
    ulica         text,
    dom           text,
    dom_drob      text,
    korpus        text,
    stroenie      text,
    vladenie      text,
    podezd        text,
    etazh         text,                       -- подвальные со знаком «-»
    kvartira      text,
    kod_domofona  text,
    doroga        text,
    kilometr      text,
    metr          text,
    adresny_uchastok text,
    ryadom        boolean NOT NULL DEFAULT false,
    obekt_okpo    text,
    mesto_utochnenie text,
    shirota       double precision,
    dolgota       double precision,

    -- сведения о происшествии
    tip_proisshestviya text,
    opisanie      text,
    chislo_postradavshih int,
    chislo_pogibshih     int,
    ugroza_lyudyam boolean NOT NULL DEFAULT false,
    priznak_chs   boolean NOT NULL DEFAULT false,
    uroven_chs    text,
    blokirovanie  boolean NOT NULL DEFAULT false,
    dop_informaciya text,

    -- ложный / злонамеренный
    lozhny_tip    text,                       -- NULL если не ложный

    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE obrashchenie
    DROP CONSTRAINT IF EXISTS obrashchenie_card_fk;
ALTER TABLE obrashchenie
    ADD CONSTRAINT obrashchenie_card_fk
    FOREIGN KEY (card_id) REFERENCES card(id) ON DELETE SET NULL;

-- ---------------------------------------------------------------- специальные части

-- Шесть служб. Поля — дословно из приложения № 2 к ПП 1931.
-- Держим в jsonb: состав полей задан нормативом и меняется постановлением,
-- а не миграцией; плюс списки (подозреваемые, больные) естественно вложенные.
CREATE TABLE IF NOT EXISTS card_special (
    id        uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    card_id   uuid NOT NULL REFERENCES card(id) ON DELETE CASCADE,
    sluzhba   text NOT NULL CHECK (sluzhba IN ('01','02','03','04','jkh','antiterror')),
    data      jsonb NOT NULL DEFAULT '{}'::jsonb,
    state     dds_state NOT NULL DEFAULT 'novaya',
    -- времена заполняет диспетчер ДДС (общая часть УКИО)
    vremya_prikaza  timestamptz,
    vremya_pribytiya timestamptz,
    vremya_okonchaniya timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (card_id, sluzhba)
);

CREATE TABLE IF NOT EXISTS brigada (
    id         uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    special_id uuid NOT NULL REFERENCES card_special(id) ON DELETE CASCADE,
    nazvanie   text NOT NULL,
    sostav     text,
    state      brigada_state NOT NULL DEFAULT 'uvedomlenie',
    vremya     timestamptz NOT NULL DEFAULT now(),
    primechanie text
);

-- ---------------------------------------------------------------- подсказки помощника

-- Журнал того, что система предложила и как оператор поступил.
-- Без этого невозможно ответить на вопрос «как ИИ влиял на решения» —
-- ровно та проблема, на которой погорел Seattle Fire Department.
CREATE TABLE IF NOT EXISTS hint_log (
    id         bigserial PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    t_ms       int NOT NULL,
    hint_id    text NOT NULL,
    kind       text NOT NULL,      -- question | field | timing | risk | draft
    payload    jsonb NOT NULL DEFAULT '{}'::jsonb,
    shown_at   timestamptz NOT NULL DEFAULT now(),
    reaction   text,               -- accepted | dismissed | ignored
    reacted_at timestamptz
);

CREATE INDEX IF NOT EXISTS i_obr_session  ON obrashchenie(session_id);
CREATE INDEX IF NOT EXISTS i_obr_card     ON obrashchenie(card_id);
CREATE INDEX IF NOT EXISTS i_card_session ON card(session_id);
CREATE INDEX IF NOT EXISTS i_spec_card    ON card_special(card_id);
CREATE INDEX IF NOT EXISTS i_hint_session ON hint_log(session_id);
