# -*- coding: utf-8 -*-
"""Голос и фон заявителя держатся на всех репликах одного билета."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tts.scene import classify


def test_fire_sticks_to_address_and_short_answer():
    sit = "Возгорание мусорного контейнера, пострадавших нет"
    fio = "Сидоров Иван Сергеевич"
    lines = [
        "Да, слышу. У нас здесь горит мусорный бак.",
        "Москва, Депо, около ст. Москва-Пассажирская Киевская. Длинное помещение недалеко от участкового пункта полиции.",
        "Мусорный контейнер.",
    ]
    plans = [classify(t, situaciya=sit, fio=fio, gruppa="1", turn=i) for i, t in enumerate(lines, 1)]
    assert {p.voice for p in plans} == {"male_baritone"}
    assert {p.sound_track for p in plans} == {"fire_inferno"}
    assert all(p.text.startswith("#пожар ") for p in plans)


def test_smoke_is_cough_not_campfire():
    smoke = classify(
        "На седьмом этаже.",
        situaciya="Задымление мусоропровода в жилом доме. Открытого пламени не видит",
        fio="Ким Олег Юрьевич", gruppa="1", turn=2,
    )
    fire = classify(
        "Горит бак.",
        situaciya="Возгорание мусорного контейнера",
        fio="Сидоров Иван", gruppa="1", turn=1,
    )
    distant = classify(
        "Вижу дым.",
        situaciya="Видит столб черного дыма со стороны жилых домов поселка, информации о пострадавших нет",
        fio="Левин Борис Степанович", gruppa="1", turn=1,
    )
    assert smoke.sound_track == "cough_smoke"
    assert smoke.text.startswith("#кашель ")
    assert smoke.voice == "male_baritone"
    assert fire.sound_track == "fire_inferno"
    assert distant.sound_track == "fire_inferno"
    assert distant.sound_volume < 0.23 < fire.sound_volume


def test_crash_impact_once_then_highway_same_voice():
    sit = "ДТП, лобовое столкновение"
    fio = "Иванов Петр"
    first = classify("На трассе удар.", situaciya=sit, fio=fio, gruppa="2", turn=1)
    later = classify("Километр 12.", situaciya=sit, fio=fio, gruppa="2", turn=2)
    assert first.sound_track == "car_crash"
    assert later.sound_track == "traffic_highway"
    assert first.voice == later.voice
    assert later.text.startswith("#дтп ")


def test_child_and_fight():
    child = classify(
        "Я на улице Карла Маркса.",
        situaciya="Ребенок 11 лет упал с велосипеда. Вызывает мама",
        fio="Смирнова", gruppa="18", turn=2,
    )
    assert child.voice == "female_panic"
    assert child.sound_track == "child_crying"
    assert child.text.startswith("#плач_ребенка ")
    fight = classify(
        "Леонтьевский переулок.",
        situaciya="Дерутся 10-15 человек палками и прутами",
        fio="Иванов Петр Иванович", gruppa="15", turn=2,
    )
    assert fight.sound_track is None
    assert fight.voice == "male_panic"
    assert "#паника" in fight.text


def test_elderly_door_is_not_a_fire():
    plan = classify(
        "Квартира 45.",
        situaciya="Открыть дверь в квартиру. В квартире проживает пожилой инвалид, одинокий, не открывает дверь 3-ий день",
        fio="Иванова Елена Сергеевна", gruppa="17", turn=1,
    )
    assert plan.scene == "door"
    assert plan.sound_track == "door_slam"
    assert plan.text.startswith("#дверь ")
    assert "#пожар" not in plan.text
    assert "#паника" not in plan.text


def test_false_fire_and_alarm_and_lights():
    elderly = classify(
        "На перекрестке.",
        situaciya="Пожилая женщина, неизвестная, около 70 лет, сильные головные боли",
        fio="Соколов Иван Петрович", gruppa="22", turn=1,
    )
    alarm = classify(
        "Сработала сигнализация.",
        situaciya="В жилом доме сработала пожарная сигнализация. Дыма и возгорания нет",
        fio="Сухов Леонид Сергеевич", gruppa="1", turn=2,
    )
    lights = classify(
        "На МКАД.",
        situaciya="На МКАД горит уличное освещение",
        fio="Зотова Алина Петровна", gruppa="14", turn=1,
    )
    assert elderly.sound_track is None
    assert alarm.sound_track == "smoke_detector"
    assert alarm.text.startswith("#сигнализация ")
    assert lights.sound_track is None


def test_river_on_water_not_on_dive_injury():
    drowning = classify(
        "Он кричит.",
        situaciya="Тонет человек в настоящее время. Мужчина, кричит о помощи",
        fio="Иванова Елена Сергеевна", gruppa="17", turn=2,
    )
    car = classify(
        "Машина в реке.",
        situaciya="Падение автомашины в воду",
        fio="Иванова Елена Сергеевна", gruppa="12", turn=1,
    )
    ice = classify(
        "На льдине.",
        situaciya="Трое мужчин плывут на льдине",
        fio="Иванова Елена Сергеевна", gruppa="17", turn=1,
    )
    bridge = classify(
        "С моста.",
        situaciya="Мужчина упал с моста в воду, кричит, что не умеет плавать",
        fio="Иванова Елена Сергеевна", gruppa="17", turn=1,
    )
    dive = classify(
        "На берегу.",
        situaciya="Мужчина 30 лет нырнул в воду, ударился о камень, рассек голову, кровотечение, в сознании",
        fio="Петрова Ирина Сергеевна", gruppa="22", turn=1,
    )
    for plan in (drowning, car, ice, bridge):
        assert plan.sound_track == "stream-river-water"
        assert plan.text.startswith("#река ")
    assert dive.sound_track is None


def test_theft_is_not_a_crash_and_city_hit_uses_siren():
    theft = classify(
        "Угнали машину.",
        situaciya="Угон машины, видели в последний раз вчера вечером",
        fio="Соколов Иван Петрович", gruppa="15", turn=1,
    )
    bomb = classify(
        "Под машиной коробка.",
        situaciya="Подозрительный автомобиль, под машиной лежит непонятная коробка с проводами",
        fio="Соколов Иван Петрович", gruppa="4", turn=1,
    )
    kidnap = classify(
        "У метро.",
        situaciya="Неизвестные затащили жену в машину у метро",
        fio="Иванов Илья Львович", gruppa="15", turn=1,
    )
    hit = classify(
        "На перекрестке.",
        situaciya="Наезд на пешехода, мужчина, без сознания",
        fio="Иванова Елена Сергеевна", gruppa="2", turn=1,
    )
    later = classify(
        "Он без сознания.",
        situaciya="Наезд на пешехода, мужчина, без сознания",
        fio="Иванова Елена Сергеевна", gruppa="2", turn=2,
    )
    train = classify(
        "На переходе.",
        situaciya="Мужчину сбила электричка, на ж/д переходе",
        fio="Иванова Елена Сергеевна", gruppa="12", turn=1,
    )
    assert theft.sound_track is None
    assert bomb.sound_track is None
    assert kidnap.sound_track is None
    assert hit.sound_track == "car_crash"
    assert later.sound_track == "siren_distant"
    assert train.sound_track == "siren_distant"
    assert later.text.startswith("#сирена ")


def test_cry_breath_and_locked_child():
    lost = classify(
        "Гуляли во дворе.",
        situaciya="Потерялся ребенок 30 минут назад — 5 лет, гулял с бабушкой",
        fio="Степанова Антонина Марковна, бабушка", gruppa="18", turn=1,
    )
    slide = classify(
        "Упал с горки.",
        situaciya="Касанов Константин Константинович, 8 лет, упал сам с горки, вызывает мама",
        fio="Касанова", gruppa="18", turn=1,
    )
    locked = classify(
        "Машина закрыта.",
        situaciya="Ребенок 4 года один в а/м, двери заблокировались",
        fio="Иванова Елена Сергеевна", gruppa="18", turn=1,
    )
    door = classify(
        "Она за дверью.",
        situaciya="Женщина просит о помощи из-за двери квартиры, дверь закрыта",
        fio="Иванова Елена Сергеевна", gruppa="17", turn=1,
    )
    wheeze = classify(
        "Муж хрипит.",
        situaciya="Не может разбудить мужа. Муж издает хрипы",
        fio="Иванова", gruppa="22", turn=1,
    )
    howl = classify(
        "В квартире.",
        situaciya="Соседи сообщили что в квартире 4 дня воет собака. Обнаружил труп",
        fio="Титов Игорь Романович", gruppa="19", turn=1,
    )
    assert lost.sound_track == "woman_crying"
    assert slide.sound_track == "child_crying"
    assert locked.sound_track == "child_crying"
    assert door.sound_track == "woman_crying"
    assert door.text.startswith("#плач_женщины ")
    assert wheeze.sound_track == "heavy_breathing"
    assert howl.sound_track == "dog_bark"
    assert howl.text.startswith("#собака ")


def test_brigade_stays_clean_and_override_keeps_bed():
    off = classify("Выехали, следуем к месту.")
    assert off.official and off.sound_track is None and off.voice == "male_baritone"
    pinned = classify(
        "Адрес такой.",
        situaciya="Возгорание мусорного контейнера",
        fio="Сидоров Иван", gruppa="1", turn=4, voice="female_warm",
    )
    assert pinned.voice == "female_warm"
    assert pinned.sound_track == "fire_inferno"
    assert pinned.text.startswith("#пожар ")
