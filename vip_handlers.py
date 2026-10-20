"""VIP obuna: bot ichida tarif tanlash, to'lov (Click / Payme — Telegram to'lovi, yoki chek yuborish) va admin tasdiqlashi."""
import html
import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup, default_state
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, LabeledPrice
from aiogram.types import Message, PreCheckoutQuery

import config
import audit
import db
import perms
import vip

router = Router()
MAX_PENDING_CHECKS = 3
METHOD_LABEL = {"click": "💳 Click", "payme": "💳 Payme", "check": "🧾 Chek yuborish"}


class VipCheck(StatesGroup):
    wait = State()


def _kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ───────────── Menyu ─────────────
async def menu_view(uid: int) -> tuple[str, InlineKeyboardMarkup | None]:
    if not vip.ON:
        return ("👑 <b>VIP obuna</b>\n\nHozircha pullik rejim o'chiq — <b>hamma narsa tekin</b>. 🎉", None)
    until = await db.vip_until(uid)
    lines = ["👑 <b>VIP obuna</b>\n"]
    if perms.is_staff(uid):
        lines.append("Siz adminsiz — hamma narsa sizga ochiq. ✅\n")
    elif until > time.time():
        lines.append(f"✅ VIP faol: <b>{vip.until_text(until)}</b> gacha. Yana sotib olsangiz muddat uzayadi.\n")
    lines.append(
        "VIP bilan:\n"
        "• barcha pullik kino, serial, multfilm va animelar ochiladi\n"
        "• teatrda o'z xonangizni ochasiz\n"
        "• chatda ismingiz yonida <b>VIP</b> belgisi turadi\n\n"
        "Tarifni tanlang 👇"
    )
    rows = [[B(text=f"{vip.days_text(p['days'])} — {vip.money(p['price'])}", callback_data=f"vip:p:{i}")]
            for i, p in enumerate(await vip.plans())]
    return "\n".join(lines), _kb(rows)


async def show_menu(m: Message) -> None:
    text, kb = await menu_view(m.from_user.id)
    await m.answer(text, reply_markup=kb)


@router.message(Command("vip"), StateFilter("*"))
async def cmd_vip(m: Message, state: FSMContext):
    await state.clear()
    await show_menu(m)


@router.callback_query(F.data == "vip:menu", StateFilter("*"))
async def cb_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    text, kb = await menu_view(c.from_user.id)
    await c.message.answer(text, reply_markup=kb)


async def _plan(idx: int):
    ps = await vip.plans()
    return ps[idx] if 0 <= idx < len(ps) else None


# ───────────── Tarif → to'lov usuli ─────────────
@router.callback_query(F.data.startswith("vip:p:"))
async def cb_plan(c: CallbackQuery):
    if not vip.ON:
        return await c.answer("Pullik rejim hozir o'chiq", show_alert=True)
    plan = await _plan(int(c.data.split(":")[2]))
    if not plan:
        return await c.answer("Tarif topilmadi, /vip ni qayta oching", show_alert=True)
    idx = c.data.split(":")[2]
    methods = list(vip.provider_tokens())
    if await vip.card_text():
        methods.append("check")
    if not methods:
        return await c.answer("To'lov usuli hali sozlanmagan. Adminga murojaat qiling.", show_alert=True)
    rows = [[B(text=METHOD_LABEL[k], callback_data=f"vip:m:{idx}:{k}")] for k in methods]
    rows.append([B(text="⬅️ Orqaga", callback_data="vip:menu")])
    await c.answer()
    await c.message.answer(
        f"👑 <b>VIP — {vip.days_text(plan['days'])}</b>\nNarxi: <b>{vip.money(plan['price'])}</b>\n\nTo'lov usulini tanlang 👇",
        reply_markup=_kb(rows),
    )


