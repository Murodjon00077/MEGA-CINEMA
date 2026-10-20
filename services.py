"""Bot va Mini App ikkalasi ishlatadigan umumiy mantiq: obuna tekshiruvi, kino yuborish."""
import asyncio
import html
import logging
import re

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import config
import db
import keyboards as kb
import perms
import vip

SUB_TEXT = (
    "📢 <b>Botdan foydalanish uchun homiy kanallarga obuna bo'ling!</b>\n\n"
    "Quyidagi kanallarga a'zo bo'ling, so'ng <b>«✅ Obunani tekshirish»</b> tugmasini bosing. "
    "Shundan keyin kino darhol yuboriladi. 🍿"
)


async def _is_member(bot: Bot, ch, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(ch["chat_id"], user_id)
    except Exception as e:  # bot kanalda admin emas / kanal o'chgan — foydalanuvchini to'smaymiz
        logging.warning("Obunani tekshirib bo'lmadi (%s): %s", ch["title"], e)
        return True
    if m.status in ("left", "kicked"):
        return False
    if m.status == "restricted" and not getattr(m, "is_member", True):
        return False
    return True


async def unsubscribed(bot: Bot, user_id: int) -> list:
    """Foydalanuvchi obuna bo'lmagan kanallar ro'yxati (bo'sh bo'lsa — hammasiga obuna)."""
    if await db.get_setting("sub_required", "1") != "1":
        return []
    channels = await db.list_channels()
    if not channels:
        return []
    ok = await asyncio.gather(*[_is_member(bot, ch, user_id) for ch in channels])
    return [ch for ch, good in zip(channels, ok) if not good]


async def ask_subscribe(bot: Bot, chat_id: int, channels: list, movie_id: int = 0) -> None:
    await bot.send_message(chat_id, SUB_TEXT, reply_markup=kb.sub_kb(channels, movie_id))


KIND_EMOJI = {"kino": "🎬", "multfilm": "🧸", "serial": "📺", "anime": "🎌"}
# Xabarlarda har bo'lim o'z nomi bilan ataladi (serial — «kino» emas)
KIND_NAME = {"kino": "kino", "multfilm": "multfilm", "serial": "serial", "anime": "anime"}
KIND_PLURAL = {"kino": "kinolar", "multfilm": "multfilmlar", "serial": "seriallar", "anime": "animelar"}


def code_of(movie) -> str:
    """Kino kodi: oddiy raqam; qismlilar uchun s.1 / m.1 / a.1."""
    if movie["series_id"]:
        return db.series_code(movie["kind"], movie["series_id"])
    return db.movie_code(movie)


def caption(movie, bot_username: str) -> str:
    e = html.escape
    ep = f" — {movie['ep']}-qism" if movie["series_id"] and movie["ep"] else ""
    head = f"{KIND_EMOJI.get(movie['kind'], '🎬')} <b>{e(movie['title'])}</b>{ep}" + (
        f" ({e(movie['year'])})" if movie["year"] else "")
    lines = [head, ""]
    if movie["genre"]:
        lines.append(f"🎭 <b>Janr:</b> {e(movie['genre'])}")
    if movie["country"]:
        lines.append(f"🌍 <b>Davlat:</b> {e(movie['country'])}")
    if movie["language"]:
        lines.append(f"🗣 <b>Til:</b> {e(movie['language'])}")
    if movie["quality"]:
        lines.append(f"📺 <b>Sifat:</b> {e(movie['quality'])}")
    lines.append(f"👁 <b>Ko'rishlar:</b> {movie['views']}")
    lines.append(f"🔢 <b>Kod:</b> <code>{code_of(movie)}</code>")
    footer = f"\n🤖 @{bot_username}"
    base = "\n".join(lines) + footer
    if movie["description"]:
        room = 1000 - len(base) - 10
        if room > 40:
            d = movie["description"].strip()
            d = d if len(d) <= room else d[: room - 1].rstrip() + "…"
            base = "\n".join(lines) + f"\n\n📝 {e(d)}\n" + footer
    return base[:1024]


async def send_movie(bot: Bot, chat_id: int, user_id: int, movie) -> None:
    me = await bot.me()
    protect = await db.get_setting("protect_content", "0") == "1"
    fav = await db.is_fav(user_id, movie["id"])
    markup = kb.movie_kb(movie["id"], fav, me.username, movie["title"], episode=bool(movie["series_id"]))
    cap = caption(movie, me.username)
    if movie["file_type"] == "video":
        await bot.send_video(chat_id, movie["file_id"], caption=cap, reply_markup=markup,
                             protect_content=protect, supports_streaming=True)
    else:
        await bot.send_document(chat_id, movie["file_id"], caption=cap, reply_markup=markup,
                                protect_content=protect)
    await db.add_view(user_id, movie["id"])


async def deliver(bot: Bot, chat_id: int, user_id: int, movie_id: int) -> str:
    """Kinoni yuboradi. Natija: 'ok' | 'sub' | 'vip' (pullik) | 'nf' (topilmadi) | 'err' (yuborib bo'lmadi)."""
    movie = await db.get_movie(movie_id)
    if not movie or (movie["status"] != "live" and not perms.is_staff(user_id)):
        return "nf"
    bad = await unsubscribed(bot, user_id)
    if bad:
        await ask_subscribe(bot, chat_id, bad, movie_id)
        return "sub"
    if not await vip.can_watch(user_id, movie):  # pullik kontent, foydalanuvchi VIP emas
        await vip.send_paywall(bot, chat_id, movie)
        return "vip"
    try:
        await send_movie(bot, chat_id, user_id, movie)
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logging.error("Kino #%s yuborilmadi: %s", movie_id, e)
        return "err"
    return "ok"


# ───────────── Adminlarga xabar / kanallarni aniqlash ─────────────
async def notify_admins(bot: Bot, text: str, reply_markup=None) -> None:
    for admin_id in perms.notify_ids():
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup, disable_web_page_preview=True)
        except Exception as e:
            logging.warning("Adminga xabar yuborilmadi (%s): %s", admin_id, e)


