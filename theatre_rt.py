"""Teatr real vaqt qatlami: yengil pub/sub (Server-Sent Events uchun).

Nega SSE? Chat, reaksiya, tomoshabinlar soni va xona holati uchun har 3 soniyada so'rov yuborish o'rniga,
har bir tomoshabin BITTA ochiq ulanish ushlab turadi, server esa yangiliklarni o'zi yuboradi.
Ma'lumotlar bazasiga qo'shimcha so'rov yo'q — hammasi xotirada.

• Reaksiyalar bazaga yozilmaydi. Ular 0.45 soniyalik oynada yig'iladi va bitta paket bo'lib tarqatiladi
  (200 kishi bosib tursa ham tarmoqqa soniyasiga ~2 ta paket chiqadi).
• Sekin mijoz (navbati to'lgan) uzib qo'yiladi — brauzer o'zi qayta ulanadi va REST orqali holatni tiklaydi.
• Hech narsa bu yerda saqlanib qolmaydi: server qayta ishga tushsa, mijozlar o'zi qayta ulanadi.
"""
import asyncio
import json
import logging
import time

log = logging.getLogger("theatre.rt")

# Ruxsat etilgan reaksiyalar (server tekshiradi; mijoz boshqa belgi yubora olmaydi)
REACTIONS = ("🔥", "😂", "😮", "❤️", "👏")
REACT_GAP = 0.6          # bir foydalanuvchi reaksiyalari orasidagi eng kam vaqt (soniya)
FLUSH = 0.45             # reaksiyalarni yig'ib tarqatish oralig'i
PER_FLUSH = 14           # bitta paketdagi alohida reaksiyalar (qolgani sanoq bo'lib ketadi)
PENDING_MAX = 240        # bir kanalda bitta oynada yig'iladigan eng ko'p reaksiya (oshsa — tashlanadi)
MAX_SUBS_USER = 3        # bitta foydalanuvchining bir vaqtdagi ulanishlari (bir nechta qurilma/tab)
MAX_SUBS_TOTAL = 2500    # butun server bo'yicha ulanishlar chegarasi (xotira himoyasi)
QUEUE_SIZE = 80          # har bir ulanish navbati


