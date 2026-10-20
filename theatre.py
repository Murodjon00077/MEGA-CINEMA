"""Teatr: kunlik reja, premyera, keyingi kinoni oldindan yuklash va Telegramdan oqim (MTProto).

• Ommaviy teatr: har kuni 08:00–24:00, har biri 2 soatlik 8 ta slot. Reja kun boshida (00:00) tuziladi:
  1 ta serial qismi (har kuni shu soatda, keyingi qism), 2 ta multfilm, qolgani kino — hammasi tasodifiy.
• Premyera: status='premiere' kino ertangi kunning tanlangan slotida chiqadi, tugagach katalogga qo'shiladi.
• Video: hozirgi va keyingi kino diskka yuklanadi (eskisi o'chadi); yuklanmaguncha to'g'ridan-to'g'ri Telegramdan oqim.
"""
import asyncio
import hashlib
import json
import logging
import os
import random
import re
import secrets
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web

import config
import db

log = logging.getLogger("theatre")

TZ = timezone(timedelta(hours=config.THEATRE_TZ))
OPEN_H = 8                 # teatr ochilish soati
SLOTS = 8                  # 08, 10, ... 22
SLOT_SEC = 2 * 3600
CHUNK = 1024 * 1024        # Telegram oqim bo'lagi (1 MiB)
PREFETCH_BEFORE = 30 * 60  # keyingi kino slot boshlanishidan shuncha oldin yuklana boshlaydi
CACHE_DIR = Path(config.DB_PATH).parent / "theatre"
MIN_FREE = 600 * 1024 * 1024  # diskda kino hajmidan tashqari shuncha joy qolishi kerak

# Teatrga mos kino: tayyor, mp4/mov (brauzer o'ynaydi), 2 soatdan uzun emas
ELIG = ("m.status='live' AND m.file_size>0 AND (m.mime='' OR m.mime LIKE 'video/mp4%' "
        "OR m.mime LIKE 'video/quicktime%') AND (m.duration=0 OR m.duration<=7200)")

_client = None
_plan_lock = asyncio.Lock()
_dl: dict[int, asyncio.Task] = {}
_failed: dict[int, float] = {}
_prog: dict[int, int] = {}   # yuklanayotgan kino: foiz
_presence: dict[str, dict[int, float]] = {}
_pw_tries: dict[tuple, list] = {}
_meta_lock = asyncio.Lock()
_enabled = True  # Sozlamalardagi «Teatr» tugmasi (o'chiq bo'lsa hech kimga ko'rinmaydi)


# ───────────── MTProto mijoz ─────────────
async def start() -> bool:
    """MTProto mijozni ishga tushiradi. API_ID/API_HASH yo'q bo'lsa yoki xato bo'lsa — teatr o'chiq."""
    global _client
    if not config.THEATRE_ON:
        log.warning("Teatr o'chiq: API_ID / API_HASH berilmagan.")
        return False
    try:
        from pyrogram import Client
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for f in CACHE_DIR.glob("*.part"):
            f.unlink(missing_ok=True)
        client = Client("theatre", api_id=config.API_ID, api_hash=config.API_HASH,
                        bot_token=config.BOT_TOKEN, in_memory=True, no_updates=True)
        await client.start()
        _client = client
        set_enabled((await db.get_setting("theatre_on", "1")) == "1")
        log.info("Teatr: MTProto ulandi (%s).", "yoqilgan" if _enabled else "sozlamada o'chirilgan")
        return True
    except Exception:
        log.exception("Teatr: MTProto ulanmadi (kutubxona o'rnatilganini va API_ID/API_HASH ni tekshiring)")
        _client = None
        return False


async def stop() -> None:
    global _client
    for t in list(_dl.values()):
        t.cancel()
    if _client:
        try:
            await _client.stop()
        except Exception:
            pass
        _client = None


def available() -> bool:
    return _client is not None


def enabled() -> bool:
    return _enabled


