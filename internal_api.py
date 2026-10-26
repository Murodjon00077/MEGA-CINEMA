"""MEGA AI SECRETARY uchun FAQAT-O'QISH (read-only) ichki API.

Bu fayl Mega Cinema loyihasiga QO'SHIMCHA modul (mavjud kodni o'zgartirmaydi). Xavfsizlik:
• INTERNAL_API_TOKEN (kamida 24 belgi) berilmasa — hech qanday yo'l ochilmaydi (o'chiq).
• Faqat GET. Baza alohida `mode=ro` ulanish bilan ochiladi (yozish imkonsiz) va `query_only` yoqilgan.
• Foydalanuvchi matni, tokenlar, file_id, to'lov cheklari (proof) HECH QACHON qaytarilmaydi — faqat sanoqlar.
• Xato qaytsa ham asosiy bot ishlashda davom etadi (har bir handler o'z xatosini ushlaydi).
"""
import asyncio
import collections
import hmac
import logging
import os
import re
import shutil
import sqlite3
import time
from datetime import date, timedelta

from aiohttp import web

import db

VERSION = "1"
_TOKEN = os.getenv("INTERNAL_API_TOKEN", "").strip()
ENABLED = len(_TOKEN) >= 24
_STARTED = time.time()
_SECRET_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{25,}|sk-[A-Za-z0-9_-]{16,}|(?i:bearer)\s+[A-Za-z0-9._~+/=-]{12,}"
                        r"|(?i:(?:password|parol|token|secret|api_?key|hash)\s*[=:]\s*)\S+")


def _clean(s: str, limit: int = 500) -> str:
    return _SECRET_RE.sub("[yashirilgan]", str(s))[:limit]


# ───────────── Xatolar jurnali (oxirgi WARNING+ yozuvlar, xotirada) ─────────────
class _Ring(logging.Handler):
    def __init__(self, size: int = 400):
        super().__init__(level=logging.WARNING)
        self.items: collections.deque = collections.deque(maxlen=size)
        self.seq = 0

    def emit(self, record: logging.LogRecord) -> None:  # hech qachon xato ko'tarmaydi
        try:
            exc = ""
            if record.exc_info and record.exc_info[0]:
                exc = f"{record.exc_info[0].__name__}: {record.exc_info[1]}"
            self.seq += 1
            self.items.append({
                "id": self.seq, "ts": int(record.created), "level": record.levelname,
                "logger": record.name, "msg": _clean(record.getMessage()), "exc": _clean(exc, 300),
            })
        except Exception:
            pass


RING = _Ring()


# ───────────── Faqat-o'qish baza ulanishi ─────────────
def _ro() -> sqlite3.Connection:
    path = getattr(db, "_path", "")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def _query(fn):
    """fn(con) ni alohida oqimda, faqat-o'qish ulanish bilan bajaradi."""
    def run():
        con = _ro()
        try:
            return fn(con)
        finally:
            con.close()
    return asyncio.to_thread(run)


def _one(con, sql, p=()):
    return con.execute(sql, p).fetchone()


def _count(con, sql, p=()) -> int:
    r = con.execute(sql, p).fetchone()
    return int(r[0] or 0) if r else 0


