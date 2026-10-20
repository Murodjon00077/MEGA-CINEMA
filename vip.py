"""Pullik (VIP) rejim: yoqish/o'chirish, tariflar, kontent qulfi, VIP holati.

• Rejim o'chiq bo'lsa hamma narsa tekin; yoqilsa «pullik» deb belgilanganlar darrov pullik bo'ladi.
• Yakka kino/multfilm/anime — o'zining `paid` belgisi. Qismli kontent — serialning `paid` belgisi;
  serialda «tekin qo'shish» yoqilgan bo'lsa dastlabki FREE_EPS qism tekin.
• Adminlar (staff) hamma narsani ko'ra oladi.
"""
import json
import time
from datetime import datetime, timedelta, timezone

from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup

import config
import db
import perms

FREE_EPS = 3
MAX_PLANS = 6
DEFAULT_PLANS = [{"days": 30, "price": 15000}, {"days": 90, "price": 39000}, {"days": 365, "price": 129000}]

ON = False  # Sozlamalardagi «Pullik rejim» tugmasi (start paytida bazadan o'qiladi)


async def load() -> None:
    global ON
    ON = (await db.get_setting("vip_on", "0")) == "1"


def set_on(v: bool) -> None:
    global ON
    ON = bool(v)


# ───────────── Tariflar va to'lov ma'lumotlari ─────────────
def clean_plans(items) -> list[dict]:
    out = []
    for it in (items or [])[:MAX_PLANS]:
        try:
            days, price = int(it.get("days")), int(it.get("price"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= days <= 3650 and 1000 <= price <= 100_000_000:
            out.append({"days": days, "price": price})
    return out


async def plans() -> list[dict]:
    raw = await db.get_setting("vip_plans", "")
    try:
        got = clean_plans(json.loads(raw)) if raw else []
    except ValueError:
        got = []
    return got or [dict(p) for p in DEFAULT_PLANS]


async def save_plans(items) -> list[dict]:
    got = clean_plans(items)
    if not got:
        raise ValueError("Kamida bitta to'g'ri tarif kiriting (kun va narx)")
    await db.set_setting("vip_plans", json.dumps(got))
    return got


async def card_text() -> str:
    return (await db.get_setting("vip_card", "")).strip()


def money(n: int) -> str:
    return f"{int(n):,}".replace(",", " ") + " so'm"


def days_text(d: int) -> str:
    return "1 yil" if d == 365 else f"{d} kun"


def until_text(ts: int) -> str:
    tz = timezone(timedelta(hours=config.THEATRE_TZ))
    return datetime.fromtimestamp(ts, tz).strftime("%d.%m.%Y")


def provider_tokens() -> dict[str, str]:
    return {k: v for k, v in (("click", config.PAYMENT_TOKEN_CLICK), ("payme", config.PAYMENT_TOKEN_PAYME)) if v}


# ───────────── VIP holati ─────────────
async def is_vip(uid: int) -> bool:
    return await db.vip_until(uid) > time.time()


async def vip_set(ids) -> set[int]:
    """Chat belgisi uchun: berilgan foydalanuvchilardan qaysilari hozir VIP (rejim o'chiq bo'lsa — hech kim)."""
    if not ON:
        return set()
    return await db.vip_users(list(set(ids)))


# ───────────── Kontent qulfi ─────────────
async def locked(movie) -> bool:
    """Kontent hozir pullikmi (rejim yoqilgan va belgilangan; serialda tekin qismlar hisobga olinadi)."""
    if not ON:
        return False
    if movie["series_id"]:
        ser = await db.get_series(movie["series_id"])
        if not ser or not ser["paid"]:
            return False
        return (movie["ep"] or 0) > (ser["free_eps"] or 0)
    return bool(movie["paid"])


async def can_watch(uid: int, movie) -> bool:
    if not await locked(movie):
        return True
    return perms.is_staff(uid) or await is_vip(uid)


def pay_tag(r) -> str:
    """Katalog lentasi: 'pro' | 'free' | '' (rejim o'chiq bo'lsa lenta yo'q)."""
    if not ON:
        return ""
    return "pro" if ("paid" in r.keys() and r["paid"]) else "free"


# ───────────── Botdagi tugmalar ─────────────
def menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="👑 VIP olish", callback_data="vip:menu")]])


async def send_paywall(bot, chat_id: int, movie) -> None:
    ep = f" — {movie['ep']}-qism" if movie["series_id"] and movie["ep"] else ""
    text = (
        f"👑 <b>{_esc(movie['title'])}</b>{ep} — <b>pullik kontent</b>\n\n"
        "Buni faqat <b>VIP</b> obunachilar ko'ra oladi. VIP bilan barcha pullik kino va seriallar, "
        "teatrda o'z xonangizni ochish hamda chatda VIP belgisi ochiladi."
    )
    await bot.send_message(chat_id, text, reply_markup=menu_kb())


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
