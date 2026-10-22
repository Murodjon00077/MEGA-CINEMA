"""Bot almashtirilgach (yangi token) katalogni yangi botga moslash.

Telegram fayl raqami (file_id) BOT BO'YICHA alohida: eski bot raqami yangi botda ishlamaydi.
Bazada har bir kinoning manba kanali va xabar raqami saqlangan. Yangi bot (manba kanalda ADMIN bo'lishi shart)
har bir xabarni sizga forward qiladi, yangi raqamni oladi, xabarni o'chiradi. Keyin qoida:

  • kino bor  + rasm bor  → katalogda qoladi
  • kino bor  + rasm yo'q → «Avto» ga tushadi (rasm qo'yib qayta chiqarasiz)
  • kino yo'q (kanaldan o'chirilgan) → katalogdan o'chadi (rasmi bor-yo'qligidan qat'i nazar)

Boshlashdan oldin bazaning zaxirasi sizga yuboriladi.

Buyruq: /fayl_yangilash       — to'xtagan joyidan davom ettiradi
        /fayl_yangilash 0     — boshidan boshlaydi
"""
import asyncio
import logging

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import autoload
import backup
import broadcast
import db
import perms
import services as svc

router = Router()
CURSOR = "refile_cursor"
_lock = asyncio.Lock()

# Faqat shu xatolar «xabar kanaldan o'chirilgan» degani. Boshqa xatolar (bot kanalda admin emas va h.k.)
# hech narsani o'chirmaydi.
_GONE = ("message to forward not found", "message_id_invalid", "message not found")


def _is_gone(e: Exception) -> bool:
    s = str(e).lower()
    return any(g in s for g in _GONE)


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
    await m.answer(
        f"🔄 Yangilash boshlandi: {left} ta kino (taxminan {left * 1.3 / 60:.0f} daqiqa).\n\n"
        "Qoida:\n"
        "• kino ✅ + rasm ✅ → katalogda qoladi\n"
        "• kino ✅ + rasm ❌ → Avto ga tushadi\n"
        "• kino ❌ (kanaldan o'chirilgan) → katalogdan o'chadi\n\n"
        "Avval bazaning zaxirasini yuboraman.")
    broadcast.spawn(_job(bot, m.chat.id, after == 0))


async def _valid_file(bot: Bot, file_id: str) -> bool:
    try:
        await bot.get_file(file_id)
        return True
    except Exception:
        return False