def fmt(event: str, data) -> bytes:
    """SSE kadri."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()


class Sub:
    __slots__ = ("key", "uid", "q", "closed", "ghost")

    def __init__(self, key: str, uid: int, ghost: bool = False):
        self.key, self.uid, self.ghost = key, uid, ghost
        self.q: asyncio.Queue = asyncio.Queue(QUEUE_SIZE)
        self.closed = False

    def close(self) -> None:
        """Ulanishni tugatadi (oqim `None` ni ko'rib to'xtaydi)."""
        if self.closed:
            return
        self.closed = True
        try:
            while True:  # navbatni bo'shatib, to'xtash belgisini qo'yamiz
                self.q.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            self.q.put_nowait(None)
        except asyncio.QueueFull:
            pass


class Hub:
    def __init__(self) -> None:
        self.subs: dict[str, set[Sub]] = {}
        self.total = 0
        self._pending: dict[str, list] = {}
        self._last_react: dict[int, float] = {}
        self._last_v: dict[str, int] = {}
        # theatre.py ulaydi (aylanma import bo'lmasligi uchun)
        self.on_seen = lambda key, uid: None
        self.on_gone = lambda key, uid: None
        self.count = lambda key: len(self.users(key))

    # ── ulanishlar ──
    def users(self, key: str) -> set:
        """Kanaldagi tomoshabinlar (👻 sharpa — bosh admin — hisobga kirmaydi)."""
        return {s.uid for s in self.subs.get(key, ()) if not s.closed and not s.ghost}

    def has(self, key: str, uid: int) -> bool:
        """Foydalanuvchining kanalda ochiq (ruxsati tekshirilgan) ulanishi bormi — bazaga murojaatsiz."""
        return any(s.uid == uid and not s.closed and not s.ghost for s in self.subs.get(key, ()))

    def subscribe(self, key: str, uid: int, ghost: bool = False) -> Sub | None:
        group = self.subs.setdefault(key, set())
        if self.total >= MAX_SUBS_TOTAL or sum(1 for s in group if s.uid == uid) >= MAX_SUBS_USER:
            if not group:
                self.subs.pop(key, None)
            return None
        sub = Sub(key, uid, ghost)
        group.add(sub)
        self.total += 1
        if not ghost:
            self.on_seen(key, uid)
        return sub

    def unsubscribe(self, sub: Sub) -> None:
        group = self.subs.get(sub.key)
        if group and sub in group:
            group.discard(sub)
            self.total = max(0, self.total - 1)
            if not sub.ghost and not any(s.uid == sub.uid and not s.ghost for s in group):
                self.on_gone(sub.key, sub.uid)
            if not group:
                self.subs.pop(sub.key, None)
                self._pending.pop(sub.key, None)
                self._last_v.pop(sub.key, None)
        sub.close()

    # ── tarqatish ──
    def publish(self, key: str, event: str, data) -> int:
        """Kanaldagi hammaga yuboradi. Navbati to'lgan (sekin) mijoz uziladi. Yetkazilganlar soni qaytadi."""
        group = self.subs.get(key)
        if not group:
            return 0
        payload = fmt(event, data)
        n = 0
        for s in list(group):
            if s.closed:
                continue
            try:
                s.q.put_nowait(payload)
                n += 1
            except asyncio.QueueFull:
                log.info("Sekin mijoz uzildi (kanal %s, uid %s)", key, s.uid)
                s.close()
        return n

    def send_to(self, key: str, uid: int, event: str, data, close: bool = False) -> None:
        """Kanalning bitta foydalanuvchisiga (uning barcha ulanishlariga) yuboradi."""
        payload = fmt(event, data)
        for s in list(self.subs.get(key, ())):
            if s.uid != uid or s.closed:
                continue
            try:
                s.q.put_nowait(payload)
            except asyncio.QueueFull:
                pass
            if close:
                # xabar oqimga yetib borishi uchun to'xtash belgisini navbat oxiriga qo'yamiz
                try:
                    s.q.put_nowait(None)
                except asyncio.QueueFull:
                    s.close()

    def close_key(self, key: str, event: str | None = None, data=None) -> None:
        """Kanalni yopadi (masalan, xona o'chirildi)."""
        if event:
            self.publish(key, event, data or {})
        for s in list(self.subs.get(key, ())):
            try:
                s.q.put_nowait(None)
            except asyncio.QueueFull:
                s.close()

    # ── reaksiyalar ──
    def allow(self, uid: int) -> bool:
        """Tezlik chegarasi: bir foydalanuvchi ≤ 1 reaksiya / 0.6 s. Ruxsat berilsa vaqtni belgilab qo'yadi."""
        now = time.monotonic()
        if now - self._last_react.get(uid, 0.0) < REACT_GAP:
            return False
        self._last_react[uid] = now
        if len(self._last_react) > 5000:  # eski yozuvlarni tozalaymiz
            cut = now - 60
            self._last_react = {u: t for u, t in self._last_react.items() if t > cut}
        return True

    def push(self, key: str, uid: int, emoji: str) -> None:
        """Reaksiyani keyingi tarqatish oynasiga qo'yadi."""
        buf = self._pending.setdefault(key, [])
        if len(buf) < PENDING_MAX:
            buf.append((uid, emoji))

    def react(self, key: str, uid: int, emoji: str) -> bool:
        """allow + push. False — juda tez bosildi yoki taqiqlangan belgi."""
        if emoji not in REACTIONS or not self.allow(uid):
            return False
        self.push(key, uid, emoji)
        return True

    def flush(self) -> None:
        """Yig'ilgan reaksiyalarni har kanalga bitta paket qilib yuboradi."""
        if not self._pending:
            return
        pend, self._pending = self._pending, {}
        for key, items in pend.items():
            if not items or key not in self.subs:
                continue
            head, rest = items[:PER_FLUSH], items[PER_FLUSH:]
            more: dict[str, int] = {}
            for _, e in rest:
                more[e] = more.get(e, 0) + 1
            self.publish(key, "rx", {"i": [[u, e] for u, e in head], "m": more})

    def tick_viewers(self) -> None:
        """Tomoshabinlar soni o'zgargan kanallarga yangi sonni yuboradi."""
        for key in list(self.subs):
            n = self.count(key)
            if self._last_v.get(key) != n:
                self._last_v[key] = n
                self.publish(key, "v", {"n": n})

    async def run(self) -> None:
        """Fon vazifasi: reaksiyalarni tarqatadi, tomoshabinlar sonini yangilaydi."""
        i = 0
        while True:
            try:
                await asyncio.sleep(FLUSH)
                self.flush()
                i += 1
                if i % 5 == 0:
                    self.tick_viewers()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Real vaqt vazifasi xatosi")


hub = Hub()