def set_enabled(v: bool) -> None:
    global _enabled
    _enabled = bool(v)


def is_on() -> bool:
    """Teatr foydalanuvchilarga ochiqmi: ulangan VA sozlamada yoqilgan."""
    return _enabled and _client is not None


# ───────────── Vaqt ─────────────
def day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d")


def slot_start(day: str, idx: int) -> int:
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ, hour=OPEN_H)
    return int(d.timestamp()) + idx * SLOT_SEC


def hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%H:%M")


def tomorrow() -> str:
    return (datetime.now(TZ) + timedelta(days=1)).strftime("%Y-%m-%d")


def premiere_slots() -> list[dict]:
    """Ertangi kunning slotlari (premyera vaqtini tanlash uchun)."""
    day = tomorrow()
    return [{"idx": i, "time": hhmm(slot_start(day, i)), "ts": slot_start(day, i)} for i in range(SLOTS)]


def premiere_ts(idx: int) -> int:
    return slot_start(tomorrow(), idx)


def mime_ok(mime: str) -> bool:
    m = (mime or "").lower()
    return m == "" or m.startswith("video/mp4") or m.startswith("video/quicktime")


# ───────────── Video ma'lumoti (hajm/format) ─────────────
async def _chunk_at(src: str, k: int) -> bytes:
    gen = _client.stream_media(src, offset=k, limit=1)
    try:
        async for ch in gen:
            return ch
    except Exception:
        return b""
    finally:
        try:
            await gen.aclose()
        except Exception:
            pass
    return b""


async def _probe(src: str) -> tuple[int, bytes]:
    """Fayl hajmini bo'lak-bo'lak binar qidiruv bilan topadi (≈25 MB trafik, bir marta)."""
    first = await _chunk_at(src, 0)
    if not first:
        return 0, b""
    if len(first) < CHUNK:
        return len(first), first[:16]
    lo, hi = 0, 1
    while await _chunk_at(src, hi):
        lo, hi = hi, hi * 2
        if hi > 16384:  # 16 GB dan oshmaydi
            return 0, b""
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if await _chunk_at(src, mid):
            lo = mid
        else:
            hi = mid
    last = await _chunk_at(src, lo)
    return lo * CHUNK + len(last), first[:16]


async def ensure_meta(row) -> bool:
    """Kinoning hajmi va formati ma'lum bo'lishini ta'minlaydi. True — teatrga mos."""
    mid = row["id"]
    row = await db.get_movie(mid) or row
    if row["file_size"] > 0:
        return mime_ok(row["mime"]) and (row["duration"] == 0 or row["duration"] <= 7200)
    if row["file_size"] < 0 or not available():
        return False
    async with _meta_lock:
        row = await db.get_movie(mid)
        if row["file_size"] != 0:
            return row["file_size"] > 0 and mime_ok(row["mime"])
        size, dur, mime = 0, 0, row["mime"] or ""
        if row["src_chat"] and row["src_msg"]:
            try:
                msg = await _client.get_messages(row["src_chat"], row["src_msg"])
                media = getattr(msg, "video", None) or getattr(msg, "document", None)
                if media:
                    size = int(getattr(media, "file_size", 0) or 0)
                    dur = int(getattr(media, "duration", 0) or 0)
                    mime = getattr(media, "mime_type", "") or mime
            except Exception as e:
                log.info("Manba postdan o'qib bo'lmadi (#%s): %s", mid, e)
        if size <= 0:
            try:
                size, head = await _probe(row["file_id"])
            except Exception as e:
                log.warning("Hajmni aniqlab bo'lmadi (#%s): %s", mid, e)
                size, head = 0, b""
            if size > 0 and not mime:
                mime = "video/mp4" if head[4:8] == b"ftyp" else "video/x-unknown"
        if size <= 0:
            await db.th_set_meta(mid, -1)
            return False
        await db.th_set_meta(mid, size, dur, mime)
        return mime_ok(mime) and (dur == 0 or dur <= 7200)


