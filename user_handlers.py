"""Oddiy foydalanuvchilar uchun handlerlar."""
import html
import re
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import CallbackQuery, FSInputFile, Message

import config
import db
import keyboards as kb
import services as svc
import vip_handlers

router = Router()

WELCOME = (
    "🎬 <b>Assalomu alaykum, {name}!</b>\n\n"
    "✨ <b>{title}</b> kino olamiga xush kelibsiz!\n\n"
    "Sevimli kinolaringizni topish va ularni to'g'ridan-to'g'ri Telegramning o'zida "
    "tomosha qilish endi juda oson va mutlaqo bepul.\n\n"
    "━━━━━━━━━━━━━━━\n"
    "🔢 <b>Kod bilan</b> — kino kodini yuboring, kino darhol keladi.\n"
    "🔎 <b>Nom bilan</b> — kino nomini yozing, bot uni katalogdan qidirib beradi.\n"
    "🎞 <b>Katalog</b> — pastdagi tugma orqali oching: posterlar, janrlar va qulay qidiruv bir joyda.\n"
    "━━━━━━━━━━━━━━━\n\n"
    "💡 <i>Maslahat: kino kodini do'stlaringizga yuboring — ular ham uni bir zumda topadi!</i>\n\n"
    "👇 Boshlash uchun pastdagi tugmani bosing."
)

HELP = (
    "ℹ️ <b>Yordam</b>\n\n"
    "• Kino kodini yuboring — kino darhol keladi\n"
    "• Kino nomini yozing — katalogda qidirib beramiz\n"
    "• <b>🎬 MEGA CINEMA</b> tugmasi — hamma bo'limlar shu yerda: top, yangilari, serial, multfilm, "
    "anime va saralanganlar\n\n"
    "<b>Buyruqlar:</b>\n"
    "/start — bosh menyu\n"
    "/help — yordam"
)


WELCOME_IMG = Path(__file__).parent / "welcome.jpg"
_welcome_fid: str | None = None  # birinchi yuborilgach Telegram file_id si saqlanadi (qayta yuklanmaydi)


async def send_welcome(bot: Bot, chat_id: int, user) -> None:
    """Salomlashuv: tepada MEGA CINEMA rasmi, ostida matn va katalog tugmasi."""
    global _welcome_fid
    text, markup = welcome_text(user), kb.main_kb()
    try:
        msg = await bot.send_photo(chat_id, _welcome_fid or FSInputFile(WELCOME_IMG), caption=text,
                                   reply_markup=markup)
        if msg.photo:
            _welcome_fid = msg.photo[-1].file_id
    except (TelegramBadRequest, FileNotFoundError, OSError):
        _welcome_fid = None
        await bot.send_message(chat_id, text, reply_markup=markup, disable_web_page_preview=True)


def welcome_text(user) -> str:
    return WELCOME.format(name=html.escape(user.first_name or "do'st"), title=html.escape(config.BOT_TITLE))


async def render(event: Message | CallbackQuery, text: str, markup) -> None:
    """Callbackda xabarni tahrirlaydi (imkoni bo'lmasa — yangisini yuboradi)."""
    if isinstance(event, CallbackQuery):
        if event.message:
            try:
                await event.message.edit_text(text, reply_markup=markup)
                return
            except TelegramBadRequest:
                pass
            await event.message.answer(text, reply_markup=markup)
    else:
        await event.answer(text, reply_markup=markup)


async def gate(bot: Bot, event: Message | CallbackQuery, movie_id: int = 0) -> bool:
    """Obunani tekshiradi. Obuna bo'lmasa so'rov yuboradi va False qaytaradi."""
    bad = await svc.unsubscribed(bot, event.from_user.id)
    if not bad:
        return True
    chat_id = event.chat.id if isinstance(event, Message) else event.message.chat.id
    await svc.ask_subscribe(bot, chat_id, bad, movie_id)
    return False