@router.callback_query(F.data.startswith("vip:m:"))
async def cb_method(c: CallbackQuery, bot: Bot, state: FSMContext):
    if not vip.ON:
        return await c.answer("Pullik rejim hozir o'chiq", show_alert=True)
    _, _, idx, method = c.data.split(":")
    plan = await _plan(int(idx))
    if not plan:
        return await c.answer("Tarif topilmadi, /vip ni qayta oching", show_alert=True)
    await c.answer()
    if method == "check":
        card = await vip.card_text()
        if not card:
            return await c.message.answer("Chek orqali to'lov hozir yoqilmagan.")
        if await db.pay_pending_of(c.from_user.id) >= MAX_PENDING_CHECKS:
            return await c.message.answer("⏳ Sizda tekshirilmagan cheklar bor. Admin ko'rib chiqquncha kuting.")
        await state.set_state(VipCheck.wait)
        await state.update_data(days=plan["days"], price=plan["price"])
        await c.message.answer(
            f"🧾 <b>Chek orqali to'lov</b>\n\n"
            f"Tarif: <b>{vip.days_text(plan['days'])}</b>\nSumma: <b>{vip.money(plan['price'])}</b>\n\n"
            f"{html.escape(card)}\n\n"
            "To'lagach <b>chek rasmini (skrinshot)</b> shu yerga yuboring 👇",
            reply_markup=_kb([[B(text="❌ Bekor qilish", callback_data="vip:menu")]]),
        )
        return
    token = vip.provider_tokens().get(method)
    if not token:
        return await c.message.answer("Bu to'lov usuli hozir ulanmagan.")
    try:
        await bot.send_invoice(
            c.message.chat.id,
            title=f"VIP — {vip.days_text(plan['days'])}",
            description=f"{config.BOT_TITLE} VIP obuna ({vip.days_text(plan['days'])})",
            payload=f"vip:{plan['days']}:{plan['price']}:{method}",
            provider_token=token,
            currency="UZS",
            prices=[LabeledPrice(label=f"VIP {vip.days_text(plan['days'])}", amount=plan["price"] * 100)],
        )
    except TelegramBadRequest as e:
        logging.error("Invoice yuborilmadi (%s): %s", method, e)
        await c.message.answer("⚠️ To'lovni boshlab bo'lmadi. Adminga murojaat qiling.")


# ───────────── Telegram to'lovi (Click / Payme) ─────────────
def _parse_payload(payload: str):
    try:
        tag, days, price, method = payload.split(":")
        return (int(days), int(price), method) if tag == "vip" else None
    except ValueError:
        return None


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    parsed = _parse_payload(q.invoice_payload)
    ok = bool(parsed) and vip.ON and any(
        p["days"] == parsed[0] and p["price"] == parsed[1] for p in await vip.plans()
    ) and q.total_amount == parsed[1] * 100 and q.currency == "UZS"
    if ok:
        await q.answer(ok=True)
    else:
        await q.answer(ok=False, error_message="Tarif yoki narx o'zgargan. /vip orqali qaytadan urinib ko'ring.")


@router.message(F.successful_payment)
async def paid(m: Message, bot: Bot):
    sp = m.successful_payment
    parsed = _parse_payload(sp.invoice_payload)
    if not parsed:
        return
    days, price, method = parsed
    until = await db.vip_grant(m.from_user.id, days)
    await db.pay_add(m.from_user.id, days, price, method, status="ok", proof=sp.telegram_payment_charge_id or "")
    if until is None:  # foydalanuvchi bazada yo'q (kamdan-kam) — adminlarga xabar
        await _notify_full(bot, f"⚠️ To'lov qabul qilindi, lekin VIP berilmadi (foydalanuvchi bazada yo'q): "
                                f"ID <code>{m.from_user.id}</code>, {vip.days_text(days)}, {vip.money(price)}")
        return await m.answer("✅ To'lov qabul qilindi. VIP tez orada yoqiladi.")
    await m.answer(f"🎉 <b>To'lov qabul qilindi!</b>\n\n👑 VIP faol: <b>{vip.until_text(until)}</b> gacha.")
    await _notify_full(bot, f"💰 <b>{METHOD_LABEL.get(method, method)}</b> to'lovi: {_user_line(m.from_user)} — "
                            f"{vip.days_text(days)}, {vip.money(price)}")


# ───────────── Chek yuborish ─────────────
def _user_line(u) -> str:
    name = html.escape(u.full_name or "—")
    return f"{name}" + (f" (@{u.username})" if u.username else "") + f" • <code>{u.id}</code>"


def _full_admins() -> list[int]:
    return [uid for uid in perms.staff_ids() if perms.is_full(uid)]


async def _notify_full(bot: Bot, text: str) -> None:
    """Ma'lumot xabari: avval chek kanaliga, bo'lmasa bosh/starshiy adminlarga."""
    if config.VIP_CHECK_CHANNEL:
        try:
            await bot.send_message(config.VIP_CHECK_CHANNEL, text)
            return
        except Exception as e:
            logging.warning("Chek kanaliga xabar yuborilmadi (%s): %s", config.VIP_CHECK_CHANNEL, e)
    for uid in _full_admins():
        try:
            await bot.send_message(uid, text)
        except Exception as e:
            logging.warning("Adminga xabar yuborilmadi (%s): %s", uid, e)


