"""Mini App serveri: statik sahifa + JSON API (Telegram initData bilan himoyalangan)."""
import asyncio
import hashlib
import hmac
import html
import json
import logging
import re
import time
from urllib.parse import parse_qsl, quote

from aiohttp import web
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, LabeledPrice

import config
import db
import broadcast
import insta
import perms
import promo
import services as svc
import theatre
import theatre_rt as rt
import trailers
import vip
import audit

SETTING_DEFAULTS = {"protect_content": "0", "sub_required": "1", "theatre_on": "1", "vip_on": "0"}
THEMES = ("default", "newyear", "ramadan", "navruz")  # dizayn rejimlari


async def _theme() -> str:
    t = await db.get_setting("theme", "default")
    return t if t in THEMES else "default"
SINGLE_KINDS = ("kino", "multfilm", "anime")
DRAFT_LIMITS = {"title": 120, "description": 800, "year": 4, "genre": 100, "country": 60, "language": 60, "quality": 20}


def verify_init_data(init_data: str, token: str, max_age: int = 7 * 86400) -> dict | None:
    """Telegram WebApp initData imzosini tekshiradi. To'g'ri bo'lsa user dict qaytaradi."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        recv_hash = pairs.pop("hash", None)
        if not recv_hash:
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, recv_hash):
            return None
        if time.time() - int(pairs.get("auth_date", "0")) > max_age:
            return None
        user = json.loads(pairs["user"])
        return user if isinstance(user, dict) and "id" in user else None
    except Exception:
        return None


def _auth(request: web.Request) -> dict:
    user = verify_init_data(request.headers.get("X-Init-Data", ""), config.BOT_TOKEN)
    if not user:
        raise web.HTTPUnauthorized(reason="auth")
    return user


def _admin(request: web.Request, *need: str) -> dict:
    """Admin (bosh/starshiy/oddiy) uchun. `need` berilsa — shulardan kamida bittasi huquqda bo'lishi kerak."""
    user = _auth(request)
    if not perms.is_staff(user["id"]):
        raise web.HTTPForbidden(reason="admin")
    if need and not perms.has_any(user["id"], *need):
        raise web.HTTPForbidden(reason="Sizda bu amal uchun ruxsat yo'q")
    return user


def _kind_ok(user: dict, kind: str) -> None:
    """Shu bo'limga (kino/serial/anime/multfilm) ruxsat bo'lmasa — 403."""
    if not perms.can_kind(user["id"], kind):
        raise web.HTTPForbidden(reason=f"Sizda «{svc.KIND_NAME.get(kind, kind)}» bo'limiga ruxsat yo'q")


def _manager(request: web.Request) -> dict:
    """Adminlarni boshqarish: faqat bosh va starshiy adminlar."""
    user = _auth(request)
    if not perms.can_manage(user["id"]):
        raise web.HTTPForbidden(reason="Adminlarni faqat starshiy admin boshqara oladi")
    return user


def _bad(msg: str) -> web.Response:
    return web.json_response({"error": msg}, status=400)


def _cv(cover_id: str) -> str:
    """Muqova versiyasi (brauzer keshini yangilash uchun). Muqova yo'q bo'lsa — bo'sh."""
    return hashlib.md5(cover_id.encode()).hexdigest()[:8] if cover_id else ""


def _label(pay: str) -> str:
    """Katalog kartochkasidagi lenta: pullik bo'lsa PRO, aks holda FREE (TOP/NEW faqat slayderda)."""
    return pay if pay in ("pro", "vip") else "free"


def _brief(r) -> dict:
    """Katalog kartochkasi. t='m' — kino, t='s' — qismli (serial/multfilm/anime); fk — saralanganlar kaliti."""
    t = r["t"] if "t" in r.keys() else "m"
    return {
        "t": t, "id": r["id"], "fk": r["fk"] if "fk" in r.keys() else r["id"],
        "kind": r["kind"], "code": db.series_code(r["kind"], r["id"]) if t == "s"
        else db.movie_code(r),
        "eps": r["eps"] if "eps" in r.keys() else 0,
        "title": r["title"], "year": r["year"], "genre": r["genre"],
        "country": r["country"], "quality": r["quality"], "views": r["views"],
        "poster": bool(r["poster_id"]),
        "cv": _cv(r["cover_id"]) if "cover_id" in r.keys() else "",
        "pay": vip.pay_tag(r),
        "lbl": _label(vip.pay_tag(r)),
        "price": int(r["pro_price"]) if "pro_price" in r.keys() and r["pro_price"] else 0,
    }


