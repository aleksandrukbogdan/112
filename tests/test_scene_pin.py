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


def test_smoke_is_quieter_than_open_fire():
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
    assert smoke.sound_track == fire.sound_track == "fire_inferno"
    assert smoke.sound_volume < 0.23 < fire.sound_volume
    assert smoke.voice == "male_baritone"


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