def parse_ref(text: str):
    """'@kanal', 't.me/kanal' yoki -100123... ni int | '@username' ga aylantiradi (aks holda None)."""
    t = (text or "").strip()
    t = re.sub(r"^(?:https?://)?t\.me/", "", t)
    t = t.split("?")[0].split("/")[0]
    if re.fullmatch(r"-?\d{5,}", t):
        return int(t)
    t = t.lstrip("@")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,}", t):
        return "@" + t
    return None


async def resolve_channel(bot: Bot, ref, need_link: bool = True, channel_only: bool = False):
    """Kanalni tekshiradi: bot admin bo'lishi shart. (chat_id, sarlavha, havola) qaytaradi.
    Muammo bo'lsa ValueError(o'zbekcha matn) ko'taradi."""
    try:
        chat = await bot.get_chat(ref)
        me = await bot.me()
        member = await bot.get_chat_member(chat.id, me.id)
    except (TelegramBadRequest, TelegramForbiddenError):
        raise ValueError("Kanal topilmadi yoki bot unda admin emas. Avval botni kanalga admin qiling.")
    if channel_only and chat.type != "channel":
        raise ValueError("Bu kanal emas (guruh bo'lishi mumkin). Avto-yuklash faqat kanallar uchun.")
    if member.status not in ("administrator", "creator"):
        raise ValueError("Bot bu kanalda admin emas. Avval botni admin qiling.")
    link = ""
    if need_link:
        if chat.username:
            link = f"https://t.me/{chat.username}"
        else:
            try:
                link = (await bot.create_chat_invite_link(chat.id)).invite_link
            except (TelegramBadRequest, TelegramForbiddenError):
                raise ValueError("Havola yaratib bo'lmadi: botga «Taklif havolasini yaratish» huquqini bering.")
    return chat.id, chat.title or str(chat.id), link