@web.middleware
async def errors(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException as e:
        if request.path.startswith("/api/"):
            return web.json_response({"error": e.reason}, status=e.status)
        raise
    except ValueError:
        return web.json_response({"error": "bad request"}, status=400)
    except Exception:
        logging.exception("API xatosi: %s", request.path)
        return web.json_response({"error": "server"}, status=500)


# ───────────── Adminlar tarixi (audit) ─────────────
_AUDIT = {
    "/api/admin/movie/update": ("movie", "Kino tahrirlandi"),
    "/api/admin/movie/delete": ("movie", "Kino o'chirildi"),
    "/api/admin/series/update": ("movie", "Serial tahrirlandi"),
    "/api/admin/series/delete": ("movie", "Serial o'chirildi"),
    "/api/admin/publish": ("publish", "Kutilayotganlar chiqarildi"),
    "/api/admin/setting": ("settings", "Sozlama o'zgartirildi"),
    "/api/admin/ad": ("settings", "Reklama qo'shildi / o'zgartirildi"),
    "/api/admin/ad/delete": ("settings", "Reklama o'chirildi"),
    "/api/admin/theme": ("settings", "Dizayn rejimi o'zgartirildi"),
    "/api/admin/channel": ("channel", "Obuna kanali qo'shildi"),
    "/api/admin/channel/delete": ("channel", "Obuna kanali o'chirildi"),
    "/api/admin/source": ("channel", "Avto-yuklash kanali ulandi"),
    "/api/admin/admin": ("admin", "Admin qo'shildi / o'zgartirildi"),
    "/api/admin/admin/delete": ("admin", "Admin olib tashlandi"),
    "/api/admin/broadcast": ("broadcast", "Hammaga xabar yuborildi"),
    "/api/admin/vip/config": ("vip", "VIP tariflari o'zgartirildi"),
    "/api/admin/vip/grant": ("vip", "VIP berildi / olib tashlandi"),
    "/api/admin/cards": ("vip", "To'lov kartasi saqlandi"),
    "/api/admin/cards/delete": ("vip", "To'lov kartasi o'chirildi"),
    "/api/admin/theatre/premiere": ("theatre", "Premyera belgilandi"),
    "/api/admin/theatre/cancel": ("theatre", "Premyera bekor qilindi"),
    "/api/admin/theatre/rebuild": ("theatre", "Teatr jadvali qayta tuzildi"),
    "/api/chat/mod": ("chat", "Chat moderatsiyasi"),
    "/api/chat/delete": ("chat", "Chat xabari o'chirildi"),
    "/api/theatre/chat/delete": ("theatre", "Teatr chat xabari o'chirildi"),
}
_MOD_LABEL = {"delete": "Chat xabari o'chirildi", "mute": "Foydalanuvchi mute qilindi",
              "unmute": "Mute olib tashlandi", "ban": "Foydalanuvchi ban qilindi", "unban": "Ban olib tashlandi"}
_SKIP_KEYS = ("pass", "token", "secret")


async def _audit_detail(path: str, body: dict) -> tuple[str, str]:
    """(amal nomi bo'lsa o'zgartirilgan, tafsilot)."""
    label = ""
    try:
        if path in ("/api/admin/movie/update", "/api/admin/movie/delete"):
            m = await db.get_movie(int(body.get("id") or 0))
            return label, (f"{m['title']} (#{m['id']})" if m else f"#{body.get('id')}")
        if path in ("/api/admin/series/update", "/api/admin/series/delete"):
            s = await db.get_series(int(body.get("id") or 0))
            return label, (f"{s['title']} (#{s['id']})" if s else f"#{body.get('id')}")
        if path == "/api/admin/publish":
            return label, f"{'xabar bilan' if body.get('notify') else 'xabarsiz'} · doira: {body.get('scope') or 'hammasi'}"
        if path == "/api/admin/setting":
            return label, f"{body.get('key')} → {'yoqildi' if body.get('value') else 'o‘chirildi'}"
        if path == "/api/admin/broadcast":
            return label, str(body.get("text") or "")[:80]
        if path == "/api/chat/mod":
            act = str(body.get("action") or "")
            label = _MOD_LABEL.get(act, "")
            who = f"ID {body.get('uid')}" if body.get("uid") else f"xabar #{body.get('id')}"
            extra = " · umrbod" if body.get("forever") else (f" · {body.get('minutes')} daqiqa" if body.get("minutes") else "")
            return label, who + extra
    except (TypeError, ValueError):
        pass
    parts = []
    for k, v in body.items():
        if any(x in k.lower() for x in _SKIP_KEYS) or isinstance(v, (dict, list)):
            continue
        sv = str(v)
        if sv and len(sv) <= 60:
            parts.append(f"{k}={sv}")
    return label, ", ".join(parts)[:160]


@web.middleware
async def audit_mw(request: web.Request, handler):
    """Adminlarning muvaffaqiyatli o'zgartiruvchi amallarini tarixga yozadi."""
    spec = _AUDIT.get(request.path) if request.method in ("POST", "PUT", "DELETE") else None
    pre = None
    if spec:
        try:
            user = verify_init_data(request.headers.get("X-Init-Data", ""), config.BOT_TOKEN)
            if user and perms.is_staff(user["id"]):
                try:
                    body = await request.json()
                except Exception:
                    body = {}
                label, detail = await _audit_detail(request.path, body if isinstance(body, dict) else {})
                pre = (user, spec[0], label or spec[1], detail)
        except Exception:
            logging.exception("Audit tayyorlashda xato")
    resp = await handler(request)
    if pre and resp.status < 400:
        await audit.log(pre[0]["id"], _uname(pre[0]), pre[1], pre[2], pre[3])
    return resp


@web.middleware
async def ban_guard(request: web.Request, handler):
    """Ban qilingan foydalanuvchi katalogning hech bir API siga kira olmaydi."""
    if request.path.startswith("/api/"):
        raw = request.headers.get("X-Init-Data") or request.query.get("a", "")
        user = verify_init_data(raw, config.BOT_TOKEN) if raw else None
        if user and not perms.is_staff(user["id"]):
            until = await db.ban_until(user["id"])
            if until is not None:
                return web.json_response({"error": "ban", "until": until}, status=403)
    return await handler(request)


# ───────────── Sahifalar ─────────────
async def index(request: web.Request):
    return web.FileResponse(config.WEBAPP_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def health(request: web.Request):
    return web.json_response({"ok": True})


def _contact_link() -> str:
    """«Adminga murojaat» tugmasi ochadigan havola."""
    if config.ADMIN_CONTACT:
        return f"https://t.me/{config.ADMIN_CONTACT}"
    if config.ADMIN_IDS:
        return f"tg://user?id={sorted(config.ADMIN_IDS)[0]}"
    return ""


# ───────────── Umumiy chat ─────────────
CHAT_MAX_LEN = 500
CHAT_COOLDOWN = 2  # soniya: bir foydalanuvchi ketma-ket yozishi orasidagi eng kam vaqt


def _chat_item(r, viewer_id: int, vips=frozenset()) -> dict:
    return {"id": r["id"], "uid": r["user_id"], "name": r["name"] or "Foydalanuvchi", "text": r["text"],
            "at": r["at"], "mine": r["user_id"] == viewer_id, "staff": perms.is_staff(r["user_id"]),
            "vip": r["user_id"] in vips}


async def _muted_resp(user: dict, room_owner: int | None = None):
    """Mute qilingan bo'lsa — javob (yozish taqiqlangan). `room_owner`: o'z xonasida yozish mumkin."""
    if perms.is_staff(user["id"]) or room_owner == user["id"]:
        return None
    until = await db.mute_until(user["id"])
    if until is None:
        return None
    left = db.fmt_left(until)
    text = "🔇 Siz chatda umrbod mute qilingansiz" if left == "umrbod" else f"🔇 Siz mute qilingansiz. Yana {left} yoza olmaysiz"
    return web.json_response({"error": text}, status=403)


async def api_chat_list(request: web.Request):
    user = _auth(request)
    after = max(int(request.query.get("after", "0") or 0), 0)
    rows = await db.chat_list(after, 50)
    vips = await vip.vip_set([r["user_id"] for r in rows])
    return web.json_response({"items": [_chat_item(r, user["id"], vips) for r in rows]})


async def api_chat_send(request: web.Request):
    user = _auth(request)
    if (resp := await _muted_resp(user)) is not None:
        return resp
    body = await request.json()
    text = re.sub(r"[ \t]+", " ", str(body.get("text") or "")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        return _bad("Xabar yozing")
    if len(text) > CHAT_MAX_LEN:
        return _bad(f"Xabar {CHAT_MAX_LEN} belgidan oshmasligi kerak")
    if time.time() - await db.chat_last_at(user["id"]) < CHAT_COOLDOWN:
        return web.json_response({"error": "Biroz sekinroq yozing 🙂"}, status=429)
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x).strip()
    name = (name or (f"@{user['username']}" if user.get("username") else "") or "Foydalanuvchi")[:40]
    msg = await db.chat_add(user["id"], name, text)
    return web.json_response({"ok": True, "item": {**msg, "mine": True, "staff": perms.is_staff(user["id"])}})


MOD_MAX_MIN = 525600  # 1 yil


async def api_chat_mod(request: web.Request):
    """Chat moderatsiyasi (umumiy chat va teatr chati): xabarni o'chirish, mute, ban va ularni bekor qilish."""
    body = await request.json()
    action = str(body.get("action") or "")
    if action == "delete":
        _admin(request)
        mid = int(body.get("id") or 0)
        if body.get("scope") == "theatre":
            await _th_chat_remove(mid)
        else:
            await db.chat_delete(mid)
        return web.json_response({"ok": True})
    if action not in ("mute", "ban", "unmute", "unban"):
        return _bad("Noto'g'ri amal")
    _admin(request, "ban")
    uid = int(body.get("uid") or 0)
    if not uid:
        return _bad("Foydalanuvchi topilmadi")
    if perms.is_staff(uid):
        return _bad("Adminga mute yoki ban qo'llab bo'lmaydi")
    if action in ("unmute", "unban"):
        await db.set_punish(uid, action[2:], 0)
        return web.json_response({"ok": True})
    if body.get("forever"):
        until = db.FOREVER
    else:
        minutes = int(body.get("minutes") or 0)
        if not 1 <= minutes <= MOD_MAX_MIN:
            return _bad("Muddatni tanlang")
        until = int(time.time()) + minutes * 60
    await db.set_punish(uid, action, until)
    return web.json_response({"ok": True, "until": until})


async def api_admin_punish(request: web.Request):
    """Foydalanuvchining hozirgi jazolari (moderatsiya oynasi uchun)."""
    _admin(request, "ban")
    uid = int(request.query.get("uid", "0") or 0)
    return web.json_response({**await db.punish_status(uid), "staff": perms.is_staff(uid)})


async def api_chat_delete(request: web.Request):
    _admin(request)
    body = await request.json()
    await db.chat_delete(int(body.get("id", 0)))
    return web.json_response({"ok": True})


# ───────────── Foydalanuvchi API ─────────────
async def api_me(request: web.Request):
    user = _auth(request)
    name = (user.get("first_name") or "").strip() or "do'st"
    return web.json_response({
        "name": name,
        "theme": await _theme(),
        "favs": await db.fav_ids(user["id"]),
        "uid": user["id"],
        "theatre": theatre.is_on(),
        "theatre_ready": theatre.available(),
        "admin": perms.is_staff(user["id"]),
        "contact": _contact_link(),
        "vip": {"on": vip.ON, "until": await db.vip_until(user["id"]), "bot": (await request.app["bot"].me()).username,
                "plans": await vip.plans()},
        **perms.info(user["id"]),
    })


async def api_movies(request: web.Request):
    user = _auth(request)
    q = request.query
    sort = q.get("sort", "new")
    offset = max(int(q.get("offset", "0") or 0), 0)
    limit = min(max(int(q.get("limit", "20") or 20), 1), 50)
    fav_user = user["id"] if sort == "fav" else None
    rows, total = await db.search_items(
        q.get("q", "")[:80], q.get("genre", "")[:40], "new" if sort == "fav" else sort,
        limit, offset, fav_user, q.get("kind", ""),
    )
    data = {"items": [_brief(r) for r in rows], "total": total}
    if offset == 0:
        data["genres"] = await db.genres()
    return web.json_response(data)


async def api_admin_items(request: web.Request):
    """Admin «Kinolar» bo'limi: tur va rasm holati bo'yicha ajratilgan ro'yxat + sonlar jadvali."""
    _admin(request, "add", "delete", "publish")
    q = request.query
    offset = max(int(q.get("offset", "0") or 0), 0)
    limit = min(max(int(q.get("limit", "30") or 30), 1), 50)
    qq = q.get("q", "")[:80]
    rows, total = await db.search_items(qq, "", "admin", limit, offset, None, q.get("kind", ""), q.get("cover", ""))
    data = {"items": [_brief(r) for r in rows], "total": total}
    if offset == 0:
        data["matrix"] = await db.item_matrix(qq)
    return web.json_response(data)


async def api_movie(request: web.Request):
    user = _auth(request)
    movie = await db.get_movie(int(request.match_info["id"]))
    if not movie or (movie["status"] != "live" and not perms.is_staff(user["id"])):
        raise web.HTTPNotFound(reason="not found")
    d = _brief(movie)
    d["description"] = movie["description"]
    d["language"] = movie["language"]
    info = await vip.pro_info(movie)
    if info:
        d["owned"] = perms.is_staff(user["id"]) or await db.buy_has(user["id"], info["t"], info["id"])
    return web.json_response(d)


async def api_series(request: web.Request):
    """Qismli kontent kartochkasi: ma'lumot + chiqarilgan qismlar ro'yxati."""
    user = _auth(request)
    ser = await db.get_series(int(request.match_info["id"]))
    if not ser:
        raise web.HTTPNotFound(reason="not found")
    is_admin = perms.is_staff(user["id"])
    eps = await db.episodes(ser["id"], live_only=not is_admin)
    if not eps:
        raise web.HTTPNotFound(reason="not found")
    watched = await db.watched_episodes(user["id"], ser["id"])
    pro_price = ser["pro_price"] if vip.ON else 0
    owned = bool(pro_price) and (is_admin or await db.buy_has(user["id"], "s", ser["id"]))
    return web.json_response({
        "price": pro_price, "owned": owned,
        "last": watched[0]["mid"] if watched else None,
        "seen": [w["mid"] for w in watched],
        "t": "s", "id": ser["id"], "fk": -ser["id"], "kind": ser["kind"],
        "code": db.series_code(ser["kind"], ser["id"]),
        "title": ser["title"], "year": ser["year"], "genre": ser["genre"], "country": ser["country"],
        "language": ser["language"], "description": ser["description"], "quality": "",
        "views": sum(e["views"] for e in eps), "poster": bool(ser["poster_id"]),
        "cv": _cv(ser["cover_id"]),
        "pay": ("pro" if pro_price else "vip" if ser["paid"] else "free") if vip.ON else "",
        "eps": [{"id": e["id"], "ep": e["ep"], "views": e["views"], "live": e["status"] == "live",
                 "pro": bool(pro_price and not owned) or bool(vip.ON and not pro_price and ser["paid"]
                                                              and (e["ep"] or 0) > (ser["free_eps"] or 0))} for e in eps],
    })


def _ad_json(a) -> dict:
    return {"ad": True, "id": a["id"], "title": a["title"], "url": a["url"], "btn": a["btn"] or "O'tish",
            "img": f"/api/ad/{a['id']}?v={_cv(a['image'])}"}


async def _hero_slides() -> list[dict]:
    """Slayder (4 ta kontent):
       1) eng oxirgi qo'yilgan — NEW; 2) oxirgi 24 soatda eng ko'p ko'rilgan — TOP;
       3-4) tasodifiy: pullik rejim yoqilgan va pullik kontent bo'lsa 1 ta pullik + 1 ta tekin, aks holda 2 ta tekin."""
    used: set = set()
    key = lambda r: (r["t"], r["id"])
    out: list[dict] = []

    def put(r, lbl: str = "") -> None:
        used.add(key(r))
        b = _brief(r)
        if lbl:
            b["lbl"] = lbl
        out.append(b)

    for r in await db.hero_latest(1):
        put(r, "new")
    pick = None
    for src in (await db.hero_top_since(int(time.time()) - 86400, 6), await db.hero_top_all(6)):
        pick = next((r for r in src if key(r) not in used), None)
        if pick:
            break
    if pick:
        put(pick, "top")

    pros = [r for r in await db.hero_random(1, 10) if key(r) not in used] if vip.ON else []
    frees = [r for r in await db.hero_random(0, 10) if key(r) not in used]
    rnd = ([pros[0]] + frees[:1]) if pros else frees[:2]
    if len(rnd) < 2:   # yetmasa — qolganlaridan to'ldiramiz
        have = {key(r) for r in rnd}
        for r in pros + frees:
            if len(rnd) >= 2:
                break
            if key(r) not in have:
                rnd.append(r); have.add(key(r))
    for r in rnd:
        put(r)
    for b in out:  # slayd uchun: tavsif va davomiylik (katalog kartochkasida bular yo'q)
        try:
            if b["t"] == "s":
                ser = await db.get_series(b["id"])
                eps = await db.episodes(b["id"])
                desc, dur = (ser["description"] if ser else ""), (eps[0]["duration"] if eps else 0)
            else:
                mv = await db.get_movie(b["id"])
                desc, dur = (mv["description"] if mv else ""), (mv["duration"] if mv else 0)
            desc = (desc or "").strip()
            b["desc"] = desc if len(desc) <= 260 else desc[:259].rstrip() + "…"
            b["dur"] = int(dur or 0)
        except Exception:
            b["desc"], b["dur"] = "", 0
    return out


async def api_hero(request: web.Request):
    """Katalog tepasidagi slayder. Reklama bo'lsa — eng boshida; reklama umuman yo'q bo'lsa — oxirida «reklama joyi»."""
    _auth(request)
    items = await _hero_slides()
    ads = [_ad_json(a) for a in await db.ads_list(True) if a["image"] and a["url"]]
    out = ads + items if ads else items + [{"slot": True}]
    return web.json_response({"items": out})


async def api_ad_image(request: web.Request):
    ad = await db.ad_get(int(request.match_info["id"]))
    if not ad or not ad["image"]:
        raise web.HTTPNotFound(reason="no image")
    return await _poster_response(request, ad["image"])


async def api_ad_click(request: web.Request):
    _auth(request)
    await db.ad_click(int(request.match_info["id"]))
    return web.json_response({"ok": True})


def _clean_url(u: str) -> str:
    u = (u or "").strip()
    if u.startswith("@"):
        u = "https://t.me/" + u[1:]
    elif re.match(r"^(t\.me|telegram\.me)/", u, re.I):
        u = "https://" + u
    return u if re.match(r"^https?://[^\s]+$", u, re.I) and len(u) <= 500 else ""


def _ad_admin(a) -> dict:
    exp, now = a["expires_at"] or 0, int(time.time())
    return {"id": a["id"], "title": a["title"], "url": a["url"], "btn": a["btn"], "active": bool(a["active"]),
            "clicks": a["clicks"], "img": f"/api/ad/{a['id']}?v={_cv(a['image'])}",
            "expires": exp, "expired": bool(exp and exp <= now), "left": max(exp - now, 0) if exp else 0}


def _ad_expiry(b: dict) -> int | None:
    """Tanlangan muddat: 'days' (0 — cheksiz) yoki 'until' (aniq sana-vaqt, unix). Yo'q bo'lsa — None."""
    now = int(time.time())
    if b.get("until"):
        exp = int(b["until"])
        if exp <= now:
            raise ValueError("past")
        return exp
    if "days" in b and b["days"] is not None:
        days = float(b["days"])
        return int(now + days * 86400) if days > 0 else 0
    return None


async def api_admin_ads(request: web.Request):
    _admin(request, "settings")
    return web.json_response({"items": [_ad_admin(a) for a in await db.ads_list()]})


async def api_admin_ad_save(request: web.Request):
    """Reklama qo'shish (id yo'q) yoki tahrirlash (id bor)."""
    _admin(request, "settings")
    b = await request.json()
    d = {}
    if "title" in b:
        d["title"] = str(b["title"]).strip()[:80]
    if "btn" in b:
        d["btn"] = str(b["btn"]).strip()[:24]
    if "url" in b:
        d["url"] = _clean_url(b["url"])
        if not d["url"]:
            return _bad("Havola noto'g'ri. https://... yoki @kanal ko'rinishida yozing")
    if "image" in b and b["image"]:
        d["image"] = str(b["image"])
    if "active" in b:
        d["active"] = 1 if b["active"] else 0
    try:
        exp = _ad_expiry(b)
    except ValueError:
        return _bad("Tugash vaqti o'tib ketgan. Kelajakdagi sana va vaqtni tanlang")
    if exp is not None:
        d["expires_at"] = exp
    aid = int(b.get("id") or 0)
    if aid:
        if not await db.ad_get(aid):
            raise web.HTTPNotFound(reason="not found")
        await db.ad_update(aid, d)
    else:
        if not d.get("image"):
            return _bad("Reklama rasmini tanlang")
        if not d.get("url"):
            return _bad("Havolani yozing")
        if exp is None:
            return _bad("Reklama qancha turishini tanlang")
        aid = await db.ad_add(d.get("title", ""), d["image"], d["url"], d.get("btn", ""), exp)
    return web.json_response({"ok": True, "id": aid})


async def api_admin_ad_delete(request: web.Request):
    _admin(request, "settings")
    b = await request.json()
    await db.ad_delete(int(b.get("id", 0)))
    return web.json_response({"ok": True})


async def api_similar(request: web.Request):
    """Kino oynasidagi «O'xshash kontent»."""
    _auth(request)
    t, iid = request.match_info["t"], int(request.match_info["id"])
    src = await (db.get_series(iid) if t == "s" else db.get_movie(iid))
    if not src:
        raise web.HTTPNotFound(reason="not found")
    rows = await db.similar_items(t, iid, src["kind"], src["genre"], src["year"], 8)
    return web.json_response({"items": [_brief(r) for r in rows]})


async def api_send(request: web.Request):
    user = _auth(request)
    body = await request.json()
    mid = int(body.get("id", 0))
    status = await svc.deliver(request.app["bot"], user["id"], user["id"], mid)
    return web.json_response({"ok": status == "ok", "status": status})


async def api_fav(request: web.Request):
    user = _auth(request)
    body = await request.json()
    key = int(body.get("id", 0))  # kino: musbat id, qismli kontent: manfiy (-serial id)
    exists = await db.get_series(-key) if key < 0 else await db.get_movie(key)
    if not exists:
        raise web.HTTPNotFound(reason="not found")
    return web.json_response({"fav": await db.toggle_fav(user["id"], key)})


async def _poster_response(request: web.Request, poster_id: str):
    config.POSTER_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.md5(poster_id.encode()).hexdigest()
    path = config.POSTER_DIR / f"{key}.jpg"
    if not path.exists():
        tmp = path.with_suffix(".part")
        await request.app["bot"].download(poster_id, destination=tmp)
        tmp.replace(path)
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=86400", "Content-Type": "image/jpeg"})


async def api_poster(request: web.Request):
    movie = await db.get_movie(int(request.match_info["id"]))
    if not movie or not movie["poster_id"]:
        raise web.HTTPNotFound(reason="no poster")
    return await _poster_response(request, movie["poster_id"])


async def api_sposter(request: web.Request):
    ser = await db.get_series(int(request.match_info["id"]))
    if not ser or not ser["poster_id"]:
        raise web.HTTPNotFound(reason="no poster")
    return await _poster_response(request, ser["poster_id"])


async def api_cover(request: web.Request):
    movie = await db.get_movie(int(request.match_info["id"]))
    if not movie or not movie["cover_id"]:
        raise web.HTTPNotFound(reason="no cover")
    return await _poster_response(request, movie["cover_id"])


async def api_scover(request: web.Request):
    ser = await db.get_series(int(request.match_info["id"]))
    if not ser or not ser["cover_id"]:
        raise web.HTTPNotFound(reason="no cover")
    return await _poster_response(request, ser["cover_id"])


async def logo(request: web.Request):
    """Muqova qo'yilmagan joylarda ko'rinadigan logo."""
    return web.FileResponse(config.WEBAPP_DIR / "logo.png", headers={"Cache-Control": "public, max-age=604800"})


# ───────────── Admin API ─────────────
def _day_start() -> int:
    """Bugungi kun boshi (00:00) — bot vaqt zonasida."""
    from datetime import datetime, timedelta, timezone
    n = datetime.now(timezone(timedelta(hours=config.THEATRE_TZ)))
    return int(n.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


async def api_admin_punished(request: web.Request):
    """Admin panel: mute list va ban list."""
    _admin(request, "ban")
    out = {}
    for kind in ("mute", "ban"):
        out[kind] = [{"uid": r["id"], "name": r["full_name"] or "", "username": r["username"] or "",
                      "until": r["until"]} for r in await db.punished_list(kind)]
    return web.json_response(out)


async def api_admin_stats(request: web.Request):
    _admin(request, "stats")
    s = await db.stats(_day_start())
    s["top"] = [{"id": t["id"], "code": db.movie_code(t), "title": t["title"], "views": t["views"]} for t in s["top"]]
    return web.json_response(s)


async def _settings_payload() -> dict:
    out = {k: (await db.get_setting(k, default)) == "1" for k, default in SETTING_DEFAULTS.items()}
    out["theme"] = await _theme()
    out["channels"] = [
        {"id": r["id"], "title": r["title"] or str(r["chat_id"]), "link": r["link"]}
        for r in await db.list_channels()
    ]
    out["sources"] = []
    for r in await db.list_sources():
        ser = await db.get_series(r["series_id"]) if r["series_id"] else None
        out["sources"].append({
            "id": r["chat_id"], "title": r["title"] or str(r["chat_id"]), "kind": r["kind"],
            "series_id": r["series_id"],
            "series": ({"title": ser["title"], "code": db.series_code(ser["kind"], ser["id"])} if ser else None),
        })
    return out


async def api_admin_settings(request: web.Request):
    _admin(request, "settings", "channels")
    return web.json_response(await _settings_payload())


async def api_admin_setting(request: web.Request):
    _admin(request, "settings")
    body = await request.json()
    key = body.get("key")
    if key not in SETTING_DEFAULTS:
        return _bad("Noma'lum sozlama")
    await db.set_setting(key, "1" if body.get("value") else "0")
    if key == "theatre_on":
        theatre.set_enabled(bool(body.get("value")))
    elif key == "vip_on":
        vip.set_on(bool(body.get("value")))
    return web.json_response({"ok": True})


async def api_admin_theme(request: web.Request):
    """Dizayn rejimini tanlash — hamma foydalanuvchida shu dizayn ko'rinadi."""
    _admin(request, "settings")
    body = await request.json()
    theme = body.get("theme")
    if theme not in THEMES:
        return _bad("Noma'lum dizayn")
    await db.set_setting("theme", theme)
    return web.json_response({"ok": True, "theme": theme})


async def api_admin_channel_add(request: web.Request):
    """Majburiy obuna kanali qo'shish."""
    _admin(request, "channels")
    body = await request.json()
    ref = svc.parse_ref(str(body.get("ref", "")))
    if body.get("kind") != "sub" or ref is None:
        return _bad("Kanalni @username yoki -100... ID ko'rinishida yozing")
    try:
        chat_id, title, link = await svc.resolve_channel(request.app["bot"], ref, need_link=True)
    except ValueError as e:
        return _bad(str(e))
    await db.add_channel(chat_id, title, link)
    return web.json_response(await _settings_payload())


async def api_admin_source_add(request: web.Request):
    """Avto-yuklash kanalini ulaydi. kind: kino|multfilm|anime|serial; parts=true yoki serial → qismli
    (bunda serial ma'lumotlari shu yerda bir marta kiritiladi, qismlar kanaldan raqam bo'yicha olinadi)."""
    user = _admin(request, "channels")
    body = await request.json()
    ref = svc.parse_ref(str(body.get("ref", "")))
    kind = body.get("kind")
    if kind not in db.KINDS:
        return _bad("Turini tanlang")
    _kind_ok(user, kind)
    if ref is None:
        return _bad("Kanalni @username yoki -100... ID ko'rinishida yozing")
    parts = bool(body.get("parts")) or kind == "serial"
    meta = None
    if parts:
        try:
            meta = _clean_meta(body)
        except ValueError as e:
            return _bad(str(e))
    try:
        chat_id, title, _ = await svc.resolve_channel(request.app["bot"], ref, need_link=False, channel_only=True)
    except ValueError as e:
        return _bad(str(e))
    if parts:
        old = await db.get_source(chat_id)
        if old and old["series_id"] and await db.get_series(old["series_id"]):
            return _bad("Bu kanal allaqachon boshqa serialga ulangan. Avval uni olib tashlang.")
        sid = await db.add_series({**meta, "kind": kind})
        await db.add_source(chat_id, title, kind, sid)
    else:
        await db.add_source(chat_id, title, kind, None)
    return web.json_response(await _settings_payload())


async def api_admin_channel_del(request: web.Request):
    _admin(request, "channels")
    body = await request.json()
    kind, ident = body.get("kind"), int(body.get("id", 0))
    if kind == "sub":
        await db.del_channel(ident)
    elif kind == "source":
        await db.del_source(ident)
    else:
        return _bad("Noma'lum tur")
    return web.json_response(await _settings_payload())


async def api_admin_pending(request: web.Request):
    """Qo'shishga tayyor kontent: guruhlar (kino/multfilm/anime ro'yxatlari va har serial alohida)."""
    user = _admin(request, "publish")
    groups = []
    for g in await db.pending_groups():
        if not perms.can_kind(user["id"], g["kind"]):
            continue
        ser = g["series"]
        groups.append({
            "key": g["key"], "kind": g["kind"], "count": len(g["rows"]),
            "series": ({"id": ser["id"], "title": ser["title"], "code": db.series_code(ser["kind"], ser["id"]),
                        "cover": bool(ser["cover_id"])} if ser else None),
            # Serialda rasm bitta — serialning o'zida; qismlar alohida rasm so'ralmaydi
            "missing": ((0 if ser["cover_id"] else 1) if ser else sum(1 for r in g["rows"] if not r["cover_id"])),
            "items": [{"id": r["id"], "code": db.movie_code(r), "title": r["title"], "year": r["year"], "genre": r["genre"],
                       "ep": r["ep"], "cover": (None if ser else bool(r["cover_id"]))}
                      for r in g["rows"]],
        })
    return web.json_response({"groups": groups, "total": sum(g["count"] for g in groups),
                              "missing": sum(g["missing"] for g in groups)})


async def api_admin_publish(request: web.Request):
    """Kutilayotganlarni chiqaradi. scope: all | single | single:<tur> | s:<serial id>.
    notify=true bo'lsa foydalanuvchilarga bitta umumiy xabar ketadi."""
    user = _admin(request, "publish")
    body = await request.json()
    bot, notify, scope = request.app["bot"], bool(body.get("notify")), str(body.get("scope") or "all")
    try:
        left = 0
        if perms.is_full(user["id"]):
            n, left = await broadcast.publish_all(bot, notify, scope)
        else:  # cheklangan admin faqat o'z bo'limlaridagilarni chiqaradi
            keys = [g["key"] for g in await db.pending_groups() if perms.can_kind(user["id"], g["kind"])]
            if scope == "all":
                targets = keys
            elif scope == "single":
                targets = [k for k in keys if k.startswith("single:")]
            elif scope in keys:
                targets = [scope]
            else:
                return _bad("Bu bo'limga ruxsat yo'q yoki kutilayotgan yo'q")
            n = 0
            for k in targets:
                pn, pl = await broadcast.publish_all(bot, notify, k)
                n, left = n + pn, left + pl
    except ValueError:
        return _bad("Noto'g'ri doira")
    return web.json_response({"published": n, "left": left})


async def api_admin_movie_get(request: web.Request):
    user = _admin(request, "add")
    m = await db.get_movie(int(request.match_info["id"]))
    if not m:
        raise web.HTTPNotFound(reason="not found")
    _kind_ok(user, m["kind"])
    return web.json_response({"movie": {"id": m["id"], "code": db.movie_code(m), **{k: m[k] or "" for k in db.META_FIELDS}, "cv": _cv(m["cover_id"]),
                                         "paid": "1" if m["paid"] else "",
                                         "pro_price": str(m["pro_price"]) if m["pro_price"] else "",
                                         "pro_card": str(m["pro_card"]) if m["pro_card"] else ""}})


async def api_admin_series_get(request: web.Request):
    user = _admin(request, "add")
    ser = await db.get_series(int(request.match_info["id"]))
    if not ser:
        raise web.HTTPNotFound(reason="not found")
    _kind_ok(user, ser["kind"])
    eps = await db.episodes(ser["id"], live_only=False)
    return web.json_response({
        "series": {"id": ser["id"], "kind": ser["kind"], "code": db.series_code(ser["kind"], ser["id"]),
                   **{k: (ser[k] if k in ser.keys() else "") or "" for k in db.META_FIELDS}, "cv": _cv(ser["cover_id"]),
                   "paid": "1" if ser["paid"] else "", "free_eps": "1" if ser["free_eps"] else "",
                   "pro_price": str(ser["pro_price"]) if ser["pro_price"] else "",
                   "pro_card": str(ser["pro_card"]) if ser["pro_card"] else ""},
        "eps": [{"id": e["id"], "ep": e["ep"], "live": e["status"] == "live"} for e in eps],
    })


async def api_admin_series_update(request: web.Request):
    user = _admin(request, "add")
    body = await request.json()
    sid = int(body.get("id", 0))
    ser = await db.get_series(sid)
    if not ser:
        raise web.HTTPNotFound(reason="not found")
    _kind_ok(user, ser["kind"])
    try:
        d = _clean_meta(body)
    except ValueError as e:
        return _bad(str(e))
    await db.update_series_meta(sid, d)
    if d.get("cover"):
        await db.set_series_cover(sid, d["cover"])
        await db.set_series_poster(sid, d["cover"])
    elif "cover" in d:
        await db.set_series_cover(sid, "")
    return web.json_response({"ok": True})


async def api_admin_series_del(request: web.Request):
    user = _admin(request, "delete")
    body = await request.json()
    sid = int(body.get("id", 0))
    ser = await db.get_series(sid)
    if not ser:
        raise web.HTTPNotFound(reason="not found")
    _kind_ok(user, ser["kind"])
    await db.delete_series(sid)
    return web.json_response({"ok": True})


async def api_admin_series_list(request: web.Request):
    """Qo'lda qism qo'shishda serial tanlash ro'yxati."""
    user = _admin(request, "add")
    return web.json_response({"items": [
        {"id": r["id"], "kind": r["kind"], "code": db.series_code(r["kind"], r["id"]), "title": r["title"],
         "eps": r["eps"], "last_ep": r["last_ep"] or 0}
        for r in await db.list_series() if perms.can_kind(user["id"], r["kind"])
    ]})


async def api_admin_movie_update(request: web.Request):
    user = _admin(request, "add")
    body = await request.json()
    mid = int(body.get("id", 0))
    mv = await db.get_movie(mid)
    if not mv:
        raise web.HTTPNotFound(reason="not found")
    _kind_ok(user, mv["kind"])
    try:
        d = _clean_meta(body)
    except ValueError as e:
        return _bad(str(e))
    await db.replace_movie_meta(mid, d)
    if d.get("cover"):
        await db.set_cover(mid, d["cover"])
        await db.set_poster(mid, d["cover"])
    elif "cover" in d:
        await db.set_cover(mid, "")
    return web.json_response({"ok": True})


async def api_admin_movie_del(request: web.Request):
    user = _admin(request, "delete")
    body = await request.json()
    mid = int(body.get("id", 0))
    mv = await db.get_movie(mid)
    if not mv:
        raise web.HTTPNotFound(reason="not found")
    _kind_ok(user, mv["kind"])
    await db.delete_movie(mid)
    return web.json_response({"ok": True})


def _clean_cover(value) -> str:
    v = str(value or "").strip()
    if v and not re.fullmatch(r"[A-Za-z0-9_-]{20,250}", v):
        raise ValueError("Muqova rasmi noto'g'ri. Qaytadan yuklang")
    return v


def _clean_meta(body: dict) -> dict:
    out = {}
    for key, limit in DRAFT_LIMITS.items():
        value = re.sub(r"[ \t]+", " ", str(body.get(key) or "")).strip()[:limit]
        if value:
            out[key] = value
    if "cover" in body:  # bor bo'lsa: file_id (almashtirish) yoki bo'sh (olib tashlash)
        out["cover"] = _clean_cover(body.get("cover"))
    paid = str(body.get("paid") or "").strip().lower() in ("1", "true")
    out["paid"] = 1 if paid else 0
    # PRO: alohida sotiladigan kontent (narx > 0). PRO bo'lsa VIP belgisi o'chadi — bittasi tanlanadi.
    raw_price = re.sub(r"\D", "", str(body.get("pro_price") or ""))
    price = int(raw_price) if raw_price else 0
    if price and not 1000 <= price <= 100_000_000:
        raise ValueError("PRO narxi 1 000 dan 100 000 000 so'mgacha bo'lsin")
    out["pro_price"], out["pro_card"] = price, (int(re.sub(r"\D", "", str(body.get("pro_card") or "")) or 0) if price else 0)
    if price:
        paid = False
        out["paid"] = 0
    # Serialda «tekin qo'shish»: pullik bo'lsa dastlabki FREE_EPS qism tekin
    out["free_eps"] = vip.FREE_EPS if paid and str(body.get("free_eps") or "").strip().lower() in ("1", "true") else 0
    if not out.get("title"):
        raise ValueError("Nomini kiriting")
    if out.get("year") and not re.fullmatch(r"(18|19|20)\d{2}", out["year"]):
        raise ValueError("Yil noto'g'ri (masalan: 2024)")
    if out.get("genre"):
        out["genre"] = ", ".join(g.strip() for g in out["genre"].split(",") if g.strip())
    return out


async def _clean_draft(body: dict, user: dict) -> dict:
    """Qo'lda qo'shish qoralamasi. Yakka kino/multfilm/anime yoki qismli kontentning bitta qismi."""
    kind = body.get("kind") or "kino"
    if kind not in db.KINDS:
        raise ValueError("Turini tanlang")
    _kind_ok(user, kind)
    parts = bool(body.get("parts")) or kind == "serial"
    if not parts:
        out = _clean_meta(body)
        if not out.get("cover"):
            raise ValueError("Avval rasm (muqova) yuklang — rasmsiz kino qo'shib bo'lmaydi")
        out["kind"] = kind
        pi = body.get("premiere_idx")
        if pi not in (None, ""):
            try:
                pi = int(pi)
            except (TypeError, ValueError):
                raise ValueError("Premyera vaqti noto'g'ri")
            if not 0 <= pi < theatre.SLOTS:
                raise ValueError("Premyera vaqti noto'g'ri")
            if not theatre.available():
                raise ValueError("Premyera uchun teatr ulanmagan (API_ID / API_HASH)")
            if await db.q_one("SELECT 1 FROM movies WHERE status='premiere' AND premiere_at=?", (theatre.premiere_ts(pi),)):
                raise ValueError("Bu vaqtga boshqa premyera qo'yilgan")
            out["premiere_idx"] = pi
        return out
    try:
        ep = int(body.get("ep") or 0)
    except (TypeError, ValueError):
        ep = 0
    if not 1 <= ep <= 9999:
        raise ValueError("Qism raqamini kiriting (masalan: 1)")
    sid = int(body.get("series_id") or 0)
    if sid:
        ser = await db.get_series(sid)
        if not ser:
            raise ValueError("Serial topilmadi")
        _kind_ok(user, ser["kind"])
        if not ser["cover_id"]:
            raise ValueError("Bu serialda rasm (muqova) yo'q. Avval «Kontent» bo'limida ✏️ orqali rasm qo'ying")
    else:  # yangi serial yaratiladi (qismi chiqmaguncha katalogda ko'rinmaydi)
        meta = _clean_meta(body)
        if not meta.get("cover"):
            raise ValueError("Avval rasm (muqova) yuklang — rasmsiz qo'shib bo'lmaydi")
        sid = await db.add_series({**meta, "kind": kind})
        ser = await db.get_series(sid)
    if await db.episode_by_no(sid, ep):
        raise ValueError(f"{ep}-qism allaqachon bor")
    return {"title": ser["title"], "kind": ser["kind"], "series_id": sid, "ep": ep}


async def api_admin_draft_get(request: web.Request):
    user = _admin(request, "add")
    return web.json_response({"draft": await db.get_draft(user["id"])})


async def api_admin_draft_save(request: web.Request):
    """Mini App'da kiritilgan ma'lumotlarni saqlaydi va botda adminga «endi videoni yuboring» deb yozadi."""
    user = _admin(request, "add")
    try:
        draft = await _clean_draft(await request.json(), user)
    except ValueError as e:
        return _bad(str(e))
    await db.save_draft(user["id"], draft)
    title = html.escape(draft["title"]) + (f" ({draft['year']})" if draft.get("year") else "")
    if draft.get("series_id"):
        title += f" — {draft['ep']}-qism"
    if draft.get("premiere_idx") is not None:
        title += f"\n⭐ Premyera: ertaga {theatre.premiere_slots()[draft['premiere_idx']]['time']} da teatrda"
    text = (
        "✅ <b>Kino ma'lumotlari saqlandi!</b>\n\n"
        f"🎬 <b>{title}</b>\n\n"
        "📤 Endi <b>kinoni (video yoki fayl)</b> shu yerga yuboring — bot uni avtomatik qo'shadi.\n"
        "❌ Bekor qilish: /cancel"
    )
    notified = True
    try:
        await request.app["bot"].send_message(user["id"], text)
    except Exception as e:
        notified = False
        logging.warning("Draft xabari yuborilmadi: %s", e)
    return web.json_response({"ok": True, "notified": notified})


async def api_admin_draft_clear(request: web.Request):
    user = _admin(request, "add")
    await db.clear_draft(user["id"])
    return web.json_response({"ok": True})


# ───────────── Adminlarni boshqarish (Sozlamalar → Adminlar) ─────────────
async def _name_of(bot, uid: int) -> str:
    """Ismi: avval bazadan (botga kirgan bo'lsa), keyin Telegramdan, bo'lmasa ID."""
    name = await db.user_name(uid)
    if name:
        return name
    try:
        chat = await bot.get_chat(uid)
        name = (chat.full_name or "").strip() or (f"@{chat.username}" if chat.username else "")
        if name:
            return name
    except Exception:
        pass
    return f"ID {uid}"


async def _admins_payload(request: web.Request, viewer_id: int) -> dict:
    bot, viewer = request.app["bot"], perms.role(viewer_id)
    items = [{"id": oid, "name": await _name_of(bot, oid), "role": "owner", "label": perms.ROLE_LABEL["owner"],
              "info": perms.summary("owner", [], []), "perms": [], "sections": [], "editable": False}
             for oid in sorted(config.ADMIN_IDS)]
    for a in await db.list_admins():
        role = "senior" if a["role"] == "senior" else "admin"
        pl = [p for p in (a["perms"] or "").split(",") if p in perms.PERMS]
        sl = [x for x in (a["sections"] or "").split(",") if x in perms.SECTIONS]
        items.append({
            "id": a["id"], "name": a["name"] or f"ID {a['id']}", "role": role, "label": perms.ROLE_LABEL[role],
            "info": perms.summary(role, pl, sl), "perms": pl, "sections": sl,
            "editable": a["id"] != viewer_id and (viewer == "owner" or role == "admin"),
        })
    return {"admins": items, "perm_labels": perms.PERMS, "section_labels": perms.SECTIONS,
            "can_senior": viewer == "owner"}


async def api_admin_admins(request: web.Request):
    user = _manager(request)
    return web.json_response(await _admins_payload(request, user["id"]))


async def api_admin_admin_save(request: web.Request):
    user = _manager(request)
    body = await request.json()
    try:
        aid = int(str(body.get("id", "")).strip())
    except ValueError:
        return _bad("Telegram ID raqamini kiriting (masalan: 123456789)")
    if aid <= 0:
        return _bad("ID noto'g'ri")
    viewer = perms.role(user["id"])
    if aid in config.ADMIN_IDS:
        return _bad("Bu bosh admin — uni o'zgartirib bo'lmaydi")
    if aid == user["id"]:
        return _bad("O'zingizning huquqlaringizni o'zgartira olmaysiz")
    role = "senior" if body.get("role") == "senior" else "admin"
    old = await db.get_admin(aid)
    if (role == "senior" or (old and old["role"] == "senior")) and viewer != "owner":
        return _bad("Starshiy adminni faqat bosh admin tayinlaydi va o'zgartiradi")
    plist = [p for p in perms.PERMS if p in (body.get("perms") or [])]
    slist = [x for x in perms.SECTIONS if x in (body.get("sections") or [])]
    if role == "admin":
        if not plist:
            return _bad("Kamida bitta huquq belgilang")
        if set(plist) & {"add", "delete", "publish"} and not slist:
            return _bad("Kamida bitta bo'lim belgilang (kino, serial, anime, multfilm)")
    else:
        plist, slist = [], []
    bot = request.app["bot"]
    name = (old["name"] if old and old["name"] else "") or await _name_of(bot, aid)
    await db.save_admin(aid, name, role, ",".join(plist), ",".join(slist), user["id"])
    await perms.reload()
    notified = True
    if not old:
        text = (
            "🎉 <b>Siz admin etib tayinlandingiz!</b>\n\n"
            f"Turi: <b>{perms.ROLE_LABEL[role]}</b>\n"
            f"{html.escape(perms.summary(role, plist, slist))}\n\n"
            "Admin panelni ochish uchun /admin yozing yoki katalogdagi ⚙️ tugmasini bosing."
        )
        try:
            await bot.send_message(aid, text)
        except Exception:
            notified = False
    return web.json_response({"ok": True, "notified": notified, **await _admins_payload(request, user["id"])})


async def api_admin_admin_del(request: web.Request):
    user = _manager(request)
    body = await request.json()
    try:
        aid = int(body.get("id", 0))
    except (TypeError, ValueError):
        return _bad("ID noto'g'ri")
    if aid in config.ADMIN_IDS:
        return _bad("Bosh adminni o'chirib bo'lmaydi")
    if aid == user["id"]:
        return _bad("O'zingizni o'chira olmaysiz")
    old = await db.get_admin(aid)
    if not old:
        raise web.HTTPNotFound(reason="not found")
    if old["role"] == "senior" and perms.role(user["id"]) != "owner":
        return _bad("Starshiy adminni faqat bosh admin o'chiradi")
    await db.del_admin(aid)
    await perms.reload()
    return web.json_response({"ok": True, **await _admins_payload(request, user["id"])})


# ───────────── Adminlar tarixi (faqat bosh admin) ─────────────
async def api_admin_history(request: web.Request):
    user = _admin(request)
    if perms.role(user["id"]) != "owner":
        raise web.HTTPForbidden(reason="Tarixni faqat bosh admin ko'ra oladi")
    before = int(request.query.get("before") or 0)
    admin_id = int(request.query.get("admin") or 0)
    rows = await db.alog_list(before, admin_id, 40)
    out = {
        "items": [{"id": r["id"], "uid": r["admin_id"], "name": r["admin_name"] or str(r["admin_id"]), "role": r["role"],
                   "cat": r["cat"], "action": r["action"], "detail": r["detail"], "at": r["at"]} for r in rows],
        "next": rows[-1]["id"] if len(rows) == 40 else 0,
    }
    if not before:
        out["admins"] = [{"id": a["admin_id"], "name": a["admin_name"] or str(a["admin_id"]), "n": a["n"]}
                         for a in await db.alog_admins()]
    return web.json_response(out)


# ───────────── VIP: tariflar, to'lov ma'lumoti, qo'lda berish ─────────────
async def api_admin_vip(request: web.Request):
    _admin(request, "settings")
    return web.json_response({
        "plans": await vip.plans(), "card": await vip.card_text(), **await db.vip_counts(),
        "click": bool(config.PAYMENT_TOKEN_CLICK), "payme": bool(config.PAYMENT_TOKEN_PAYME),
    })


async def api_admin_vip_config(request: web.Request):
    _admin(request, "settings")
    body = await request.json()
    try:
        plans = await vip.save_plans(body.get("plans"))
    except ValueError as e:
        return _bad(str(e))
    await db.set_setting("vip_card", str(body.get("card") or "").strip()[:500])
    return web.json_response({"ok": True, "plans": plans})


async def api_admin_vip_grant(request: web.Request):
    """days > 0 — VIP berish/uzaytirish, days = 0 — VIP ni olib tashlash."""
    _admin(request, "settings")
    body = await request.json()
    uid, days = int(body.get("uid") or 0), int(body.get("days") or 0)
    if uid <= 0:
        return _bad("Foydalanuvchi ID sini kiriting")
    if days == 0:
        if not await db.vip_revoke(uid):
            return _bad("Bunday foydalanuvchi botga kirmagan")
        return web.json_response({"ok": True, "until": 0})
    if not 1 <= days <= 3650:
        return _bad("Kun soni 1 dan 3650 gacha bo'lsin")
    until = await db.vip_grant(uid, days)
    if until is None:
        return _bad("Bunday foydalanuvchi botga kirmagan (u botga /start yozishi kerak)")
    try:
        await request.app["bot"].send_message(
            uid, f"🎁 Sizga <b>VIP</b> berildi: <b>{vip.until_text(until)}</b> gacha. 👑")
    except Exception:
        pass
    return web.json_response({"ok": True, "until": until})


# ───────────── PRO: dona sotib olish (Click / Payme / chek), to'lov kartalari ─────────────
MAX_CHECK = 10 * 1024 * 1024


def _card_view(c) -> dict:
    return {"id": c["id"], "title": c["title"], "number": c["number"], "holder": c["holder"], "active": bool(c["active"])}


async def _pro_card(info: dict):
    """Kontent uchun tanlangan karta (faol bo'lsa), yo'qsa — birinchi faol karta."""
    c = await db.card_get(info["card"]) if info["card"] else None
    if c and c["active"]:
        return c
    rows = await db.card_list(active_only=True)
    return rows[0] if rows else None


async def _pro_item(user: dict, t: str, item_id: int):
    if t not in ("m", "s"):
        raise web.HTTPNotFound(reason="not found")
    info = await vip.pro_info_of(t, item_id)
    if not info:
        raise web.HTTPNotFound(reason="Bu kontent sotilmaydi")
    owned = perms.is_staff(user["id"]) or await db.buy_has(user["id"], t, item_id)
    return info, owned


async def api_pay_options(request: web.Request):
    user = _auth(request)
    t, item_id = request.query.get("t", ""), int(request.query.get("id", 0) or 0)
    info, owned = await _pro_item(user, t, item_id)
    card = await _pro_card(info)
    return web.json_response({
        "t": t, "id": item_id, "title": info["title"], "price": info["price"], "owned": owned,
        "methods": list(vip.provider_tokens()),
        "card": {"title": card["title"], "number": card["number"], "holder": card["holder"]} if card else None,
        "pending": await db.pay_pending_item(user["id"], t, item_id),
    })


async def api_pay_invoice(request: web.Request):
    """Click / Payme: Telegram hisob-kitob havolasi yaratiladi; ilova `openInvoice` bilan ochadi."""
    user = _auth(request)
    body = await request.json()
    t, item_id, method = str(body.get("t") or ""), int(body.get("id") or 0), str(body.get("method") or "")
    info, owned = await _pro_item(user, t, item_id)
    if owned:
        return _bad("Bu kontent sizda allaqachon bor")
    token = vip.provider_tokens().get(method)
    if not token:
        return _bad("Bu to'lov usuli hozir ulanmagan")
    try:
        link = await request.app["bot"].create_invoice_link(
            title=info["title"][:32], description=f"{config.BOT_TITLE}: «{info['title']}» — PRO"[:250],
            payload=f"pro:{t}:{item_id}:{info['price']}:{method}", provider_token=token, currency="UZS",
            prices=[LabeledPrice(label="PRO", amount=info["price"] * 100)])
    except TelegramAPIError as e:
        logging.error("Hisob havolasi yaratilmadi (%s): %s", method, e)
        return _bad("To'lovni boshlab bo'lmadi. Keyinroq urinib ko'ring")
    return web.json_response({"link": link})


async def api_pay_check(request: web.Request):
    """Karta orqali to'lov: chek rasmi bosh va starshiy adminlarga ketadi, ular tasdiqlasa kontent ochiladi."""
    user = _auth(request)
    fields, data = {}, None
    reader = await request.multipart()
    async for part in reader:
        if part.name == "file" and part.filename:
            if not (part.headers.get("Content-Type") or "").lower().startswith("image/"):
                return _bad("Faqat rasm (skrinshot) yuboring")
            buf = bytearray()
            while True:
                chunk = await part.read_chunk(256 * 1024)
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > MAX_CHECK:
                    return _bad("Rasm 10 MB dan katta bo'lmasligi kerak")
            data = bytes(buf) or None
        elif part.name in ("t", "id"):
            fields[part.name] = (await part.text())[:20]
    if not data:
        return _bad("Chek rasmini tanlang")
    t, item_id = fields.get("t", ""), int(fields.get("id") or 0)
    info, owned = await _pro_item(user, t, item_id)
    if owned:
        return _bad("Bu kontent sizda allaqachon bor")
    card = await _pro_card(info)
    if not card:
        return _bad("Karta orqali to'lov hozir sozlanmagan")
    if await db.pay_pending_item(user["id"], t, item_id):
        return _bad("Bu kontent uchun chek allaqachon yuborilgan. Admin tekshirishini kuting")
    if await db.pay_pending_of(user["id"]) >= 3:
        return _bad("Sizda tekshirilmagan cheklar ko'p. Admin ko'rib chiqquncha kuting")
    from vip_handlers import submit_pro_check
    ok = await submit_pro_check(request.app["bot"], user, info, card, data)
    if not ok:
        return _bad("Chekni adminlarga yetkazib bo'lmadi. Keyinroq urinib ko'ring")
    return web.json_response({"ok": True})


def _clean_card(body: dict) -> tuple[str, str, str]:
    title = re.sub(r"\s+", " ", str(body.get("title") or "")).strip()[:40]
    number = re.sub(r"\D", "", str(body.get("number") or ""))
    holder = re.sub(r"\s+", " ", str(body.get("holder") or "")).strip()[:40]
    if not title:
        raise ValueError("Karta nomini kiriting (masalan: Humo — Alisher)")
    if not 13 <= len(number) <= 19:
        raise ValueError("Karta raqami noto'g'ri (16 ta raqam)")
    return title, number, holder


async def api_admin_cards(request: web.Request):
    _admin(request, "settings")
    return web.json_response({"items": [_card_view(c) for c in await db.card_list()],
                              "click": bool(config.PAYMENT_TOKEN_CLICK), "payme": bool(config.PAYMENT_TOKEN_PAYME)})


async def api_admin_cards_pick(request: web.Request):
    """Kontent formasida karta tanlash uchun: faqat nomlar (raqamsiz)."""
    _admin(request, "add")
    return web.json_response({"items": [{"id": c["id"], "title": c["title"]} for c in await db.card_list(active_only=True)]})


async def api_admin_cards_save(request: web.Request):
    _admin(request, "settings")
    body = await request.json()
    try:
        title, number, holder = _clean_card(body)
    except ValueError as e:
        return _bad(str(e))
    cid = await db.card_save(int(body.get("id") or 0), title, number, holder, bool(body.get("active", True)))
    return web.json_response({"ok": True, "id": cid})


async def api_admin_cards_delete(request: web.Request):
    _admin(request, "settings")
    body = await request.json()
    await db.card_delete(int(body.get("id") or 0))
    return web.json_response({"ok": True})


# ───────────── Muqova rasmi yuklash ─────────────
MAX_COVER = 10 * 1024 * 1024


async def api_admin_cover_upload(request: web.Request):
    """Rasmni Telegramga yuklab, file_id qaytaradi (poster kabi saqlanadi). Kontentga formani saqlaganda ulanadi."""
    user = _admin(request, "add")
    data = None
    reader = await request.multipart()
    async for part in reader:
        if part.name == "file" and part.filename:
            if not (part.headers.get("Content-Type") or "").lower().startswith("image/"):
                return _bad("Faqat rasm yuklash mumkin")
            buf = bytearray()
            while True:
                chunk = await part.read_chunk(256 * 1024)
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > MAX_COVER:
                    return _bad("Rasm 10 MB dan katta bo'lmasligi kerak")
            data = bytes(buf) or None
    if not data:
        return _bad("Rasm tanlang")
    try:
        sent = await request.app["bot"].send_photo(
            user["id"], BufferedInputFile(data, filename="cover.jpg"),
            caption="🖼 Muqova rasmi saqlandi", disable_notification=True)
    except TelegramAPIError as e:
        logging.warning("Muqova yuklanmadi: %s", e)
        return _bad("Rasm Telegramga yuklanmadi. Botga /start yozganingizni tekshiring.")
    return web.json_response({"ok": True, "cover": sent.photo[-1].file_id})


# ───────────── Hammaga xabar (matn / rasm / video) ─────────────
MAX_VIDEO = 49 * 1024 * 1024   # Telegram bot yuklash chegarasi ~50 MB
MAX_PHOTO = 10 * 1024 * 1024


async def api_admin_broadcast(request: web.Request):
    """Mini App'dan: matn + (ixtiyoriy) rasm yoki video. Avval adminning o'ziga yuboriladi, so'ng hammaga nusxalanadi."""
    user = _admin(request, "broadcast")
    text, media, fname, ctype = "", None, "media", ""
    reader = await request.multipart()
    async for part in reader:
        if part.name == "text":
            text = (await part.text()).strip()
        elif part.name == "file" and part.filename:
            fname, ctype = part.filename, (part.headers.get("Content-Type") or "").lower()
            buf = bytearray()
            while True:
                chunk = await part.read_chunk(256 * 1024)
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > MAX_VIDEO:
                    return _bad("Fayl 49 MB dan katta bo'lmasligi kerak")
            media = bytes(buf) or None
    if not text and not media:
        return _bad("Matn yozing yoki rasm/video tanlang")
    caption = html.escape(text)
    bot = request.app["bot"]
    try:
        if media:
            if len(caption) > 1024:
                return _bad("Rasm/video izohi 1024 belgidan oshmasligi kerak")
            file = BufferedInputFile(media, filename=fname)
            if ctype.startswith("image/"):
                if len(media) > MAX_PHOTO:
                    return _bad("Rasm 10 MB dan katta bo'lmasligi kerak")
                sent = await bot.send_photo(user["id"], file, caption=caption or None)
            elif ctype.startswith("video/"):
                sent = await bot.send_video(user["id"], file, caption=caption or None, supports_streaming=True)
            else:
                return _bad("Faqat rasm yoki video yuborish mumkin")
        else:
            if len(caption) > 4000:
                return _bad("Matn 4000 belgidan oshmasligi kerak")
            sent = await bot.send_message(user["id"], caption)
    except TelegramAPIError as e:
        logging.warning("Ommaviy xabar tayyorlanmadi: %s", e)
        return _bad("Telegram xabarni qabul qilmadi. Avval botga /start yozganingizni tekshiring.")
    total = len(await db.broadcast_ids())
    broadcast.spawn(broadcast.copy_all(bot, user["id"], user["id"], sent.message_id))
    return web.json_response({"ok": True, "total": total})


# ───────────── Teatr ─────────────
TH_CHAT_MAX = 300


def _th_on() -> None:
    if not theatre.available():
        raise web.HTTPServiceUnavailable(reason="Teatr hali ulanmagan")
    if not theatre.enabled():
        raise web.HTTPServiceUnavailable(reason="Teatr hozircha yopiq")


def _auth_q(request: web.Request) -> dict:
    """<video src> sarlavha yubora olmaydi, shuning uchun initData ?a= orqali keladi."""
    user = verify_init_data(request.query.get("a", ""), config.BOT_TOKEN)
    if not user:
        raise web.HTTPUnauthorized(reason="auth")
    return user


def _uname(user: dict) -> str:
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x).strip()
    return (name or (f"@{user['username']}" if user.get("username") else "") or "Foydalanuvchi")[:40]


async def api_th_state(request: web.Request):
    _th_on()
    user = _auth(request)
    st = await theatre.state()
    if request.query.get("w"):
        theatre.seen("pub", user["id"])
    st["viewers"] = theatre.viewers("pub")
    return web.json_response(st)


async def api_th_plan(request: web.Request):
    _th_on()
    user = _auth(request)
    return web.json_response(await theatre.plan_view(uid=user["id"]))


async def api_th_video(request: web.Request):
    _th_on()
    user = _auth_q(request)
    if await db.is_banned(user["id"]) and not perms.is_staff(user["id"]):
        raise web.HTTPForbidden(reason="ban")
    mid = int(request.match_info["id"])
    row = await db.get_movie(mid)
    if not row:
        raise web.HTTPNotFound(reason="not found")
    rid = request.query.get("r", "")
    if rid:
        room = await db.th_room_get(rid)
        if not room or room["movie_id"] != mid:
            raise web.HTTPNotFound(reason="not found")
        if not (await db.th_member_has(rid, user["id"]) or perms.role(user["id"]) == "owner"):
            raise web.HTTPForbidden(reason="locked")
    elif not (await theatre.in_today_plan(mid) or perms.is_staff(user["id"])):
        raise web.HTTPForbidden(reason="forbidden")
    return await theatre.serve(request, row)


async def _th_chat_room(user: dict, room: str, for_post: bool) -> str | None:
    if room == "pub":
        key = await theatre.current_key()
        if key == "pub:closed":
            if for_post:
                raise web.HTTPBadRequest(reason="Teatr hozir yopiq")
            return None
        return key
    r = await db.th_room_get(room)
    if not r:
        raise web.HTTPNotFound(reason="Xona topilmadi")
    # Parolli xonaga faqat a'zolar va bosh admin (sharpa) kira oladi; boshqa adminlar faqat ochiq xonaga
    if not (await db.th_member_has(r["id"], user["id"]) or perms.role(user["id"]) == "owner"
            or (perms.is_staff(user["id"]) and not r["pw"])):
        raise web.HTTPForbidden(reason="locked")
    return r["id"]


def _th_chat_item(r, viewer_id: int, vips=frozenset()) -> dict:
    return {"id": r["id"], "uid": r["user_id"], "name": r["name"] or "Foydalanuvchi", "text": r["text"],
            "at": r["at"], "mine": r["user_id"] == viewer_id, "staff": perms.is_staff(r["user_id"]),
            "vip": r["user_id"] in vips}


async def api_th_chat_list(request: web.Request):
    _th_on()
    user = _auth(request)
    key = await _th_chat_room(user, request.query.get("room", "pub"), False)
    if key is None:
        return web.json_response({"key": "pub:closed", "items": []})
    after = max(int(request.query.get("after", "0") or 0), 0)
    rows = await db.th_chat_list(key, after, 50)
    vips = await vip.vip_set([r["user_id"] for r in rows])
    return web.json_response({"key": key, "items": [_th_chat_item(r, user["id"], vips) for r in rows]})


async def api_th_chat_send(request: web.Request):
    _th_on()
    user = _auth(request)
    body = await request.json()
    room_param = str(body.get("room") or "pub")
    owned = None
    if room_param != "pub":  # o'zi ochgan xonada mute ta'sir qilmaydi
        r = await db.th_room_get(room_param)
        owned = r["owner"] if r else None
    if (resp := await _muted_resp(user, owned)) is not None:
        return resp
    key = await _th_chat_room(user, room_param, True)
    if room_param != "pub" and perms.role(user["id"]) == "owner" and not await db.th_member_has(key, user["id"]):
        return _bad("Sharpa rejimida xabar yozib bo'lmaydi 👻")
    text = re.sub(r"[ \t]+", " ", str(body.get("text") or "")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        return _bad("Xabar yozing")
    if len(text) > TH_CHAT_MAX:
        return _bad(f"Xabar {TH_CHAT_MAX} belgidan oshmasligi kerak")
    if time.time() - await db.th_chat_last_at(user["id"]) < CHAT_COOLDOWN:
        return web.json_response({"error": "Biroz sekinroq yozing 🙂"}, status=429)
    msg = await db.th_chat_add(key, user["id"], _uname(user), text)
    item = {**msg, "staff": perms.is_staff(user["id"]), "vip": user["id"] in await vip.vip_set([user["id"]])}
    # real vaqt: zaldagilarga darrov tarqatamiz (public zal kaliti seansga qarab o'zgaradi, kanal esa doim «pub»)
    rt.hub.publish("pub" if room_param == "pub" else key, "chat", {"key": key, "item": item})
    return web.json_response({"ok": True, "key": key, "item": {**item, "mine": True}})


async def _th_chat_remove(mid: int) -> None:
    """Teatr chat xabarini o'chiradi va zaldagilarga ham bildiradi."""
    row = await db.q_one("SELECT room FROM theatre_chat WHERE id=?", (mid,))
    await db.th_chat_delete(mid)
    if row:
        rt.hub.publish("pub" if row["room"].startswith("pub:") else row["room"], "chat_del", {"id": mid})


async def api_th_chat_delete(request: web.Request):
    _admin(request)
    body = await request.json()
    await _th_chat_remove(int(body.get("id", 0)))
    return web.json_response({"ok": True})


# — Shaxsiy xonalar —
def _th_room_items(rows, user: dict) -> list:
    out = []
    for r in rows:
        art = theatre._art({"id": r["mid"], "series_id": r["mseries"], "scover": r["scover"] or "",
                            "cover_id": r["mcover"] or ""})
        out.append({"id": r["id"], "title": r["title"], "locked": bool(r["pw"]), "owner": r["owner_name"],
                    "movie": r["mtitle"] + (f" · {r['mep']}-qism" if r["mseries"] and r["mep"] else ""),
                    "art": art, "mid": r["mid"], "viewers": theatre.viewers(r["id"]),
                    "mine": r["owner"] == user["id"]})
    return out


async def api_th_rooms(request: web.Request):
    _th_on()
    user = _auth(request)
    rows = await db.th_rooms_list(int(time.time()) - 3 * 3600)
    return web.json_response({"items": _th_room_items(rows, user)})


async def api_th_home(request: web.Request):
    """Teatr bosh sahifasi uchun hammasi bitta so'rovda: efir, bugungi reja, premyeralar, xonalar."""
    _th_on()
    user = _auth(request)
    st = await theatre.state()
    st["viewers"] = theatre.viewers("pub")
    rows = await db.th_rooms_list(int(time.time()) - 3 * 3600, 12)
    return web.json_response({
        "state": st, "plan": await theatre.plan_view(uid=user["id"]),
        "premieres": await theatre.premieres_view(user["id"]), "rooms": _th_room_items(rows, user),
    })


async def api_th_remind(request: web.Request):
    """Seansdan 10 daqiqa oldin Telegramga eslatma: yoqish / o'chirish."""
    _th_on()
    user = _auth(request)
    body = await request.json()
    try:
        start = int(body.get("start") or 0)
    except (TypeError, ValueError):
        return _bad("Seans noto'g'ri")
    on = body.get("on")
    try:
        res = await theatre.remind_toggle(user["id"], start, None if on is None else bool(on))
    except theatre.RemindError as e:
        return _bad(str(e))
    return web.json_response({"ok": True, **res})


async def api_th_room_create(request: web.Request):
    _th_on()
    user = _auth(request)
    if vip.ON and not perms.is_staff(user["id"]) and not await vip.is_vip(user["id"]):
        return web.json_response({"error": "👑 Xona ochish faqat VIP obunachilar uchun", "vip": True}, status=403)
    body = await request.json()
    title = re.sub(r"\s+", " ", str(body.get("title") or "")).strip()[:40]
    if len(title) < 2:
        return _bad("Xonaga nom bering (kamida 2 belgi)")
    mv = await db.get_movie(int(body.get("movie_id") or 0))
    if not mv or (mv["status"] != "live" and not perms.is_staff(user["id"])):
        return _bad("Kinoni tanlang")
    if await db.th_rooms_of(user["id"], int(time.time()) - 6 * 3600) >= 2:
        return _bad("Bir vaqtda ko'pi bilan 2 ta xona ochish mumkin")
    if not await theatre.ensure_meta(mv):
        return _bad("Bu kino teatr uchun mos emas (format yoki hajmi). Boshqasini tanlang")
    pw = str(body.get("password") or "").strip()[:20]
    salt = theatre.secrets.token_hex(8) if pw else ""
    rid = theatre.new_room_id()
    while await db.th_room_get(rid):
        rid = theatre.new_room_id()
    await db.th_room_add({"id": rid, "owner": user["id"], "owner_name": _uname(user), "title": title,
                          "movie_id": mv["id"], "salt": salt, "pw": theatre.hash_pw(salt, pw)})
    return web.json_response({"ok": True, "id": rid})


async def api_th_room_join(request: web.Request):
    _th_on()
    user = _auth(request)
    body = await request.json()
    rid = str(body.get("id") or "").strip().upper()
    room = await db.th_room_get(rid)
    if not room:
        return _bad("Bunday xona topilmadi yoki yopilgan")
    if not perms.is_staff(user["id"]) and await db.th_kicked_has(rid, user["id"]):
        return web.json_response({"error": "Siz bu xonadan chiqarib yuborilgansiz", "kicked": True}, status=403)
    if not await db.th_member_has(rid, user["id"]) and room["pw"]:
        if not theatre.pw_allowed(user["id"], rid):
            return web.json_response({"error": "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring"}, status=429)
        if not theatre.check_pw(room, str(body.get("password") or "")):
            theatre.pw_failed(user["id"], rid)
            return _bad("Parol noto'g'ri")
    if not await db.th_member_has(rid, user["id"]):
        await db.th_member_add(rid, user["id"])
        rt.hub.publish(rid, "roster", {})
    return web.json_response({"ok": True, "id": rid})


async def api_th_room_get(request: web.Request):
    _th_on()
    user = _auth(request)
    rid = request.match_info["id"].upper()
    room = await db.th_room_get(rid)
    if not room:
        raise web.HTTPNotFound(reason="Xona topilmadi yoki yopilgan")
    ghost = False
    if not await db.th_member_has(rid, user["id"]):
        if perms.role(user["id"]) == "owner":
            ghost = True  # 👻 Bosh admin: parolsiz, sezdirmay kuzatadi (a'zo bo'lmaydi, tomoshabin hisobiga kirmaydi)
        elif not perms.is_staff(user["id"]) and await db.th_kicked_has(rid, user["id"]):
            raise web.HTTPForbidden(reason="Siz bu xonadan chiqarib yuborilgansiz")
        elif room["pw"]:
            raise web.HTTPForbidden(reason="locked")
        else:
            await db.th_member_add(rid, user["id"])
            rt.hub.publish(rid, "roster", {})
    mv = await db.get_movie(room["movie_id"])
    ser = await db.get_series(mv["series_id"]) if mv["series_id"] else None
    art = theatre._art({"id": mv["id"], "series_id": mv["series_id"], "scover": ser["cover_id"] if ser else "",
                        "cover_id": mv["cover_id"]})
    now = time.time()
    if not ghost:
        theatre.seen(rid, user["id"])
        if now - room["last_active"] > 60:
            await db.th_room_touch(rid)
    extra = {}
    if ghost:
        extra = {"members": [{"id": m["id"], "name": m["full_name"] or "—"} for m in await db.th_members(rid)],
                 "created": room["created_at"], "owner_id": room["owner"]}
    return web.json_response({
        "room": {"id": rid, "title": room["title"], "owner": room["owner_name"], "host": room["owner"] == user["id"],
                 "locked": bool(room["pw"]), "playing": bool(room["playing"]), "pos": theatre.room_pos(room, now),
                 "now": now, "ghost": ghost, **extra,
                 "movie": {"id": mv["id"], "title": mv["title"], "duration": mv["duration"],
                           "ep": mv["ep"] if mv["series_id"] else None, "art": art}},
        "viewers": theatre.viewers(rid),
    })


async def api_th_room_state(request: web.Request):
    user = _auth(request)
    rid = request.match_info["id"].upper()
    room = await db.th_room_get(rid)
    if not room:
        raise web.HTTPNotFound(reason="Xona topilmadi")
    if room["owner"] != user["id"]:
        raise web.HTTPForbidden(reason="Faqat xona egasi boshqaradi")
    body = await request.json()
    pos = max(float(body.get("pos") or 0), 0.0)
    now = time.time()
    playing = bool(body.get("playing"))
    await db.th_room_state(rid, playing, pos, now)
    rt.hub.publish(rid, "state", {"playing": playing, "pos": pos, "now": now})  # tomoshabinlar darrov moslashadi
    return web.json_response({"ok": True})


async def api_th_room_delete(request: web.Request):
    user = _auth(request)
    rid = request.match_info["id"].upper()
    room = await db.th_room_get(rid)
    if not room:
        raise web.HTTPNotFound(reason="Xona topilmadi")
    is_owner_admin = perms.role(user["id"]) == "owner"
    if room["owner"] != user["id"] and not (is_owner_admin or (perms.is_staff(user["id"]) and not room["pw"])):
        raise web.HTTPForbidden(reason="Faqat xona egasi yopa oladi")
    if room["owner"] != user["id"]:
        await audit.log(user["id"], _uname(user), "theatre", "Teatr xonasi yopildi",
                        f"{room['title']} (#{rid}) · egasi: {room['owner_name']}")
    await db.th_room_del(rid)
    rt.hub.close_key(rid, "closed", {})
    return web.json_response({"ok": True})


# — Real vaqt: oqim (SSE), reaksiyalar, ishtirokchilar, taklif —
SSE_LIFE = 600  # bitta oqim umri (soniya): keyin brauzer o'zi qayta ulanadi va ruxsatlar (ban, chiqarilish) qayta tekshiriladi


async def _th_channel(user: dict, room: str) -> tuple[str, bool]:
    """Real vaqt kanali va 👻 sharpa belgisi. Ruxsat qoidalari chat bilan bir xil."""
    room = (room or "pub").strip()
    if room == "pub":
        return "pub", False
    key = await _th_chat_room(user, room.upper(), False)
    ghost = perms.role(user["id"]) == "owner" and not await db.th_member_has(key, user["id"])
    return key, ghost


async def api_th_events(request: web.Request):
    """Server-Sent Events: chat, reaksiya, tomoshabinlar soni, xona holati — bitta ochiq ulanishda."""
    _th_on()
    user = _auth_q(request)
    key, ghost = await _th_channel(user, request.query.get("room", "pub"))
    sub = rt.hub.subscribe(key, user["id"], ghost)
    if sub is None:
        raise web.HTTPTooManyRequests(reason="Juda ko'p ulanish")
    resp = web.StreamResponse(status=200, headers={
        "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no", "Connection": "keep-alive"})
    try:
        await resp.prepare(request)
        await resp.write(b"retry: 4000\n\n" + rt.fmt("hello", {"v": theatre.viewers(key), "now": time.time()}))
        end = time.monotonic() + SSE_LIFE
        while not sub.closed:
            left = end - time.monotonic()
            if left <= 0:
                break
            try:
                ev = await asyncio.wait_for(sub.q.get(), min(15, left))
            except asyncio.TimeoutError:
                if not ghost:
                    theatre.seen(key, user["id"])
                await resp.write(b": ka\n\n")
                continue
            if ev is None:
                break
            await resp.write(ev)
    except (ConnectionResetError, ConnectionError):
        pass
    finally:
        rt.hub.unsubscribe(sub)
    return resp


async def api_th_react(request: web.Request):
    """Reaksiya (🔥 😂 😮 ❤️ 👏). Bazaga yozilmaydi; server tezlikni cheklaydi va paketlab tarqatadi."""
    _th_on()
    user = _auth(request)
    body = await request.json()
    emoji, room = str(body.get("emoji") or ""), str(body.get("room") or "pub").strip()
    if emoji not in rt.REACTIONS:
        return _bad("Noto'g'ri reaksiya")
    uid = user["id"]
    if not rt.hub.allow(uid):
        return web.json_response({"ok": False, "slow": True})
    key = "pub" if room == "pub" else room.upper()
    if not rt.hub.has(key, uid):  # ochiq oqim bo'lmasa (zaxira rejim) — ruxsatni bazadan tekshiramiz
        key, ghost = await _th_channel(user, room)
        if ghost:
            return web.json_response({"ok": False})
    if key == "pub" and await theatre.current_key() == "pub:closed":
        return web.json_response({"ok": False})
    rt.hub.push(key, uid, emoji)
    return web.json_response({"ok": True})


def _ice_servers() -> list:
    out = [{"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]}]
    if config.TURN_URLS:
        out.append({"urls": config.TURN_URLS, "username": config.TURN_USER, "credential": config.TURN_PASS})
    return out


async def api_th_voice_state(request: web.Request):
    """Ovozli suhbat: mikrofon (gapirish) va naushnik (eshitish) holati. Avto: mikrofon o'chiq, naushnik yoniq."""
    _th_on()
    user = _auth(request)
    body = await request.json()
    room = str(body.get("room") or "").strip().upper()
    if not room or room == "PUB":
        return _bad("Ovozli suhbat faqat shaxsiy xonalarda ishlaydi")
    uid = user["id"]
    if not rt.hub.has(room, uid):  # ruxsat real vaqt oqimi ulanganda tekshirilgan (a'zo, chiqarilmagan)
        return _bad("Real vaqt ulanishi yo'q. Xonani qayta oching")
    mic, ears = bool(body.get("mic")), bool(body.get("ears", True))
    if mic:
        rm = await db.th_room_get(room)
        muted = await _muted_resp(user, rm["owner"] if rm else None)
        if muted:
            return muted
    if rt.hub.voice_set(room, uid, mic, ears) == "full":
        return _bad(f"Bir vaqtda {rt.MAX_SPEAKERS} kishi gapira oladi. Biroz kuting")
    return web.json_response({"ok": True, **rt.hub.voice_snapshot(room), "ice": _ice_servers()})


async def api_th_voice_signal(request: web.Request):
    """WebRTC signal (offer/answer/ice) — faqat xonadagi boshqa a'zoga uzatiladi. Ovozning o'zi serverdan o'tmaydi."""
    _th_on()
    user = _auth(request)
    body = await request.json()
    room, to, kind = str(body.get("room") or "").strip().upper(), int(body.get("to") or 0), str(body.get("kind") or "")
    data = body.get("data")
    if kind not in ("offer", "answer", "ice") or not to or to == user["id"] or len(json.dumps(data or "")) > 12000:
        return _bad("Noto'g'ri signal")
    if not rt.hub.allow_sig(user["id"]):
        return web.json_response({"ok": False})
    if not rt.hub.has(room, user["id"]) or not rt.hub.has(room, to):
        return web.json_response({"ok": False})
    rt.hub.send_to(room, to, "vsig", {"f": user["id"], "k": kind, "d": data})
    return web.json_response({"ok": True})


async def _th_room_for_members(request: web.Request, user: dict):
    """Xonani qaytaradi (a'zo yoki 👻 bosh admin bo'lsa), aks holda xato."""
    rid = request.match_info["id"].upper()
    room = await db.th_room_get(rid)
    if not room:
        raise web.HTTPNotFound(reason="Xona topilmadi yoki yopilgan")
    member = await db.th_member_has(rid, user["id"])
    ghost = perms.role(user["id"]) == "owner" and not member
    if not (member or ghost):
        raise web.HTTPForbidden(reason="locked")
    return room, ghost


async def api_th_room_members(request: web.Request):
    _th_on()
    user = _auth(request)
    room, ghost = await _th_room_for_members(request, user)
    rid, rows, on = room["id"], await db.th_members(room["id"]), theatre.online(room["id"])
    vips = await vip.vip_set([r["id"] for r in rows])
    items = [{"id": r["id"], "name": (r["full_name"] or (room["owner_name"] if r["id"] == room["owner"] else "") or "Foydalanuvchi")[:40],
              "online": r["id"] in on, "host": r["id"] == room["owner"], "staff": perms.is_staff(r["id"]),
              "vip": r["id"] in vips} for r in rows]
    items.sort(key=lambda x: (not x["host"], not x["online"], x["name"].lower()))
    can_kick = room["owner"] == user["id"] or ghost or (perms.is_staff(user["id"]) and not room["pw"])
    return web.json_response({"items": items[:200], "me": user["id"], "host": room["owner"],
                              "can_kick": can_kick, "viewers": len(on), "total": len(items)})


async def api_th_room_kick(request: web.Request):
    """Xona egasi (yoki admin) foydalanuvchini xonadan chiqaradi: u qayta kira olmaydi."""
    _th_on()
    user = _auth(request)
    rid = request.match_info["id"].upper()
    room = await db.th_room_get(rid)
    if not room:
        raise web.HTTPNotFound(reason="Xona topilmadi yoki yopilgan")
    body = await request.json()
    target = int(body.get("uid") or 0)
    is_host, boss = room["owner"] == user["id"], perms.role(user["id"]) == "owner"
    if not (is_host or boss or (perms.is_staff(user["id"]) and not room["pw"])):
        raise web.HTTPForbidden(reason="Faqat xona egasi foydalanuvchini chiqara oladi")
    if not target or target == user["id"]:
        return _bad("O'zingizni chiqarib bo'lmaydi")
    if target == room["owner"]:
        return _bad("Xona egasini chiqarib bo'lmaydi")
    if perms.is_staff(target) and not boss:
        return _bad("Adminni chiqarib bo'lmaydi")
    if not await db.th_member_has(rid, target):
        return _bad("Bu foydalanuvchi xonada emas")
    await db.th_kick(rid, target)
    theatre.gone(rid, target)
    rt.hub.send_to(rid, target, "kicked", {"uid": target}, close=True)
    rt.hub.publish(rid, "roster", {})
    if not is_host:
        await audit.log(user["id"], _uname(user), "theatre", "Teatr xonasidan foydalanuvchi chiqarildi",
                        f"{room['title']} (#{rid}) · ID {target}")
    return web.json_response({"ok": True})


async def api_th_room_invite(request: web.Request):
    """Xona egasi uchun taklif havolasi. Havolada faqat xona kodi bor — parol hech qachon havolaga qo'shilmaydi."""
    _th_on()
    user = _auth(request)
    rid = request.match_info["id"].upper()
    room = await db.th_room_get(rid)
    if not room:
        raise web.HTTPNotFound(reason="Xona topilmadi yoki yopilgan")
    if room["owner"] != user["id"]:
        raise web.HTTPForbidden(reason="Faqat xona egasi taklif havolasini oladi")
    me = await request.app["bot"].me()
    link = f"https://t.me/{me.username}?start=r_{rid}"
    text = f"🎬 «{room['title']}» xonasida birga kino ko'ramiz!" + ("\n🔒 Xona parolli — parolni mendan so'rang." if room["pw"] else "")
    return web.json_response({"link": link, "text": text, "locked": bool(room["pw"]),
                              "share": f"https://t.me/share/url?url={quote(link)}&text={quote(text)}"})


# — Premyera (admin) —
async def api_admin_th(request: web.Request):
    user = _admin(request, "publish")
    pend = [r for r in await db.q_all(
        "SELECT id, title, year, kind FROM movies WHERE status='pending' AND series_id IS NULL ORDER BY id DESC LIMIT 100")
        if perms.can_kind(user["id"], r["kind"])]
    sched = await db.q_all(
        "SELECT id, title, premiere_at FROM movies WHERE status='premiere' ORDER BY premiere_at")
    return web.json_response({
        "ready": theatre.available(), "day": theatre.tomorrow(), "slots": theatre.premiere_slots(),
        "pending": [{"id": r["id"], "title": r["title"], "year": r["year"]} for r in pend],
        "scheduled": [{"id": r["id"], "title": r["title"], "time": theatre.hhmm(r["premiere_at"]),
                       "day": theatre.day_of(r["premiere_at"]), "started": r["premiere_at"] <= time.time()}
                      for r in sched],
    })


async def api_admin_th_premiere(request: web.Request):
    user = _admin(request, "publish")
    if not theatre.available():
        return _bad("Teatr ulanmagan (API_ID / API_HASH ni tekshiring)")
    body = await request.json()
    mv = await db.get_movie(int(body.get("movie_id") or 0))
    idx = int(body.get("idx", -1))
    if not mv or mv["status"] != "pending" or mv["series_id"]:
        return _bad("Premyera uchun «kutilayotgan» yakka kino tanlang")
    _kind_ok(user, mv["kind"])
    if not 0 <= idx < theatre.SLOTS:
        return _bad("Vaqtni tanlang")
    if not await theatre.ensure_meta(mv):
        return _bad("Bu video teatrga mos emas (mp4 emas, juda uzun yoki ochilmadi)")
    ts = theatre.premiere_ts(idx)
    if await db.q_one("SELECT 1 FROM movies WHERE status='premiere' AND premiere_at=?", (ts,)):
        return _bad("Bu vaqtga boshqa premyera qo'yilgan")
    await db.q_run("UPDATE movies SET status='premiere', premiere_at=? WHERE id=? AND status='pending'", (ts, mv["id"]))
    return web.json_response({"ok": True})


async def api_admin_th_rebuild(request: web.Request):
    _admin(request, "publish")
    if not theatre.available():
        return _bad("Teatr ulanmagan")
    n = await theatre.rebuild_today()
    return web.json_response({"ok": True, "slots": n})


async def api_admin_th_cancel(request: web.Request):
    user = _admin(request, "publish")
    body = await request.json()
    mv = await db.get_movie(int(body.get("movie_id") or 0))
    if not mv or mv["status"] != "premiere":
        raise web.HTTPNotFound(reason="not found")
    _kind_ok(user, mv["kind"])
    if (mv["premiere_at"] or 0) <= time.time():
        return _bad("Premyera boshlangan — bekor qilib bo'lmaydi")
    await db.q_run("UPDATE movies SET status='pending', premiere_at=NULL WHERE id=?", (mv["id"],))
    await theatre.on_premiere_cancel(request.app["bot"], mv["id"], mv["title"])  # eslatma qo'ygan tomoshabinlarga xabar
    return web.json_response({"ok": True})


# ───────────── Treyler (Instagram) va kanallarga e'lon ─────────────
TRAILER_SLOTS = {"trailer": "🎞 Treyler kanali (Telegram)", **promo.SLOTS}
ACCT_KINDS = {"kino": ("kino", "serial"), "anime": ("anime",), "multfilm": ("multfilm",)}


def _slot_key(slot: str) -> str:
    return trailers.CHAT_KEY if slot == "trailer" else promo.key(slot)


def _accts_for(user: dict) -> list[str]:
    return [a for a in insta.ACCTS if any(perms.can_kind(user["id"], k) for k in ACCT_KINDS[a])]


async def api_admin_trailers(request: web.Request):
    user = _admin(request, "publish", "channels", "settings")
    counts = await db.trailer_counts()
    groups = [{"key": a, "label": insta.LABEL[a], "waiting": counts[a], "ready": insta.configured(a),
               "env": insta.ENV_NAME[a]} for a in _accts_for(user)]
    slots = []
    for slot, label in TRAILER_SLOTS.items():
        key = _slot_key(slot)
        cid = await db.get_setting(key, "")
        slots.append({"slot": slot, "label": label, "id": cid,
                      "title": (await db.get_setting(key + "_title", "")) if cid else ""})
    return web.json_response({"groups": groups, "slots": slots, "total": await db.trailer_total(),
                              "running": trailers.running(), "backfill": await promo.backfill_count(),
                              "backfilling": promo.backfilling()})


async def api_admin_trailers_run(request: web.Request):
    """scope: all | kino | anime | multfilm — tanlangan akkaunt(lar)ga kutayotgan treylerlarni yuklaydi."""
    user = _admin(request, "publish")
    body = await request.json()
    scope = str(body.get("scope") or "all")
    allowed = _accts_for(user)
    if scope == "all":
        accts = allowed
    elif scope in allowed:
        accts = [scope]
    else:
        return _bad("Bu bo'limga ruxsat yo'q")
    if not accts:
        return _bad("Ruxsat berilgan bo'lim yo'q")
    if not trailers.start(request.app["bot"], accts):
        return _bad("Yuklash allaqachon ketyapti. Tugagach adminlarga hisobot keladi")
    return web.json_response({"ok": True})


async def api_admin_promo_backfill(request: web.Request):
    """Hozirgacha chiqarilgan (rasmi bor) kontentni o'z kanallariga bir marta yuboradi."""
    _admin(request, "channels")
    if promo.backfilling():
        return _bad("Yuborish allaqachon ketyapti. Tugagach adminlarga hisobot keladi")
    n = await promo.backfill_count()
    if not n:
        return _bad("Yuboriladigan narsa yo'q (kanallar ulanmagan yoki hammasi yuborilgan)")
    broadcast.spawn(promo.backfill(request.app["bot"]))
    return web.json_response({"ok": True, "count": n})


async def api_admin_trailers_channel(request: web.Request):
    """Treyler kanali yoki e'lon kanalini ulash (ref bo'sh bo'lsa — olib tashlash)."""
    _admin(request, "channels")
    body = await request.json()
    slot = str(body.get("slot") or "")
    if slot not in TRAILER_SLOTS:
        return _bad("Noma'lum kanal turi")
    key = _slot_key(slot)
    ref_text = str(body.get("ref") or "").strip()
    if not ref_text:
        await db.set_setting(key, "")
        await db.set_setting(key + "_title", "")
        return web.json_response({"ok": True})
    ref = svc.parse_ref(ref_text)
    if ref is None:
        return _bad("Kanalni @username yoki -100... ID ko'rinishida yozing")
    try:
        chat_id, title, _ = await svc.resolve_channel(request.app["bot"], ref, need_link=False, channel_only=True)
    except ValueError as e:
        return _bad(str(e))
    await db.set_setting(key, str(chat_id))
    await db.set_setting(key + "_title", title)
    return web.json_response({"ok": True})



# ───────────── Baho, izohlar va profil ─────────────
COMMENT_MAX_LEN = 300
COMMENT_COOLDOWN = 20  # soniya


async def _item_exists(fk: int) -> bool:
    return bool(await (db.get_series(-fk) if fk < 0 else db.get_movie(fk)))


def _user_name(user: dict) -> str:
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x).strip()
    return (name or (f"@{user['username']}" if user.get("username") else "") or "Foydalanuvchi")[:40]


async def api_reviews(request: web.Request):
    user = _auth(request)
    fk = int(request.query.get("fk", "0") or 0)
    if not fk or not await _item_exists(fk):
        raise web.HTTPNotFound(reason="not found")
    avg, cnt, mine = await db.rate_summary(fk, user["id"])
    rows = await db.comments_list(fk, 40)
    vips = await vip.vip_set([r["user_id"] for r in rows])
    items = [{"id": r["id"], "name": r["name"], "text": r["text"], "at": r["at"],
              "mine": r["user_id"] == user["id"], "vip": r["user_id"] in vips,
              "staff": perms.is_staff(r["user_id"])} for r in rows]
    return web.json_response({"avg": avg, "count": cnt, "mine": mine,
                              "comments": items, "total": await db.comments_total(fk)})


async def api_rate(request: web.Request):
    user = _auth(request)
    body = await request.json()
    fk = int(body.get("fk", 0))
    stars = int(body.get("stars", 0))
    if not 1 <= stars <= 5:
        return _bad("1 dan 5 gacha baho bering")
    if not fk or not await _item_exists(fk):
        raise web.HTTPNotFound(reason="not found")
    await db.rate_set(user["id"], fk, stars)
    avg, cnt, mine = await db.rate_summary(fk, user["id"])
    return web.json_response({"avg": avg, "count": cnt, "mine": mine})


async def api_comment(request: web.Request):
    user = _auth(request)
    if (resp := await _muted_resp(user)) is not None:
        return resp
    body = await request.json()
    fk = int(body.get("fk", 0))
    text = re.sub(r"[ \t]+", " ", str(body.get("text") or "")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        return _bad("Izoh yozing")
    if len(text) > COMMENT_MAX_LEN:
        return _bad(f"Izoh {COMMENT_MAX_LEN} belgidan oshmasligi kerak")
    if not fk or not await _item_exists(fk):
        raise web.HTTPNotFound(reason="not found")
    if not perms.is_staff(user["id"]) and time.time() - await db.comment_last_at(user["id"]) < COMMENT_COOLDOWN:
        return web.json_response({"error": "Biroz sekinroq yozing 🙂"}, status=429)
    c = await db.comment_add(fk, user["id"], _user_name(user), text)
    return web.json_response({"ok": True, "item": {
        "id": c["id"], "name": c["name"], "text": c["text"], "at": c["at"], "mine": True,
        "vip": user["id"] in await vip.vip_set([user["id"]]), "staff": perms.is_staff(user["id"])}})


async def api_comment_delete(request: web.Request):
    user = _auth(request)
    body = await request.json()
    row = await db.comment_get(int(body.get("id", 0)))
    if not row:
        return web.json_response({"ok": True})
    if row["user_id"] != user["id"] and not perms.is_staff(user["id"]):
        raise web.HTTPForbidden(reason="forbidden")
    await db.comment_delete(row["id"])
    return web.json_response({"ok": True})


async def api_profile(request: web.Request):
    user = _auth(request)
    uid = user["id"]
    hist = await db.history_items(uid, 30)
    return web.json_response({
        "uid": uid, "name": _user_name(user), "username": user.get("username") or "",
        "vip_until": await db.vip_until(uid), "staff": perms.is_staff(uid),
        "stats": await db.profile_stats(uid), "history": [_brief(r) for r in hist]})



def make_app(bot) -> web.Application:
    app = web.Application(middlewares=[errors, ban_guard, audit_mw], client_max_size=60 * 1024 * 1024)
    app["bot"] = bot
    r = app.router
    r.add_get("/", index)
    r.add_get("/health", health)
    r.add_get("/logo.png", logo)
    r.add_get("/api/me", api_me)
    r.add_get("/api/movies", api_movies)
    r.add_get(r"/api/movie/{id:\d+}", api_movie)
    r.add_get(r"/api/series/{id:\d+}", api_series)
    r.add_get(r"/api/sposter/{id:\d+}", api_sposter)
    r.add_get(r"/api/cover/{id:\d+}", api_cover)
    r.add_get(r"/api/scover/{id:\d+}", api_scover)
    r.add_get("/api/hero", api_hero)
    r.add_get(r"/api/ad/{id:\d+}", api_ad_image)
    r.add_post(r"/api/ad/{id:\d+}/click", api_ad_click)
    r.add_get("/api/admin/ads", api_admin_ads)
    r.add_post("/api/admin/ad", api_admin_ad_save)
    r.add_post("/api/admin/ad/delete", api_admin_ad_delete)
    r.add_get(r"/api/similar/{t:[ms]}/{id:\d+}", api_similar)
    r.add_get("/api/chat", api_chat_list)
    r.add_post("/api/chat", api_chat_send)
    r.add_post("/api/chat/delete", api_chat_delete)
    r.add_get("/api/theatre/state", api_th_state)
    r.add_get("/api/theatre/plan", api_th_plan)
    r.add_get(r"/api/theatre/v/{id:\d+}", api_th_video)
    r.add_get("/api/theatre/chat", api_th_chat_list)
    r.add_post("/api/theatre/chat", api_th_chat_send)
    r.add_post("/api/theatre/chat/delete", api_th_chat_delete)
    r.add_get("/api/theatre/rooms", api_th_rooms)
    r.add_get("/api/theatre/home", api_th_home)
    r.add_post("/api/theatre/remind", api_th_remind)
    r.add_post("/api/theatre/react", api_th_react)
    r.add_get("/api/theatre/events", api_th_events)
    r.add_post("/api/theatre/voice/state", api_th_voice_state)
    r.add_post("/api/theatre/voice/signal", api_th_voice_signal)
    r.add_get("/api/theatre/room/{id}/invite", api_th_room_invite)
    r.add_get("/api/theatre/room/{id}/members", api_th_room_members)
    r.add_post("/api/theatre/room/{id}/kick", api_th_room_kick)
    r.add_post("/api/theatre/room", api_th_room_create)
    r.add_post("/api/theatre/room/join", api_th_room_join)
    r.add_get("/api/theatre/room/{id}", api_th_room_get)
    r.add_post("/api/theatre/room/{id}/state", api_th_room_state)
    r.add_post("/api/theatre/room/{id}/delete", api_th_room_delete)
    r.add_get("/api/admin/theatre", api_admin_th)
    r.add_post("/api/admin/theatre/premiere", api_admin_th_premiere)
    r.add_post("/api/admin/theatre/cancel", api_admin_th_cancel)
    r.add_post("/api/admin/theatre/rebuild", api_admin_th_rebuild)
    r.add_post("/api/chat/mod", api_chat_mod)
    r.add_get("/api/admin/punish", api_admin_punish)
    r.add_post("/api/send", api_send)
    r.add_post("/api/fav", api_fav)
    r.add_get("/api/reviews", api_reviews)
    r.add_post("/api/rate", api_rate)
    r.add_post("/api/comment", api_comment)
    r.add_post("/api/comment/delete", api_comment_delete)
    r.add_get("/api/profile", api_profile)
    r.add_get(r"/api/poster/{id:\d+}", api_poster)
    # admin
    r.add_get("/api/admin/stats", api_admin_stats)
    r.add_get("/api/admin/punished", api_admin_punished)
    r.add_get("/api/admin/settings", api_admin_settings)
    r.add_post("/api/admin/setting", api_admin_setting)
    r.add_post("/api/admin/theme", api_admin_theme)
    r.add_post("/api/admin/channel", api_admin_channel_add)
    r.add_post("/api/admin/source", api_admin_source_add)
    r.add_get("/api/admin/series", api_admin_series_list)
    r.add_get(r"/api/admin/series/{id:\d+}", api_admin_series_get)
    r.add_post("/api/admin/series/update", api_admin_series_update)
    r.add_post("/api/admin/series/delete", api_admin_series_del)
    r.add_post("/api/admin/channel/delete", api_admin_channel_del)
    r.add_get("/api/admin/pending", api_admin_pending)
    r.add_post("/api/admin/publish", api_admin_publish)
    r.add_get(r"/api/admin/movie/{id:\d+}", api_admin_movie_get)
    r.add_post("/api/admin/movie/update", api_admin_movie_update)
    r.add_post("/api/admin/movie/delete", api_admin_movie_del)
    r.add_get("/api/admin/draft", api_admin_draft_get)
    r.add_post("/api/admin/draft", api_admin_draft_save)
    r.add_post("/api/admin/draft/clear", api_admin_draft_clear)
    r.add_get("/api/admin/admins", api_admin_admins)
    r.add_post("/api/admin/admin", api_admin_admin_save)
    r.add_post("/api/admin/admin/delete", api_admin_admin_del)
    r.add_post("/api/admin/broadcast", api_admin_broadcast)
    r.add_post("/api/admin/cover", api_admin_cover_upload)
    r.add_get("/api/admin/history", api_admin_history)
    r.add_get("/api/admin/items", api_admin_items)
    r.add_get("/api/admin/trailers", api_admin_trailers)
    r.add_post("/api/admin/trailers/run", api_admin_trailers_run)
    r.add_post("/api/admin/trailers/channel", api_admin_trailers_channel)
    r.add_post("/api/admin/promo/backfill", api_admin_promo_backfill)
    r.add_get("/api/admin/vip", api_admin_vip)
    r.add_post("/api/admin/vip/config", api_admin_vip_config)
    r.add_post("/api/admin/vip/grant", api_admin_vip_grant)
    r.add_get("/api/pay/options", api_pay_options)
    r.add_post("/api/pay/invoice", api_pay_invoice)
    r.add_post("/api/pay/check", api_pay_check)
    r.add_get("/api/admin/cards", api_admin_cards)
    r.add_get("/api/admin/cards/pick", api_admin_cards_pick)
    r.add_post("/api/admin/cards", api_admin_cards_save)
    r.add_post("/api/admin/cards/delete", api_admin_cards_delete)
    return app


async def start(bot, port: int) -> web.AppRunner:
    runner = web.AppRunner(make_app(bot))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    return runner