# ───────────── Tizim holati (faqat stdlib) ─────────────
def _mem() -> dict:
    """Konteyner (cgroup) chegarasi bo'yicha xotira; bo'lmasa /proc/meminfo."""
    try:
        for cur, lim in (("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
                         ("/sys/fs/cgroup/memory/memory.usage_in_bytes", "/sys/fs/cgroup/memory/memory.limit_in_bytes")):
            if os.path.exists(cur) and os.path.exists(lim):
                used = int(open(cur).read().strip())
                raw = open(lim).read().strip()
                if raw.isdigit() and int(raw) < 1 << 50:
                    return {"mem_pct": round(used * 100 / int(raw), 1), "mem_used_mb": used // 2**20,
                            "mem_total_mb": int(raw) // 2**20}
        info = {}
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0])
        total, avail = info["MemTotal"], info.get("MemAvailable", info.get("MemFree", 0))
        return {"mem_pct": round((total - avail) * 100 / total, 1), "mem_used_mb": (total - avail) // 1024,
                "mem_total_mb": total // 1024}
    except Exception:
        return {"mem_pct": None, "mem_used_mb": None, "mem_total_mb": None}


def _rss_mb():
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return None


async def _loop_lag_ms() -> float:
    t = time.perf_counter()
    await asyncio.sleep(0.05)
    return round(max(0.0, (time.perf_counter() - t - 0.05) * 1000), 1)


# ───────────── So'rovlar ─────────────
def q_health(con) -> dict:
    t = time.perf_counter()
    _one(con, "SELECT 1")
    path = getattr(db, "_path", "")
    size = os.path.getsize(path) if os.path.exists(path) else 0
    wal = os.path.getsize(path + "-wal") if os.path.exists(path + "-wal") else 0
    return {
        "ok": True, "latency_ms": round((time.perf_counter() - t) * 1000, 2),
        "size_mb": round(size / 2**20, 2), "wal_mb": round(wal / 2**20, 2),
        "movies_live": _count(con, "SELECT COUNT(*) FROM movies WHERE status='live' AND series_id IS NULL"),
        "episodes_live": _count(con, "SELECT COUNT(*) FROM movies WHERE status='live' AND series_id IS NOT NULL"),
        "pending": _count(con, "SELECT COUNT(*) FROM movies WHERE status='pending'"),
        "users": _count(con, "SELECT COUNT(*) FROM users"),
    }


def q_overview(con) -> dict:
    now = int(time.time())
    return {
        "users": _count(con, "SELECT COUNT(*) FROM users"),
        "new_24h": _count(con, "SELECT COUNT(*) FROM users WHERE joined_at>=?", (now - 86400,)),
        "active_24h": _count(con, "SELECT COUNT(*) FROM users WHERE last_active>=?", (now - 86400,)),
        "active_7d": _count(con, "SELECT COUNT(*) FROM users WHERE last_active>=?", (now - 7 * 86400,)),
        "active_30d": _count(con, "SELECT COUNT(*) FROM users WHERE last_active>=?", (now - 30 * 86400,)),
        "banned": _count(con, "SELECT COUNT(*) FROM users WHERE banned=1 AND (ban_until=0 OR ban_until>?)", (now,)),
        "blocked": _count(con, "SELECT COUNT(*) FROM users WHERE blocked=1"),
        "movies_live": _count(con, "SELECT COUNT(*) FROM movies WHERE status='live' AND series_id IS NULL"),
        "series_live": _count(con, "SELECT COUNT(DISTINCT series_id) FROM movies WHERE status='live' AND series_id IS NOT NULL"),
        "pending": _count(con, "SELECT COUNT(*) FROM movies WHERE status='pending'"),
        "views_total": _count(con, "SELECT COUNT(*) FROM views"),
        "views_24h": _count(con, "SELECT COUNT(*) FROM views WHERE at>=?", (now - 86400,)),
        "channels": _count(con, "SELECT COUNT(*) FROM channels"),
        "vip_active": _count(con, "SELECT COUNT(*) FROM users WHERE vip_until>?", (now,)),
        "vip_expiring_3d": _count(con, "SELECT COUNT(*) FROM users WHERE vip_until>? AND vip_until<=?", (now, now + 3 * 86400)),
        "payments_pending": _count(con, "SELECT COUNT(*) FROM payments WHERE status='pending'"),
        "vip_mode": (_one(con, "SELECT value FROM settings WHERE key='vip_on'") or ["0"])[0] == "1",
        "first_user_at": int(_one(con, "SELECT MIN(joined_at) FROM users")[0] or 0),
        "first_view_at": int(_one(con, "SELECT MIN(at) FROM views")[0] or 0),
        "last_content_at": int(_one(con, "SELECT MAX(created_at) FROM movies WHERE status='live'")[0] or 0),
        "ts": now,
    }


def q_daily(con, days: int, tz_min: int) -> dict:
    """Har kun uchun sanoqlar (tz_min — UTC dan daqiqa farqi). Ma'lumot bo'lmagan kun — 0."""
    now = int(time.time())
    off = tz_min * 60
    today = (now + off) // 86400
    first = today - days + 1
    start_ts = first * 86400 - off
    rows: dict[int, dict] = {d: {"new_users": 0, "viewers": 0, "views": 0, "new_items": 0, "pay_count": 0,
                                 "pay_sum": 0} for d in range(first, today + 1)}
    for r in con.execute("SELECT (joined_at+?)/86400 d, COUNT(*) c FROM users WHERE joined_at>=? GROUP BY d", (off, start_ts)):
        if r["d"] in rows:
            rows[r["d"]]["new_users"] = r["c"]
    for r in con.execute("SELECT (at+?)/86400 d, COUNT(*) v, COUNT(DISTINCT user_id) u FROM views WHERE at>=? GROUP BY d", (off, start_ts)):
        if r["d"] in rows:
            rows[r["d"]]["views"], rows[r["d"]]["viewers"] = r["v"], r["u"]
    for r in con.execute("SELECT (created_at+?)/86400 d, COUNT(*) c FROM movies WHERE status='live' AND created_at>=? GROUP BY d", (off, start_ts)):
        if r["d"] in rows:
            rows[r["d"]]["new_items"] = r["c"]
    for r in con.execute("SELECT (created_at+?)/86400 d, COUNT(*) c, COALESCE(SUM(amount),0) s FROM payments "
                         "WHERE status='ok' AND created_at>=? GROUP BY d", (off, start_ts)):
        if r["d"] in rows:
            rows[r["d"]]["pay_count"], rows[r["d"]]["pay_sum"] = r["c"], int(r["s"])
    epoch = date(1970, 1, 1)
    items = [{"day": (epoch + timedelta(days=d)).isoformat(), **v} for d, v in sorted(rows.items())]
    return {"days": items, "first_view_at": int(_one(con, "SELECT MIN(at) FROM views")[0] or 0),
            "first_user_at": int(_one(con, "SELECT MIN(joined_at) FROM users")[0] or 0)}


def q_top(con, days: int, limit: int, kind: str) -> list:
    since = int(time.time()) - days * 86400
    rows = con.execute(
        """SELECT m.*, s.title AS s_title, COUNT(*) AS n
           FROM views v JOIN movies m ON m.id=v.movie_id LEFT JOIN series s ON s.id=m.series_id
           WHERE v.at>=? GROUP BY COALESCE('s'||m.series_id, 'm'||m.id) ORDER BY n DESC, m.id DESC LIMIT ?""",
        (since, 200 if kind else limit)).fetchall()
    out = []
    for m in rows:
        if kind and m["kind"] != kind:
            continue
        try:
            code = db.movie_code(m)
        except Exception:
            code = str(m["id"])
        out.append({"title": _clean(m["s_title"] or m["title"], 120), "kind": m["kind"], "code": code,
                    "views": int(m["n"]), "series": bool(m["series_id"])})
        if len(out) >= limit:
            break
    return out


# ───────────── HTTP qatlam ─────────────
def _authorized(request) -> bool:
    got = request.headers.get("X-Internal-Token", "")
    if not got:
        auth = request.headers.get("Authorization", "")
        got = auth[7:] if auth.lower().startswith("bearer ") else ""
    return bool(got) and hmac.compare_digest(got.encode(), _TOKEN.encode())


def _int(request, key, default, lo, hi) -> int:
    try:
        v = int(request.query.get(key, default))
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _guarded(fn):
    async def handler(request):
        if not ENABLED:
            return web.json_response({"error": "disabled"}, status=404)
        if not _authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            return web.json_response(await fn(request))
        except Exception as e:  # asosiy botga ta'sir qilmaydi
            logging.getLogger("internal_api").error("ichki API xatosi: %s", type(e).__name__)
            return web.json_response({"error": "internal", "type": type(e).__name__}, status=500)
    return handler


@_guarded
async def h_health(request):
    out = {"ok": True, "version": VERSION, "ts": int(time.time()), "uptime_s": int(time.time() - _STARTED)}
    try:
        out["db"] = await _query(q_health)
    except Exception as e:
        out["db"] = {"ok": False, "error": type(e).__name__}
    tg = {"ok": False}
    bot = request.app.get("bot")
    if bot is not None:
        t = time.perf_counter()
        try:
            me = await asyncio.wait_for(bot.get_me(), 8)
            tg = {"ok": True, "username": getattr(me, "username", ""), "latency_ms": round((time.perf_counter() - t) * 1000)}
        except Exception as e:
            tg = {"ok": False, "error": type(e).__name__}
    out["telegram"] = tg
    out["loop_lag_ms"] = await _loop_lag_ms()
    try:
        du = shutil.disk_usage(os.path.dirname(getattr(db, "_path", "")) or ".")
        disk = {"disk_pct": round(du.used * 100 / du.total, 1), "disk_free_mb": du.free // 2**20}
    except Exception:
        disk = {"disk_pct": None, "disk_free_mb": None}
    try:
        load1 = round(os.getloadavg()[0], 2)
    except Exception:
        load1 = None
    out["system"] = {**disk, **_mem(), "load1": load1, "rss_mb": _rss_mb(), "cpus": os.cpu_count()}
    try:
        import config
        out["features"] = {"theatre": bool(config.THEATRE_ON), "webapp": bool(config.WEBAPP_URL)}
    except Exception:
        out["features"] = {}
    return out


@_guarded
async def h_overview(request):
    return await _query(q_overview)


@_guarded
async def h_daily(request):
    days, tz = _int(request, "days", 30, 1, 120), _int(request, "tz_min", 300, -720, 840)
    return await _query(lambda con: q_daily(con, days, tz))


@_guarded
async def h_top(request):
    days, limit = _int(request, "days", 7, 1, 365), _int(request, "limit", 10, 1, 50)
    kind = request.query.get("kind", "")
    if kind and kind not in ("kino", "serial", "multfilm", "anime"):
        kind = ""
    return {"items": await _query(lambda con: q_top(con, days, limit, kind))}


@_guarded
async def h_errors(request):
    after, limit = _int(request, "after", 0, 0, 1 << 40), _int(request, "limit", 100, 1, 300)
    items = [x for x in list(RING.items) if x["id"] > after][:limit]
    return {"last_id": RING.seq, "items": items, "boot_ts": int(_STARTED)}


def register(app: "web.Application") -> None:
    """Marshrutlarni qo'shadi (token bo'lmasa — hech narsa qo'shmaydi)."""
    if not ENABLED:
        logging.info("Ichki API o'chiq (INTERNAL_API_TOKEN yo'q yoki 24 belgidan qisqa)")
        return
    logging.getLogger().addHandler(RING)
    r = app.router
    r.add_get("/internal/v1/health", h_health)
    r.add_get("/internal/v1/overview", h_overview)
    r.add_get("/internal/v1/daily", h_daily)
    r.add_get("/internal/v1/top", h_top)
    r.add_get("/internal/v1/errors", h_errors)
    logging.info("Ichki faqat-o'qish API yoqildi (/internal/v1/*)")
