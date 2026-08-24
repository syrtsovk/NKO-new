#!/usr/bin/env python3
"""Собирает навигацию по реестру: общий индекс организаций и ленту по каждой.

Нужен, чтобы помощник не читал тысячи писем подряд. Он открывает индекс,
сужает до организации, читает её ленту — и только потом лезет в конкретные письма.

Запуск:
    python3 karta.py
"""

import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sroki import (  # переиспользуем разбор карточек, чтобы правила были в одном месте
    KOREN,
    PAPKA_ORGANIZACIY,
    chitat_shapku,
    kak_data,
    kak_summa,
    sobrat_pisma,
    summa_propisyu,
)

INDEKS = PAPKA_ORGANIZACIY / "ИНДЕКС.md"
BEZ_DELA = "Без дела"


def po_organizaciyam(pisma: list) -> dict:
    sgruppirovano = defaultdict(list)
    for pismo in pisma:
        sgruppirovano[pismo["_organizaciya"]].append(pismo)
    return sgruppirovano


def papka_organizacii(pisma_org: list) -> Path:
    return pisma_org[0]["_put"].parents[1]


def otkrytyh(pisma_org: list) -> int:
    return len([p for p in pisma_org if (p.get("статус") or "").strip().lower() != "закрыто"])


def poslednee_sobytie(pisma_org: list):
    daty = [kak_data(p.get("получено", "")) for p in pisma_org]
    daty = [d for d in daty if d]
    return max(daty) if daty else None


def prosrocheno(pisma_org: list, segodnya: date) -> int:
    schet = 0
    for p in pisma_org:
        if (p.get("статус") or "").strip().lower() == "закрыто":
            continue
        srok_otveta = kak_data(p.get("срок_ответа", ""))
        if srok_otveta and not kak_data(p.get("ответ_отправлен", "")) and srok_otveta < segodnya:
            schet += 1
            continue
        srok_oplaty = kak_data(p.get("срок_оплаты", ""))
        if srok_oplaty and not kak_data(p.get("оплачено", "")) and srok_oplaty < segodnya:
            schet += 1
    return schet


def dolg(pisma_org: list) -> int:
    itogo = 0
    for p in pisma_org:
        if kak_data(p.get("оплачено", "")):
            continue
        summa = kak_summa(p.get("сумма", ""))
        if summa and kak_data(p.get("срок_оплаты", "")):
            itogo += summa
    return itogo


def sobrat_lentu(nazvanie: str, pisma_org: list, segodnya: date) -> str:
    """Одна страница на организацию: вся переписка, сгруппированная по делам."""
    dela = defaultdict(list)
    for p in pisma_org:
        dela[(p.get("дело") or BEZ_DELA).strip() or BEZ_DELA].append(p)

    strok = [
        "<!-- Файл собирается скриптом 2-Автоматизации/sroki/karta.py. Руками не править. -->",
        "",
        f"# {nazvanie} — вся переписка",
        "",
        f"Собрано {segodnya.strftime('%d.%m.%Y')}. Писем: {len(pisma_org)}, из них открытых: {otkrytyh(pisma_org)}.",
        "",
        "> Это оглавление. Прочитай его целиком, найди нужное дело и открывай только его письма.",
        "",
    ]

    # сначала дела с открытыми письмами, внутри — свежее сверху
    poryadok = sorted(
        dela.items(),
        key=lambda para: (otkrytyh(para[1]) == 0, para[0] == BEZ_DELA, para[0]),
    )

    for imya_dela, pisma_dela in poryadok:
        otkryto = otkrytyh(pisma_dela)
        metka = f" — открыто: {otkryto}" if otkryto else " — закрыто"
        strok += [f"## {imya_dela}{metka}", "", "| Дата | Куда | Что | Состояние | Файл |", "|---|---|---|---|---|"]

        for p in sorted(pisma_dela, key=lambda z: kak_data(z.get("получено", "")) or date.min, reverse=True):
            data = kak_data(p.get("получено", ""))
            storona = "→ им" if (p.get("направление") or "").strip() == "исходящее" else "← нам"
            sostoyanie = sostoyanie_pisma(p, segodnya)
            imya_fayla = p["_put"].name
            strok.append(
                f"| {data.strftime('%d.%m.%Y') if data else '?'} "
                f"| {storona} | {p.get('тема') or p['_put'].stem} | {sostoyanie} "
                f"| [{imya_fayla}](Переписка/{imya_fayla}) |"
            )
        strok.append("")

    obshiy_dolg = dolg(pisma_org)
    if obshiy_dolg:
        strok += [f"**Неоплаченных требований на {summa_propisyu(obshiy_dolg)}.**", ""]

    return "\n".join(strok) + "\n"