async def _job(bot: Bot, admin_chat: int, fresh: bool) -> None:
    async with _lock:
        if fresh:
            try:
                await backup.send_backup(bot, [admin_chat], "Fayl yangilashdan oldingi zaxira")
            except Exception:
                logging.exception("Zaxira yuborilmadi")
        after = int(await db.get_setting(CURSOR, "0") or 0)
        rows = await db.movies_with_source(after)
        ok = fail = 0
        deleted: list[str] = []
        reasons: dict[str, int] = {}
        streak = 0
        aborted = False
        for i, r in enumerate(rows, 1):
            try:
                msg = await bot.forward_message(admin_chat, r["src_chat"], r["src_msg"])
            except Exception as e:
                if _is_gone(e):
                    mv = await db.get_movie(r["id"])
                    if mv:
                        ep = f" — {mv['ep']}-qism" if mv["series_id"] and mv["ep"] else ""
                        deleted.append(f"{mv['title']}{ep}")
                    await db.delete_movie(r["id"])
                    sid = r["series_id"]
                    if sid and not await db.episodes(sid, live_only=False):
                        await db.delete_series(sid)  # qismlari qolmagan serial ham ketadi
                    streak = 0
                else:
                    fail += 1
                    streak += 1
                    key = str(e)[:90]
                    reasons[key] = reasons.get(key, 0) + 1
                    if streak >= 5 and ok == 0:
                        aborted = True  # boshidan bitta sabab (odatda: bot kanalda admin emas)
                        break
                await db.set_setting(CURSOR, str(r["id"]))
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
                await svc.notify_admins(bot, f"🔄 Yangilash: {i}/{len(rows)} (✅ {ok}, 🗑 {len(deleted)}, ❌ {fail})")
            await asyncio.sleep(1.2)

        if aborted:
            txt = ("🔄 <b>Yangilash to'xtadi</b>\n\n⚠️ " + "\n⚠️ ".join(f"{k} ({n})" for k, n in list(reasons.items())[:3]) +
                   "\n\nYangi botni manba kanallarga <b>admin</b> qiling va <code>/fayl_yangilash</code> ni qayta yuboring. "
                   "Hech narsa o'chirilmadi.")
            await svc.notify_admins(bot, txt)
            return

        # Serial posterlari — qismning yangi rasmidan
        try:
            for sid in await db.series_ids_with_episodes():
                eps = await db.episodes(sid, live_only=False)
                poster = next((e["poster_id"] for e in eps if e["poster_id"]), "")
                if poster:
                    await db.set_series_poster(sid, poster)
        except Exception:
            logging.exception("Serial posterlarini yangilashda xatolik")

        # Muqova rasmlari: yangi botda ochiladiganlari qoladi, ochilmaydiganlari tozalanadi
        bad_covers = 0
        movie_covers, series_covers = await db.refile_covers()
        for mid, cid in movie_covers:
            if not await _valid_file(bot, cid):
                await db.clear_movie_cover(mid)
                bad_covers += 1
        for sid, cid in series_covers:
            if not await _valid_file(bot, cid):
                await db.clear_series_cover(sid)
                bad_covers += 1
        to_avto = await db.demote_uncovered()
        live = await db.live_title_count()
        no_src = await db.no_source_count()

        txt = ("🔄 <b>Yangilash tugadi</b>\n\n"
               f"✅ Kino yangilandi: {ok} ta\n"
               f"📚 Katalogda qoldi: {live} ta\n"
               f"📥 Avto ga tushdi (rasmi yo'q): {to_avto} ta\n"
               f"🗑 Kanaldan o'chirilgani uchun katalogdan o'chdi: {len(deleted)} ta")
        if deleted:
            txt += "\n" + "\n".join(f"• {t}" for t in deleted[:40])
            if len(deleted) > 40:
                txt += f"\n…va yana {len(deleted) - 40} ta"
        if fail:
            txt += f"\n\n⚠️ Yangilanmadi: {fail} ta"
            txt += "\n" + "\n".join(f"• {k} ({n})" for k, n in list(reasons.items())[:5])
            txt += "\nSababini tuzatib, <code>/fayl_yangilash</code> ni qayta yuboring (davom etadi)."
        if no_src:
            txt += (f"\n\nℹ️ {no_src} ta kino manba kanalsiz qo'shilgan (qo'lda), ularni bu usul bilan yangilab bo'lmaydi: "
                    "ular yuborilmasligi mumkin.")
        if to_avto:
            txt += "\n\n👉 Katalog → ⚙️ Admin → Avto: rasmlarini qo'yib, qayta chiqaring."
        await svc.notify_admins(bot, txt)


@router.message(Command("rasm_tikla"))
async def cmd_cover_fix(m: Message, bot: Bot):
    """Muqovasi yo'q (Avto dagi) kontentga video kadrini muqova qilib, XABARSIZ qayta chiqaradi."""
    if not (m.from_user and perms.is_full(m.from_user.id)):
        return
    if _lock.locked():
        await m.answer("⏳ Fayl yangilash ketyapti, tugagach qayta yuboring.")
        return
    a, b = await db.thumb_as_cover()
    ids, left = await db.publish_pending()  # xabarsiz: foydalanuvchilarga ham, kanallarga ham e'lon ketmaydi
    live = await db.live_title_count()
    await m.answer(
        "🖼 <b>Rasmlar tiklandi</b>\n\n"
        f"Video kadri muqova bo'ldi: {a} ta kino, {b} ta serial\n"
        f"Katalogga qaytdi: {len(ids)} ta qism/kino (e'lon yuborilmadi)\n"
        f"📚 Hozir katalogda: {live} ta\n"
        + (f"📥 Rasmsiz qolgan (Avto da): {left} ta\n" if left else "")
        + "\nXohlasangiz, istalgan kinoning muqovasini Mini App → tahrirlash orqali chiroyli rasmga almashtirasiz.")
