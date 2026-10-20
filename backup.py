"""Baza zaxirasi: bepul hostinglarda fayllar o'chib ketishi mumkin, shuning uchun
baza nusxasi vaqti-vaqti bilan adminlarga Telegram orqali yuboriladi."""
import asyncio
import logging
import os
import tempfile
import time

from aiogram import Bot
from aiogram.types import FSInputFile

import config
import db

_pending: asyncio.Task | None = None


async def send_backup(bot: Bot, chat_ids=None, note: str = "") -> int:
    """Zaxira faylni adminlarga yuboradi. Nechta adminga yetganini qaytaradi."""
    path = os.path.join(tempfile.gettempdir(), "kino_bot_backup.db")
    await db.backup_to(path)
    name = time.strftime("kino_zaxira_%Y-%m-%d_%H-%M.db")
    ok = 0
    for cid in chat_ids or config.ADMIN_IDS:
        try:
            await bot.send_document(
                cid, FSInputFile(path, filename=name),
                caption=f"💾 <b>Baza zaxirasi</b>{note}\n\nBaza yo'qolsa: /restore yozing va shu faylni yuboring.",
            )
            ok += 1
        except Exception as e:
            logging.warning("Zaxira yuborilmadi (%s): %s", cid, e)
    return ok


def schedule(bot: Bot, delay: int = 120) -> None:
    """Yangi kino qo'shilgandan keyin 2 daqiqa ichida zaxira yuboradi (ketma-ket qo'shilsa — bittagina)."""
    global _pending
    if _pending and not _pending.done():
        _pending.cancel()

    async def _later():
        await asyncio.sleep(delay)
        await send_backup(bot, note=" (yangi kino qo'shilgandan keyin)")

    _pending = asyncio.create_task(_later())


async def periodic(bot: Bot, hours: int = 6) -> None:
    while True:
        await asyncio.sleep(hours * 3600)
        try:
            await send_backup(bot, note=" (avtomatik)")
        except Exception:
            logging.exception("Avtomatik zaxira xatosi")
