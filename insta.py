"""Instagramga Reels yuklash (instagrapi — login/parol orqali, rasmiy API emas).

Diqqat: bu norasmiy usul. Instagram akkauntni vaqtincha cheklashi mumkin, shuning uchun
yuklashlar orasida pauza qo'yiladi va sessiya fayli saqlanadi (har safar qayta login qilinmaydi).
"""
import asyncio
import logging
from pathlib import Path

import config

ACCTS = ("kino", "anime", "multfilm")
LABEL = {"kino": "🎬 Kino / Serial", "anime": "🎌 Anime", "multfilm": "🧸 Multfilm"}
ENV_NAME = {"kino": "IG_KINO", "anime": "IG_ANIME", "multfilm": "IG_MULT"}


class LoginFailed(Exception):
    """Login/parol, 2 bosqichli tasdiq yoki Instagram «challenge» — qayta urinishdan foyda yo'q."""


def configured(acct: str) -> bool:
    u, p = config.IG_ACCOUNTS.get(acct, ("", ""))
    return bool(u and p)


_clients: dict = {}


def _login(acct: str):
    try:
        from instagrapi import Client
    except ImportError as e:
        raise LoginFailed("instagrapi o'rnatilmagan (pip install -r requirements.txt)") from e
    user, pw = config.IG_ACCOUNTS[acct]
    session: Path = config.IG_DIR / f"ig_{acct}.json"
    cl = Client()
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