async def _send_check(bot: Bot, fid: str, cap: str, kb: InlineKeyboardMarkup) -> int:
    """Chekni kanalga yuboradi (tasdiqlash tugmalari bilan). Kanal bo'sh/ishlamasa — adminlar lichkasiga."""
    if config.VIP_CHECK_CHANNEL:
        try:
            await bot.send_photo(config.VIP_CHECK_CHANNEL, fid, caption=cap, reply_markup=kb)
            return 1
        except Exception as e:
            logging.error("Chek kanalga yuborilmadi (%s): %s — adminlarga yuboriladi", config.VIP_CHECK_CHANNEL, e)
    sent = 0
    for uid in _full_admins():
        try:
            await bot.send_photo(uid, fid, caption=cap, reply_markup=kb)
            sent += 1
        except Exception as e:
            logging.warning("Chek adminga yuborilmadi (%s): %s", uid, e)
    return sent


@router.message(StateFilter(VipCheck.wait), F.photo)
async def check_photo(m: Message, bot: Bot, state: FSMContext):
    data = await state.get_data()
    days, price = int(data.get("days") or 0), int(data.get("price") or 0)
    await state.clear()
    if not days or not price:
        return await m.answer("Seans tugadi. /vip ni qayta oching.")
    fid = m.photo[-1].file_id
    pid = await db.pay_add(m.from_user.id, days, price, "check", proof=fid)
    kb = _kb([[B(text="✅ Tasdiqlash", callback_data=f"vipok:{pid}"), B(text="❌ Rad etish", callback_data=f"viprej:{pid}")]])
    cap = (f"🧾 <b>Yangi chek #{pid}</b>\n\n{_user_line(m.from_user)}\n"
           f"Tarif: <b>{vip.days_text(days)}</b> • Summa: <b>{vip.money(price)}</b>")
    sent = await _send_check(bot, fid, cap, kb)
    if not sent:
        await m.answer("⚠️ Chekni adminlarga yetkazib bo'lmadi. Keyinroq urinib ko'ring yoki adminga yozing.")
        return
    await m.answer("✅ Chek yuborildi. Admin tekshirgach VIP yoqiladi va sizga xabar keladi. ⏳")


@router.message(StateFilter(VipCheck.wait))
async def check_wrong(m: Message):
    await m.answer("❗ Chek <b>rasmini</b> (foto) yuboring yoki /vip bilan qaytib chiqing.")


async def _decide(c: CallbackQuery, bot: Bot, ok: bool) -> None:
    if not perms.is_full(c.from_user.id):
        return await c.answer("Bu tugmani faqat bosh yoki starshiy admin bosa oladi", show_alert=True)
    pid = int(c.data.split(":")[1])
    pay = await db.pay_get(pid)
    if not pay:
        return await c.answer("To'lov topilmadi", show_alert=True)
    if not await db.pay_decide(pid, "ok" if ok else "rejected", c.from_user.id):
        return await c.answer("Bu chek allaqachon ko'rib chiqilgan", show_alert=True)
    uid = pay["user_id"]
    if ok:
        until = await db.vip_grant(uid, pay["days"])
        note = f"👑 VIP faol: <b>{vip.until_text(until)}</b> gacha." if until else ""
        try:
            await bot.send_message(uid, f"🎉 <b>Chekingiz tasdiqlandi!</b>\n\n{note}")
        except Exception:
            pass
        mark = f"✅ Tasdiqlandi — {html.escape(c.from_user.full_name or '')}"
        if until is None:
            mark += "\n⚠️ Foydalanuvchi bazada yo'q, VIP berilmadi"
    else:
        try:
            await bot.send_message(uid, "❌ <b>Chekingiz tasdiqlanmadi.</b>\n\nTo'lov topilmasa yoki xato bo'lsa, "
                                        "adminga murojaat qiling yoki /vip orqali qaytadan urinib ko'ring.")
        except Exception:
            pass
        mark = f"❌ Rad etildi — {html.escape(c.from_user.full_name or '')}"
    await audit.log(c.from_user.id, c.from_user.full_name or "", "vip",
                    "VIP chek tasdiqlandi" if ok else "VIP chek rad etildi",
                    f"chek #{pid} · foydalanuvchi {uid} · {vip.days_text(pay['days'])} · {vip.money(pay['amount'])}")
    await c.answer("Tayyor")
    try:
        await c.message.edit_caption(caption=(c.message.caption or "") + f"\n\n{mark}", reply_markup=None)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("vipok:"))
async def cb_ok(c: CallbackQuery, bot: Bot):
    await _decide(c, bot, True)


@router.callback_query(F.data.startswith("viprej:"))
async def cb_rej(c: CallbackQuery, bot: Bot):
    await _decide(c, bot, False)
