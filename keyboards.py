"""Barcha tugmalar (klaviaturalar)."""
from urllib.parse import quote

from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup, WebAppInfo

import config


def _kb(rows: list[list[B]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def webapp_button(text: str = "🎬 MEGA CINEMA", query: str = "") -> B | None:
    """Katalogni ochuvchi tugma. query berilsa katalog qidiruvi shu matn bilan ochiladi (masalan s.1)."""
    if not config.WEBAPP_URL:
        return None
    url = config.WEBAPP_URL
    if query:
        url += ("&" if "?" in url else "?") + "q=" + quote(query)
    return B(text=text, web_app=WebAppInfo(url=url))


# ───────────── Foydalanuvchi ─────────────
def main_kb() -> InlineKeyboardMarkup:
    """Bosh menyu: faqat katalogni ochuvchi «MEGA CINEMA» tugmasi."""
    w = webapp_button("🎬 MEGA CINEMA")
    if w:
        return _kb([[w]])
    return _kb([])  # katalog manzili (WEBAPP_URL) sozlanmagan bo'lsa


def sub_kb(channels, movie_id: int = 0) -> InlineKeyboardMarkup:
    rows = [[B(text=f"📢 {ch['title'] or 'Kanal'}", url=ch["link"])] for ch in channels]
    rows.append([B(text="✅ Obunani tekshirish", callback_data=f"chk:{movie_id}")])
    return _kb(rows)


def movie_kb(movie_id: int, is_fav: bool, bot_username: str, title: str, episode: bool = False) -> InlineKeyboardMarkup:
    link = f"https://t.me/{bot_username}?start=m_{movie_id}"
    share = f"https://t.me/share/url?url={quote(link)}&text={quote('🎬 ' + title)}"
    rows = [] if episode else [
        [B(text="💔 Saralanganlardan olib tashlash" if is_fav else "⭐ Saralanganlarga qo'shish",
           callback_data=f"fav:{movie_id}")],
    ]
    rows.append([B(text="📤 Do'stlarga ulashish", url=share)])
    w = webapp_button("🎞 Katalogni ochish")
    if w:
        rows.append([w])
    return _kb(rows)


def catalog_query_kb(query: str) -> InlineKeyboardMarkup | None:
    w = webapp_button("🎞 Katalogda ochish", query=query)
    return _kb([[w]]) if w else None


def open_catalog_kb() -> InlineKeyboardMarkup | None:
    w = webapp_button()
    return _kb([[w]]) if w else None


# ───────────── Admin ─────────────
def admin_kb() -> InlineKeyboardMarkup:
    return _kb([
        [B(text="📊 Statistika", callback_data="adm:stats"), B(text="🎬 Kino qo'shish", callback_data="adm:add")],
        [B(text="🗑 Kino o'chirish", callback_data="adm:del"), B(text="📋 Kinolar ro'yxati", callback_data="adm:list")],
        [B(text="📣 Xabar yuborish", callback_data="adm:bc"), B(text="📢 Majburiy obuna", callback_data="adm:ch")],
        [B(text="🚫 Ban / Unban", callback_data="adm:ban"), B(text="⚙️ Sozlamalar", callback_data="adm:set")],
        [B(text="⏳ Kutilayotganlar", callback_data="adm:pend"), B(text="📥 Avto-yuklash kanali", callback_data="adm:src")],
        [B(text="💾 Zaxira nusxa", callback_data="adm:backup"), B(text="♻️ Tiklash", callback_data="adm:restore")],
    ])


def back_admin_kb() -> InlineKeyboardMarkup:
    return _kb([[B(text="⬅️ Admin panel", callback_data="adm:panel")]])


def cancel_kb() -> InlineKeyboardMarkup:
    return _kb([[B(text="❌ Bekor qilish", callback_data="adm:cancel")]])


def skip_kb() -> InlineKeyboardMarkup:
    return _kb([
        [B(text="⏭ O'tkazib yuborish", callback_data="adm:skip")],
        [B(text="❌ Bekor qilish", callback_data="adm:cancel")],
    ])


def added_kb(movie_id: int) -> InlineKeyboardMarkup:
    return _kb([
        [B(text="📣 Hammaga tarqatish", callback_data=f"adm:push:{movie_id}")],
        [B(text="👁 Ko'rish", callback_data=f"m:{movie_id}"), B(text="➕ Yana qo'shish", callback_data="adm:add")],
        [B(text="⬅️ Admin panel", callback_data="adm:panel")],
    ])


def confirm_del_kb(movie_id: int) -> InlineKeyboardMarkup:
    return _kb([
        [B(text="✅ Ha, o'chirish", callback_data=f"adm:delok:{movie_id}"),
         B(text="❌ Yo'q", callback_data="adm:panel")],
    ])


def channels_kb(channels) -> InlineKeyboardMarkup:
    rows = [[B(text=f"🗑 {ch['title'] or ch['chat_id']}", callback_data=f"adm:chdel:{ch['id']}")] for ch in channels]
    rows.append([B(text="➕ Kanal qo'shish", callback_data="adm:chadd")])
    rows.append([B(text="⬅️ Admin panel", callback_data="adm:panel")])
    return _kb(rows)


def sources_kb(sources) -> InlineKeyboardMarkup:
    rows = [[B(text=f"🗑 {s['title'] or s['chat_id']}", callback_data=f"adm:srcdel:{s['chat_id']}")] for s in sources]
    rows.append([B(text="➕ Kanal qo'shish", callback_data="adm:srcadd")])
    rows.append([B(text="⬅️ Admin panel", callback_data="adm:panel")])
    return _kb(rows)


def announce_kb(movie_id: int) -> InlineKeyboardMarkup:
    """Yangi kino e'lonining tugmalari (foydalanuvchilarga boradi)."""
    rows = [[B(text="▶️ Tomosha qilish", callback_data=f"m:{movie_id}")]]
    w = webapp_button("🎞 Katalogni ochish")
    if w:
        rows.append([w])
    return _kb(rows)


def announce_many_kb() -> InlineKeyboardMarkup:
    """Bir nechta yangi kino e'loni tugmalari."""
    w = webapp_button("🎞 Katalogni ochish")
    return _kb([[w]] if w else [])


def pending_kb() -> InlineKeyboardMarkup:
    return _kb([
        [B(text="🚀 Chiqarish + hammaga xabar", callback_data="adm:pubok:1")],
        [B(text="🤫 Xabarsiz chiqarish", callback_data="adm:pubok:0")],
        [B(text="⬅️ Admin panel", callback_data="adm:panel")],
    ])


def pending_notice_kb() -> InlineKeyboardMarkup:
    return _kb([[B(text="⏳ Ko'rish va chiqarish", callback_data="adm:pend")]])


def settings_kb(protect: bool, sub_required: bool) -> InlineKeyboardMarkup:
    return _kb([
        [B(text=f"{'✅' if protect else '❌'} Kontentni himoyalash (yuklash/forward taqiq)",
           callback_data="adm:tgl:protect_content")],
        [B(text=f"{'✅' if sub_required else '❌'} Majburiy obuna",
           callback_data="adm:tgl:sub_required")],
        [B(text="⬅️ Admin panel", callback_data="adm:panel")],
    ])


def broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return _kb([
        [B(text="✅ Yuborish", callback_data="adm:bcok"), B(text="❌ Bekor qilish", callback_data="adm:cancel")],
    ])
