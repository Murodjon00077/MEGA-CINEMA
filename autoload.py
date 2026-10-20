"""Kanaldan avtomatik yuklash.

Admin o'z kanaliga kino (video/fayl) tashlaydi, izohiga nom, yil, janr, davlat yozadi —
bot postni o'zi o'qib bazaga **«kutilmoqda»** holatida saqlaydi (foydalanuvchilar hali ko'rmaydi).
Admin tayyor bo'lgach «Hammasini chiqarish» tugmasini bosadi — hammasi katalogga chiqadi
va foydalanuvchilarga bitta umumiy xabar ketadi.

Kanalda oldindan turgan kinolar: postlarni botning o'ziga forward qiling — xuddi shunday o'qiladi.

Shart: bot kanalda ADMIN bo'lishi va kanal «Avto-yuklash kanallari» ro'yxatida turishi kerak.
"""
import asyncio
import html
import sqlite3

from aiogram import Bot, F, Router
from aiogram.types import Message

import backup
import broadcast
import caption_parser as cp
import db
import keyboards as kb
import services as svc

router = Router()

VIDEO_EXT = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".wmv", ".flv", ".ts")
NOTICE_DELAY = 4  # soniya: ketma-ket kelgan kinolar bitta xabarga jamlanadi

_state: dict = {"titles": [], "dups": 0, "noposter": []}
_notice_task: asyncio.Task | None = None


def media_of(m: Message):
    """(media, 'video'|'document') yoki (None, '') — kino bo'lmagan fayllarni o'tkazib yuboradi."""
    if m.video:
        return m.video, "video"
    d = m.document
    if d and ((d.mime_type or "").startswith("video/") or (d.file_name or "").lower().endswith(VIDEO_EXT)):
        return d, "document"
    return None, ""


def link_for(chat, msg_id: int) -> str:
    if getattr(chat, "username", None):
        return f"https://t.me/{chat.username}/{msg_id}"
    cid = str(chat.id)
    return f"https://t.me/c/{cid[4:] if cid.startswith('-100') else cid.lstrip('-')}/{msg_id}"


# ───────────── Adminga jamlangan bildirishnoma ─────────────
def queue_notice(bot: Bot, title: str | None = None, dup: bool = False, noposter_id: int | None = None) -> None:
    global _notice_task
    if dup:
        _state["dups"] += 1
    if title:
        _state["titles"].append(title)
    if noposter_id:
        _state["noposter"].append(noposter_id)
    if _notice_task is None or _notice_task.done():
        _notice_task = broadcast.spawn(_flush_notice(bot))


async def _flush_notice(bot: Bot) -> None:
    await asyncio.sleep(NOTICE_DELAY)
    titles, dups, noposter = _state["titles"], _state["dups"], _state["noposter"]
    _state.update(titles=[], dups=0, noposter=[])
    if not titles and not dups:
        return
    total = (await db.stats())["pending"]
    lines = []
    if titles:
        lines.append(f"⏳ <b>Kutilmoqda: +{len(titles)} ta kino</b> (jami kutayotgan: {total})\n")
        lines += [f"• {html.escape(t)}" for t in titles[:10]]
        if len(titles) > 10:
            lines.append(f"…va yana {len(titles) - 10} ta")
    if dups:
        lines.append(f"\n♻️ Takroriy (o'tkazib yuborildi): {dups}")
    if titles:
        lines.append("\n🖼 Chiqarishdan oldin har bir <b>kino va serialga</b> rasm qo'ying (serialda qismlarga emas, serialning o'ziga): "
                     "katalog → ⚙️ Admin → Avto → ✏️. Rasmsiz chiqmaydi.")
    lines.append("\nFoydalanuvchilar hali ko'rmaydi. Tayyor bo'lgach chiqaring 👇")
    await svc.notify_admins(bot, "\n".join(lines), reply_markup=kb.pending_notice_kb())


# ───────────── Import ─────────────
def _thumb(media) -> str:
    return getattr(getattr(media, "thumbnail", None), "file_id", "") or ""


