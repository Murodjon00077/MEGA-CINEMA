"""Adminlar va huquqlar.

• Bosh admin (starshiy)  — .env dagi ADMIN_IDS: hamma huquq, adminlarni boshqaradi, o'chirib bo'lmaydi.
• Starshiy admin          — Mini App orqali tayinlanadi: hamma huquq, oddiy adminlarni qo'shadi/o'chiradi.
• Admin                   — faqat belgilangan huquqlar va bo'limlar (kino, serial, anime, multfilm).
"""
import config
import db

PERMS = {
    "add": "🎬 Kino qo'shish va tahrirlash",
    "delete": "🗑 Kino o'chirish",
    "publish": "📤 Kutayotganlarni chiqarish",
    "broadcast": "📣 Hammaga xabar yuborish",
    "channels": "📢 Kanallar (obuna, avto-yuklash)",
    "ban": "🚫 Ban / unban",
    "stats": "📊 Statistika",
    "settings": "🛠 Umumiy sozlamalar",
}
SECTIONS = {"kino": "🎬 Kino", "serial": "📺 Serial", "anime": "🎌 Anime", "multfilm": "🧸 Multfilm"}
ROLE_LABEL = {"owner": "👑 Bosh admin", "senior": "⭐ Starshiy admin", "admin": "👤 Admin"}

_cache: dict[int, dict] = {}


async def reload() -> None:
    """Adminlar ro'yxatini bazadan xotiraga yuklaydi (har o'zgarishdan keyin chaqiriladi)."""
    _cache.clear()
    for r in await db.list_admins():
        _cache[r["id"]] = {
            "id": r["id"], "name": r["name"], "role": "senior" if r["role"] == "senior" else "admin",
            "perms": {p for p in (r["perms"] or "").split(",") if p in PERMS},
            "sections": {s for s in (r["sections"] or "").split(",") if s in SECTIONS},
        }


def role(uid: int) -> str | None:
    if uid in config.ADMIN_IDS:
        return "owner"
    a = _cache.get(uid)
    return a["role"] if a else None


def is_staff(uid: int) -> bool:
    return role(uid) is not None


def is_full(uid: int) -> bool:
    """Bosh yoki starshiy admin — hamma huquq."""
    return role(uid) in ("owner", "senior")


def can_manage(uid: int) -> bool:
    return is_full(uid)


def has(uid: int, perm: str) -> bool:
    if is_full(uid):
        return True
    a = _cache.get(uid)
    return bool(a and perm in a["perms"])


def has_any(uid: int, *perm_list: str) -> bool:
    return any(has(uid, p) for p in perm_list)


def can_kind(uid: int, kind: str) -> bool:
    """Shu bo'lim (kino/serial/anime/multfilm) bilan ishlay oladimi."""
    if is_full(uid):
        return True
    a = _cache.get(uid)
    return bool(a and kind in a["sections"])


def allowed_kinds(uid: int) -> set[str]:
    if is_full(uid):
        return set(SECTIONS)
    a = _cache.get(uid)
    return set(a["sections"]) if a else set()


def staff_ids() -> set[int]:
    return set(config.ADMIN_IDS) | set(_cache)


def notify_ids() -> set[int]:
    """Avto-yuklash va tarqatish hisobotlari kimga boradi: bosh/starshiy adminlar va kino qo'shadiganlar."""
    ids = set(config.ADMIN_IDS)
    for uid, a in _cache.items():
        if a["role"] == "senior" or a["perms"] & {"add", "publish"}:
            ids.add(uid)
    return ids


def info(uid: int) -> dict:
    """Mini App uchun: rol, huquqlar, bo'limlar."""
    r = role(uid)
    if r is None:
        return {"role": None, "full": False, "manage": False, "perms": [], "sections": []}
    if r in ("owner", "senior"):
        return {"role": r, "full": True, "manage": True, "perms": list(PERMS), "sections": list(SECTIONS)}
    a = _cache[uid]
    return {"role": "admin", "full": False, "manage": False,
            "perms": sorted(a["perms"]), "sections": sorted(a["sections"])}


def summary(role_name: str, perm_list, section_list) -> str:
    """Admin turini qisqa matnda ko'rsatadi (ro'yxat uchun)."""
    if role_name == "owner":
        return "Hamma huquq · adminlarni boshqaradi"
    if role_name == "senior":
        return "Hamma huquq · oddiy adminlarni boshqaradi"
    p = ", ".join(PERMS[x].split(" ", 1)[1] for x in PERMS if x in perm_list) or "—"
    s = ", ".join(SECTIONS[x].split(" ", 1)[1] for x in SECTIONS if x in section_list) or "—"
    return f"{p} · Bo'limlar: {s}"