# ───────────── Kunlik reja ─────────────
def _free_near(slots: dict, idx: int):
    if idx not in slots:
        return idx
    for j in range(idx + 1, SLOTS):
        if j not in slots:
            return j
    for j in range(idx - 1, -1, -1):
        if j not in slots:
            return j
    return None


def _pick(pool, n: int, soft_avoid: set, hard_avoid: set) -> list[int]:
    ids = [r["id"] for r in pool if r["id"] not in hard_avoid]
    fresh = [i for i in ids if i not in soft_avoid]
    random.shuffle(fresh)
    out = fresh[:n]
    if len(out) < n:
        rest = [i for i in ids if i not in out]
        random.shuffle(rest)
        out += rest[: n - len(out)]
    return out


async def _next_ep(sid: int, last: int):
    """Serialning KEYINGI qismi — qat'iy tartibda (eng kichik raqamdan). Hajmi noma'lum qismning
    ma'lumoti shu zahoti aniqlanadi; faqat formati mos kelmaydigan (mp4 emas) qism o'tkazib yuboriladi."""
    rows = await db.q_all(
        "SELECT * FROM movies WHERE series_id=? AND ep>? AND status='live' ORDER BY ep", (sid, last))
    for r in rows:
        if r["file_size"] > 0:
            ok = mime_ok(r["mime"]) and (r["duration"] == 0 or r["duration"] <= 7200)
        else:
            ok = await ensure_meta(r)
        if ok:
            return r
    return None


async def _recent_ids(day: str, days: int = 10) -> set:
    since = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = await db.q_all("SELECT movie_id FROM theatre_slots WHERE day>=? AND day<?", (since, day))
    return {r["movie_id"] for r in rows}