async def reply_deliver(bot: Bot, event: Message | CallbackQuery, movie_id: int) -> str:
    chat_id = event.chat.id if isinstance(event, Message) else event.message.chat.id
    res = await svc.deliver(bot, chat_id, event.from_user.id, movie_id)
    if res == "nf":
        await bot.send_message(chat_id, "❌ Kino topilmadi. U o'chirilgan bo'lishi mumkin.")
    elif res == "err":
        await bot.send_message(chat_id, "⚠️ Kinoni yuborishda xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring.")
    return res


# ───────────── Buyruqlar ─────────────
@router.message(CommandStart())
async def cmd_start(m: Message, command: CommandObject, bot: Bot, state: FSMContext):
    await state.clear()
    payload = command.args or ""
    if payload == "vip":  # Mini App'dagi «VIP olish» tugmasi
        await vip_handlers.show_menu(m)
        return
    cm = re.fullmatch(r"c_([sam])_(\d+)", payload)  # kanal e'lonidan: serial / anime / multfilm
    if cm:
        q = f"{cm.group(1)}.{cm.group(2)}"
        code = db.parse_series_code(q)
        if code:
            await answer_code(m, bot, q, code)
            return
    mid = int(payload[2:]) if payload.startswith("m_") and payload[2:].isdigit() else 0
    if not await gate(bot, m, mid):
        return
    if mid:
        res = await svc.deliver(bot, m.chat.id, m.from_user.id, mid)
        if res in ("ok", "vip"):
            return
    await send_welcome(bot, m.chat.id, m.from_user)


@router.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(HELP, reply_markup=kb.open_catalog_kb())


@router.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(HELP, reply_markup=kb.open_catalog_kb())


# Eski xabarlardagi tugmalar / eski buyruqlar: bo'limlar endi faqat katalogda
LEGACY_TEXT = "🎞 Top, yangilari, saralanganlar va boshqa barcha bo'limlar endi <b>katalogda</b>. 👇"


@router.message(Command("top", "new", "random", "favs"))
async def cmd_legacy(m: Message):
    await m.answer(LEGACY_TEXT, reply_markup=kb.open_catalog_kb())


@router.callback_query(F.data.in_({"top", "new", "rand", "favs", "menu"}))
async def cb_legacy(c: CallbackQuery, bot: Bot):
    await c.answer()
    await bot.send_message(c.message.chat.id, LEGACY_TEXT, reply_markup=kb.open_catalog_kb())


@router.callback_query(F.data.startswith("chk:"))
async def cb_check(c: CallbackQuery, bot: Bot):
    mid = int(c.data.split(":")[1] or 0)
    bad = await svc.unsubscribed(bot, c.from_user.id)
    if bad:
        await c.answer("❌ Siz hali hamma kanallarga obuna bo'lmadingiz!", show_alert=True)
        try:
            await c.message.edit_reply_markup(reply_markup=kb.sub_kb(bad, mid))
        except TelegramBadRequest:
            pass
        return
    await c.answer("✅ Rahmat! Obuna tasdiqlandi.")
    chat_id = c.message.chat.id
    try:
        await c.message.delete()
    except TelegramBadRequest:
        pass
    if mid:
        res = await svc.deliver(bot, chat_id, c.from_user.id, mid)
        if res in ("ok", "vip"):
            return
    await send_welcome(bot, chat_id, c.from_user)


# ───────────── Kino ochish / saralash ─────────────
@router.callback_query(F.data.startswith("m:"))
async def cb_movie(c: CallbackQuery, bot: Bot):
    await c.answer()
    await reply_deliver(bot, c, int(c.data.split(":")[1]))


@router.callback_query(F.data.startswith("fav:"))
async def cb_fav(c: CallbackQuery, bot: Bot):
    mid = int(c.data.split(":")[1])
    movie = await db.get_movie(mid)
    if not movie:
        return await c.answer("Kino topilmadi", show_alert=True)
    now = await db.toggle_fav(c.from_user.id, mid)
    await c.answer("⭐ Saralanganlarga qo'shildi" if now else "💔 Saralanganlardan olib tashlandi")
    me = await bot.me()
    try:
        await c.message.edit_reply_markup(reply_markup=kb.movie_kb(mid, now, me.username, movie["title"]))
    except TelegramBadRequest:
        pass


