"""Kanal posti izohidan (caption) kino ma'lumotlarini ajratib oladi.

Qabul qilinadigan ko'rinish (hammasi ixtiyoriy, faqat nom shart):

    🎬 Qasoskor (2023)          <- birinchi qator = nom (yoki "Nomi: Qasoskor")
    Yili: 2023
    Janri: Jangari, Drama
    Davlati: AQSH
    Tili: O'zbek tilida
    Sifati: 1080p
    Tavsif: Qisqa mazmun...
"""
import re

ALIASES = {
    "title": {"nom", "nomi", "nomlanishi", "kino", "kino nomi", "film", "film nomi", "serial",
              "title", "name", "название", "фильм"},
    "year": {"yil", "yili", "yilda", "chiqqan yili", "chiqarilgan yili", "year", "год"},
    "genre": {"janr", "janri", "janrlari", "genre", "жанр"},
    "country": {"davlat", "davlati", "mamlakat", "mamlakati", "country", "страна"},
    "language": {"til", "tili", "tilida", "language", "язык", "tarjima"},
    "quality": {"sifat", "sifati", "quality", "качество"},
    "description": {"tavsif", "tavsifi", "mazmun", "mazmuni", "haqida", "syujet", "description", "описание"},
}
LOOKUP = {alias: field for field, names in ALIASES.items() for alias in names}

LIMITS = {"title": 120, "description": 800, "genre": 100, "country": 60, "language": 60, "quality": 20}

_LEAD = re.compile(r"^[^\w]+", re.UNICODE)            # qator boshidagi emoji va belgilar
_KV = re.compile(r"^([^:：\n]{1,25}?)\s*[:：]\s*(.*)$")   # "Kalit: qiymat"
_YEAR = re.compile(r"(?<!\d)(?:18|19|20)\d{2}(?!\d)")
_PAREN_YEAR = re.compile(r"\s*[\(\[]\s*((?:18|19|20)\d{2})\s*[\)\]]\s*")
_VIDEO_EXT = re.compile(r"\.(mp4|mkv|avi|mov|webm|m4v|wmv|flv|ts)$", re.I)
_QUOTES = " \t\"'«»“”„‘’`-–—|"


def _key(s: str) -> str:
    return re.sub(r"[^\w\s]", "", s.casefold()).strip()


def _title_from_filename(name: str) -> str:
    name = _VIDEO_EXT.sub("", name or "")
    return re.sub(r"\s+", " ", name.replace("_", " ").replace(".", " ")).strip()


def _clean_genre(value: str) -> str:
    seen, out = set(), []
    for part in re.split(r"[,/|;#•]+", value):
        part = part.strip(" .-–—")
        if not part:
            continue
        part = part[:1].upper() + part[1:]
        if part.casefold() not in seen:
            seen.add(part.casefold())
            out.append(part)
    return ", ".join(out)


def quality_from_size(width: int | None, height: int | None) -> str:
    """Video o'lchamidan taxminiy sifat (kengekran kinolarda ham to'g'ri chiqishi uchun eng uzun tomon)."""
    side = max(width or 0, height or 0)
    if side >= 3000:
        return "4K"
    if side >= 1800:
        return "1080p"
    if side >= 1200:
        return "720p"
    if side >= 800:
        return "480p"
    return ""


def parse_caption(text: str, filename: str | None = None) -> dict:
    """Izohdan {'title', 'year', 'genre', 'country', 'language', 'quality', 'description'} ajratadi.
    Topilmagan maydonlar natijada bo'lmaydi."""
    out: dict[str, str] = {}
    desc: list[str] = []
    in_desc = False
    first_plain: str | None = None

    for raw in (text or "").splitlines():
        line = _LEAD.sub("", raw.strip()).strip()
        if not line:
            if in_desc and desc:
                desc.append("")
            continue
        field = None
        m = _KV.match(line)
        if m:
            field = LOOKUP.get(_key(m.group(1)))
        if field:
            value = m.group(2).strip()
            in_desc = field == "description"
            if in_desc:
                if value:
                    desc.append(value)
            elif value and field not in out:
                out[field] = value
            continue
        if in_desc:
            desc.append(line)
        elif first_plain is None:
            first_plain = line

    title = out.get("title") or first_plain or _title_from_filename(filename or "")
    pm = _PAREN_YEAR.search(title)
    if pm:
        out.setdefault("year", pm.group(1))
        title = _PAREN_YEAR.sub(" ", title)
    title = re.sub(r"\s+", " ", title).strip(_QUOTES)
    if title:
        out["title"] = title[: LIMITS["title"]]
    else:
        out.pop("title", None)

    ym = _YEAR.search(out.get("year", ""))
    if ym:
        out["year"] = ym.group(0)
    else:
        out.pop("year", None)

    if "genre" in out:
        genre = _clean_genre(out["genre"])
        if genre:
            out["genre"] = genre[: LIMITS["genre"]]
        else:
            out.pop("genre")

    d = re.sub(r"\n{3,}", "\n\n", "\n".join(desc)).strip()
    if d:
        out["description"] = d[: LIMITS["description"]]

    for k in ("country", "language", "quality"):
        if k in out:
            out[k] = out[k].strip()[: LIMITS[k]]
            if not out[k]:
                out.pop(k)
    return out


# ───────────── Qism raqami (serial / multfilm / anime) ─────────────
_EP_WORDS = r"(?:qism|qismi|seriya|seriyasi|серия|серии|ep|episode|epizod|part|bo'?lim|chast|часть)"
_EP_AFTER = re.compile(rf"(?<!\d)(\d{{1,4}})\s*[-–.]?\s*{_EP_WORDS}\b", re.I)
_EP_BEFORE = re.compile(rf"\b{_EP_WORDS}\s*[-–:.#№]?\s*(\d{{1,4}})(?!\d)", re.I)
_SXEY = re.compile(r"\bs\d{1,2}\s*e(\d{1,4})\b", re.I)
_BARE = re.compile(r"^\W*#?\s*(\d{1,4})\W*$")
_QUALITY_NUMS = {144, 240, 360, 480, 540, 720, 1080, 1440, 2160, 4320}


def parse_episode(text: str | None, filename: str | None = None) -> int | None:
    """Post izohidagi qism raqamini topadi: '1', '1-qism', 'Qism 3', 'S01E05', '12 seriya'...
    Izohda raqam bo'lmasa fayl nomidagi oxirgi raqamga qaraydi. Topilmasa None."""
    text = (text or "").strip()
    for pat in (_BARE, _EP_AFTER, _EP_BEFORE, _SXEY):
        for line in text.splitlines():
            m = pat.search(line.strip()) if pat is not _BARE else pat.match(line.strip())
            if m:
                n = int(m.group(1))
                if n > 0:
                    return n
    # izohning birinchi qatoridagi yagona mustaqil raqam (masalan: "Naruto 12")
    first = text.splitlines()[0] if text else ""
    nums = [int(x) for x in re.findall(r"(?<![\d.])(\d{1,3})(?![\d.])", first)
            if int(x) not in _QUALITY_NUMS and int(x) > 0]
    if len(nums) == 1 and not _YEAR.search(first):
        return nums[0]
    if filename:
        base = _VIDEO_EXT.sub("", filename)
        nums = [int(x) for x in re.findall(r"(?<!\d)(\d{1,3})(?!\d)", base) if int(x) not in _QUALITY_NUMS and int(x) > 0]
        if nums:
            return nums[-1]
    return None
