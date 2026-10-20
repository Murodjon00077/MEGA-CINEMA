"""Adminlar tarixi (audit): kim, qachon, nima qildi. Faqat bosh admin ko'radi."""
import logging

import db
import perms


async def log(uid: int, name: str, cat: str, action: str, detail: str = "") -> None:
    """Tarixga yozadi. Hech qachon asosiy amalni buzmaydi."""
    try:
        await db.alog_add(uid, (name or "")[:60], perms.role(uid) or "admin", cat, action[:80], (detail or "")[:300])
    except Exception:
        logging.exception("Tarixga yozib bo'lmadi")
