"""Sozlamalar (.env fayldan o'qiladi)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(BASE_DIR / ".env")

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS: set[int] = {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.lstrip("-").isdigit()
}
# Mini App chatidagi «Adminga murojaat» tugmasi shu foydalanuvchining lichkasini ochadi (@siz, @ belgisiz ham bo'ladi).
# Bo'sh qoldirsangiz — birinchi bosh adminning ID si ishlatiladi.
ADMIN_CONTACT: str = os.getenv("ADMIN_CONTACT", "").strip().lstrip("@")
# Teatr (MTProto orqali kinoni Telegramdan oqim qilib berish). my.telegram.org dan olinadi.
API_ID: int = int(os.getenv("API_ID", "0") or 0) if os.getenv("API_ID", "").strip().isdigit() else 0
API_HASH: str = os.getenv("API_HASH", "").strip()
THEATRE_ON: bool = bool(API_ID and API_HASH)
# VIP to'lovi: BotFather → Bot Settings → Payments dan olingan «provider token»lar (bo'sh bo'lsa shu usul ko'rinmaydi)
PAYMENT_TOKEN_CLICK: str = os.getenv("PAYMENT_TOKEN_CLICK", "").strip()
PAYMENT_TOKEN_PAYME: str = os.getenv("PAYMENT_TOKEN_PAYME", "").strip()
# VIP cheklari tushadigan kanal/guruh ID si (masalan -1001234567890). Bot u yerda admin bo'lishi shart.
# Bo'sh bo'lsa yoki yuborib bo'lmasa — cheklar eski usulda bosh/starshiy adminlarning lichkasiga boradi.
VIP_CHECK_CHANNEL: int = int(os.getenv("VIP_CHECK_CHANNEL", "0").strip() or 0) if os.getenv("VIP_CHECK_CHANNEL", "0").strip().lstrip("-").isdigit() else 0
THEATRE_TZ: int = int(os.getenv("THEATRE_TZ", "5") or 5)  # vaqt zonasi (UTC+5 — O'zbekiston)
BOT_TITLE: str = os.getenv("BOT_TITLE", "MEGA CINEMA").strip()
PORT: int = int(os.getenv("PORT", "8080"))
DB_PATH: str = os.getenv("DB_PATH", str(BASE_DIR / "data" / "bot.db"))

# WEBAPP_URL berilmasa, Render o'zi beradigan RENDER_EXTERNAL_URL ishlatiladi
_raw_url = (os.getenv("WEBAPP_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
WEBAPP_URL_RAW: str = _raw_url
# Telegram Mini App faqat HTTPS ni qabul qiladi
WEBAPP_URL: str = _raw_url if _raw_url.startswith("https://") else ""

WEBAPP_DIR: Path = BASE_DIR
POSTER_DIR: Path = BASE_DIR / "data" / "posters"


# ───────────── Instagram treylerlar (instagrapi, login-parol orqali) ─────────────
# 3 ta akkaunt: kino+serial, anime, multfilm. Bo'sh qoldirilgani o'chiq turadi.
IG_ACCOUNTS: dict[str, tuple[str, str]] = {
    "kino": (os.getenv("IG_KINO_USER", "").strip(), os.getenv("IG_KINO_PASS", "").strip()),
    "anime": (os.getenv("IG_ANIME_USER", "").strip(), os.getenv("IG_ANIME_PASS", "").strip()),
    "multfilm": (os.getenv("IG_MULT_USER", "").strip(), os.getenv("IG_MULT_PASS", "").strip()),
}
IG_PROXY: str = os.getenv("IG_PROXY", "").strip()
IG_DELAY: int = int(os.getenv("IG_DELAY", "90") or 90)  # ketma-ket yuklashlar orasida (soniya)
IG_AD_CAPTION: str = os.getenv("IG_AD_CAPTION", "🤖 Bot va kanal — profil bioda 🔗").strip()
IG_DIR: Path = Path(DB_PATH).parent  # sessiya fayllari shu yerda saqlanadi
