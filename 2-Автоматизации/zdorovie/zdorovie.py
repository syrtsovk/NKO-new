#!/usr/bin/env python3
"""Проверка здоровья реестра: ловит то, что портится незаметно.

Данные в таких реестрах гниют тихо: разъезжаются названия дел, копятся дубли
организаций, поля называются с опечаткой и молча перестают работать. По одному
файлу этого не видно — видно только всё сразу.

Запуск:
    python3 zdorovie.py                — собрать 1-Работа/ЗДОРОВЬЕ.md
    python3 zdorovie.py --telegram     — то же плюс отправить в телеграм
    python3 zdorovie.py --tihiy        — молчать в телеграме, если всё чисто
"""

import argparse
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

KOREN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KOREN / "2-Автоматизации" / "sroki"))
from sroki import (
    PAPKA_ORGANIZACIY,
    chitat_shapku,
    kak_data,
    otpravit_v_telegram,
    sobrat_pisma,
)

OTCHET = KOREN / "1-Работа" / "ЗДОРОВЬЕ.md"
SVODKA = KOREN / "1-Работа" / "СВОДКА.md"

POLYA_PISMA = {
    "организация", "направление", "источник", "от_кого", "дело", "тема",
    "получено", "тип", "срок_ответа", "ответ_отправлен", "сумма",
    "срок_оплаты", "оплачено", "ждём_ответа_до", "статус",
}
POLYA_DAT = ("получено", "срок_ответа", "ответ_отправлен", "срок_оплаты", "оплачено", "ждём_ответа_до")
ZNACHENIYA = {
    "направление": {"входящее", "исходящее"},
    "источник": {"почта", "бумага", "кио", "суд", "лично"},
    "тип": {"претензия", "запрос", "иск", "уведомление", "ответ", "прочее"},
    "статус": {"в работе", "закрыто"},
}

POHOZHE = 0.82        # порог, за которым две строки считаем опечаткой одной и той же
DAVNO_DNEY = 400      # открытое письмо старше этого срока почти наверняка забыто
ZASTOY_DNEY = 7       # столько дней без пересборки сводки — сроки уже не считаются
TISHINA_DNEY = 14     # столько дней без сохранений — работу перестали отправлять


def pohozhi(a: str, b: str) -> bool:
    a, b = a.strip().lower().replace("ё", "е"), b.strip().lower().replace("ё", "е")
    if a == b:
        return False
    # «Ставка по Садовой» внутри «Льготная ставка по Садовой» — самый частый вид разъезда,
    # а по проценту совпадения такая пара не проходит: одна строка заметно длиннее.
    if len(a) >= 6 and len(b) >= 6 and (a in b or b in a):
        return True
    return SequenceMatcher(None, a, b).ratio() >= POHOZHE


def blizkie_pary(znacheniya: set) -> list:
    spisok = sorted(znacheniya)
    return [(a, b) for i, a in enumerate(spisok) for b in spisok[i + 1:] if pohozhi(a, b)]


def proverit_pisma(pisma: list, segodnya: date) -> dict:
    nahodki = defaultdict(list)

    for p in pisma:
        put = p["_put"].relative_to(KOREN)
        gde = f"{p['_organizaciya']} · {p.get('тема') or p['_put'].stem}"
        status = (p.get("статус") or "").strip().lower()

        chuzhie = set(p) - POLYA_PISMA - {"_put", "_organizaciya"}
        for pole in sorted(chuzhie):
            blizkoe = [z for z in POLYA_PISMA if pohozhi(pole, z)]
            podskazka = f" — похоже на «{blizkoe[0]}»" if blizkoe else ""
            nahodki["чужие поля"].append(f"{gde}: поле «{pole}»{podskazka} · `{put}`")

        for pole in POLYA_DAT:
            znachenie = (p.get(pole) or "").strip()
            if znachenie and kak_data(znachenie) is None:
                nahodki["сломанные даты"].append(f"{gde}: «{pole}» = `{znachenie}` · `{put}`")

        for pole, dopustimye in ZNACHENIYA.items():
            znachenie = (p.get(pole) or "").strip().lower()
            if znachenie and znachenie not in dopustimye:
                nahodki["непонятные значения"].append(
                    f"{gde}: «{pole}» = «{znachenie}», а бывает только {', '.join(sorted(dopustimye))} · `{put}`"
                )

        if not (p.get("дело") or "").strip():
            nahodki["письма без дела"].append(f"{gde} · `{put}`")

        if status != "закрыто":
            otslezhivaetsya = any(kak_data(p.get(pole, "")) for pole in ("срок_ответа", "срок_оплаты", "ждём_ответа_до"))
            if not otslezhivaetsya:
                nahodki["письма без сроков"].append(f"{gde} · `{put}`")

            otvet = kak_data(p.get("ответ_отправлен", ""))
            srok_oplaty = kak_data(p.get("срок_оплаты", ""))
            oplacheno = kak_data(p.get("оплачено", ""))
            zhdyom_ih = kak_data(p.get("ждём_ответа_до", ""))
            # у исходящего «ответ_отправлен» — это дата нашей отправки, а не закрытие вопроса:
            # пока ждём ответа второй стороны, письмо законно остаётся в работе
            ischerpano = otvet and (not srok_oplaty or oplacheno) and not zhdyom_ih
            if ischerpano:
                nahodki["забыли закрыть"].append(f"{gde}: ответ отправлен, а статус «в работе» · `{put}`")

            poluceno = kak_data(p.get("получено", ""))
            if poluceno and (segodnya - poluceno).days > DAVNO_DNEY:
                nahodki["висят больше года"].append(
                    f"{gde}: получено {poluceno.strftime('%d.%m.%Y')}, всё ещё в работе · `{put}`"
                )

    po_organizaciyam = defaultdict(set)
    for p in pisma:
        delo = (p.get("дело") or "").strip()
        if delo:
            po_organizaciyam[p["_organizaciya"]].add(delo)
    for organizaciya, dela in po_organizaciyam.items():
        for a, b in blizkie_pary(dela):
            nahodki["дела разъехались"].append(f"{organizaciya}: «{a}» и «{b}» — это одно дело или разные?")

    return nahodki


