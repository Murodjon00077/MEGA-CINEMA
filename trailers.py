"""Treylerlar: Telegram kanaldan o'qish → Instagram'ga (kod bo'yicha akkauntga) yuklash.

Treyler kanali: bot kanalda ADMIN bo'lishi shart. Videoning izohiga faqat kod yoziladi:
  1, 2, 3 → kino        s.1 → serial  (ikkalasi «Kino/Serial» akkauntiga)
  a.1     → anime       m.1 → multfilm
  izoh BO'SH → reklama: uchala akkauntga ham tushadi, izohi «Silka bioda».
Bir xil kod qayta ishlatilsa — o'tkazib yuborilmaydi, ikkalasi ham yuklanadi.
"""
import asyncio
import html
import logging
import random
import re
import tempfile
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.types import Message

import autoload
import broadcast
import config
import db
import insta
import services as svc

router = Router()
CHAT_KEY = "trailer_chat"

_CODE = re.compile(r"([sam])\s*\.\s*(\d+)|(\d+)", re.I)
_GROUP = {"s": "kino", "a": "anime", "m": "multfilm"}


def parse_code(caption: str | None):
    """(kod, guruh) | ('', 'ad') — izoh bo'sh | None — kod tushunarsiz."""
    text = (caption or "").strip()
    if not text:
        return "", "ad"
    first = text.splitlines()[0].strip(" .,;:!#")
    m = _CODE.fullmatch(first)
    if not m:
        return None
    if m.group(1):
        pre = m.group(1).lower()
        return f"{pre}.{int(m.group(2))}", _GROUP[pre]
    return str(int(m.group(3))), "kino"


async def is_trailer_chat(m: Message) -> bool:
    cid = await db.get_setting(CHAT_KEY, "")
    return bool(cid) and str(m.chat.id) == cid


# ───────────── Kanaldan qabul qilish ─────────────
_notes: list[str] = []
_note_task: asyncio.Task | None = None


def _queue_note(bot: Bot, line: str) -> None:
    global _note_task
    _notes.append(line)
    if _note_task is None or _note_task.done():
        _note_task = broadcast.spawn(_flush_notes(bot))


async def _flush_notes(bot: Bot) -> None:
    await asyncio.sleep(4)
    lines = _notes[:]
    _notes.clear()
    if lines:
        await svc.notify_admins(bot, "🎞 <b>Treyler qabul qilindi</b>\n\n" + "\n".join(lines[:15])
                                + (f"\n…va yana {len(lines) - 15} ta" if len(lines) > 15 else "")
                                + "\n\nInstagramga yuklash: katalog → ⚙️ Admin → Avto → 🎞 Treyler")


async def process(m: Message, bot: Bot, edited: bool) -> None:
    media, _ = autoload.media_of(m)
    if not media:
        return
    parsed = parse_code(m.caption)
    existing = await db.trailer_by_src(m.chat.id, m.message_id)
    if existing:
        # Hali yuklanmagan bo'lsa, izoh tahrirlanganda kod yangilanadi
        if edited and parsed and not await db.trailer_has_posts(existing["id"]):
            await db.trailer_set_code(existing["id"], parsed[0], parsed[1])
        return
    if parsed is None:
        await svc.notify_admins(
            bot,
            "⚠️ Treyler izohidagi <b>kod tushunarsiz</b> — qo'shilmadi.\n\n"
            "Izohga faqat kod yozing: <code>1</code>, <code>s.1</code>, <code>a.1</code>, <code>m.1</code> "
            "(reklama bo'lsa — izohni bo'sh qoldiring). Postni tahrirlasangiz bot qayta o'qiydi.",
        )
        return
    code, grp = parsed
    try:
        await db.trailer_add(m.chat.id, m.message_id, media.file_id,
                             getattr(media, "file_size", 0) or 0, code, grp)
    except Exception:
        return  # parallel yangilanish — allaqachon qo'shilgan
    label = "📢 reklama (uchala akkauntga)" if grp == "ad" else insta.LABEL[grp]
    _queue_note(bot, f"• <code>{html.escape(code) or '—'}</code> → {label}")


@router.channel_post(F.video | F.document, is_trailer_chat)
async def on_post(m: Message, bot: Bot):
    await process(m, bot, edited=False)


@router.edited_channel_post(F.video | F.document, is_trailer_chat)
async def on_edit(m: Message, bot: Bot):
    await process(m, bot, edited=True)


# ───────────── Instagramga yuklash ─────────────
_run_lock = asyncio.Lock()


def running() -> bool:
    return _run_lock.locked()


def caption_for(row) -> str:
    return config.IG_AD_CAPTION if row["grp"] == "ad" else row["code"]


async def _run(bot: Bot, accts: list[str]) -> None:
    async with _run_lock:
        lines = []
        tmp = Path(tempfile.mkdtemp(prefix="trailers_"))
        for acct in accts:
            label = insta.LABEL[acct]
            if not insta.configured(acct):
                lines.append(f"{label}: ⚠️ akkaunt ulanmagan ({insta.ENV_NAME[acct]}_USER / _PASS yo'q)")
                continue
            rows = await db.trailers_waiting(acct)
            ok = fail = 0
            err = ""
            for i, r in enumerate(rows):
                path = tmp / f"{r['id']}.mp4"
                try:
                    await bot.download(r["file_id"], destination=path)
                except Exception as e:
                    fail += 1
                    await db.trailer_mark(r["id"], acct, "fail", "Telegramdan yuklab bo'lmadi (20 MB dan kattami?)")
                    err = err or f"Telegramdan yuklab bo'lmadi: {str(e)[:80]}"
                    continue
                try:
                    pk = await insta.upload_async(acct, str(path), caption_for(r))
                    await db.trailer_mark(r["id"], acct, "done", pk)
                    ok += 1
                except insta.LoginFailed as e:
                    fail += 1
                    err = str(e)
                    await db.trailer_mark(r["id"], acct, "fail", err)
                    break  # login muammosi — qolganlarini urinib o'tirmaymiz
                except Exception as e:
                    fail += 1
                    err = f"{type(e).__name__}: {str(e)[:120]}"
                    logging.warning("IG %s treyler #%s: %s", acct, r["id"], e)
                    await db.trailer_mark(r["id"], acct, "fail", err)
                finally:
                    path.unlink(missing_ok=True)
                if i < len(rows) - 1:
                    await asyncio.sleep(random.uniform(config.IG_DELAY, config.IG_DELAY * 1.5))
            left = len(await db.trailers_waiting(acct))
            line = f"{label}: ✅ {ok} ta yuklandi" + (f", ❌ {fail} ta xato" if fail else "") + (f" (qoldi: {left})" if left else "")
            if err:
                line += f"\n   ↳ {html.escape(err)}"
            lines.append(line)
        await svc.notify_admins(bot, "📸 <b>Instagram treylerlar</b>\n\n" + "\n".join(lines))


def start(bot: Bot, accts: list[str]) -> bool:
    """Fonda yuklashni boshlaydi. Allaqachon ishlayotgan bo'lsa False."""
    if _run_lock.locked():
        return False
    broadcast.spawn(_run(bot, accts))
    return True
