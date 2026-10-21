"""Instagramga Reels yuklash (instagrapi — login/parol orqali, rasmiy API emas).

Diqqat: bu norasmiy usul. Instagram akkauntni vaqtincha cheklashi mumkin, shuning uchun
yuklashlar orasida pauza qo'yiladi va sessiya fayli saqlanadi (har safar qayta login qilinmaydi).
"""
import asyncio
import logging
import queue
from pathlib import Path

import config

ACCTS = ("kino", "anime", "multfilm")
LABEL = {"kino": "🎬 Kino / Serial", "anime": "🎌 Anime", "multfilm": "🧸 Multfilm"}
ENV_NAME = {"kino": "IG_KINO", "anime": "IG_ANIME", "multfilm": "IG_MULT"}


def _moviepy_compat() -> None:
    """instagrapi ba'zi versiyalarda `moviepy.VideoFileClip` ni kutadi, moviepy 1.x esa uni `moviepy.editor` da beradi.
    Ikkala versiya bilan ishlashi uchun nomlarni moviepy'ga ko'chirib qo'yamiz."""
    try:
        import moviepy
        if hasattr(moviepy, "VideoFileClip"):
            return
        import moviepy.editor as ed
        for n in dir(ed):
            if not n.startswith("_") and not hasattr(moviepy, n):
                setattr(moviepy, n, getattr(ed, n))
    except Exception as e:
        logging.warning("moviepy moslashtirish: %s", e)


class LoginFailed(Exception):
    """Login/parol, 2 bosqichli tasdiq yoki Instagram «challenge» — qayta urinishdan foyda yo'q."""


def configured(acct: str) -> bool:
    u, p = config.IG_ACCOUNTS.get(acct, ("", ""))
    return bool(u and p)


_clients: dict = {}

# ───── Instagram «tasdiq kodi» (challenge): kodni admin Telegram orqali yuboradi ─────
_notifier = None  # callable(str) — adminlarga xabar (boshqa thread'dan chaqiriladi)
_codes: dict[str, queue.Queue] = {}
_waiting: list[str] = []
CODE_WAIT = 300  # soniya


def set_notifier(fn) -> None:
    global _notifier
    _notifier = fn


def waiting() -> list[str]:
    return list(_waiting)


def submit_code(code: str, acct: str | None = None) -> str | None:
    """Admin yuborgan kodni kutayotgan akkauntga uzatadi. Qaysi akkaunt uchun ekani qaytariladi."""
    acct = acct or (_waiting[0] if _waiting else None)
    if not acct or acct not in _waiting:
        return None
    _codes.setdefault(acct, queue.Queue()).put(code)
    return acct


def _code_handler(acct: str):
    def handler(username, choice):
        q = _codes.setdefault(acct, queue.Queue())
        while not q.empty():
            q.get_nowait()
        _waiting.append(acct)
        try:
            if _notifier:
                _notifier(
                    f"🔐 <b>{LABEL[acct]}</b> akkaunti (<code>{username}</code>) uchun Instagram tasdiq kodi so'radi.\n\n"
                    f"Kod <b>{str(choice).split('.')[-1].lower()}</b> orqali yuborildi (emailni / SMS'ni tekshiring).\n"
                    f"Uni {CODE_WAIT // 60} daqiqa ichida shu yerga yozing:\n<code>/igcode 123456</code>\n\n"
                    "Instagram ilovasida «Bu men edim» degan bildirishnoma kelsa, uni ham tasdiqlang.")
            return q.get(timeout=CODE_WAIT)
        except queue.Empty:
            return False
        finally:
            if acct in _waiting:
                _waiting.remove(acct)
    return handler


def _login(acct: str):
    try:
        from instagrapi import Client
    except ImportError as e:
        raise LoginFailed("instagrapi o'rnatilmagan (pip install -r requirements.txt)") from e
    _moviepy_compat()
    user, pw = config.IG_ACCOUNTS[acct]
    session: Path = config.IG_DIR / f"ig_{acct}.json"
    cl = Client()
    cl.challenge_code_handler = _code_handler(acct)
    if config.IG_PROXY:
        cl.set_proxy(config.IG_PROXY)
    cl.delay_range = [2, 6]
    logged = False
    if session.exists():
        try:
            cl.load_settings(session)
            cl.login(user, pw)
            cl.get_timeline_feed()  # sessiya haqiqatan ishlayaptimi
            logged = True
        except Exception as e:
            logging.info("IG %s: eski sessiya yaroqsiz (%s), qayta login", acct, e)
            old_uuids = cl.get_settings().get("uuids")
            cl = Client()
            cl.challenge_code_handler = _code_handler(acct)
            if config.IG_PROXY:
                cl.set_proxy(config.IG_PROXY)
            cl.delay_range = [2, 6]
            if old_uuids:
                cl.set_uuids(old_uuids)
    if not logged:
        try:
            cl.login(user, pw)
        except Exception as e:
            raise LoginFailed(f"{type(e).__name__}: {str(e)[:150]}") from e
    try:
        cl.dump_settings(session)
    except Exception:
        pass
    return cl


def upload(acct: str, path: str, caption: str) -> str:
    """Sinxron (thread ichida chaqiriladi). Reels'ning media id'sini qaytaradi."""
    _moviepy_compat()
    cl = _clients.get(acct)
    if cl is None:
        cl = _clients[acct] = _login(acct)
    try:
        media = cl.clip_upload(Path(path), caption)
    except Exception as e:
        name = type(e).__name__
        if name in ("LoginRequired", "ChallengeRequired", "TwoFactorRequired", "BadPassword",
                    "ReloginAttemptExceeded", "FeedbackRequired", "PleaseWaitFewMinutes"):
            _clients.pop(acct, None)
            raise LoginFailed(f"{name}: {str(e)[:150]}") from e
        raise
    return str(getattr(media, "pk", "") or "")


async def upload_async(acct: str, path: str, caption: str) -> str:
    return await asyncio.to_thread(upload, acct, path, caption)


def session_path(acct: str) -> Path:
    return config.IG_DIR / f"ig_{acct}.json"