def proverit_organizacii(nahodki: dict) -> None:
    if not PAPKA_ORGANIZACIY.exists():
        return
    imena = {}
    for papka in sorted(PAPKA_ORGANIZACIY.iterdir()):
        if not papka.is_dir() or papka.name.startswith("_"):
            continue
        kartochka = papka / "карточка.md"
        if not kartochka.exists():
            nahodki["организации без карточки"].append(f"{papka.name}: нет файла карточка.md")
            continue
        shapka = chitat_shapku(kartochka)
        imya = (shapka.get("организация") or "").strip()
        if not imya:
            nahodki["организации без карточки"].append(f"{papka.name}: в карточке не заполнено название")
            continue
        imena[imya] = papka.name
        pusto = [pole for pole in ("инн", "куратор") if not (shapka.get(pole) or "").strip()]
        if pusto:
            nahodki["карточки не заполнены"].append(f"{imya}: не заполнено {', '.join(pusto)}")

        perepiska = papka / "Переписка"
        if perepiska.exists():
            skany = {f.stem for f in perepiska.iterdir() if f.suffix.lower() in {".pdf", ".jpg", ".jpeg", ".png"}}
            kartochki = {f.stem for f in perepiska.glob("*.md")}
            for sirota in sorted(skany - kartochki):
                nahodki["сканы без карточки"].append(f"{imya}: файл «{sirota}» лежит, а карточки на него нет")

    for a, b in blizkie_pary(set(imena)):
        nahodki["организации задвоились"].append(f"«{a}» и «{b}» — не одна ли это организация?")


def proverit_sistemu(nahodki: dict, segodnya: date) -> None:
    """Скрипты и ритуалы ломаются молча — это заметно только по датам."""
    if SVODKA.exists():
        sobrana = datetime.fromtimestamp(SVODKA.stat().st_mtime).date()
        prostoy = (segodnya - sobrana).days
        if prostoy > ZASTOY_DNEY:
            nahodki["система"].append(
                f"Сводку по срокам не пересобирали {prostoy} дн. Значит, просрочки сейчас никто не видит."
            )
    else:
        nahodki["система"].append("Сводки по срокам нет вообще — запусти `python3 2-Автоматизации/sroki/sroki.py`")

    try:
        vyvod = subprocess.run(
            ["git", "log", "-1", "--format=%cd", "--date=short"],
            cwd=KOREN, capture_output=True, text=True, timeout=10,
        )
        posledniy = kak_data(vyvod.stdout.strip())
        if posledniy:
            tishina = (segodnya - posledniy).days
            if tishina > TISHINA_DNEY:
                nahodki["система"].append(
                    f"Последнее сохранение было {tishina} дн. назад. Либо работы нет, "
                    "либо её перестали отправлять в общее место — второе хуже."
                )
    except Exception:
        pass


PODSKAZKI = {
    "сломанные даты": "Скажи помощнику: «почини даты в карточках». Дата должна быть вида 2026-08-24 — иначе срок не считается.",
    "чужие поля": "Скорее всего опечатка в названии поля. Такое поле не читается ничем, и срок по нему теряется.",
    "непонятные значения": "Приведи к одному из допустимых — иначе фильтры и отчёты будут врать.",
    "дела разъехались": "Одно дело названо по-разному. Скажи помощнику, какое название верное, — он сведёт.",
    "организации задвоились": "Похоже, одна организация заведена дважды. Проверь и объедини папки.",
    "письма без дела": "Без дела письмо выпадает из цепочки и читается в отрыве от истории спора.",
    "письма без сроков": "У письма не отслеживается ни один срок. Если срок в письме не указан — ставь 30 дней от получения.",
    "забыли закрыть": "Ответ отправлен, а письмо числится в работе. Переведи в «закрыто», чтобы не мешало.",
    "висят больше года": "Скорее всего забыли закрыть. Проверь и закрой или перенеси в Архив.",
    "сканы без карточки": "Скан есть, карточки нет — для поиска этого письма не существует. Заведи карточку.",
    "организации без карточки": "Без карточки организация выпадает из индекса.",
    "карточки не заполнены": "Не критично, но по пустому куратору непонятно, кто ведёт организацию.",
    "система": "Это про сами механизмы, а не про данные. Чинить в первую очередь.",
}
PORYADOK = [
    "система", "сломанные даты", "чужие поля", "непонятные значения",
    "сканы без карточки", "организации задвоились", "дела разъехались",
    "письма без сроков", "письма без дела", "забыли закрыть",
    "висят больше года", "организации без карточки", "карточки не заполнены",
]
SROCHNOE = {"система", "сломанные даты", "чужие поля", "сканы без карточки", "организации задвоились"}