def sostoyanie_pisma(pismo: dict, segodnya: date) -> str:
    if (pismo.get("статус") or "").strip().lower() == "закрыто":
        return "закрыто"
    metki = []
    srok_otveta = kak_data(pismo.get("срок_ответа", ""))
    if srok_otveta and not kak_data(pismo.get("ответ_отправлен", "")):
        dney = (srok_otveta - segodnya).days
        metki.append("🔴 ответ просрочен" if dney < 0 else f"ответить за {dney} дн.")
    srok_oplaty = kak_data(pismo.get("срок_оплаты", ""))
    if srok_oplaty and not kak_data(pismo.get("оплачено", "")):
        dney = (srok_oplaty - segodnya).days
        metki.append("🔴 оплата просрочена" if dney < 0 else f"оплатить за {dney} дн.")
    zhdyom = kak_data(pismo.get("ждём_ответа_до", ""))
    if zhdyom and (zhdyom - segodnya).days < 0:
        metki.append("🔴 они молчат")
    return " · ".join(metki) if metki else "в работе"


def sobrat_indeks(sgruppirovano: dict, segodnya: date) -> str:
    strok = [
        "<!-- Файл собирается скриптом 2-Автоматизации/sroki/karta.py. Руками не править. -->",
        "",
        "# Все организации",
        "",
        f"Собрано {segodnya.strftime('%d.%m.%Y')}. Организаций: {len(sgruppirovano)}.",
        "",
        "> **Начинай поиск отсюда.** Нашёл организацию — открывай её `ЛЕНТА.md`, "
        "там вся переписка по делам. Отдельные письма читай в последнюю очередь.",
        "",
        "| Организация | Куратор | Писем | Открыто | Просрочено | Долг | Последнее событие |",
        "|---|---|---|---|---|---|---|",
    ]

    for nazvanie in sorted(sgruppirovano):
        pisma_org = sgruppirovano[nazvanie]
        papka = papka_organizacii(pisma_org)
        kartochka = chitat_shapku(papka / "карточка.md")
        posl = poslednee_sobytie(pisma_org)
        prosr = prosrocheno(pisma_org, segodnya)
        dolg_org = dolg(pisma_org)
        ssylka = f"[{nazvanie}]({papka.name}/ЛЕНТА.md)"
        strok.append(
            f"| {ssylka} | {kartochka.get('куратор', '—') or '—'} | {len(pisma_org)} "
            f"| {otkrytyh(pisma_org)} | {('🔴 ' + str(prosr)) if prosr else '—'} "
            f"| {summa_propisyu(dolg_org) if dolg_org else '—'} "
            f"| {posl.strftime('%d.%m.%Y') if posl else '—'} |"
        )

    bez_pisem = []
    if PAPKA_ORGANIZACIY.exists():
        for papka in sorted(PAPKA_ORGANIZACIY.iterdir()):
            if not papka.is_dir() or papka.name.startswith("_"):
                continue
            kartochka = chitat_shapku(papka / "карточка.md")
            imya = kartochka.get("организация") or papka.name
            if imya not in sgruppirovano:
                bez_pisem.append(imya)
    if bez_pisem:
        strok += ["", "## Без переписки", "", ", ".join(bez_pisem), ""]

    return "\n".join(strok) + "\n"


def main() -> int:
    segodnya = date.today()
    pisma = sobrat_pisma()
    sgruppirovano = po_organizaciyam(pisma)

    for nazvanie, pisma_org in sgruppirovano.items():
        papka = papka_organizacii(pisma_org)
        (papka / "ЛЕНТА.md").write_text(sobrat_lentu(nazvanie, pisma_org, segodnya), encoding="utf-8")

    INDEKS.parent.mkdir(parents=True, exist_ok=True)
    INDEKS.write_text(sobrat_indeks(sgruppirovano, segodnya), encoding="utf-8")

    print(f"Карта собрана: организаций {len(sgruppirovano)}, писем {len(pisma)}")
    print(f"  {INDEKS.relative_to(KOREN)}")
    print("  ЛЕНТА.md в каждой организации")
    return 0


if __name__ == "__main__":
    sys.exit(main())
