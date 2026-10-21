"""Bot almashtirilgach (yangi token) kinolarning fayl raqamlarini (file_id) qayta olish.

Telegram fayl raqami BOT BO'YICHA alohida: eski bot raqami yangi botda ishlamaydi ("yuborishda xatolik").
Bazada har bir kinoning manba kanali va xabar raqami saqlangan. Yangi bot (manba kanalda ADMIN bo'lishi shart)
har bir xabarni sizga forward qiladi, yangi raqamni oladi, xabarni o'chiradi.

Buyruq: /fayl_yangilash       — to'xtagan joyidan davom ettiradi
        /fayl_yangilash 0     — boshidan boshlaydi
"""
import asyncio
import logging

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import autoload
import broadcast
import db
import perms
import services as svc

router = Router()
CURSOR = "refile_cursor"
_lock = asyncio.Lock()


@router.message(Command("fayl_yangilash"))
async def cmd_refile(m: Message, command: CommandObject, bot: Bot):
    if not (m.from_user and perms.is_full(m.from_user.id)):
        return
    if _lock.locked():
        await m.answer("⏳ Yangilash allaqachon ketyapti. Tugagach hisobot keladi.")
        return
    if (command.args or "").strip() == "0":
        await db.set_setting(CURSOR, "0")
    after = int(await db.get_setting(CURSOR, "0") or 0)
    left = len(await db.movies_with_source(after))
    if not left:
        await m.answer("Yangilanadigan kino qolmadi. Boshidan boshlash uchun: <code>/fayl_yangilash 0</code>")
        return
    await m.answer(f"🔄 Fayl raqamlarini yangilash boshlandi: {left} ta kino.\n"
                   f"Taxminan {left * 1.3 / 60:.0f} daqiqa ketadi. Har 100 tadan keyin xabar beraman.")
    broadcast.spawn(_job(bot, m.chat.id))


async def _job(bot: Bot, admin_chat: int) -> None:
    async with _lock:
        after = int(await db.get_setting(CURSOR, "0") or 0)
        rows = await db.movies_with_source(after)
        ok = fail = 0
        reasons: dict[str, int] = {}
        streak = 0
        for i, r in enumerate(rows, 1):
            try:
                msg = await bot.forward_message(admin_chat, r["src_chat"], r["src_msg"])
            except Exception as e:
                fail += 1
                streak += 1
                key = str(e)[:90]
                reasons[key] = reasons.get(key, 0) + 1
                if streak >= 5 and ok == 0:
                    break  # birinchi 5 tasi ham o'xshamadi — sabab bitta (bot kanalda admin emas)
                await asyncio.sleep(1)
                continue
            media, ftype = autoload.media_of(msg)
            if media:
                thumb = getattr(getattr(media, "thumbnail", None), "file_id", "") or ""
                await db.movie_set_file(r["id"], media.file_id, ftype, thumb)
                ok += 1
                streak = 0
            else:
                fail += 1
                reasons["xabarda video yo'q"] = reasons.get("xabarda video yo'q", 0) + 1
            try:
                await msg.delete()
            except Exception:
                pass
            await db.set_setting(CURSOR, str(r["id"]))
            if i % 100 == 0:
                await svc.notify_admins(bot, f"🔄 Fayl yangilash: {i}/{len(rows)} (✅ {ok}, ❌ {fail})")
            await asyncio.sleep(1.2)

        # Seriallar posteri — birinchi qismning yangi rasmidan
        try:
            for sid in await db.series_ids_with_episodes():
                eps = await db.episodes(sid, live_only=False)
                poster = next((e["poster_id"] for e in eps if e["poster_id"]), "")
                if poster:
                    await db.set_series_poster(sid, poster)
        except Exception:
            logging.exception("Serial posterlarini yangilashda xatolik")

        covers = await db.covers_count()
        txt = f"🔄 <b>Fayl raqamlari yangilandi</b>\n\n✅ {ok} ta   ❌ {fail} ta"
        if reasons:
            txt += "\n\n⚠️ Sabablar:\n" + "\n".join(f"• {k} ({n})" for k, n in list(reasons.items())[:5])
            txt += "\n\nBot manba kanallarda ADMIN ekanini tekshiring, so'ng <code>/fayl_yangilash</code> ni qayta yuboring (davom etadi)."
        if covers:
            txt += f"\n\n🖼 Muqova rasmlari ({covers} ta) eski bot orqali yuklangan edi, ular yangi botda ochilmaydi. Mini App → tahrirlash orqali qayta qo'ying."
        await svc.notify_admins(bot, txt)