def sobrat_otchet(nahodki: dict, segodnya: date, vsego_pisem: int) -> str:
    najdeno = sum(len(v) for v in nahodki.values())
    srochnyh = sum(len(nahodki.get(k, [])) for k in SROCHNOE)

    strok = [
        "<!-- Файл собирается скриптом 2-Автоматизации/zdorovie. Руками не править. -->",
        "",
        "# Здоровье реестра",
        "",
        f"Проверено {segodnya.strftime('%d.%m.%Y')}. Писем в реестре: {vsego_pisem}.",
        "",
    ]

    if not najdeno:
        strok += ["Всё чисто. Ничего чинить не нужно.", ""]
        return "\n".join(strok)

    strok += [
        f"Нашлось замечаний: **{najdeno}**, из них требуют внимания сейчас: **{srochnyh}**.",
        "",
        "> Это не аврал. Так реестры и живут: мелочи копятся, пока их не видно все разом. "
        "Чинить лучше сверху вниз — верхние ломают работу молча.",
        "",
    ]

    for klyuch in PORYADOK:
        zapisi = nahodki.get(klyuch)
        if not zapisi:
            continue
        znachok = "🔴" if klyuch in SROCHNOE else "🟡"
        strok += [f"## {znachok} {klyuch.capitalize()} — {len(zapisi)}", "", PODSKAZKI.get(klyuch, ""), ""]
        for zapis in zapisi[:25]:
            strok.append(f"- {zapis}")
        if len(zapisi) > 25:
            strok.append(f"- …и ещё {len(zapisi) - 25}")
        strok.append("")

    return "\n".join(strok) + "\n"


def sobrat_soobshenie(nahodki: dict, segodnya: date) -> str:
    srochnyh = sum(len(nahodki.get(k, [])) for k in SROCHNOE)
    vsego = sum(len(v) for v in nahodki.values())
    if not vsego:
        return f"✅ {segodnya.strftime('%d.%m.%Y')} — реестр в порядке, чинить нечего."

    strok = [f"🩺 <b>Здоровье реестра на {segodnya.strftime('%d.%m.%Y')}</b>", ""]
    if srochnyh:
        strok.append(f"🔴 Требует внимания: {srochnyh}")
    strok.append(f"Всего замечаний: {vsego}")
    strok.append("")
    for klyuch in PORYADOK:
        if nahodki.get(klyuch):
            znachok = "🔴" if klyuch in SROCHNOE else "🟡"
            strok.append(f"{znachok} {klyuch} — {len(nahodki[klyuch])}")
    strok += ["", "Подробности и что делать — в файле 1-Работа/ЗДОРОВЬЕ.md"]
    return "\n".join(strok)


def main() -> int:
    razbor = argparse.ArgumentParser(description="Проверка здоровья реестра")
    razbor.add_argument("--telegram", action="store_true", help="отправить итог в телеграм")
    razbor.add_argument("--tihiy", action="store_true", help="молчать в телеграме, если всё чисто")
    razbor.add_argument("--data", help="считать на другую дату, вид 2026-08-24")
    argumenty = razbor.parse_args()

    segodnya = kak_data(argumenty.data) if argumenty.data else date.today()
    if segodnya is None:
        print("Дата должна быть вида 2026-08-24", file=sys.stderr)
        return 2

    pisma = sobrat_pisma()
    nahodki = proverit_pisma(pisma, segodnya)
    proverit_organizacii(nahodki)
    proverit_sistemu(nahodki, segodnya)

    OTCHET.parent.mkdir(parents=True, exist_ok=True)
    OTCHET.write_text(sobrat_otchet(nahodki, segodnya, len(pisma)), encoding="utf-8")

    vsego = sum(len(v) for v in nahodki.values())
    srochnyh = sum(len(nahodki.get(k, [])) for k in SROCHNOE)
    print(f"Проверено писем: {len(pisma)}. Замечаний: {vsego}, срочных: {srochnyh}")
    print(f"  {OTCHET.relative_to(KOREN)}")

    if argumenty.telegram:
        if argumenty.tihiy and vsego == 0:
            print("Тихий режим: всё чисто, в телеграм не пишу")
            return 0
        if not otpravit_v_telegram(sobrat_soobshenie(nahodki, segodnya)):
            return 1
        print("Отправлено в телеграм")
    return 0


if __name__ == "__main__":
    sys.exit(main())