async def answer_code(m: Message, bot: Bot, q: str, code: tuple) -> None:
    """s.1 / m.1 / a.1 kodi: nomi chiqadi va katalogga yo'naltiriladi (bot ichida kod yozilganda ham,
    kanaldagi «Tomosha qilish» tugmasi orqali kirilganda ham shu ishlaydi)."""
    ser = await db.get_series(code[1]) if code[1] else None
    eps = await db.episodes(ser["id"]) if ser and ser["kind"] == code[0] else []
    # Faqat oddiy kino kodi videoni darhol yuboradi. Serial / multfilm / anime (qismli ham, dona ham):
    # nomi chiqadi va katalogga yo'naltiriladi — ko'rish u yerdan.
    single = None
    if not eps:
        single = await db.movie_by_num(code[2], code[0])
        if single and single["status"] != "live":
            single = None
    if not await gate(bot, m):
        return
    if not eps and not single:
        await m.answer(
            f"❌ <code>{html.escape(q)}</code> kodli {svc.KIND_NAME.get(code[0], 'kontent')} topilmadi.\n\n"
            f"Kodni tekshirib qayta yuboring yoki katalogdan {svc.KIND_NAME.get(code[0], 'kontent')} qidiring.",
            reply_markup=kb.open_catalog_kb(),
        )
        return
    if eps:
        kind, title, c = ser["kind"], ser["title"], db.series_code(ser["kind"], ser["id"])
        head = f"{svc.KIND_EMOJI.get(kind, '📺')} <b>{html.escape(title)}</b> — <b>{len(eps)}</b> ta qism bor."
        how = "kerakli qismni tanlang"
    else:
        kind, title, c = single["kind"], single["title"], db.movie_code(single)
        head = f"{svc.KIND_EMOJI.get(kind, '🎬')} <b>{html.escape(title)}</b>"
        how = "«Botda ko'rish» tugmasini bosing"
    await m.answer(
        f"{head}\n\n"
        f"Ko'rish uchun <b>katalogni</b> oching: u yerda qidiruvga "
        f"<code>{c}</code> deb yozing va {how}. 👇",
        reply_markup=kb.catalog_query_kb(c),
    )
    return


# ───────────── Matn: kod yoki qidiruv ─────────────
@router.message(StateFilter(default_state), F.text & ~F.text.startswith("/"))
async def on_text(m: Message, bot: Bot):
    q = m.text.strip()
    code = db.parse_series_code(q)
    if code:
        await answer_code(m, bot, q, code)
        return
    if q.isdigit():
        # Oddiy raqam faqat yakka kinoning kodi (1, 2, 3...). Serial/multfilm/anime qismlariga tegishli emas.
        movie = await db.movie_by_num(int(q)) if len(q) < 10 else None
        if not await gate(bot, m, movie["id"] if movie else 0):
            return
        if not movie:
            await m.answer(
                f"❌ <code>{html.escape(q)}</code> kodli kino topilmadi.\n\n"
                "Kodni tekshirib qayta yuboring yoki kino nomini yozib qidiring.",
                reply_markup=kb.open_catalog_kb(),
            )
            return
        await reply_deliver(bot, m, movie["id"])
        return

    if not await gate(bot, m):
        return
    if len(q) < 2:
        await m.answer("🔎 Qidirish uchun kamida 2 ta belgi yozing.")
        return
    rows, _ = await db.search_movies(q, limit=1)
    series = await db.search_series(q, limit=1)
    if not rows and not series:
        await m.answer(
            f"😔 <b>«{html.escape(q[:60])}»</b> bo'yicha hech narsa topilmadi.\n\n"
            "Boshqacha yozib ko'ring yoki katalogdan qidiring.",
            reply_markup=kb.open_catalog_kb(),
        )
        return
    await m.answer(
        f"🔎 <b>«{html.escape(q[:60])}»</b> bo'yicha natijalar topildi.\n\n"
        "Ko'rish uchun katalogni oching 👇",
        reply_markup=kb.catalog_query_kb(q[:60]),
    )
