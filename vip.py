"""Pullik rejim: yoqish/o'chirish, tariflar, kontent qulfi, VIP holati va PRO (dona sotiladigan) kontent.

• VIP (`paid` belgisi): VIP obunachilar ko'radi.
• PRO (`pro_price` > 0): kontent alohida sotiladi — faqat sotib olganlar (va adminlar) ko'radi; VIP obuna bunga yetmaydi.
  Serial/multfilm/anime uchun narx butun serialga (barcha qismlar) bitta.

• Rejim o'chiq bo'lsa hamma narsa tekin; yoqilsa «pullik» deb belgilanganlar darrov pullik bo'ladi.
• Yakka kino/multfilm/anime — o'zining `paid` belgisi. Qismli kontent — serialning `paid` belgisi;
  serialda «tekin qo'shish» yoqilgan bo'lsa dastlabki FREE_EPS qism tekin.
• Adminlar (staff) hamma narsani ko'ra oladi.
"""
import json
import time
from datetime import datetime, timedelta, timezone

from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup, WebAppInfo

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


async def pro_info(movie) -> dict | None:
    """PRO kontent bo'lsa: {t, id, price, card, title}; aks holda None (rejim o'chiq bo'lsa ham None)."""
    if not ON:
        return None
    if movie["series_id"]:
        ser = await db.get_series(movie["series_id"])
        if ser and ser["pro_price"] > 0:
            return {"t": "s", "id": ser["id"], "price": ser["pro_price"], "card": ser["pro_card"], "title": ser["title"]}
        return None
    if movie["pro_price"] > 0:
        return {"t": "m", "id": movie["id"], "price": movie["pro_price"], "card": movie["pro_card"], "title": movie["title"]}
    return None


async def pro_info_of(t: str, item_id: int) -> dict | None:
    """Katalog kartochkasi (t='m' kino / t='s' qismli) bo'yicha PRO ma'lumoti."""
    if not ON:
        return None
    row = await (db.get_series(item_id) if t == "s" else db.get_movie(item_id))
    if not row or row["pro_price"] <= 0:
        return None
    return {"t": t, "id": item_id, "price": row["pro_price"], "card": row["pro_card"], "title": row["title"]}


async def access(uid: int, movie) -> str:
    """'ok' — ko'ra oladi; 'pro' — sotib olish kerak; 'vip' — VIP kerak."""
    if perms.is_staff(uid):
        return "ok"
    info = await pro_info(movie)
    if info:
        return "ok" if await db.buy_has(uid, info["t"], info["id"]) else "pro"
    if await locked(movie):
        return "ok" if await is_vip(uid) else "vip"
    return "ok"


async def can_watch(uid: int, movie) -> bool:
    return await access(uid, movie) == "ok"


def pay_tag(r) -> str:
    """Katalog lentasi: 'pro' (alohida sotiladi) | 'vip' | 'free' | '' (rejim o'chiq bo'lsa lenta yo'q)."""
    if not ON:
        return ""
    keys = r.keys()
    if "pro_price" in keys and r["pro_price"]:
        return "pro"
    return "vip" if ("paid" in keys and r["paid"]) else "free"


# ───────────── Botdagi tugmalar ─────────────
def menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="👑 VIP olish", callback_data="vip:menu")]])


def app_button(text: str, **params) -> B | None:
    if not config.WEBAPP_URL:
        return None
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return B(text=text, web_app=WebAppInfo(url=config.WEBAPP_URL + ("&" if "?" in config.WEBAPP_URL else "?") + qs))


async def send_paywall(bot, chat_id: int, movie, acc: str = "vip") -> None:
    ep = f" — {movie['ep']}-qism" if movie["series_id"] and movie["ep"] else ""
    if acc == "pro":
        info = await pro_info(movie)
        if info:
            text = (f"💎 <b>{_esc(info['title'])}</b>{ep} — <b>PRO kontent</b>\n\n"
                    f"Bu kontent alohida sotiladi: <b>{money(info['price'])}</b>"
                    + (" (butun serial, barcha qismlar)" if info["t"] == "s" else "") + ".\n"
                    "Sotib olish ilova ichida: Click, Payme yoki karta orqali chek yuborib.")
            btn = app_button("💎 Sotib olish", buy=f"{info['t']}:{info['id']}")
            await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn]]) if btn else None)
            return
    text = (
        f"👑 <b>{_esc(movie['title'])}</b>{ep} — <b>pullik kontent</b>\n\n"
        "Buni faqat <b>VIP</b> obunachilar ko'ra oladi. VIP bilan barcha pullik kino va seriallar, "
        "teatrda o'z xonangizni ochish hamda chatda VIP belgisi ochiladi."
    )
    await bot.send_message(chat_id, text, reply_markup=menu_kb())


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