async def import_episode(bot: Bot, series, media, ftype: str, caption: str | None,
                         src_chat: int, src_msg: int, link: str) -> str:
    """Qismli kontent: qism raqami postdagi izohdan olinadi. 'added' | 'dup' | 'noep' qaytaradi."""
    ep = cp.parse_episode(caption, getattr(media, "file_name", None))
    if not ep:
        await svc.notify_admins(
            bot,
            f"⚠️ <b>{html.escape(series['title'])}</b>: qism raqami topilmadi — qo'shilmadi.\n\n"
            "Video izohiga faqat qism raqamini yozing (masalan <code>3</code>). "
            "Postni tahrirlasangiz bot qayta o'qiydi.\n" + (f"🔗 {link}" if link else ""),
        )
        return "noep"
    if await db.episode_by_no(series["id"], ep):
        queue_notice(bot, dup=True)
        return "dup"
    thumb = _thumb(media)
    quality = cp.quality_from_size(media.width, media.height) if ftype == "video" else ""
    data = {
        "title": series["title"], "description": series["description"], "year": series["year"],
        "genre": series["genre"], "country": series["country"], "language": series["language"],
        "quality": quality, "file_id": media.file_id, "file_type": ftype, "poster": thumb,
        "src_chat": src_chat, "src_msg": src_msg, "status": "pending",
        "kind": series["kind"], "series_id": series["id"], "ep": ep, **db.media_meta(media),
    }
    try:
        movie_id = await db.add_movie(data)
    except sqlite3.IntegrityError:
        return "dup"
    if thumb and not series["poster_id"]:
        await db.set_series_poster(series["id"], thumb)
    backup.schedule(bot)
    queue_notice(bot, f"{series['title']} — {ep}-qism", noposter_id=None if (thumb or series["poster_id"]) else movie_id)
    return "added"


async def import_media(bot: Bot, media, ftype: str, caption: str | None,
                       src_chat: int, src_msg: int, link: str = "") -> str:
    """Bitta kinoni/qismni «kutilmoqda» holatida bazaga qo'shadi. 'added' | 'dup' | 'notitle' | 'noep'."""
    if await db.movie_by_source(src_chat, src_msg):
        queue_notice(bot, dup=True)
        return "dup"
    source = await db.get_source(src_chat)
    if source and source["series_id"]:
        series = await db.get_series(source["series_id"])
        if series:
            return await import_episode(bot, series, media, ftype, caption, src_chat, src_msg, link)
    kind = source["kind"] if source and source["kind"] in ("kino", "multfilm", "anime") else "kino"
    info = cp.parse_caption(caption or "", getattr(media, "file_name", None))
    if not info.get("title"):
        await svc.notify_admins(
            bot,
            "⚠️ Kino <b>nomi topilmadi</b> — qo'shilmadi.\n\n"
            "Post izohining birinchi qatoriga kino nomini yozing "
            "(kanaldagi postni tahrirlasangiz bot qayta o'qiydi).\n" + (f"🔗 {link}" if link else ""),
        )
        return "notitle"

    thumb = _thumb(media)
    quality = info.get("quality") or (cp.quality_from_size(media.width, media.height) if ftype == "video" else "")
    data = {
        **info,
        "quality": quality,
        "file_id": media.file_id,
        "file_type": ftype,
        "poster": thumb,
        "src_chat": src_chat,
        "src_msg": src_msg,
        "status": "pending",
        "kind": kind,
        **db.media_meta(media),
    }
    try:
        movie_id = await db.add_movie(data)
    except sqlite3.IntegrityError:  # parallel yangilanish: shu post allaqachon qo'shilgan
        return "dup"
    backup.schedule(bot)
    queue_notice(bot, data["title"], noposter_id=None if thumb else movie_id)
    return "added"


async def process(m: Message, bot: Bot, edited: bool) -> None:
    """Manba kanaldagi post (yangi yoki tahrirlangan)."""
    if not await db.is_source(m.chat.id):
        return
    media, ftype = media_of(m)
    if not media:
        return
    existing = await db.movie_by_source(m.chat.id, m.message_id)
    if existing:
        if not edited:
            return
        if existing["series_id"]:
            # Qism: izoh tahrirlansa qism raqami yangilanadi
            ep = cp.parse_episode(m.caption, getattr(media, "file_name", None))
            if ep and ep != existing["ep"]:
                try:
                    await db.set_episode_no(existing["id"], ep)
                except sqlite3.IntegrityError:
                    await svc.notify_admins(bot, f"⚠️ {ep}-qism allaqachon mavjud — o'zgartirilmadi.")
                    return
                await svc.notify_admins(
                    bot, f"✏️ <b>{html.escape(existing['title'])}</b>: qism raqami {ep} ga o'zgartirildi")
            return
        # Kino: post izohi tahrirlansa — ma'lumotlar ham yangilanadi (fayl va poster tegilmaydi)
        info = cp.parse_caption(m.caption or "", getattr(media, "file_name", None))
        if info.get("title"):
            await db.update_movie_meta(existing["id"], info)
            await svc.notify_admins(
                bot, f"✏️ Yangilandi: <b>{html.escape(info['title'])}</b> (kod: <code>{existing['id']}</code>)"
            )
        return
    await import_media(bot, media, ftype, m.caption, m.chat.id, m.message_id, link_for(m.chat, m.message_id))


@router.channel_post(F.video | F.document)
async def on_channel_post(m: Message, bot: Bot):
    await process(m, bot, edited=False)


@router.edited_channel_post(F.video | F.document)
async def on_channel_edit(m: Message, bot: Bot):
    await process(m, bot, edited=True)