async def _build(day: str) -> list[tuple[int, int, str]]:
    slots: dict[int, tuple[int, str]] = {}
    used: set[int] = set()
    day0 = slot_start(day, 0)

    # 1) Premyera — o'z vaqtida (serial va boshqalar undan keyin suriladi)
    for r in await db.q_all(
            "SELECT id, premiere_at FROM movies WHERE status='premiere' AND premiere_at>=? AND premiere_at<?",
            (day0, day0 + SLOTS * SLOT_SEC)):
        idx = _free_near(slots, min(max((r["premiere_at"] - day0) // SLOT_SEC, 0), SLOTS - 1))
        if idx is not None:
            slots[idx] = (r["id"], "premiere")
            used.add(r["id"])

    # 2) Serial: har kuni shu soatda keyingi qism; tugasa — yangi tasodifiy serial
    try:
        st = json.loads(await db.get_setting("th_serial", "{}") or "{}")
    except ValueError:
        st = {}
    sid, last = st.get("sid"), int(st.get("last_ep") or 0)
    ep_row = await _next_ep(sid, last) if sid else None
    if not ep_row:
        cands = await db.q_all(
            "SELECT DISTINCT m.series_id AS sid FROM movies m JOIN series s ON s.id=m.series_id "
            "WHERE s.kind='serial' AND m.series_id IS NOT NULL AND m.status='live'")
        ids = [r["sid"] for r in cands if r["sid"] != sid] or [r["sid"] for r in cands]
        random.shuffle(ids)  # serial tasodifiy, lekin qismi — doim 1-qismdan
        for cand in ids:
            ep_row = await _next_ep(cand, 0)
            if ep_row:
                sid = cand
                st = {"sid": sid, "last_ep": 0, "slot": st.get("slot", random.randrange(SLOTS))}
                break
    if ep_row:
        pref = int(st.get("slot", random.randrange(SLOTS)))
        idx = _free_near(slots, min(max(pref, 0), SLOTS - 1))
        if idx is not None:
            slots[idx] = (ep_row["id"], "serial")
            used.add(ep_row["id"])
            st.update(sid=sid, last_ep=ep_row["ep"], slot=pref)
            await db.set_setting("th_serial", json.dumps(st))

    recent = await _recent_ids(day)

    # 3) Ikkita multfilm
    toons = await db.q_all(f"SELECT m.id FROM movies m WHERE m.kind='multfilm' AND m.series_id IS NULL AND {ELIG}")
    free = [i for i in range(SLOTS) if i not in slots]
    random.shuffle(free)
    for mid in _pick(toons, 2, recent | used, used):
        if free:
            slots[free.pop()] = (mid, "toon")
            used.add(mid)

    # 4) Qolgan slotlarga kinolar
    free = [i for i in range(SLOTS) if i not in slots]
    if free:
        films = await db.q_all(
            f"SELECT m.id FROM movies m WHERE m.kind IN ('kino','anime') AND m.series_id IS NULL AND {ELIG}")
        if not films:
            films = await db.q_all(f"SELECT m.id FROM movies m WHERE m.series_id IS NULL AND {ELIG}")
        picks = _pick(films, len(free), recent | used, used)
        ids = [r["id"] for r in films]
        while ids and len(picks) < len(free):  # kino yetmasa — takrorlanadi
            picks.append(random.choice(ids))
        for idx, mid in zip(free, picks):
            slots[idx] = (mid, "film")

    return [(i, m, t) for i, (m, t) in sorted(slots.items())]


async def rebuild_today() -> int:
    """Admin: serialni qaytadan (1-qismdan) boshlab, bugungi rejani qayta tuzadi."""
    await db.set_setting("th_serial", "{}")
    day = day_of(time.time())
    async with _plan_lock:
        await db.q_run("DELETE FROM theatre_slots WHERE day=?", (day,))
    return len(await plan_day(day))


async def plan_day(day: str) -> list:
    rows = await db.th_get_slots(day)
    if rows:
        return rows
    async with _plan_lock:
        rows = await db.th_get_slots(day)
        if rows:
            return rows
        built = await _build(day)
        if not built:  # kontent yo'q — bo'sh reja saqlanmaydi (keyin qayta uriniladi)
            return []
        await db.th_set_slots(day, built)
        log.info("Teatr rejasi tuzildi: %s (%s ta slot)", day, len(built))
        return await db.th_get_slots(day)


async def _movies_by_id(ids: list[int]) -> dict:
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    rows = await db.q_all(
        f"SELECT id, title, kind, series_id, ep, year, duration, poster_id FROM movies WHERE id IN ({q})", tuple(ids))
    return {r["id"]: r for r in rows}


def _mv(r) -> dict | None:
    if not r:
        return None
    return {"id": r["id"], "title": r["title"], "kind": r["kind"], "ep": r["ep"] if r["series_id"] else None,
            "year": r["year"], "duration": r["duration"], "poster": bool(r["poster_id"])}


async def plan_view(day: str | None = None) -> dict:
    """Bugungi reja (foydalanuvchiga ko'rsatiladigan ro'yxat)."""
    now = time.time()
    day = day or day_of(now)
    rows = await plan_day(day)
    by = {r["idx"]: r for r in rows}
    mv = await _movies_by_id([r["movie_id"] for r in rows])
    items = []
    for i in range(SLOTS):
        st = slot_start(day, i)
        r = by.get(i)
        items.append({
            "idx": i, "start": st, "end": st + SLOT_SEC, "time": hhmm(st),
            "tag": r["tag"] if r else "", "movie": _mv(mv.get(r["movie_id"])) if r else None,
            "state": "now" if st <= now < st + SLOT_SEC else ("done" if now >= st + SLOT_SEC else "next"),
        })
    return {"day": day, "now": now, "items": items}


async def state() -> dict:
    """Ommaviy teatr hozirgi holati."""
    ts = time.time()
    local = datetime.fromtimestamp(ts, TZ)
    day = day_of(ts)
    secs = local.hour * 3600 + local.minute * 60 + local.second
    plan = await plan_day(day)
    by = {r["idx"]: r for r in plan}
    ids = [r["movie_id"] for r in plan]
    mv = await _movies_by_id(ids)

    def view(i):
        r = by.get(i)
        if not r:
            return {"idx": i, "start": slot_start(day, i), "time": hhmm(slot_start(day, i)), "movie": None, "tag": ""}
        return {"idx": i, "start": slot_start(day, i), "time": hhmm(slot_start(day, i)),
                "movie": _mv(mv.get(r["movie_id"])), "tag": r["tag"]}

    if secs < OPEN_H * 3600:
        return {"now": ts, "phase": "closed", "opens": slot_start(day, 0), "opens_time": hhmm(slot_start(day, 0)),
                "next": view(0), "key": "pub:closed",
                "prep": prep_pct(by[0]["movie_id"]) if 0 in by else None}
    idx = min((secs - OPEN_H * 3600) // SLOT_SEC, SLOTS - 1)
    cur = view(idx)
    start = slot_start(day, idx)
    elapsed = ts - start
    movie = cur["movie"]
    phase = "live" if movie else "break"
    if movie and movie["duration"] and elapsed > movie["duration"] + 3:
        phase = "break"
    nxt = view(idx + 1) if idx + 1 < SLOTS else None
    return {"now": ts, "phase": phase, "key": f"pub:{day}:{idx}", "slot": cur, "start": start,
            "end": start + SLOT_SEC, "elapsed": elapsed, "movie": movie, "next": nxt,
            "last": idx == SLOTS - 1, "day": day,
            "prep": prep_pct(by[idx + 1]["movie_id"]) if idx + 1 in by else None}


async def current_key() -> str:
    ts = time.time()
    local = datetime.fromtimestamp(ts, TZ)
    secs = local.hour * 3600 + local.minute * 60 + local.second
    if secs < OPEN_H * 3600:
        return "pub:closed"
    return f"pub:{day_of(ts)}:{min((secs - OPEN_H * 3600) // SLOT_SEC, SLOTS - 1)}"


async def in_today_plan(mid: int) -> bool:
    now = time.time()
    for day in (day_of(now), day_of(now - 3600)):  # yarim tundan keyin ham oxirgi slot ko'rinsin
        if any(r["movie_id"] == mid for r in await db.th_get_slots(day)):
            return True
    return False


# ───────────── Oldindan yuklash (yumalab boruvchi kesh) ─────────────
def cached_path(mid: int) -> Path | None:
    p = CACHE_DIR / f"{mid}.mp4"
    return p if p.exists() else None


async def _download(mid: int) -> None:
    row = await db.get_movie(mid)
    if not row or row["file_size"] <= 0:
        return
    size = row["file_size"]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(CACHE_DIR).free < size + MIN_FREE:
        log.warning("Teatr: diskda joy yetmaydi (#%s) — to'g'ridan-to'g'ri oqim ishlatiladi", mid)
        _failed[mid] = time.time()
        return
    part, final = CACHE_DIR / f"{mid}.part", CACHE_DIR / f"{mid}.mp4"
    gen = _client.stream_media(row["file_id"])
    try:
        written = 0
        _prog[mid] = 0
        with open(part, "wb") as f:
            async for chunk in gen:
                f.write(chunk)
                written += len(chunk)
                _prog[mid] = min(99, written * 100 // size)
        if part.stat().st_size != size:
            raise RuntimeError(f"hajm mos emas: {part.stat().st_size} != {size}")
        os.replace(part, final)
        log.info("Teatr: #%s diskka yuklandi (%.0f MB)", mid, size / 1048576)
    except asyncio.CancelledError:
        part.unlink(missing_ok=True)
        raise
    except Exception as e:
        log.warning("Teatr: #%s yuklanmadi: %s", mid, e)
        part.unlink(missing_ok=True)
        _failed[mid] = time.time()
    finally:
        try:
            await gen.aclose()
        except Exception:
            pass
        _dl.pop(mid, None)
        _prog.pop(mid, None)


def prep_pct(mid: int):
    """Kino serverda qanchalik tayyor: 100 — diskda, 0..99 — yuklanmoqda, None — yuklash boshlanmagan."""
    if cached_path(mid):
        return 100
    return _prog.get(mid)


def _prefetch(mid: int) -> None:
    if mid in _dl or cached_path(mid):
        return
    if time.time() - _failed.get(mid, 0) < 600:
        return
    _dl[mid] = asyncio.create_task(_download(mid))


async def _tick(bot) -> None:
    now = time.time()
    st = await state()
    await plan_day(day_of(now))

    # kerakli kinolar: hozirgisi va (30 daqiqa qolganda) keyingisi
    keep: set[int] = set()
    if st["phase"] == "live" and st.get("movie"):
        keep.add(st["movie"]["id"])
    nxt = st.get("next")
    if nxt and nxt.get("movie"):
        until = (st["opens"] if st["phase"] == "closed" else nxt["start"]) - now
        if until <= PREFETCH_BEFORE:
            keep.add(nxt["movie"]["id"])
    if not _enabled:  # o'chirilgan: hech narsa yuklanmaydi, disk bo'shatiladi
        keep = set()
    for f in CACHE_DIR.glob("*.mp4"):
        try:
            if int(f.stem) not in keep:
                f.unlink(missing_ok=True)
        except ValueError:
            pass
    for mid, task in list(_dl.items()):
        if mid not in keep:
            task.cancel()
    if available() and _enabled:
        for mid in keep:
            _prefetch(mid)

    await _finish_premieres(bot, now)

    # hajmi noma'lum kinolarni asta-sekin aniqlab boramiz (kuniga reja tuzilishi uchun)
    if available() and _enabled and not _dl:
        for r in await db.q_all(
                "SELECT * FROM movies WHERE file_size=0 AND status IN ('live','premiere') ORDER BY id DESC LIMIT 2"):
            await ensure_meta(r)


async def _finish_premieres(bot, now: float) -> None:
    """Premyera slotidan keyin kino katalogga qo'shiladi."""
    rows = await db.q_all(
        "SELECT s.day, s.idx, s.movie_id, m.title FROM theatre_slots s JOIN movies m ON m.id=s.movie_id "
        "WHERE s.tag='premiere' AND m.status='premiere'")
    done = [r for r in rows if slot_start(r["day"], r["idx"]) + SLOT_SEC <= now]
    # reja tuzilmay qolgan eskirgan premyeralar ham chiqariladi
    stale = await db.q_all(
        "SELECT id, title FROM movies WHERE status='premiere' AND premiere_at IS NOT NULL AND premiere_at < ?",
        (int(now) - SLOT_SEC - 3600,))
    ids = {r["movie_id"]: r["title"] for r in done} | {r["id"]: r["title"] for r in stale}
    for mid, title in ids.items():
        await db.q_run("UPDATE movies SET status='live' WHERE id=? AND status='premiere'", (mid,))
        log.info("Premyera tugadi, katalogga qo'shildi: #%s", mid)
        try:
            import services as svc
            await svc.notify_admins(bot, f"⭐ Premyera tugadi: <b>{title}</b> endi katalogda.")
        except Exception:
            pass


async def run(bot) -> None:
    """Fon vazifasi: har 15 soniyada reja, kesh va premyeralarni yangilaydi."""
    last_clean = 0.0
    while True:
        try:
            await _tick(bot)
            if time.time() - last_clean > 3600:
                last_clean = time.time()
                await db.th_rooms_cleanup(int(time.time()) - 6 * 3600)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Teatr fon vazifasi xatosi")
        await asyncio.sleep(15)


# ───────────── Videoni berish (Range bilan) ─────────────
async def serve(request: web.Request, row) -> web.StreamResponse:
    path = cached_path(row["id"])
    if path:
        return web.FileResponse(path, headers={"Content-Type": "video/mp4", "Cache-Control": "private, max-age=3600"})
    if not available() or row["file_size"] <= 0:
        raise web.HTTPServiceUnavailable(reason="Video hozircha tayyor emas")
    size = row["file_size"]
    start, end, status = 0, size - 1, 200
    m = re.match(r"bytes=(\d*)-(\d*)$", request.headers.get("Range", "").strip())
    if m and (m.group(1) or m.group(2)):
        a, b = m.groups()
        if a == "":
            start = max(size - int(b), 0)
        else:
            start = int(a)
            end = min(int(b), size - 1) if b else size - 1
        if start > end or start >= size:
            return web.Response(status=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206
    length = end - start + 1
    headers = {"Content-Type": "video/mp4", "Accept-Ranges": "bytes", "Content-Length": str(length),
               "Cache-Control": "private, max-age=3600"}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if request.method == "HEAD":
        return web.Response(status=status, headers=headers)

    first = start // CHUNK
    skip = start - first * CHUNK
    need = (skip + length + CHUNK - 1) // CHUNK
    gen = _client.stream_media(row["file_id"], offset=first, limit=need)
    it = gen.__aiter__()
    try:
        chunk = await it.__anext__()  # birinchi bo'lak xato bersa — JSON xato qaytaramiz
    except StopAsyncIteration:
        raise web.HTTPBadGateway(reason="Telegramdan video kelmadi")
    except Exception as e:
        log.warning("Oqim boshlanmadi (#%s): %s", row["id"], e)
        raise web.HTTPBadGateway(reason="Telegramdan video olib bo'lmadi")
    resp = web.StreamResponse(status=status, headers=headers)
    await resp.prepare(request)
    remaining = length
    try:
        while True:
            if skip:
                chunk, skip = chunk[skip:], 0
            if len(chunk) > remaining:
                chunk = chunk[:remaining]
            await resp.write(chunk)
            remaining -= len(chunk)
            if remaining <= 0:
                break
            try:
                chunk = await it.__anext__()
            except StopAsyncIteration:
                break
        await resp.write_eof()
    except (ConnectionResetError, ConnectionError):
        pass  # tomoshabin yopib ketdi / oldinga o'tkazdi
    finally:
        try:
            await gen.aclose()
        except Exception:
            pass
    return resp


# ───────────── Tomoshabinlar (onlayn) ─────────────
def seen(key: str, uid: int) -> None:
    _presence.setdefault(key, {})[uid] = time.time()


def viewers(key: str) -> int:
    d = _presence.get(key) or {}
    cut = time.time() - 25
    for u in [u for u, t in d.items() if t < cut]:
        d.pop(u, None)
    return len(d)


# ───────────── Xonalar ─────────────
ROOM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_room_id() -> str:
    return "".join(secrets.choice(ROOM_ALPHABET) for _ in range(5))


def hash_pw(salt: str, pw: str) -> str:
    return hashlib.sha256((salt + pw).encode()).hexdigest() if pw else ""


def check_pw(room, pw: str) -> bool:
    if not room["pw"]:
        return True
    import hmac
    return hmac.compare_digest(hash_pw(room["salt"], pw or ""), room["pw"])


def pw_allowed(uid: int, rid: str) -> bool:
    """Parolni taxmin qilishdan himoya: 10 daqiqada 6 ta urinish."""
    now = time.time()
    tries = [t for t in _pw_tries.get((uid, rid), []) if now - t < 600]
    _pw_tries[(uid, rid)] = tries
    return len(tries) < 6


def pw_failed(uid: int, rid: str) -> None:
    _pw_tries.setdefault((uid, rid), []).append(time.time())


def room_pos(room, now: float | None = None) -> float:
    """Xonadagi kinoning hozirgi o'rni (soniya) — host o'ynayotgan bo'lsa vaqt o'tib boradi."""
    now = now or time.time()
    return float(room["pos"]) + (now - float(room["pos_at"]) if room["playing"] else 0.0)
