"""SQLite bilan ishlash (aiosqlite)."""
import asyncio
import json
import shutil
import sqlite3
import time
from pathlib import Path

import aiosqlite

_db: aiosqlite.Connection | None = None
_path: str = ""

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  full_name TEXT,
  username TEXT,
  joined_at INTEGER NOT NULL,
  last_active INTEGER NOT NULL,
  banned INTEGER NOT NULL DEFAULT 0,
  ban_until INTEGER NOT NULL DEFAULT 0,
  mute_until INTEGER NOT NULL DEFAULT 0,
  blocked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS movies(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  year TEXT NOT NULL DEFAULT '',
  genre TEXT NOT NULL DEFAULT '',
  genre_lc TEXT NOT NULL DEFAULT '',
  country TEXT NOT NULL DEFAULT '',
  language TEXT NOT NULL DEFAULT '',
  quality TEXT NOT NULL DEFAULT '',
  file_id TEXT NOT NULL,
  file_type TEXT NOT NULL DEFAULT 'video',
  poster_id TEXT NOT NULL DEFAULT '',
  cover_id TEXT NOT NULL DEFAULT '',
  search_text TEXT NOT NULL DEFAULT '',
  views INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  src_chat INTEGER,
  src_msg INTEGER,
  status TEXT NOT NULL DEFAULT 'live',
  kind TEXT NOT NULL DEFAULT 'kino',
  series_id INTEGER,
  ep INTEGER,
  num INTEGER,
  file_size INTEGER NOT NULL DEFAULT 0,
  duration INTEGER NOT NULL DEFAULT 0,
  mime TEXT NOT NULL DEFAULT '',
  premiere_at INTEGER
);
CREATE TABLE IF NOT EXISTS series(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL DEFAULT 'serial',
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  year TEXT NOT NULL DEFAULT '',
  genre TEXT NOT NULL DEFAULT '',
  genre_lc TEXT NOT NULL DEFAULT '',
  country TEXT NOT NULL DEFAULT '',
  language TEXT NOT NULL DEFAULT '',
  poster_id TEXT NOT NULL DEFAULT '',
  cover_id TEXT NOT NULL DEFAULT '',
  search_text TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  num INTEGER
);
CREATE TABLE IF NOT EXISTS ads(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL DEFAULT '',
  image TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  btn TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  clicks INTEGER NOT NULL DEFAULT 0,
  expires_at INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS admins(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT 'admin',
  perms TEXT NOT NULL DEFAULT '',
  sections TEXT NOT NULL DEFAULT '',
  added_by INTEGER,
  added_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sources(
  chat_id INTEGER PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'kino',
  series_id INTEGER
);
CREATE TABLE IF NOT EXISTS drafts(
  admin_id INTEGER PRIMARY KEY,
  data TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS favorites(
  user_id INTEGER NOT NULL,
  movie_id INTEGER NOT NULL,
  PRIMARY KEY(user_id, movie_id)
);
CREATE TABLE IF NOT EXISTS channels(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER UNIQUE NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  link TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS views(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  movie_id INTEGER NOT NULL,
  at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS theatre_slots(
  day TEXT NOT NULL,
  idx INTEGER NOT NULL,
  movie_id INTEGER NOT NULL,
  tag TEXT NOT NULL DEFAULT 'film',
  PRIMARY KEY(day, idx)
);
CREATE TABLE IF NOT EXISTS theatre_rooms(
  id TEXT PRIMARY KEY,
  owner INTEGER NOT NULL,
  owner_name TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  movie_id INTEGER NOT NULL,
  salt TEXT NOT NULL DEFAULT '',
  pw TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  last_active INTEGER NOT NULL,
  playing INTEGER NOT NULL DEFAULT 0,
  pos REAL NOT NULL DEFAULT 0,
  pos_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS theatre_members(
  room TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  PRIMARY KEY(room, user_id)
);
CREATE TABLE IF NOT EXISTS theatre_chat(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  room TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS trailers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  src_chat INTEGER NOT NULL,
  src_msg INTEGER NOT NULL,
  file_id TEXT NOT NULL,
  file_size INTEGER NOT NULL DEFAULT 0,
  code TEXT NOT NULL DEFAULT '',
  grp TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE(src_chat, src_msg)
);
CREATE TABLE IF NOT EXISTS trailer_posts(
  trailer_id INTEGER NOT NULL,
  acct TEXT NOT NULL,
  status TEXT NOT NULL,
  info TEXT NOT NULL DEFAULT '',
  at INTEGER NOT NULL,
  PRIMARY KEY(trailer_id, acct)
);
CREATE TABLE IF NOT EXISTS promo_sent(
  kind TEXT NOT NULL,
  ref INTEGER NOT NULL,
  at INTEGER NOT NULL,
  PRIMARY KEY(kind, ref)
);
CREATE INDEX IF NOT EXISTS idx_thchat_room ON theatre_chat(room, id);
CREATE INDEX IF NOT EXISTS idx_views_at ON views(at);
CREATE INDEX IF NOT EXISTS idx_views_user ON views(user_id, at);
CREATE INDEX IF NOT EXISTS idx_movies_views ON movies(views DESC);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(last_active);
"""

_APOS = str.maketrans({"’": "'", "‘": "'", "ʻ": "'", "ʼ": "'", "`": "'", "´": "'"})


def norm(s) -> str:
    """Qidiruv uchun matnni bir xil ko'rinishga keltiradi (o', o‘, oʻ ... bir xil)."""
    return str(s or "").translate(_APOS).casefold().strip()


async def init(path: str) -> None:
    global _db, _path
    _path = path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.executescript(SCHEMA)
    await _migrate()
    await _load_nums()


async def _migrate() -> None:
    """Eski bazalarni (zaxiradan tiklanganlarini ham) yangi tuzilishga keltiradi."""
    ucols = {r["name"] for r in await _all("PRAGMA table_info(users)")}
    for name in ("ban_until", "mute_until", "vip_until"):  # muddatli ban / mute / VIP muddati
        if name not in ucols:
            await _db.execute(f"ALTER TABLE users ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")
    cols = {r["name"] for r in await _all("PRAGMA table_info(movies)")}
    for name in ("src_chat", "src_msg"):
        if name not in cols:
            await _db.execute(f"ALTER TABLE movies ADD COLUMN {name} INTEGER")
    if "status" not in cols:
        await _db.execute("ALTER TABLE movies ADD COLUMN status TEXT NOT NULL DEFAULT 'live'")
    # Teatr uchun: video hajmi/davomiyligi/formati va premyera vaqti
    for name, ddl in (("file_size", "INTEGER NOT NULL DEFAULT 0"), ("duration", "INTEGER NOT NULL DEFAULT 0"),
                      ("mime", "TEXT NOT NULL DEFAULT ''"), ("premiere_at", "INTEGER")):
        if name not in cols:
            await _db.execute(f"ALTER TABLE movies ADD COLUMN {name} {ddl}")
    for name, ddl in (("kind", "TEXT NOT NULL DEFAULT 'kino'"), ("series_id", "INTEGER"), ("ep", "INTEGER")):
        if name not in cols:
            await _db.execute(f"ALTER TABLE movies ADD COLUMN {name} {ddl}")
    # Yakka kinolarning o'z kodi (1, 2, 3...) — qismlar (serial/multfilm/anime) bu hisobga kirmaydi
    if "num" not in cols:
        await _db.execute("ALTER TABLE movies ADD COLUMN num INTEGER")
    # Eski yagona (barcha turlar uchun umumiy) indeks olib tashlanadi: endi har tur o'z raqamini sanaydi
    await _db.execute("DROP INDEX IF EXISTS idx_movies_num")
    scols = {r["name"] for r in await _all("PRAGMA table_info(sources)")}
    if "kind" not in scols:
        await _db.execute("ALTER TABLE sources ADD COLUMN kind TEXT NOT NULL DEFAULT 'kino'")
    if "series_id" not in scols:
        await _db.execute("ALTER TABLE sources ADD COLUMN series_id INTEGER")
    sercols = {r["name"] for r in await _all("PRAGMA table_info(series)")}
    if "cover_id" not in cols:
        await _db.execute("ALTER TABLE movies ADD COLUMN cover_id TEXT NOT NULL DEFAULT ''")
    if "cover_id" not in sercols:
        await _db.execute("ALTER TABLE series ADD COLUMN cover_id TEXT NOT NULL DEFAULT ''")
    # Pullik (VIP) rejim: kino/serial pullik belgisi, serialda tekin qismlar soni, to'lovlar
    if "paid" not in cols:
        await _db.execute("ALTER TABLE movies ADD COLUMN paid INTEGER NOT NULL DEFAULT 0")
    if "paid" not in sercols:
        await _db.execute("ALTER TABLE series ADD COLUMN paid INTEGER NOT NULL DEFAULT 0")
    adcols = {r["name"] for r in await _all("PRAGMA table_info(ads)")}
    if adcols and "expires_at" not in adcols:
        await _db.execute("ALTER TABLE ads ADD COLUMN expires_at INTEGER NOT NULL DEFAULT 0")
    if "free_eps" not in sercols:
        await _db.execute("ALTER TABLE series ADD COLUMN free_eps INTEGER NOT NULL DEFAULT 0")
    await _db.execute(
        """CREATE TABLE IF NOT EXISTS admin_log(
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             admin_id INTEGER NOT NULL,
             admin_name TEXT NOT NULL DEFAULT '',
             role TEXT NOT NULL DEFAULT '',
             cat TEXT NOT NULL DEFAULT '',
             action TEXT NOT NULL,
             detail TEXT NOT NULL DEFAULT '',
             at INTEGER NOT NULL)"""
    )
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_alog_admin ON admin_log(admin_id, id)")
    await _db.execute(
        """CREATE TABLE IF NOT EXISTS payments(
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             user_id INTEGER NOT NULL,
             days INTEGER NOT NULL,
             amount INTEGER NOT NULL,
             method TEXT NOT NULL,
             status TEXT NOT NULL DEFAULT 'pending',
             proof TEXT NOT NULL DEFAULT '',
             created_at INTEGER NOT NULL,
             decided_by INTEGER,
             decided_at INTEGER)"""
    )
    if "num" not in sercols:
        await _db.execute("ALTER TABLE series ADD COLUMN num INTEGER")
    # Raqami yo'q seriallarga HAR TUR uchun alohida tartib raqami beriladi: s.1, s.2 / m.1 / a.1 ...
    for kind in KIND_PREFIX:
        rows = await _all("SELECT id FROM series WHERE kind=? AND (num IS NULL OR num=0) ORDER BY id", (kind,))
        last = (await _one("SELECT COALESCE(MAX(num), 0) AS n FROM series WHERE kind=?", (kind,)))["n"]
        for r in rows:
            last += 1
            await _db.execute("UPDATE series SET num=? WHERE id=?", (last, r["id"]))
    await _db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_series_num ON series(kind, num)")
    await _reconcile_nums()
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_movies_series ON movies(series_id)")
    await _db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_movies_ep ON movies(series_id, ep) WHERE series_id IS NOT NULL"
    )
    # Bir kanal postidan bitta kino (takroriy qo'shilishdan himoya)
    await _db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_movies_src ON movies(src_chat, src_msg) WHERE src_chat IS NOT NULL"
    )
    await _db.commit()


async def close() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None


# ───────────── Zaxira nusxa / tiklash ─────────────
REQUIRED_TABLES = {"users", "movies", "favorites", "channels", "settings", "views"}


async def backup_to(dest: str) -> None:
    """Butun bazaning yaxlit nusxasini dest faylga yozadi."""
    Path(dest).unlink(missing_ok=True)
    safe = dest.replace("'", "''")
    await _db.execute(f"VACUUM INTO '{safe}'")


def validate_backup(path: str) -> bool:
    """Fayl haqiqiy kino bot bazasi ekanini tekshiradi."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            con.execute("SELECT COUNT(*) FROM movies").fetchone()
        finally:
            con.close()
        return REQUIRED_TABLES <= names
    except Exception:
        return False


async def replace_from(src: str) -> None:
    """Joriy bazani src fayl bilan almashtiradi (tiklash)."""
    await close()
    for suffix in ("", "-wal", "-shm"):
        Path(_path + suffix).unlink(missing_ok=True)
    shutil.copyfile(src, _path)
    await init(_path)


async def _one(sql: str, params=()):
    cur = await _db.execute(sql, params)
    row = await cur.fetchone()
    await cur.close()
    return row


async def _all(sql: str, params=()):
    cur = await _db.execute(sql, params)
    rows = await cur.fetchall()
    await cur.close()
    return rows


async def _run(sql: str, params=()) -> int:
    cur = await _db.execute(sql, params)
    await _db.commit()
    last = cur.lastrowid
    await cur.close()
    return last


# ───────────── Foydalanuvchilar ─────────────
async def touch_user(uid: int, full_name: str, username: str | None) -> bool:
    """Foydalanuvchini qo'shadi/yangilaydi. Ban qilingan bo'lsa True qaytaradi."""
    now = int(time.time())
    await _db.execute(
        """INSERT INTO users(id, full_name, username, joined_at, last_active)
           VALUES(?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET full_name=excluded.full_name,
             username=excluded.username, last_active=excluded.last_active, blocked=0""",
        (uid, full_name, username, now, now),
    )
    await _db.commit()
    return await is_banned(uid)


FOREVER = 4102444800  # «umrbod» (2100-yil)


def fmt_left(until: int) -> str:
    """Jazo tugashigacha qolgan vaqt: «2 soat 10 daqiqa» yoki «umrbod»."""
    if until >= FOREVER - 1:
        return "umrbod"
    mins = max((int(until - time.time()) + 59) // 60, 1)  # daqiqaga yuqoriga yaxlitlanadi
    d, mins = divmod(mins, 1440)
    h, m = divmod(mins, 60)
    parts = []
    if d:
        parts.append(f"{d} kun")
    if h:
        parts.append(f"{h} soat")
    if m or not parts:
        parts.append(f"{m or 1} daqiqa")
    return " ".join(parts[:2])


async def ban_until(uid: int):
    """Faol ban bo'lsa — tugash vaqti (umrbod: FOREVER), bo'lmasa None."""
    row = await _one("SELECT banned, ban_until FROM users WHERE id=?", (uid,))
    if not row or not row["banned"]:
        return None
    if row["ban_until"] == 0:  # eski (muddatsiz) ban
        return FOREVER
    return row["ban_until"] if row["ban_until"] > time.time() else None


async def mute_until(uid: int):
    row = await _one("SELECT mute_until FROM users WHERE id=?", (uid,))
    return row["mute_until"] if row and row["mute_until"] > time.time() else None


async def set_ban(uid: int, flag: bool) -> None:
    await set_punish(uid, "ban", FOREVER if flag else 0)


async def set_punish(uid: int, kind: str, until: int) -> None:
    """kind: 'ban' | 'mute'. until: tugash vaqti (unix), 0 — jazoni bekor qilish."""
    now = int(time.time())
    if kind == "ban":
        await _run(
            """INSERT INTO users(id, full_name, username, joined_at, last_active, banned, ban_until)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET banned=excluded.banned, ban_until=excluded.ban_until""",
            (uid, "", None, now, now, 1 if until else 0, until),
        )
    else:
        await _run(
            """INSERT INTO users(id, full_name, username, joined_at, last_active, mute_until)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET mute_until=excluded.mute_until""",
            (uid, "", None, now, now, until),
        )


async def punish_status(uid: int) -> dict:
    return {"ban": await ban_until(uid) or 0, "mute": await mute_until(uid) or 0}


async def is_banned(uid: int) -> bool:
    return await ban_until(uid) is not None


async def mark_blocked(uid: int) -> None:
    await _run("UPDATE users SET blocked=1 WHERE id=?", (uid,))


async def broadcast_ids() -> list[int]:
    rows = await _all(
        "SELECT id FROM users WHERE blocked=0 AND (banned=0 OR (ban_until>0 AND ban_until<=?))", (int(time.time()),))
    return [r["id"] for r in rows]


# ───────────── Kinolar ─────────────
META_FIELDS = ("title", "description", "year", "genre", "country", "language", "quality")


def _derive(d: dict) -> tuple[str, str]:
    """(qidiruv matni, kichik harfli janrlar) — d dagi meta maydonlardan."""
    genre = d.get("genre", "") or ""
    search = norm(" ".join([d.get("title", ""), genre, d.get("year", ""), d.get("country", ""), d.get("language", "")]))
    genre_lc = ",".join(norm(g) for g in genre.split(",") if g.strip())
    return search, genre_lc


_movie_lock = asyncio.Lock()


async def add_movie(d: dict) -> int:
    search, genre_lc = _derive(d)
    async with _movie_lock:
        num = None
        if not d.get("series_id"):  # yakka kontent: o'z turi ichida tartib raqami (kodi)
            num = await _next_num(d.get("kind", "kino"))
        return await _run(
            """INSERT INTO movies(title, description, year, genre, genre_lc, country, language, quality,
                                  file_id, file_type, poster_id, search_text, created_at, src_chat, src_msg, status,
                                  kind, series_id, ep, cover_id, num, file_size, duration, mime, premiere_at, paid)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                d["title"], d.get("description", ""), d.get("year", ""), d.get("genre", ""), genre_lc,
                d.get("country", ""), d.get("language", ""), d.get("quality", ""),
                d["file_id"], d.get("file_type", "video"), d.get("poster", ""), search, int(time.time()),
                d.get("src_chat"), d.get("src_msg"), d.get("status", "live"),
                d.get("kind", "kino"), d.get("series_id") or None, d.get("ep"), d.get("cover", ""), num,
                int(d.get("file_size") or 0), int(d.get("duration") or 0), d.get("mime") or "", d.get("premiere_at"),
                1 if d.get("paid") else 0,
            ),
        )


async def _next_num(kind: str) -> int:
    """Shu tur uchun keyingi bo'sh raqam. Yakka (dona) va qismli kontent BIR hisobdan foydalanadi,
    shuning uchun m.1 ham dona multfilm, ham qismli multfilm bo'la olmaydi."""
    a = (await _one("SELECT COALESCE(MAX(num), 0) AS n FROM movies WHERE series_id IS NULL AND kind=?", (kind,)))["n"]
    b = (await _one("SELECT COALESCE(MAX(num), 0) AS n FROM series WHERE kind=?", (kind,)))["n"]
    return max(a, b) + 1


async def _reconcile_nums() -> None:
    """Eski bazani tuzatadi: raqami yo'q yoki qismli kontent bilan to'qnashgan yakka kontentga
    o'z turi bo'yicha yangi raqam beradi, so'ng (tur, raqam) bo'yicha yagona indeks yaratadi."""
    used = {(r["kind"], r["num"]) for r in await _all("SELECT kind, num FROM series WHERE num IS NOT NULL")}
    keep, need = set(), []
    for r in await _all("SELECT id, kind, num FROM movies WHERE series_id IS NULL ORDER BY id"):
        k = (r["kind"], r["num"])
        if r["num"] and k not in keep and k not in used:
            keep.add(k)
        else:
            need.append(r)
    top: dict[str, int] = {}
    for kind, num in keep | used:
        top[kind] = max(top.get(kind, 0), num)
    for r in need:
        top[r["kind"]] = top.get(r["kind"], 0) + 1
        await _db.execute("UPDATE movies SET num=? WHERE id=?", (top[r["kind"]], r["id"]))
    await _db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_movies_kind_num ON movies(kind, num) "
        "WHERE series_id IS NULL AND num IS NOT NULL"
    )


async def movie_by_num(n: int, kind: str = "kino"):
    """Yakka kontentni foydalanuvchi ko'radigan kodi bo'yicha topadi: kino — 1, 2, 3; multfilm — m.1; anime — a.1."""
    return await _one("SELECT * FROM movies WHERE series_id IS NULL AND kind=? AND num=?", (kind, n))


async def movie_by_code(text: str):
    """'12' (kino) yoki 'm.3' / 'a.3' (dona multfilm / anime) -> yakka kontent qatori yoki None."""
    t = (text or "").strip().lower()
    if t.isdigit():
        return await movie_by_num(int(t)) if len(t) < 10 else None
    c = parse_series_code(t)
    return await movie_by_num(c[2], c[0]) if c else None


async def update_movie_meta(mid: int, d: dict) -> None:
    """Faqat berilgan meta maydonlarni yangilaydi (fayl va poster tegilmaydi)."""
    movie = await get_movie(mid)
    if not movie:
        return
    cur = {k: movie[k] for k in META_FIELDS}
    cur.update({k: v for k, v in d.items() if k in META_FIELDS and v})
    search, genre_lc = _derive(cur)
    await _run(
        """UPDATE movies SET title=?, description=?, year=?, genre=?, genre_lc=?, country=?,
                             language=?, quality=?, search_text=? WHERE id=?""",
        (cur["title"], cur["description"], cur["year"], cur["genre"], genre_lc, cur["country"],
         cur["language"], cur["quality"], search, mid),
    )


async def replace_movie_meta(mid: int, d: dict) -> None:
    """Admin tahriri: maydonlar to'liq almashtiriladi (bo'sh qiymat = tozalash)."""
    cur = {k: d.get(k, "") or "" for k in META_FIELDS}
    search, genre_lc = _derive(cur)
    await _run(
        """UPDATE movies SET title=?, description=?, year=?, genre=?, genre_lc=?, country=?,
                             language=?, quality=?, search_text=?, paid=? WHERE id=?""",
        (cur["title"], cur["description"], cur["year"], cur["genre"], genre_lc, cur["country"],
         cur["language"], cur["quality"], search, 1 if d.get("paid") else 0, mid),
    )


async def set_cover(mid: int, cover_id: str) -> None:
    """Mini App muqovasi (faqat ilova ichida ko'rinadi). Bo'sh qiymat = olib tashlash (logo chiqadi)."""
    await _run("UPDATE movies SET cover_id=? WHERE id=?", (cover_id or "", mid))


async def set_series_cover(sid: int, cover_id: str) -> None:
    await _run("UPDATE series SET cover_id=? WHERE id=?", (cover_id or "", sid))


async def set_poster(mid: int, poster_id: str) -> None:
    await _run("UPDATE movies SET poster_id=? WHERE id=?", (poster_id, mid))


async def get_movie(mid: int):
    return await _one("SELECT * FROM movies WHERE id=?", (mid,))


async def pending_movies():
    return await _all("SELECT * FROM movies WHERE status='pending' ORDER BY id")


def _scope_sql(scope) -> tuple[str, tuple]:
    """Chiqarish doirasi: None/'all' | 'single' | 'single:<tur>' | 's:<serial id>'."""
    if not scope or scope == "all":
        return "", ()
    if scope == "single":
        return " AND series_id IS NULL", ()
    if scope.startswith("single:"):
        return " AND series_id IS NULL AND kind=?", (scope.split(":", 1)[1],)
    if scope.startswith("s:") and scope[2:].isdigit():
        return " AND series_id=?", (int(scope[2:]),)
    raise ValueError("bad scope")


# Rasm (muqova) qo'yilgan kutilayotganlar: yakka kino — o'zining, qism — serialning muqovasi bor
_HAS_COVER = ("((series_id IS NULL AND cover_id != '') OR "
              "(series_id IS NOT NULL AND series_id IN (SELECT id FROM series WHERE cover_id != '')))")


async def publish_pending(scope=None) -> tuple[list[int], int]:
    """Kutilayotganlardan faqat RASMI borlarini chiqaradi (butun yoki doira bo'yicha).
    (chiqarilgan id'lar, rasmi yo'qligi uchun qolganlar soni) qaytaradi."""
    extra, params = _scope_sql(scope)
    ids = [r["id"] for r in await _all(
        f"SELECT id FROM movies WHERE status='pending' AND {_HAS_COVER}{extra} ORDER BY id", params)]
    if ids:
        marks = ",".join("?" * len(ids))
        await _run(f"UPDATE movies SET status='live' WHERE status='pending' AND id IN ({marks})", tuple(ids))
    # Qolganlar: kino yoki serial NOMI bo'yicha (serialning qismlari alohida sanalmaydi)
    left = (await _one(
        f"SELECT COUNT(DISTINCT CASE WHEN series_id IS NULL THEN 'm' || id ELSE 's' || series_id END) c "
        f"FROM movies WHERE status='pending'{extra}", params))["c"]
    return ids, left


# ───────────── Qismli kontent (serial / multfilm / anime) ─────────────
KIND_PREFIX = {"serial": "s", "multfilm": "m", "anime": "a"}
PREFIX_KIND = {v: k for k, v in KIND_PREFIX.items()}
KINDS = ("kino", "multfilm", "serial", "anime")


# Har tur o'z raqamini alohida sanaydi (s.1, s.2 | m.1 | a.1). Ichki id (series.id) esa umumiy.
_NUM: dict[int, int] = {}                 # series.id -> turdagi tartib raqami
_BYNUM: dict[tuple[str, int], int] = {}   # (tur, raqam) -> series.id
_series_lock = asyncio.Lock()


async def _load_nums() -> None:
    _NUM.clear()
    _BYNUM.clear()
    for r in await _all("SELECT id, kind, num FROM series"):
        if r["num"]:
            _NUM[r["id"]] = r["num"]
            _BYNUM[(r["kind"], r["num"])] = r["id"]


def movie_code(m) -> str:
    """Yakka kontent kodi: kino — 1, 2, 3; multfilm — m.1; anime — a.1."""
    keys = m.keys()
    if "series_id" in keys and m["series_id"]:
        return series_code(m["kind"], m["series_id"])
    num = (m["num"] if "num" in keys else None) or m["id"]
    pre = KIND_PREFIX.get(m["kind"]) if "kind" in keys else None
    return f"{pre}.{num}" if pre else str(num)


def series_code(kind: str, sid: int) -> str:
    return f"{KIND_PREFIX.get(kind, 's')}.{_NUM.get(sid, sid)}"


def parse_series_code(text: str):
    """'s.12' / 'M.3' -> (tur, series.id yoki 0, raqam) yoki None. Shunday qismli kontent yo'q bo'lsa id=0."""
    import re
    m = re.fullmatch(r"([sma])\s*\.\s*(\d+)", (text or "").strip().lower())
    if not m:
        return None
    kind = PREFIX_KIND[m.group(1)]
    return kind, _BYNUM.get((kind, int(m.group(2))), 0), int(m.group(2))


async def add_series(d: dict) -> int:
    search, genre_lc = _derive(d)
    kind = d.get("kind", "serial")
    async with _movie_lock:  # dona kontent bilan bir qulf: raqamlar to'qnashmasin
        num = await _next_num(kind)
        sid = await _run(
            """INSERT INTO series(kind, title, description, year, genre, genre_lc, country, language,
                                  poster_id, search_text, created_at, num, cover_id, paid, free_eps)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (kind, d["title"], d.get("description", ""), d.get("year", ""), d.get("genre", ""),
             genre_lc, d.get("country", ""), d.get("language", ""), d.get("poster", ""), search,
             int(time.time()), num, d.get("cover", ""), 1 if d.get("paid") else 0,
             int(d.get("free_eps") or 0) if d.get("paid") else 0),
        )
        _NUM[sid] = num
        _BYNUM[(kind, num)] = sid
    return sid


async def get_series(sid: int):
    return await _one("SELECT * FROM series WHERE id=?", (sid,))


async def list_series():
    return await _all(
        """SELECT s.*, (SELECT COUNT(*) FROM movies e WHERE e.series_id=s.id) AS eps,
                  (SELECT MAX(ep) FROM movies e WHERE e.series_id=s.id) AS last_ep
           FROM series s ORDER BY s.id DESC"""
    )


async def update_series_meta(sid: int, d: dict) -> None:
    """Serial ma'lumotlarini to'liq almashtiradi va qismlardagi nusxalarni ham yangilaydi."""
    cur = {k: (d.get(k) or "") for k in META_FIELDS}
    search, genre_lc = _derive(cur)
    await _db.execute(
        """UPDATE series SET title=?, description=?, year=?, genre=?, genre_lc=?, country=?, language=?,
                             search_text=?, paid=?, free_eps=? WHERE id=?""",
        (cur["title"], cur["description"], cur["year"], cur["genre"], genre_lc, cur["country"],
         cur["language"], search, 1 if d.get("paid") else 0,
         int(d.get("free_eps") or 0) if d.get("paid") else 0, sid),
    )
    await _db.execute(
        """UPDATE movies SET title=?, description=?, year=?, genre=?, genre_lc=?, country=?, language=?,
                             search_text=? WHERE series_id=?""",
        (cur["title"], cur["description"], cur["year"], cur["genre"], genre_lc, cur["country"],
         cur["language"], search, sid),
    )
    await _db.commit()


async def set_series_poster(sid: int, poster_id: str) -> None:
    await _run("UPDATE series SET poster_id=? WHERE id=?", (poster_id, sid))


async def delete_series(sid: int) -> None:
    await _db.execute("DELETE FROM movies WHERE series_id=?", (sid,))
    await _db.execute("DELETE FROM favorites WHERE movie_id=?", (-sid,))
    await _db.execute("DELETE FROM sources WHERE series_id=?", (sid,))
    await _db.execute("DELETE FROM series WHERE id=?", (sid,))
    await _db.commit()
    for key, val in list(_BYNUM.items()):
        if val == sid:
            del _BYNUM[key]
    _NUM.pop(sid, None)


async def episodes(sid: int, live_only: bool = True):
    extra = " AND status='live'" if live_only else ""
    return await _all(f"SELECT * FROM movies WHERE series_id=?{extra} ORDER BY ep", (sid,))


async def set_episode_no(mid: int, ep: int) -> None:
    await _run("UPDATE movies SET ep=? WHERE id=?", (ep, mid))


async def episode_by_no(sid: int, ep: int):
    return await _one("SELECT * FROM movies WHERE series_id=? AND ep=?", (sid, ep))


async def search_series(q: str, limit: int = 5):
    """Botdagi matnli qidiruv uchun: kamida bitta chiqarilgan qismi bor seriallar."""
    where, params = [], []
    for w in norm(q).split():
        where.append("s.search_text LIKE ?")
        params.append(f"%{w}%")
    w_sql = (" AND " + " AND ".join(where)) if where else ""
    return await _all(
        f"""SELECT s.* FROM series s WHERE EXISTS
            (SELECT 1 FROM movies e WHERE e.series_id=s.id AND e.status='live'){w_sql}
            ORDER BY s.id DESC LIMIT ?""",
        [*params, limit],
    )


_ITEMS = """
SELECT 'm' AS t, m.id AS id, m.id AS fk, m.title AS title, m.year AS year, m.genre AS genre, m.genre_lc AS genre_lc,
       m.country AS country, m.quality AS quality, m.views AS views, m.poster_id AS poster_id, m.kind AS kind,
       m.search_text AS search_text, m.created_at AS created_at, 0 AS eps, m.cover_id AS cover_id, m.num AS num, m.paid AS paid
FROM movies m WHERE m.status='live' AND m.series_id IS NULL
UNION ALL
SELECT 's', s.id, -s.id, s.title, s.year, s.genre, s.genre_lc, s.country, '', v.views, s.poster_id, s.kind,
       s.search_text, v.last, v.n, s.cover_id, NULL, s.paid
FROM series s JOIN (
  SELECT series_id, SUM(views) AS views, MAX(created_at) AS last, COUNT(*) AS n
  FROM movies WHERE status='live' AND series_id IS NOT NULL GROUP BY series_id
) v ON v.series_id = s.id
"""

_ITEM_ORDER = {
    "new": "i.created_at DESC, i.t, i.id DESC",
    "top": "i.views DESC, i.id DESC",
    "year": "CAST(i.year AS INTEGER) DESC, i.id DESC",
    # Admin ro'yxati: turi bo'yicha guruh, har bir turda avval rasmsizlar, keyin yangilari
    "admin": ("CASE i.kind WHEN 'kino' THEN 0 WHEN 'multfilm' THEN 1 WHEN 'serial' THEN 2 ELSE 3 END, "
              "(i.cover_id != '') ASC, i.created_at DESC, i.t, i.id DESC"),
}


def _item_where(q: str, genre: str, kind: str, cover: str) -> tuple[list, list]:
    where, params = [], []
    code = parse_series_code(q)
    if code:
        where.append("((i.t='s' AND i.kind=? AND i.id=?) OR (i.t='m' AND i.kind=? AND i.num=?))")
        params += [code[0], code[1], code[0], code[2]]
    else:
        for w in norm(q).split():
            where.append("i.search_text LIKE ?")
            params.append(f"%{w}%")
    if kind in KINDS:
        where.append("i.kind=?")
        params.append(kind)
    if cover == "with":
        where.append("i.cover_id != ''")
    elif cover == "without":
        where.append("i.cover_id = ''")
    if genre:
        where.append("(',' || i.genre_lc || ',') LIKE ?")
        params.append(f"%,{norm(genre)},%")
    return where, params


async def item_matrix(q: str = "") -> dict:
    """Admin uchun: {tur: {with: rasmli soni, without: rasmsiz soni}} (qidiruvga mos)."""
    where, params = _item_where(q, "", "", "")
    w_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = await _all(
        f"WITH items AS ({_ITEMS}) SELECT i.kind AS k, (i.cover_id != '') AS c, COUNT(*) AS n "
        f"FROM items i{w_sql} GROUP BY i.kind, c", params)
    out: dict = {}
    for r in rows:
        out.setdefault(r["k"], {"with": 0, "without": 0})["with" if r["c"] else "without"] = r["n"]
    return out


async def search_items(q: str = "", genre: str = "", sort: str = "new", limit: int = 20, offset: int = 0,
                       fav_user: int | None = None, kind: str = "", cover: str = ""):
    """Katalog uchun: kinolar + seriallar aralash. (qatorlar, jami) qaytaradi."""
    frm = "FROM items i"
    params: list = []
    if fav_user is not None:
        frm += " JOIN favorites f ON f.movie_id=i.fk AND f.user_id=?"
        params.append(fav_user)
    where, wparams = _item_where(q, genre, kind, cover)
    params += wparams
    w_sql = (" WHERE " + " AND ".join(where)) if where else ""
    cte = f"WITH items AS ({_ITEMS}) "
    total = (await _one(f"{cte}SELECT COUNT(*) AS c {frm}{w_sql}", params))["c"]
    order = _ITEM_ORDER.get(sort, _ITEM_ORDER["new"])
    rows = await _all(f"{cte}SELECT i.* {frm}{w_sql} ORDER BY {order} LIMIT ? OFFSET ?", [*params, limit, offset])
    return rows, total


async def hero_items(limit: int = 5):
    """Katalog tepasidagi slayder uchun: avval muqovasi borlar, eng yangilaridan."""
    cte = f"WITH items AS ({_ITEMS}) "
    return await _all(
        f"{cte}SELECT i.* FROM items i ORDER BY (i.cover_id != '') DESC, i.created_at DESC LIMIT ?", (limit,))


_CTE = f"WITH items AS ({_ITEMS}) "


async def hero_latest(limit: int = 1):
    """Slayder 1: eng oxirgi qo'yilgan kontent (muqovasi borlari birinchi)."""
    return await _all(
        f"{_CTE}SELECT i.* FROM items i ORDER BY (i.cover_id != '') DESC, i.created_at DESC, i.id DESC LIMIT ?", (limit,))


async def hero_top_since(since: int, limit: int = 6):
    """Slayder 2: berilgan vaqtdan beri (oxirgi 24 soat) eng ko'p ko'rilganlar, ko'pdan kamga."""
    keys = await _all(
        """SELECT CASE WHEN m.series_id IS NULL THEN 'm' ELSE 's' END AS t,
                  COALESCE(m.series_id, m.id) AS id, COUNT(*) AS n
           FROM views v JOIN movies m ON m.id = v.movie_id
           WHERE v.at >= ? AND m.status = 'live'
           GROUP BY 1, 2 ORDER BY n DESC, id DESC LIMIT ?""", (since, limit))
    rows = []
    for k in keys:
        r = await _one(f"{_CTE}SELECT i.* FROM items i WHERE i.t=? AND i.id=?", (k["t"], k["id"]))
        if r:
            rows.append(r)
    return rows


async def hero_top_all(limit: int = 6):
    """Zaxira: oxirgi 24 soatda hech kim ko'rmagan bo'lsa — umumiy eng ko'p ko'rilgan."""
    return await _all(f"{_CTE}SELECT i.* FROM items i ORDER BY i.views DESC, i.id DESC LIMIT ?", (limit,))


async def hero_random(paid: int, limit: int = 10):
    """Tasodifiy kontent: paid=1 — pullik, paid=0 — tekin (muqovasi borlari birinchi)."""
    return await _all(
        f"{_CTE}SELECT i.* FROM items i WHERE COALESCE(i.paid, 0) = ? "
        "ORDER BY (i.cover_id != '') DESC, RANDOM() LIMIT ?", (1 if paid else 0, limit))


# ───────────── Reklama (slayderda ko'rinadi) ─────────────
async def ads_list(active_only: bool = False):
    """active_only: yoqilgan va muddati tugamaganlar (expires_at=0 — cheksiz)."""
    if active_only:
        return await _all("SELECT * FROM ads WHERE active=1 AND (expires_at=0 OR expires_at>?) ORDER BY id",
                          (int(time.time()),))
    return await _all("SELECT * FROM ads ORDER BY id")


async def ad_get(aid: int):
    return await _one("SELECT * FROM ads WHERE id=?", (aid,))


async def ad_add(title: str, image: str, url: str, btn: str, expires_at: int = 0) -> int:
    return await _run(
        "INSERT INTO ads(title, image, url, btn, active, expires_at, created_at) VALUES(?,?,?,?,1,?,?)",
        (title, image, url, btn, expires_at, int(time.time())))


async def ad_update(aid: int, d: dict) -> None:
    cols = [k for k in ("title", "image", "url", "btn", "active", "expires_at") if k in d]
    if not cols:
        return
    await _run(f"UPDATE ads SET {', '.join(c + '=?' for c in cols)} WHERE id=?", [*(d[c] for c in cols), aid])


async def ad_delete(aid: int) -> None:
    await _run("DELETE FROM ads WHERE id=?", (aid,))


async def ad_click(aid: int) -> None:
    await _run("UPDATE ads SET clicks=clicks+1 WHERE id=?", (aid,))


async def similar_items(t: str, iid: int, kind: str, genre: str, year: str, limit: int = 8):
    """O'xshash kontent: umumiy janrlar (eng muhim), bir xil tur va yaqin yil bo'yicha ball beradi."""
    genres = [g for g in (norm(x) for x in (genre or "").split(",")) if g]
    conds, params = ["i.kind=?"], [kind]
    for g in genres:
        conds.append("(',' || i.genre_lc || ',') LIKE ?")
        params.append(f"%,{g},%")
    sql = (f"WITH items AS ({_ITEMS}) SELECT i.* FROM items i "
           f"WHERE NOT (i.t=? AND i.id=?) AND ({' OR '.join(conds)}) ORDER BY i.views DESC LIMIT 300")
    rows = await _all(sql, [t, iid, *params])
    gset = set(genres)
    y0 = int(year) if str(year or "").isdigit() else None

    def score(r) -> int:
        have = {norm(x) for x in (r["genre_lc"] or "").split(",") if x.strip()}
        sc = 3 * len(gset & have) + (2 if r["kind"] == kind else 0)
        ry = str(r["year"] or "")
        if y0 is not None and ry.isdigit() and abs(int(ry) - y0) <= 3:
            sc += 1
        return sc

    ranked = sorted(rows, key=lambda r: (-score(r), -(r["views"] or 0)))
    return ranked[:limit]


async def watched_episodes(uid: int, sid: int):
    """Foydalanuvchi ko'rgan qismlar (eng oxirgisi birinchi)."""
    return await _all(
        "SELECT v.movie_id AS mid, MAX(v.at) AS at FROM views v JOIN movies m ON m.id=v.movie_id "
        "WHERE v.user_id=? AND m.series_id=? GROUP BY v.movie_id ORDER BY at DESC, mid DESC",
        (uid, sid),
    )


async def pending_groups():
    """Kutilayotganlarni guruhlaydi: [{key,label_kind,series,rows}] — avval yakka kinolar, keyin seriallar."""
    rows = await pending_movies()
    singles: dict[str, list] = {}
    eps: dict[int, list] = {}
    for r in rows:
        if r["series_id"] is None:
            singles.setdefault(r["kind"], []).append(r)
        else:
            eps.setdefault(r["series_id"], []).append(r)
    groups = []
    for kind in KINDS:
        if singles.get(kind):
            groups.append({"key": f"single:{kind}", "kind": kind, "series": None, "rows": singles[kind]})
    for sid, lst in eps.items():
        ser = await get_series(sid)
        if ser:
            groups.append({"key": f"s:{sid}", "kind": ser["kind"], "series": ser,
                           "rows": sorted(lst, key=lambda r: r["ep"] or 0)})
    return groups


async def movie_by_source(chat_id: int, msg_id: int):
    return await _one("SELECT * FROM movies WHERE src_chat=? AND src_msg=?", (chat_id, msg_id))


async def delete_movie(mid: int) -> None:
    await _db.execute("DELETE FROM movies WHERE id=?", (mid,))
    await _db.execute("DELETE FROM favorites WHERE movie_id=?", (mid,))
    await _db.commit()


_ORDER = {
    "new": "m.id DESC",
    "top": "m.views DESC, m.id DESC",
    "year": "CAST(m.year AS INTEGER) DESC, m.id DESC",
}


async def search_movies(q: str = "", genre: str = "", sort: str = "new",
                        limit: int = 20, offset: int = 0, fav_user: int | None = None,
                        status: str = "live"):
    """(qatorlar, jami) qaytaradi. Odatda faqat chiqarilgan (live) kinolar."""
    frm = "FROM movies m"
    params: list = []
    if fav_user is not None:
        frm += " JOIN favorites f ON f.movie_id=m.id AND f.user_id=?"
        params.append(fav_user)
    where = ["m.status = ?", "m.series_id IS NULL"]
    params.append(status)
    for w in norm(q).split():
        where.append("m.search_text LIKE ?")
        params.append(f"%{w}%")
    if genre:
        where.append("(',' || m.genre_lc || ',') LIKE ?")
        params.append(f"%,{norm(genre)},%")
    w_sql = (" WHERE " + " AND ".join(where)) if where else ""
    total = (await _one(f"SELECT COUNT(*) AS c {frm}{w_sql}", params))["c"]
    order = _ORDER.get(sort, _ORDER["new"])
    rows = await _all(f"SELECT m.* {frm}{w_sql} ORDER BY {order} LIMIT ? OFFSET ?", [*params, limit, offset])
    return rows, total


async def top_movies(limit: int = 10):
    return await _all("SELECT * FROM movies WHERE status='live' AND series_id IS NULL ORDER BY views DESC, id DESC LIMIT ?", (limit,))


async def new_movies(limit: int = 10):
    return await _all("SELECT * FROM movies WHERE status='live' AND series_id IS NULL ORDER BY id DESC LIMIT ?", (limit,))


async def random_movie():
    return await _one("SELECT * FROM movies WHERE status='live' AND series_id IS NULL ORDER BY RANDOM() LIMIT 1")


async def genres(limit: int = 24) -> list[str]:
    rows = await _all(
        "SELECT genre FROM movies WHERE genre != '' AND status='live' AND series_id IS NULL "
        "UNION ALL SELECT genre FROM series WHERE genre != '' AND id IN "
        "(SELECT series_id FROM movies WHERE status='live' AND series_id IS NOT NULL)"
    )
    count: dict[str, int] = {}
    shown: dict[str, str] = {}
    for r in rows:
        for g in r["genre"].split(","):
            g = g.strip()
            if not g:
                continue
            k = norm(g)
            shown.setdefault(k, g)
            count[k] = count.get(k, 0) + 1
    ordered = sorted(count, key=lambda k: (-count[k], k))
    return [shown[k] for k in ordered[:limit]]


async def add_view(uid: int, mid: int) -> None:
    await _db.execute("INSERT INTO views(user_id, movie_id, at) VALUES(?,?,?)", (uid, mid, int(time.time())))
    await _db.execute("UPDATE movies SET views = views + 1 WHERE id=?", (mid,))
    await _db.commit()


# ───────────── Saralanganlar ─────────────
async def is_fav(uid: int, mid: int) -> bool:
    return bool(await _one("SELECT 1 FROM favorites WHERE user_id=? AND movie_id=?", (uid, mid)))


async def toggle_fav(uid: int, mid: int) -> bool:
    """Yangi holatni qaytaradi: True = saralanganlarda."""
    if await is_fav(uid, mid):
        await _run("DELETE FROM favorites WHERE user_id=? AND movie_id=?", (uid, mid))
        return False
    await _run("INSERT OR IGNORE INTO favorites(user_id, movie_id) VALUES(?,?)", (uid, mid))
    return True


async def fav_ids(uid: int) -> list[int]:
    rows = await _all("SELECT movie_id FROM favorites WHERE user_id=?", (uid,))
    return [r["movie_id"] for r in rows]


async def fav_movies(uid: int, limit: int = 30):
    return await _all(
        "SELECT m.* FROM movies m JOIN favorites f ON f.movie_id=m.id WHERE f.user_id=? AND m.status='live' AND m.series_id IS NULL ORDER BY m.id DESC LIMIT ?",
        (uid, limit),
    )


# ───────────── Kanallar (majburiy obuna) ─────────────
async def add_channel(chat_id: int, title: str, link: str) -> None:
    await _run(
        """INSERT INTO channels(chat_id, title, link) VALUES(?,?,?)
           ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, link=excluded.link""",
        (chat_id, title, link),
    )


async def list_channels():
    return await _all("SELECT * FROM channels ORDER BY id")


async def del_channel(cid: int) -> None:
    await _run("DELETE FROM channels WHERE id=?", (cid,))


# ───────────── Avto-yuklash kanallari (manba) ─────────────
async def add_source(chat_id: int, title: str, kind: str = "kino", series_id: int | None = None) -> None:
    """Qayta ulansa tur/serial o'zgarmaydi, faqat nomi yangilanadi (berilgan bo'lsa — yangilanadi)."""
    await _run(
        """INSERT INTO sources(chat_id, title, kind, series_id) VALUES(?,?,?,?)
           ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title,
             kind=CASE WHEN excluded.series_id IS NOT NULL OR excluded.kind != 'kino' THEN excluded.kind ELSE sources.kind END,
             series_id=COALESCE(excluded.series_id, sources.series_id)""",
        (chat_id, title, kind, series_id),
    )


async def get_source(chat_id: int):
    return await _one("SELECT * FROM sources WHERE chat_id=?", (chat_id,))


async def list_sources():
    return await _all("SELECT * FROM sources ORDER BY rowid")


async def del_source(chat_id: int) -> None:
    await _run("DELETE FROM sources WHERE chat_id=?", (chat_id,))


async def is_source(chat_id: int) -> bool:
    return bool(await _one("SELECT 1 FROM sources WHERE chat_id=?", (chat_id,)))


# ───────────── Qoralama: Mini App'da kiritilgan, video kutilayotgan kino ─────────────
async def save_draft(admin_id: int, data: dict) -> None:
    await _run(
        """INSERT INTO drafts(admin_id, data, created_at) VALUES(?,?,?)
           ON CONFLICT(admin_id) DO UPDATE SET data=excluded.data, created_at=excluded.created_at""",
        (admin_id, json.dumps(data, ensure_ascii=False), int(time.time())),
    )


async def get_draft(admin_id: int) -> dict | None:
    row = await _one("SELECT data FROM drafts WHERE admin_id=?", (admin_id,))
    if not row:
        return None
    try:
        return json.loads(row["data"])
    except ValueError:
        return None


async def clear_draft(admin_id: int) -> None:
    await _run("DELETE FROM drafts WHERE admin_id=?", (admin_id,))


# ───────────── Adminlar ─────────────
async def list_admins():
    return await _all("SELECT * FROM admins ORDER BY added_at, id")


async def get_admin(aid: int):
    return await _one("SELECT * FROM admins WHERE id=?", (aid,))


async def save_admin(aid: int, name: str, role: str, perms: str, sections: str, added_by: int | None) -> None:
    await _run(
        """INSERT INTO admins(id, name, role, perms, sections, added_by, added_at) VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name, role=excluded.role,
             perms=excluded.perms, sections=excluded.sections""",
        (aid, name, role, perms, sections, added_by, int(time.time())),
    )


async def del_admin(aid: int) -> None:
    await _run("DELETE FROM admins WHERE id=?", (aid,))


async def user_name(uid: int) -> str:
    """Foydalanuvchining bazadagi ismi (botga kirgan bo'lsa)."""
    row = await _one("SELECT full_name, username FROM users WHERE id=?", (uid,))
    if not row:
        return ""
    return (row["full_name"] or "").strip() or (f"@{row['username']}" if row["username"] else "")


# ───────────── Teatr ─────────────
def media_meta(media) -> dict:
    """Telegram video/fayldan teatr uchun kerakli ma'lumot."""
    return {
        "file_size": int(getattr(media, "file_size", 0) or 0),
        "duration": int(getattr(media, "duration", 0) or 0),
        "mime": getattr(media, "mime_type", "") or "",
    }


async def q_all(sql: str, params=()):
    return await _all(sql, params)


async def q_one(sql: str, params=()):
    return await _one(sql, params)


async def q_run(sql: str, params=()) -> int:
    return await _run(sql, params)


async def th_get_slots(day: str) -> list:
    return await _all("SELECT * FROM theatre_slots WHERE day=? ORDER BY idx", (day,))


async def th_set_slots(day: str, rows: list[tuple[int, int, str]]) -> None:
    """rows: (idx, movie_id, tag). Kun rejasi bir marta yoziladi."""
    await _db.execute("DELETE FROM theatre_slots WHERE day=?", (day,))
    await _db.executemany(
        "INSERT INTO theatre_slots(day, idx, movie_id, tag) VALUES(?,?,?,?)",
        [(day, i, m, t) for i, m, t in rows],
    )
    await _db.commit()


async def th_set_meta(mid: int, size: int, duration: int = 0, mime: str = "") -> None:
    await _run(
        "UPDATE movies SET file_size=?, duration=CASE WHEN ?>0 THEN ? ELSE duration END, "
        "mime=CASE WHEN ?!='' THEN ? ELSE mime END WHERE id=?",
        (size, duration, duration, mime, mime, mid),
    )


async def th_room_add(d: dict) -> None:
    now = time.time()
    await _run(
        """INSERT INTO theatre_rooms(id, owner, owner_name, title, movie_id, salt, pw, created_at, last_active, pos_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (d["id"], d["owner"], d["owner_name"], d["title"], d["movie_id"], d["salt"], d["pw"], int(now), int(now), now),
    )
    await _run("INSERT OR IGNORE INTO theatre_members(room, user_id) VALUES(?,?)", (d["id"], d["owner"]))


async def th_room_get(rid: str):
    return await _one("SELECT * FROM theatre_rooms WHERE id=?", (rid,))


async def th_rooms_list(since: int, limit: int = 30):
    return await _all(
        "SELECT r.*, m.title AS mtitle FROM theatre_rooms r JOIN movies m ON m.id=r.movie_id "
        "WHERE r.last_active >= ? ORDER BY r.last_active DESC LIMIT ?", (since, limit))


async def th_rooms_of(owner: int, since: int) -> int:
    row = await _one("SELECT COUNT(*) c FROM theatre_rooms WHERE owner=? AND last_active>=?", (owner, since))
    return row["c"]


async def th_room_state(rid: str, playing: bool, pos: float, pos_at: float) -> None:
    await _run("UPDATE theatre_rooms SET playing=?, pos=?, pos_at=?, last_active=? WHERE id=?",
               (1 if playing else 0, pos, pos_at, int(time.time()), rid))


async def th_room_touch(rid: str) -> None:
    await _run("UPDATE theatre_rooms SET last_active=? WHERE id=?", (int(time.time()), rid))


async def th_room_del(rid: str) -> None:
    await _db.execute("DELETE FROM theatre_rooms WHERE id=?", (rid,))
    await _db.execute("DELETE FROM theatre_members WHERE room=?", (rid,))
    await _db.execute("DELETE FROM theatre_chat WHERE room=?", (rid,))
    await _db.commit()


async def th_rooms_cleanup(older_than: int) -> None:
    for r in await _all("SELECT id FROM theatre_rooms WHERE last_active < ?", (older_than,)):
        await th_room_del(r["id"])
    await _run("DELETE FROM theatre_chat WHERE room LIKE 'pub:%' AND at < ?", (int(time.time()) - 2 * 86400,))


async def th_member_add(rid: str, uid: int) -> None:
    await _run("INSERT OR IGNORE INTO theatre_members(room, user_id) VALUES(?,?)", (rid, uid))


async def th_member_has(rid: str, uid: int) -> bool:
    return bool(await _one("SELECT 1 FROM theatre_members WHERE room=? AND user_id=?", (rid, uid)))


async def th_chat_add(room: str, uid: int, name: str, text: str) -> dict:
    now = int(time.time())
    mid = await _run("INSERT INTO theatre_chat(room, user_id, name, text, at) VALUES(?,?,?,?,?)",
                     (room, uid, name, text, now))
    return {"id": mid, "uid": uid, "name": name, "text": text, "at": now}


async def th_chat_list(room: str, after: int = 0, limit: int = 50) -> list:
    if after > 0:
        return await _all("SELECT * FROM theatre_chat WHERE room=? AND id>? ORDER BY id LIMIT ?", (room, after, limit))
    rows = await _all("SELECT * FROM theatre_chat WHERE room=? ORDER BY id DESC LIMIT ?", (room, limit))
    return list(reversed(rows))


async def th_chat_last_at(uid: int) -> int:
    row = await _one("SELECT MAX(at) m FROM theatre_chat WHERE user_id=?", (uid,))
    return (row["m"] or 0) if row else 0


async def th_chat_delete(mid: int) -> None:
    await _run("DELETE FROM theatre_chat WHERE id=?", (mid,))


# ───────────── Umumiy chat ─────────────
CHAT_KEEP = 1000  # bazada shuncha oxirgi xabar saqlanadi


async def chat_add(uid: int, name: str, text: str) -> dict:
    now = int(time.time())
    mid = await _run("INSERT INTO chat_messages(user_id, name, text, at) VALUES(?,?,?,?)", (uid, name, text, now))
    if mid % 50 == 0:  # ba'zida eskilarini tozalab turamiz
        await _run("DELETE FROM chat_messages WHERE id <= ?", (mid - CHAT_KEEP,))
    return {"id": mid, "uid": uid, "name": name, "text": text, "at": now}


async def chat_list(after: int = 0, limit: int = 50) -> list:
    """after=0 — oxirgi `limit` ta xabar; after>0 — shundan keyingi yangilari."""
    if after > 0:
        return await _all("SELECT * FROM chat_messages WHERE id > ? ORDER BY id LIMIT ?", (after, limit))
    rows = await _all("SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?", (limit,))
    return list(reversed(rows))


async def chat_last_at(uid: int) -> int:
    row = await _one("SELECT MAX(at) m FROM chat_messages WHERE user_id=?", (uid,))
    return (row["m"] or 0) if row else 0


async def chat_delete(mid: int) -> None:
    await _run("DELETE FROM chat_messages WHERE id=?", (mid,))


# ───────────── Sozlamalar ─────────────
# ───────────── Adminlar tarixi ─────────────
ALOG_KEEP = 5000  # eng so'nggi shuncha yozuv saqlanadi


async def alog_add(admin_id: int, name: str, role: str, cat: str, action: str, detail: str = "") -> None:
    await _db.execute(
        "INSERT INTO admin_log(admin_id, admin_name, role, cat, action, detail, at) VALUES(?,?,?,?,?,?,?)",
        (admin_id, name, role, cat, action, detail, int(time.time())),
    )
    await _db.execute("DELETE FROM admin_log WHERE id <= (SELECT MAX(id) FROM admin_log) - ?", (ALOG_KEEP,))
    await _db.commit()


async def alog_list(before: int = 0, admin_id: int = 0, limit: int = 40) -> list:
    sql, params = "SELECT * FROM admin_log WHERE 1=1", []
    if before:
        sql += " AND id < ?"; params.append(before)
    if admin_id:
        sql += " AND admin_id = ?"; params.append(admin_id)
    return await _all(sql + " ORDER BY id DESC LIMIT ?", (*params, limit))


async def alog_admins() -> list:
    """Tarixda uchragan adminlar (filtr uchun): eng oxirgi ismi bilan."""
    return await _all(
        "SELECT admin_id, admin_name, role, COUNT(*) n FROM admin_log GROUP BY admin_id ORDER BY MAX(id) DESC")


async def th_members(rid: str) -> list:
    return await _all(
        "SELECT u.id, u.full_name FROM theatre_members tm LEFT JOIN users u ON u.id=tm.user_id WHERE tm.room=?", (rid,))


# ───────────── VIP va to'lovlar ─────────────
async def vip_until(uid: int) -> int:
    row = await _one("SELECT vip_until FROM users WHERE id=?", (uid,))
    return int(row["vip_until"] or 0) if row else 0


async def vip_grant(uid: int, days: int) -> int | None:
    """VIP muddatini `days` kunga uzaytiradi (faol bo'lsa — ustiga qo'shiladi). Foydalanuvchi botga kirmagan bo'lsa None."""
    row = await _one("SELECT vip_until FROM users WHERE id=?", (uid,))
    if not row:
        return None
    until = max(int(time.time()), int(row["vip_until"] or 0)) + days * 86400
    await _run("UPDATE users SET vip_until=? WHERE id=?", (until, uid))
    return until


async def vip_revoke(uid: int) -> bool:
    row = await _one("SELECT 1 FROM users WHERE id=?", (uid,))
    if not row:
        return False
    await _run("UPDATE users SET vip_until=0 WHERE id=?", (uid,))
    return True


async def vip_users(ids: list[int]) -> set[int]:
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    rows = await _all(f"SELECT id FROM users WHERE vip_until>? AND id IN ({marks})", (int(time.time()), *ids))
    return {r["id"] for r in rows}


async def vip_counts() -> dict:
    now = int(time.time())
    return {
        "active": (await _one("SELECT COUNT(*) c FROM users WHERE vip_until>?", (now,)))["c"],
        "pending": (await _one("SELECT COUNT(*) c FROM payments WHERE status='pending'"))["c"],
    }


async def pay_add(uid: int, days: int, amount: int, method: str, status: str = "pending", proof: str = "") -> int:
    return await _run(
        "INSERT INTO payments(user_id, days, amount, method, status, proof, created_at) VALUES(?,?,?,?,?,?,?)",
        (uid, days, amount, method, status, proof, int(time.time())),
    )


async def pay_get(pid: int):
    return await _one("SELECT * FROM payments WHERE id=?", (pid,))


async def pay_pending_of(uid: int) -> int:
    return (await _one("SELECT COUNT(*) c FROM payments WHERE user_id=? AND status='pending'", (uid,)))["c"]


async def pay_decide(pid: int, status: str, by: int) -> bool:
    """Kutilayotgan to'lovni hal qiladi. Allaqachon hal qilingan bo'lsa False (ikki marta tasdiqlanmasin)."""
    cur = await _db.execute(
        "UPDATE payments SET status=?, decided_by=?, decided_at=? WHERE id=? AND status='pending'",
        (status, by, int(time.time()), pid),
    )
    n = cur.rowcount
    await cur.close()
    await _db.commit()
    return n > 0


async def get_setting(key: str, default: str = "") -> str:
    row = await _one("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


async def set_setting(key: str, value: str) -> None:
    await _run(
        "INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


# ───────────── Statistika ─────────────
async def punished_list(kind: str) -> list:
    """Hozir faol mute / ban ro'yxati (jazo tugashiga yaqinlari avval)."""
    now = int(time.time())
    if kind == "mute":
        return await _all(
            "SELECT id, full_name, username, mute_until AS until FROM users WHERE mute_until>? "
            "ORDER BY mute_until LIMIT 200", (now,))
    return await _all(
        "SELECT id, full_name, username, CASE WHEN ban_until=0 THEN ? ELSE ban_until END AS until FROM users "
        "WHERE banned=1 AND (ban_until=0 OR ban_until>?) ORDER BY until LIMIT 200", (FOREVER, now))


async def stats(day_start: int | None = None) -> dict:
    now = int(time.time())
    day, week = now - 86400, now - 7 * 86400
    ds = day if day_start is None else day_start  # «bugun» — kun boshidan (00:00)
    one = lambda sql, p=(): _one(sql, p)  # noqa: E731
    return {
        "users": (await one("SELECT COUNT(*) c FROM users"))["c"],
        "new_day": (await one("SELECT COUNT(*) c FROM users WHERE joined_at>=?", (day,)))["c"],
        "new_week": (await one("SELECT COUNT(*) c FROM users WHERE joined_at>=?", (week,)))["c"],
        "active_day": (await one("SELECT COUNT(*) c FROM users WHERE last_active>=?", (day,)))["c"],
        "banned": (await one("SELECT COUNT(*) c FROM users WHERE banned=1 AND (ban_until=0 OR ban_until>?)", (now,)))["c"],
        "blocked": (await one("SELECT COUNT(*) c FROM users WHERE blocked=1"))["c"],
        "movies": (await one("SELECT COUNT(*) c FROM movies WHERE status='live' AND series_id IS NULL"))["c"],
        "pending": (await one("SELECT COUNT(*) c FROM movies WHERE status='pending'"))["c"],
        "series": (await one(
            "SELECT COUNT(DISTINCT series_id) c FROM movies WHERE status='live' AND series_id IS NOT NULL"))["c"],
        "views": (await one("SELECT COUNT(*) c FROM views"))["c"],
        "views_day": (await one("SELECT COUNT(*) c FROM views WHERE at>=?", (day,)))["c"],
        "users_today": (await one("SELECT COUNT(*) c FROM users WHERE joined_at>=?", (ds,)))["c"],
        "views_today": (await one("SELECT COUNT(*) c FROM views WHERE at>=?", (ds,)))["c"],
        "movies_today": (await one("SELECT COUNT(*) c FROM movies WHERE created_at>=?", (ds,)))["c"],
        "movies_all": (await one("SELECT COUNT(*) c FROM movies"))["c"],
        "muted": (await one("SELECT COUNT(*) c FROM users WHERE mute_until>?", (now,)))["c"],
        "channels": (await one("SELECT COUNT(*) c FROM channels"))["c"],
        "top": [dict(r) for r in await top_movies(5)],
    }


# ───────────── Treylerlar (Instagram) ─────────────
# grp: kino (kino + serial) | anime | multfilm | ad (kodsiz — reklama, uchala akkauntga ketadi)
TRAILER_ACCTS = ("kino", "anime", "multfilm")


async def trailer_add(src_chat: int, src_msg: int, file_id: str, size: int, code: str, grp: str) -> int:
    return await _run(
        "INSERT INTO trailers(src_chat, src_msg, file_id, file_size, code, grp, created_at) VALUES(?,?,?,?,?,?,?)",
        (src_chat, src_msg, file_id, int(size or 0), code, grp, int(time.time())),
    )


async def trailer_by_src(chat_id: int, msg_id: int):
    return await _one("SELECT * FROM trailers WHERE src_chat=? AND src_msg=?", (chat_id, msg_id))


async def trailer_has_posts(tid: int) -> bool:
    return bool(await _one("SELECT 1 FROM trailer_posts WHERE trailer_id=? AND status='done'", (tid,)))


async def trailer_set_code(tid: int, code: str, grp: str) -> None:
    await _run("UPDATE trailers SET code=?, grp=? WHERE id=?", (code, grp, tid))


async def trailers_waiting(acct: str):
    """Shu akkauntga hali yuklanmagan treylerlar (o'z guruhi + kodsiz reklamalar)."""
    return await _all(
        """SELECT * FROM trailers t
           WHERE (t.grp=? OR t.grp='ad')
             AND NOT EXISTS(SELECT 1 FROM trailer_posts p WHERE p.trailer_id=t.id AND p.acct=? AND p.status='done')
           ORDER BY t.id""",
        (acct, acct),
    )


async def trailer_counts() -> dict:
    out = {}
    for a in TRAILER_ACCTS:
        out[a] = len(await trailers_waiting(a))
    return out


async def trailer_total() -> int:
    return (await _one("SELECT COUNT(*) c FROM trailers"))["c"]


async def trailer_mark(tid: int, acct: str, status: str, info: str = "") -> None:
    await _run(
        """INSERT INTO trailer_posts(trailer_id, acct, status, info, at) VALUES(?,?,?,?,?)
           ON CONFLICT(trailer_id, acct) DO UPDATE SET status=excluded.status, info=excluded.info, at=excluded.at""",
        (tid, acct, status, info[:300], int(time.time())),
    )


# ───────────── Kanalga e'lon qilinganlar (qayta yuborilmasin) ─────────────
async def promo_mark(kind: str, ref: int) -> None:
    """kind: 'm' (yakka kino/anime/multfilm) | 's' (serial yoki qismli to'plam)."""
    await _run("INSERT OR IGNORE INTO promo_sent(kind, ref, at) VALUES(?,?,?)", (kind, ref, int(time.time())))


async def promo_backlog():
    """Hali kanalga yuborilmagan, RASMI (muqovasi) bor chiqarilgan kontent: (yakka kinolar, seriallar)."""
    singles = await _all(
        """SELECT * FROM movies WHERE status='live' AND series_id IS NULL AND cover_id!=''
           AND NOT EXISTS(SELECT 1 FROM promo_sent p WHERE p.kind='m' AND p.ref=movies.id) ORDER BY id""")
    series = await _all(
        """SELECT * FROM series s WHERE s.cover_id!=''
           AND EXISTS(SELECT 1 FROM movies e WHERE e.series_id=s.id AND e.status='live')
           AND NOT EXISTS(SELECT 1 FROM promo_sent p WHERE p.kind='s' AND p.ref=s.id) ORDER BY s.id""")
    return singles, series


# ───────────── Bot almashganda fayl raqamlarini yangilash ─────────────
async def movies_with_source(after_id: int = 0):
    return await _all("SELECT id, src_chat, src_msg, poster_id FROM movies WHERE src_chat IS NOT NULL AND id>? ORDER BY id",
                      (after_id,))


async def movie_set_file(mid: int, file_id: str, file_type: str, poster_id: str = "") -> None:
    if poster_id:
        await _run("UPDATE movies SET file_id=?, file_type=?, poster_id=? WHERE id=?", (file_id, file_type, poster_id, mid))
    else:
        await _run("UPDATE movies SET file_id=?, file_type=? WHERE id=?", (file_id, file_type, mid))


async def series_ids_with_episodes():
    rows = await _all("SELECT DISTINCT series_id AS sid FROM movies WHERE series_id IS NOT NULL")
    return [r["sid"] for r in rows]


async def covers_count() -> int:
    a = (await _one("SELECT COUNT(*) c FROM movies WHERE cover_id!=''"))["c"]
    b = (await _one("SELECT COUNT(*) c FROM series WHERE cover_id!=''"))["c"]
    return a + b
