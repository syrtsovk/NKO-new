#!/usr/bin/env python3
"""Собирает оглавление библиотеки уставов из паспортов образцов.

Запуск:
    python3 2-Автоматизации/ustavy/indeks.py
"""

import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

KOREN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KOREN / "2-Автоматизации" / "sroki"))
from sroki import chitat_shapku, kak_data  # разбор шапок живёт в одном месте

BIBLIOTEKA = KOREN / "1-Работа" / "Регистрация" / "Образцы уставов"
INDEKS = BIBLIOTEKA / "ИНДЕКС.md"
RUBRIKATOR = BIBLIOTEKA / "_рубрикатор.md"


def nazvaniya_rubrik() -> dict:
    """Коды направлений и их человеческие названия — прямо из рубрикатора."""
    imena = {}
    if not RUBRIKATOR.exists():
        return imena
    for stroka in RUBRIKATOR.read_text(encoding="utf-8").splitlines():
        if not stroka.startswith("| `"):
            continue
        yacheyki = [y.strip() for y in stroka.strip("|").split("|")]
        if len(yacheyki) >= 2:
            imena[yacheyki[0].strip("`")] = yacheyki[1]
    return imena


def sobrat_pasporta() -> list:
    obraztsy = []
    if not BIBLIOTEKA.exists():
        return obraztsy
    for put in sorted(BIBLIOTEKA.rglob("паспорт.md")):
        shapka = chitat_shapku(put)
        if not shapka:
            continue
        shapka["_papka"] = put.parent
        shapka["_est_tekst"] = (put.parent / "текст.md").exists()
        obraztsy.append(shapka)
    return obraztsy


def god(shapka: dict) -> str:
    data = kak_data(shapka.get("зарегистрирован", ""))
    return str(data.year) if data else "?"


def sobrat_indeks(obraztsy: list, imena: dict) -> str:
    segodnya = date.today()
    strok = [
        "<!-- Файл собирается скриптом 2-Автоматизации/ustavy/indeks.py. Руками не править. -->",
        "",
        "# Все образцы уставов",
        "",
        f"Собрано {segodnya.strftime('%d.%m.%Y')}. Образцов: {len(obraztsy)}.",
        "",
        "> **Начинай отсюда.** Подбери образец по форме и направлению, прочитай его паспорт. "
        "Полный текст открывай только тогда, когда образец точно подходит — тексты большие.",
        "",
    ]

    if not obraztsy:
        strok += [
            "Пока пусто. Как добавить образец — в [README](README.md).",
            "",
        ]
        return "\n".join(strok)

    po_forme = defaultdict(list)
    for o in obraztsy:
        po_forme[o.get("форма", "Без формы")].append(o)

    strok += ["## Сколько чего есть", "", "| Форма | Образцов | Направления |", "|---|---|---|"]
    for forma in sorted(po_forme):
        napravleniya = sorted({o.get("направление", "?") for o in po_forme[forma]})
        chitaemye = ", ".join(imena.get(n, n) for n in napravleniya)
        strok.append(f"| {forma} | {len(po_forme[forma])} | {chitaemye} |")
    strok.append("")

    for forma in sorted(po_forme):
        strok += [
            f"## {forma}",
            "",
            "| Направление | Название | Территория | Год | Членство | Доходная | Паспорт |",
            "|---|---|---|---|---|---|---|",
        ]
        for o in sorted(po_forme[forma], key=lambda z: (z.get("направление", "я"), -int(god(z)) if god(z).isdigit() else 0)):
            papka = o["_papka"]
            ssylka = f"[открыть]({papka.relative_to(BIBLIOTEKA).as_posix()}/паспорт.md)"
            napravlenie = imena.get(o.get("направление", ""), o.get("направление", "?"))
            strok.append(
                f"| {napravlenie} | {o.get('название', '?')[:70]} | {o.get('территория', '?')} "
                f"| {god(o)} | {o.get('членство', '?')} | {o.get('доходная_деятельность', '?')} | {ssylka} |"
            )
        strok.append("")

    bez_teksta = [o for o in obraztsy if not o["_est_tekst"]]
    if bez_teksta:
        strok += [
            "## Образцы без текста",
            "",
            "У этих есть паспорт, но нет самого текста устава — по ним можно ориентироваться, "
            "но нельзя брать формулировки.",
            "",
        ]
        for o in bez_teksta:
            strok.append(f"- {o.get('название', '?')}")
        strok.append("")

    return "\n".join(strok) + "\n"


def main() -> int:
    obraztsy = sobrat_pasporta()
    INDEKS.parent.mkdir(parents=True, exist_ok=True)
    INDEKS.write_text(sobrat_indeks(obraztsy, nazvaniya_rubrik()), encoding="utf-8")
    print(f"Индекс уставов собран: образцов {len(obraztsy)}")
    print(f"  {INDEKS.relative_to(KOREN)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
