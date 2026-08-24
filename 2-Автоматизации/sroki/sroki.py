#!/usr/bin/env python3
"""Считает сроки по карточкам писем и собирает сводку «что горит».

Запуск:
    python3 sroki.py                 — пересобрать 1-Работа/СВОДКА.md
    python3 sroki.py --telegram      — то же плюс отправить в телеграм
    python3 sroki.py --tihiy         — молчать, если гореть нечему (для расписания)

Зависимостей нет, нужен только Python 3.9+.
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

KOREN = Path(__file__).resolve().parents[2]
PAPKA_ORGANIZACIY = KOREN / "1-Работа" / "Организации"
SVODKA = KOREN / "1-Работа" / "СВОДКА.md"

SKORO_DNEY = 3  # за сколько дней до срока начинаем предупреждать

DATA_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def chitat_shapku(put: Path) -> dict:
    """Достаёт поля из шапки карточки. Пустая шапка — пустой словарь."""
    try:
        tekst = put.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    if not tekst.startswith("---"):
        return {}
    konec = tekst.find("\n---", 3)
    if konec == -1:
        return {}
    polya = {}
    for stroka in tekst[3:konec].splitlines():
        if not stroka.strip() or stroka.lstrip().startswith("#"):
            continue
        if ":" not in stroka:
            continue
        klyuch, _, znachenie = stroka.partition(":")
        polya[klyuch.strip()] = znachenie.strip().strip('"').strip("'")
    return polya


def kak_data(znachenie: str):
    """Достаёт дату вида 2026-08-24. Всё остальное — None."""
    if not znachenie:
        return None
    sovpadenie = DATA_RE.search(znachenie)
    if not sovpadenie:
        return None
    try:
        return datetime.strptime(sovpadenie.group(), "%Y-%m-%d").date()
    except ValueError:
        return None


def kak_summa(znachenie: str):
    if not znachenie:
        return None
    cifry = re.sub(r"[^\d]", "", znachenie)
    return int(cifry) if cifry else None


def summa_propisyu(summa: int) -> str:
    return f"{summa:,}".replace(",", " ") + " ₽"


def sobrat_pisma() -> list:
    """Все карточки писем из всех организаций."""
    pisma = []
    if not PAPKA_ORGANIZACIY.exists():
        return pisma
    for put in sorted(PAPKA_ORGANIZACIY.rglob("Переписка/*.md")):
        if put.name.startswith("_"):
            continue
        if "Архив" in put.parts:  # закрытые дела прошлых лет не мешают текущей работе
            continue
        polya = chitat_shapku(put)
        if not polya:
            continue
        polya["_put"] = put
        polya["_organizaciya"] = polya.get("организация") or put.parents[1].name
        pisma.append(polya)
    return pisma


POLYA_DAT = ("получено", "срок_ответа", "ответ_отправлен", "срок_оплаты", "оплачено", "ждём_ответа_до")


def proverit(pisma: list, segodnya: date) -> dict:
    """Раскладывает письма по трём корзинам просрочек плюс список кривых карточек."""
    nashi_otvety, nashi_oplaty, ih_molchanie, brak = [], [], [], []

    for pismo in pisma:
        # дата заполнена, но записана не как 2026-08-24 — срок потеряется молча
        krivye = [
            p for p in POLYA_DAT
            if (pismo.get(p) or "").strip() and kak_data(pismo.get(p, "")) is None
        ]
        if krivye:
            brak.append({
                "организация": pismo["_organizaciya"],
                "тема": pismo.get("тема") or pismo["_put"].stem,
                "поля": krivye,
                "файл": str(pismo["_put"].relative_to(KOREN)),
            })

        if (pismo.get("статус") or "").strip().lower() == "закрыто":
            continue

        obshee = {
            "организация": pismo["_organizaciya"],
            "тема": pismo.get("тема") or pismo["_put"].stem,
            "от_кого": pismo.get("от_кого", ""),
            "файл": str(pismo["_put"].relative_to(KOREN)),
        }

        srok_otveta = kak_data(pismo.get("срок_ответа", ""))
        if srok_otveta and not kak_data(pismo.get("ответ_отправлен", "")):
            nashi_otvety.append({**obshee, "срок": srok_otveta, "дней": (srok_otveta - segodnya).days})

        srok_oplaty = kak_data(pismo.get("срок_оплаты", ""))
        if srok_oplaty and not kak_data(pismo.get("оплачено", "")):
            nashi_oplaty.append({
                **obshee,
                "срок": srok_oplaty,
                "дней": (srok_oplaty - segodnya).days,
                "сумма": kak_summa(pismo.get("сумма", "")),
            })

        zhdyom = kak_data(pismo.get("ждём_ответа_до", ""))
        if zhdyom and (zhdyom - segodnya).days < 0:
            ih_molchanie.append({**obshee, "срок": zhdyom, "дней": (zhdyom - segodnya).days})

    for korzina in (nashi_otvety, nashi_oplaty, ih_molchanie):
        korzina.sort(key=lambda z: z["дней"])

    return {
        "ответы": nashi_otvety,
        "оплаты": nashi_oplaty,
        "молчание": ih_molchanie,
        "брак": brak,
    }


def gorit(zapis: dict) -> bool:
    return zapis["дней"] <= SKORO_DNEY


def znachok(dney: int) -> str:
    if dney < 0:
        return "🔴"
    if dney <= SKORO_DNEY:
        return "🟡"
    return "🟢"


def slovami(dney: int) -> str:
    if dney < 0:
        return f"просрочено на {abs(dney)} дн."
    if dney == 0:
        return "сегодня последний день"
    return f"осталось {dney} дн."


def sobrat_svodku(itogi: dict, segodnya: date, vsego_pisem: int) -> str:
    strok = [
        "<!-- Файл собирается скриптом 2-Автоматизации/sroki. Руками не править. -->",
        "",
        "# Что горит по срокам",
        "",
        f"Собрано {segodnya.strftime('%d.%m.%Y')}. Всего писем в работе: {vsego_pisem}.",
        "",
    ]

    razdely = [
        ("Мы должны ответить", itogi["ответы"], "Не ответим в срок — разговор уходит в суд."),
        ("Мы должны заплатить", itogi["оплаты"], "Пропустим срок — пойдут пени."),
        ("Нам не ответили", itogi["молчание"], "Их срок вышел. Есть повод давить и ссылаться на бездействие."),
    ]

    goryachih = 0
    for zagolovok, zapisi, poyasnenie in razdely:
        strok += [f"## {zagolovok}", "", poyasnenie, ""]
        if not zapisi:
            strok += ["Пусто.", ""]
            continue
        est_summa = any(z.get("сумма") for z in zapisi)
        shapka = "| | Организация | Что | Срок | Состояние |"
        razdelitel = "|---|---|---|---|---|"
        if est_summa:
            shapka = "| | Организация | Что | Сумма | Срок | Состояние |"
            razdelitel = "|---|---|---|---|---|---|"
        strok += [shapka, razdelitel]
        for z in zapisi:
            goryachih += 1 if gorit(z) else 0
            yacheyki = [znachok(z["дней"]), z["организация"], z["тема"]]
            if est_summa:
                yacheyki.append(summa_propisyu(z["сумма"]) if z.get("сумма") else "—")
            yacheyki += [z["срок"].strftime("%d.%m.%Y"), slovami(z["дней"])]
            strok.append("| " + " | ".join(yacheyki) + " |")
        strok.append("")

    if itogi["брак"]:
        strok += [
            "## Карточки, где сломаны даты",
            "",
            "Дата написана не так, как надо, — срок по ней **не считается**. "
            "Правильный вид: `2026-08-24`. Скажи помощнику «почини даты в карточках».",
            "",
            "| Организация | Что | Какие поля |",
            "|---|---|---|",
        ]
        for z in itogi["брак"]:
            strok.append(f"| {z['организация']} | {z['тема']} | {', '.join(z['поля'])} |")
        strok.append("")

    strok += [
        "---",
        "",
        "🔴 срок прошёл · 🟡 осталось три дня или меньше · 🟢 время есть",
        "",
        f"Требует внимания прямо сейчас: {goryachih}.",
    ]
    return "\n".join(strok) + "\n"


def sobrat_soobshenie(itogi: dict, segodnya: date) -> str:
    """Короткий текст для телеграма — только то, что горит."""
    chasti = []
    razdely = [
        ("Ответить", itogi["ответы"]),
        ("Заплатить", itogi["оплаты"]),
        ("Нам не ответили", itogi["молчание"]),
    ]
    for zagolovok, zapisi in razdely:
        goryachie = [z for z in zapisi if gorit(z)]
        if not goryachie:
            continue
        strok = [f"<b>{zagolovok}</b>"]
        for z in goryachie[:10]:
            summa = f" — {summa_propisyu(z['сумма'])}" if z.get("сумма") else ""
            strok.append(f"{znachok(z['дней'])} {z['организация']}: {z['тема']}{summa} ({slovami(z['дней'])})")
        if len(goryachie) > 10:
            strok.append(f"…и ещё {len(goryachie) - 10}")
        chasti.append("\n".join(strok))

    if itogi["брак"]:
        chasti.append(
            f"⚠️ <b>Сломаны даты в карточках: {len(itogi['брак'])}</b>\n"
            "По ним сроки не считаются. Скажи помощнику «почини даты в карточках»."
        )

    if not chasti:
        return f"✅ {segodnya.strftime('%d.%m.%Y')} — сроки чистые, гореть нечему."

    shapka = f"⏰ <b>Сроки на {segodnya.strftime('%d.%m.%Y')}</b>"
    return shapka + "\n\n" + "\n\n".join(chasti)


def chitat_env() -> dict:
    """Токен и адрес чата: сначала переменные окружения, потом файл .env рядом."""
    znacheniya = {}
    fayl = Path(__file__).resolve().parent / ".env"
    if fayl.exists():
        for stroka in fayl.read_text(encoding="utf-8").splitlines():
            stroka = stroka.strip()
            if not stroka or stroka.startswith("#") or "=" not in stroka:
                continue
            klyuch, _, znachenie = stroka.partition("=")
            znacheniya[klyuch.strip()] = znachenie.strip().strip('"').strip("'")
    for klyuch in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if os.environ.get(klyuch):
            znacheniya[klyuch] = os.environ[klyuch]
    return znacheniya


def otpravit_v_telegram(tekst: str) -> bool:
    nastroyki = chitat_env()
    token = nastroyki.get("TELEGRAM_BOT_TOKEN")
    chat = nastroyki.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Не отправил: нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID (см. .env.example)", file=sys.stderr)
        return False

    dannye = urllib.parse.urlencode({
        "chat_id": chat,
        "text": tekst,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    zapros = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=dannye
    )
    try:
        with urllib.request.urlopen(zapros, timeout=20) as otvet:
            return json.load(otvet).get("ok", False)
    except Exception as oshibka:  # сеть, неверный токен, бот не запущен
        print(f"Не отправил: {oshibka}", file=sys.stderr)
        return False


def main() -> int:
    razbor = argparse.ArgumentParser(description="Сводка по срокам переписки")
    razbor.add_argument("--telegram", action="store_true", help="отправить сводку в телеграм")
    razbor.add_argument("--tihiy", action="store_true", help="молчать в телеграм, если гореть нечему")
    razbor.add_argument("--data", help="считать на другую дату, вид 2026-08-24 (для проверки)")
    argumenty = razbor.parse_args()

    segodnya = kak_data(argumenty.data) if argumenty.data else date.today()
    if segodnya is None:
        print("Дата должна быть вида 2026-08-24", file=sys.stderr)
        return 2

    pisma = sobrat_pisma()
    v_rabote = [p for p in pisma if (p.get("статус") or "").strip().lower() != "закрыто"]
    itogi = proverit(pisma, segodnya)

    SVODKA.parent.mkdir(parents=True, exist_ok=True)
    SVODKA.write_text(sobrat_svodku(itogi, segodnya, len(v_rabote)), encoding="utf-8")
    print(f"Сводка собрана: {SVODKA.relative_to(KOREN)}")

    import karta  # ленивый импорт: karta берёт разбор карточек отсюда же
    karta.main()

    goryachih = sum(
        len([z for z in itogi[k] if gorit(z)])
        for k in ("ответы", "оплаты", "молчание")
    ) + len(itogi["брак"])
    print(f"Требует внимания: {goryachih}")

    if argumenty.telegram:
        if argumenty.tihiy and goryachih == 0:
            print("Тихий режим: гореть нечему, в телеграм не пишу")
            return 0
        if otpravit_v_telegram(sobrat_soobshenie(itogi, segodnya)):
            print("Отправлено в телеграм")
        else:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
