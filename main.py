"""Kino bot — ishga tushirish: python main.py"""
import asyncio
import logging

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BotCommand, BotCommandScopeChat, CallbackQuery, MenuButtonWebApp, Message,
                           WebAppInfo)

import admin_handlers as admin
import autoload
import backup
import config
import db
import perms
import theatre
import trailers
import user_handlers as user
import vip
import vip_handlers
import web


class TrackMiddleware(BaseMiddleware):
    """Foydalanuvchini bazaga yozadi va ban qilinganlarni to'xtatadi."""

    async def __call__(self, handler, event, data):
        u = data.get("event_from_user")
        if u and not u.is_bot:
            banned = await db.touch_user(u.id, u.full_name, u.username)
            if banned and not perms.is_staff(u.id):
                left = await db.ban_until(u.id)
                text = "🚫 Siz botdan foydalanishdan bloklangansiz."
                if left and left < db.FOREVER - 1:
                    text += f"\n⏳ Blok tugashiga: {db.fmt_left(left)}"
                if isinstance(event, Message):
                    await event.answer(text)
                elif isinstance(event, CallbackQuery):
                    await event.answer(text, show_alert=True)
                return None
        return await handler(event, data)


async def setup_bot(bot: Bot) -> None:
    await bot.set_my_commands([
        BotCommand(command="start", description="🏠 Bosh menyu"),
        BotCommand(command="vip", description="👑 VIP obuna"),
        BotCommand(command="help", description="ℹ️ Yordam"),
    ])
    for admin_id in perms.staff_ids():
        try:
            await bot.set_my_commands(
                [BotCommand(command="admin", description="👨‍💻 Admin panel"),
                 BotCommand(command="start", description="🏠 Bosh menyu"),
                 BotCommand(command="vip", description="👑 VIP obuna"),
                 BotCommand(command="poster", description="🖼 Poster o'zgartirish"),
                 BotCommand(command="cancel", description="❌ Bekor qilish")],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception:
            pass
    if config.WEBAPP_URL:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="🎬 MEGA CINEMA", web_app=WebAppInfo(url=config.WEBAPP_URL))
            )
        except Exception as e:
            logging.warning("Menyu tugmasini o'rnatib bo'lmadi: %s", e)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN topilmadi. .env faylini to'ldiring (.env.example ga qarang).")
    if not config.ADMIN_IDS:
        logging.warning("ADMIN_IDS bo'sh — admin panel ishlamaydi!")
    if config.WEBAPP_URL_RAW and not config.WEBAPP_URL:
        logging.warning("WEBAPP_URL https:// bilan boshlanishi shart — Mini App o'chirildi.")
    if not config.WEBAPP_URL:
        logging.warning("Mini App o'chiq (WEBAPP_URL yo'q) — katalog tugmasi ko'rinmaydi.")

    await db.init(config.DB_PATH)
    await perms.reload()
    await vip.load()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(TrackMiddleware())
    dp.callback_query.outer_middleware(TrackMiddleware())
    dp.include_router(admin.router)
    dp.include_router(vip_handlers.router)
    dp.include_router(user.router)
    dp.include_router(trailers.router)  # treyler kanali autoload'dan OLDIN turishi shart
    dp.include_router(autoload.router)

    runner = await web.start(bot, config.PORT)
    me = await bot.me()
    logging.info("Bot ishga tushdi: @%s | Mini App serveri: :%s", me.username, config.PORT)
    await setup_bot(bot)
    await bot.delete_webhook(drop_pending_updates=True)

    # Baza bo'sh bo'lsa (masalan, hosting fayllarni o'chirgan) — adminlarga eslatamiz
    if (await db.stats())["movies"] == 0:
        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    "ℹ️ <b>Bot ishga tushdi, baza hozircha bo'sh.</b>\n\n"
                    "Agar oldin kinolar bo'lgan bo'lsa — /restore yozing va oxirgi zaxira faylini yuboring.",
                )
            except Exception:
                pass
    backup_task = asyncio.create_task(backup.periodic(bot))
    theatre_task = None
    if await theatre.start():  # API_ID / API_HASH bo'lsa teatr ishga tushadi
        theatre_task = asyncio.create_task(theatre.run(bot))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        backup_task.cancel()
        if theatre_task:
            theatre_task.cancel()
        await theatre.stop()
        await runner.cleanup()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
