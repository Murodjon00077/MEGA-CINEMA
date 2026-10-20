"""Yangi chiqqan kontentni alohida Telegram kanallarga e'lon qilish.

Kino va serial — bitta kanalga, anime — boshqasiga, multfilm — yana boshqasiga.
Post: rasm + matn + «▶️ Tomosha qilish» tugmasi (botga o'tadi).
"""
import asyncio
import html
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup

import db
import services as svc

SLOTS = {"kino": "🎬 Kino va serial kanali", "anime": "🎌 Anime kanali", "multfilm": "🧸 Multfilm kanali"}


def key(slot: str) -> str:
    return f"promo_{slot}"


def group_of(kind: str) -> str:
    return {"anime": "anime", "multfilm": "multfilm"}.get(kind, "kino")  # serial ham «kino» kanaliga


async def chat_of(slot: str) -> int | None:
    v = await db.get_setting(key(slot), "")
    return int(v) if v.lstrip("-").isdigit() else None


def _ranges(nums: list[int]) -> str:
    nums = sorted(set(n for n in nums if n))
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}–{nums[j]}")
        i = j + 1
    return ", ".join(out)


def _start_link(bot_username: str, movie, series_id: int | None) -> str:
    """Oddiy kino — to'g'ridan-to'g'ri yuboriladi. Serial/anime/multfilm — botda «katalogga kiring» chiqadi
    (xuddi kod yozilgandagidek), keyin katalogda shu kontent ochiladi."""
    if movie["kind"] == "kino" and not series_id:
        return f"https://t.me/{bot_username}?start=m_{movie['id']}"
    code = db.series_code(movie["kind"], series_id) if series_id else db.movie_code(movie)
    return f"https://t.me/{bot_username}?start=c_{code.replace('.', '_')}"


def _text(movie, code: str, eps: list[int] | None) -> str:
    e = html.escape
    emoji = svc.KIND_EMOJI.get(movie["kind"], "🎬")
    name = svc.KIND_NAME.get(movie["kind"], "kontent")
    if eps:
        head = f"🆕 <b>Yangi qism{'lar' if len(set(eps)) > 1 else ''} qo'shildi!</b>"
        title = f"{emoji} <b>{e(movie['title'])}</b> — {_ranges(eps)}-qism"
    else:
        head = f"🆕 <b>Yangi {name} qo'shildi!</b>"
        title = f"{emoji} <b>{e(movie['title'])}</b>" + (f" ({e(movie['year'])})" if movie["year"] else "")
    lines = [head, "", title]
    if movie["genre"]:
        lines.append(f"🎭 {e(movie['genre'])}")
    if movie["country"]:
        lines.append(f"🌍 {e(movie['country'])}")
    if movie["language"]:
        lines.append(f"🗣 {e(movie['language'])}")
    lines.append(f"🔢 Kod: <code>{code}</code>")
    desc = (movie["description"] or "").strip()
    text = "\n".join(lines)
    if desc:
        desc = desc if len(desc) <= 250 else desc[:249].rstrip() + "…"
        text += f"\n\n📝 {e(desc)}"
    text += "\n\n👇 Tomosha qilish uchun tugmani bosing"
    return text[:1024]


async def post_published(bot: Bot, movie_ids: list[int]) -> None:
    """Chiqarilgan kontent haqida tegishli kanallarga post yuboradi (bittadan: kino yoki serial)."""
    movies = [m for m in [await db.get_movie(i) for i in movie_ids] if m]
    if not movies:
        return
    me = await bot.me()
    items: list[tuple[str, dict]] = []  # (slot, post)
    seen_series: dict[int, dict] = {}
    for m in movies:
        sid = m["series_id"]
        if sid:
            if sid in seen_series:
                seen_series[sid]["eps"].append(m["ep"] or 0)
                continue
            ser = await db.get_series(sid)
            post = {"movie": m, "series": ser, "eps": [m["ep"] or 0], "sid": sid}
            seen_series[sid] = post
        else:
            post = {"movie": m, "series": None, "eps": None, "sid": None}
        items.append((group_of(m["kind"]), post))

    sent: dict[str, int] = {}
    problems: list[str] = []
    for slot, p in items:
        chat = await chat_of(slot)
        if not chat:
            continue
        m, ser, sid = p["movie"], p["series"], p["sid"]
        code = db.series_code(m["kind"], sid) if sid else db.movie_code(m)
        photo = ""
        if ser is not None:
            photo = ser["cover_id"] or ser["poster_id"] or ""
        else:
            photo = m["cover_id"] or m["poster_id"] or ""
        text = _text(m, code, p["eps"])
        markup = InlineKeyboardMarkup(inline_keyboard=[[B(text="▶️ Tomosha qilish", url=_start_link(me.username, m, sid))]])
        try:
            try:
                if not photo:
                    raise ValueError
                await bot.send_photo(chat, photo, caption=text, reply_markup=markup)
            except Exception:
                await bot.send_message(chat, text, reply_markup=markup, disable_web_page_preview=True)
            sent[slot] = sent.get(slot, 0) + 1
        except Exception as e:
            logging.warning("Promo %s kanaliga yuborilmadi: %s", slot, e)
            problem = f"{SLOTS[slot]}: {str(e)[:100]}"
            if problem not in problems:
                problems.append(problem)
        await asyncio.sleep(3)  # kanalga: daqiqasiga ~20 ta limit

    if sent or problems:
        txt = "📢 <b>Kanallarga e'lon</b>\n\n" + "\n".join(f"{SLOTS[s]}: {n} ta post" for s, n in sent.items())
        if problems:
            txt += "\n\n⚠️ " + "\n⚠️ ".join(html.escape(x) for x in problems) + \
                   "\n(bot kanalda admin va «Xabar yuborish» huquqiga egami?)"
        await svc.notify_admins(bot, txt)
