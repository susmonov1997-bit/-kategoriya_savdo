"""Telegram bot (aiogram 3): Excel qabul qilish → tekshirish → bazaga yuklash → hisobot.

Ishga tushirish:  python bot.py
"""
from __future__ import annotations

import asyncio
import html
import io
import logging

import asyncpg
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp,
                           Message, WebAppInfo)

import config
from access import ROLE_NAMES, get_access, invalidate
from db import create_pool, migrate
from fact import ensure_fact
from loaders_common import LoaderError
from loaders_dispatch import load_any, load_any_ex
import bot_stock
from bot_stock import prompt_new
from stock_notify import schedule_summary
from notify import broadcast_upload, get_setting, is_muted, period_text, send_users_update, set_setting
from export_missing import (all_pending_kb, build_missing_xlsx, file_name as missing_file_name, has_missing,
                            missing_caption, pending_report, pending_text, report_of)

log = logging.getLogger(__name__)
router = Router()
TG_LIMIT = 4000  # xabar uzunligi zaxira bilan


async def send_long(m: Message, text: str) -> None:
    from notify import split_text
    for p in split_text(text, TG_LIMIT):
        await m.answer(p)


_BG: set[asyncio.Task] = set()


def _bg(coro) -> None:
    """Fon vazifasi — havola saqlanadi (aks holda GC uni yarim yo'lda o'chirib yuborishi mumkin)."""
    t = asyncio.create_task(coro)
    _BG.add(t)
    t.add_done_callback(_BG.discard)


async def safe_edit(c: CallbackQuery, text: str, reply_markup=None) -> None:
    """edit_text: «message is not modified» va boshqa Telegram xatolarida yiqilmaydi."""
    try:
        await c.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            log.warning("Xabarni tahrirlab bo'lmadi: %s", e)


def _app_kb(text: str = "📊 Savdoni ochish") -> InlineKeyboardMarkup | None:
    if not config.WEBAPP_URL:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, web_app=WebAppInfo(url=config.WEBAPP_URL))]])


def _who(u) -> str:
    name = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or "—"
    un = f" @{u['username']}" if u.get("username") else ""
    return f"{html.escape(name)}{html.escape(un)} (ID <code>{u['tg_id']}</code>)"


WELCOME_UPLOAD = (
    "\n\nYuklash: Excel faylni shu yerga tashlang (20 MB gacha) yoki Mini App → <b>📥 Yuklash</b> (100 MB gacha):\n"
    "• <b>Kunlik savdo</b> (Chiqim tovarlar …xlsx)\n"
    "• <b>Tovar spravochnigi</b> · <b>Filial spravochnigi</b> · <b>Qo'shimcha daromad %</b>\n"
    "• <b>Filial qoldig'i</b> · <b>Ombor qoldig'i</b> (sana fayl nomida: …_17.10.2026.xlsx)\n"
    "Fayl turi avtomatik aniqlanadi.")


@router.message(CommandStart())
async def start(m: Message, pool: asyncpg.Pool) -> None:
    acc = await get_access(pool, m.from_user.id)
    if acc.can_view:
        text = f"Salom! Siz: <b>{ROLE_NAMES[acc.level]}</b>.\nSavdoni ko'rish uchun pastdagi tugmani bosing."
        if acc.can_upload:
            text += WELCOME_UPLOAD
            text += "\n/kutayotgan — spravochnikda yo'qligi uchun kutayotgan barcha savdo (Excel)"
        text += "\n\n/status — bazadagi ma'lumotlar holati\n/attrs — kategoriyalar va xususiyatlar"
        text += "\n/kurs — dollar kursi (front marja uchun)"
        text += "\n/qoldiq ART-12 — tovar qayerda qancha bor"
        if acc.is_admin:
            text += "\n/users — foydalanuvchilar va kirish so'rovlari"
            text += "\n/xabar — yuklashdan keyingi xabarlarni o'chirish/yoqish"
            text += "\n/yangi — yangi filial va ombor bo'limlarini to'ldirish"
        await m.answer(text, reply_markup=_app_kb())
        return
    if acc.status == "pending":
        await m.answer("⏳ Kirish so'rovingiz yuborilgan. Admin tasdiqlashi bilan sizga xabar keladi.")
        return
    note = {"rejected": "Oldingi so'rovingiz rad etilgan. ", "revoked": "Kirish huquqingiz bekor qilingan. "}.get(
        acc.status or "", "")
    await m.answer(
        f"👋 Bu bot kompaniya ichki savdo tahlili uchun.\n{note}Foydalanish uchun admin ruxsati kerak.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔑 Kirish so'rash", callback_data="acc:req")]]))


def _decision_kb(tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Ko'ruvchi", callback_data=f"acc:ok:viewer:{tg_id}"),
         InlineKeyboardButton(text="📥 Yuklovchi", callback_data=f"acc:ok:uploader:{tg_id}")],
        [InlineKeyboardButton(text="❌ Rad etish", callback_data=f"acc:no:{tg_id}")]])


@router.callback_query(F.data == "acc:req")
async def access_request(c: CallbackQuery, bot: Bot, pool: asyncpg.Pool) -> None:
    u = c.from_user
    acc = await get_access(pool, u.id)
    if acc.can_view:
        await c.answer("Sizda allaqachon kirish bor — /start", show_alert=True)
        return
    if acc.status == "pending":
        await c.answer("So'rov allaqachon yuborilgan, kuting", show_alert=True)
        return
    if not config.ADMIN_IDS:
        await c.answer("Admin sozlanmagan (ADMIN_IDS bo'sh)", show_alert=True)
        return
    row = await pool.fetchrow(
        """INSERT INTO users (tg_id, first_name, last_name, username, status, requested_at, decided_at, decided_by)
           VALUES ($1,$2,$3,$4,'pending',now(),NULL,NULL)
           ON CONFLICT (tg_id) DO UPDATE SET first_name=EXCLUDED.first_name, last_name=EXCLUDED.last_name,
             username=EXCLUDED.username, status='pending', requested_at=now(), decided_at=NULL, decided_by=NULL
           RETURNING *""", u.id, u.first_name, u.last_name, u.username)
    invalidate(u.id)
    text = f"🆕 <b>Kirish so'rovi</b>\n{_who(dict(row))}\n\nQaysi huquq bilan ruxsat berasiz?"
    sent = 0
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=_decision_kb(u.id))
            sent += 1
        except Exception as e:  # noqa: BLE001  (admin botni hali ochmagan bo'lishi mumkin)
            log.warning("Adminga (%s) so'rov yuborilmadi: %s", admin_id, e)
    await safe_edit(c, "✅ So'rovingiz adminga yuborildi. Tasdiqlanishi bilan xabar keladi."
                              if sent else "⚠️ So'rov saqlandi, lekin adminga yetkazilmadi — admin botga /start bosishi kerak.")
    await c.answer()


@router.callback_query(F.data.startswith("acc:ok:") | F.data.startswith("acc:no:"))
async def access_decide(c: CallbackQuery, bot: Bot, pool: asyncpg.Pool) -> None:
    if c.from_user.id not in config.ADMIN_IDS:
        await c.answer("Faqat admin", show_alert=True)
        return
    parts = c.data.split(":")
    approve = parts[1] == "ok"
    role = parts[2] if approve else None
    tg_id = int(parts[-1])
    cur = await pool.fetchrow("SELECT * FROM users WHERE tg_id = $1", tg_id)
    if cur is None:
        await c.answer("Foydalanuvchi topilmadi", show_alert=True)
        return
    if cur["status"] != "pending":
        state = f"{cur['status']}" + (f", {ROLE_NAMES.get(cur['role'])}" if cur["status"] == "approved" else "")
        await c.answer(f"Allaqachon hal qilingan: {state}", show_alert=True)
        return
    row = await pool.fetchrow(
        """UPDATE users SET status=$2, role=COALESCE($3, role), decided_at=now(), decided_by=$4
           WHERE tg_id=$1 RETURNING *""", tg_id, "approved" if approve else "rejected", role, c.from_user.id)
    invalidate(tg_id)
    who = _who(dict(row))
    by = html.escape(c.from_user.first_name or str(c.from_user.id))
    if approve:
        await safe_edit(c, f"✅ <b>Ruxsat berildi</b> — {ROLE_NAMES[role]}\n{who}\n<i>Tasdiqladi: {by}</i>")
        msg = f"✅ Kirish tasdiqlandi! Siz: <b>{ROLE_NAMES[role]}</b>.\nSavdoni ko'rish uchun tugmani bosing."
        if role == "uploader":
            msg += WELCOME_UPLOAD
        try:
            await bot.send_message(tg_id, msg, reply_markup=_app_kb())
        except Exception as e:  # noqa: BLE001
            log.warning("Foydalanuvchiga xabar yuborilmadi: %s", e)
    else:
        await safe_edit(c, f"❌ <b>Rad etildi</b>\n{who}\n<i>Rad etdi: {by}</i>")
        try:
            await bot.send_message(tg_id, "❌ Kirish so'rovingiz rad etildi.")
        except Exception as e:  # noqa: BLE001
            log.warning("Foydalanuvchiga xabar yuborilmadi: %s", e)
    await c.answer("Saqlandi")


async def _users_view(pool: asyncpg.Pool) -> tuple[str, InlineKeyboardMarkup | None]:
    rows = await pool.fetch(
        """SELECT * FROM users WHERE status IN ('pending','approved')
           ORDER BY status DESC, requested_at DESC LIMIT 40""")
    pending = [r for r in rows if r["status"] == "pending"]
    approved = [r for r in rows if r["status"] == "approved"]
    lines = [f"👥 <b>Foydalanuvchilar</b> — tasdiqlangan: {len(approved)}, kutilmoqda: {len(pending)}",
             f"⭐ Adminlar (ADMIN_IDS): {', '.join(map(str, sorted(config.ADMIN_IDS))) or '—'}"]
    kb: list[list[InlineKeyboardButton]] = []
    if pending:
        lines.append("\n<b>Kutilmoqda:</b>")
        for r in pending:
            lines.append("• " + _who(dict(r)))
            kb.append([InlineKeyboardButton(text=f"👁 {r['first_name'] or r['tg_id']}", callback_data=f"acc:ok:viewer:{r['tg_id']}"),
                       InlineKeyboardButton(text="📥", callback_data=f"acc:ok:uploader:{r['tg_id']}"),
                       InlineKeyboardButton(text="❌", callback_data=f"acc:no:{r['tg_id']}")])
    if approved:
        lines.append("\n<b>Tasdiqlangan</b> (🔁 — rolni almashtirish, 🚫 — kirishni bekor qilish):")
        for r in approved:
            lines.append(f"• {_who(dict(r))} — {ROLE_NAMES[r['role']]}")
            kb.append([InlineKeyboardButton(text=f"🔁 {r['first_name'] or r['tg_id']}: {ROLE_NAMES[r['role']]}",
                                            callback_data=f"acc:role:{r['tg_id']}"),
                       InlineKeyboardButton(text="🚫", callback_data=f"acc:rev:{r['tg_id']}")])
    if not rows:
        lines.append("\nHozircha foydalanuvchi yo'q. Yangi odam botga /start bosib, «Kirish so'rash» tugmasini bosadi.")
    return "\n".join(lines), (InlineKeyboardMarkup(inline_keyboard=kb) if kb else None)


@router.message(Command("users"))
async def users_cmd(m: Message, pool: asyncpg.Pool) -> None:
    if m.from_user.id not in config.ADMIN_IDS:
        return
    text, kb = await _users_view(pool)
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("acc:role:") | F.data.startswith("acc:rev:"))
async def users_manage(c: CallbackQuery, bot: Bot, pool: asyncpg.Pool) -> None:
    if c.from_user.id not in config.ADMIN_IDS:
        await c.answer("Faqat admin", show_alert=True)
        return
    _, action, tg_id = c.data.split(":")
    tg_id = int(tg_id)
    if action == "role":
        row = await pool.fetchrow(
            """UPDATE users SET role = CASE role WHEN 'viewer' THEN 'uploader' ELSE 'viewer' END,
                 decided_at=now(), decided_by=$2 WHERE tg_id=$1 AND status='approved' RETURNING role""",
            tg_id, c.from_user.id)
        note = f"Yangi rol: {ROLE_NAMES[row['role']]}" if row else "Topilmadi"
    else:
        row = await pool.fetchrow(
            """UPDATE users SET status='revoked', decided_at=now(), decided_by=$2
               WHERE tg_id=$1 AND status='approved' RETURNING tg_id""", tg_id, c.from_user.id)
        note = "Kirish bekor qilindi" if row else "Topilmadi"
        if row:
            try:
                await bot.send_message(tg_id, "🚫 Botdan foydalanish huquqingiz bekor qilindi.")
            except Exception:  # noqa: BLE001
                pass
    invalidate(tg_id)
    text, kb = await _users_view(pool)
    await safe_edit(c, text, reply_markup=kb)
    await c.answer(note)


@router.message(Command("attrs"))
async def attrs(m: Message, pool: asyncpg.Pool) -> None:
    if not (await get_access(pool, m.from_user.id)).can_view:
        return
    rows = await pool.fetch(
        """SELECT c.name cat, a.slot, a.source_col, a.name, a.data_type, a.unit, a.is_filter,
                  (SELECT count(*) FROM products p WHERE p.category_id = c.id) skus
           FROM categories c LEFT JOIN category_attributes a ON a.category_id = c.id
           ORDER BY c.name, a.sort_order, a.slot"""
    )
    out, cur = [], None
    for r in rows:
        if r["cat"] != cur:
            cur = r["cat"]
            out.append(f"\n<b>{html.escape(cur)}</b> — {r['skus']} SKU")
        if r["slot"] is not None:
            unit = f", {r['unit']}" if r["unit"] else ""
            off = "" if r["is_filter"] else " 🙈 yashirin"
            out.append(f"  {r['slot']}. {html.escape(r['name'])}{unit} <i>({html.escape(r['source_col'])}, {r['data_type']})</i>{off}")
    await send_long(m, "\n".join(out).strip() or "Hozircha bo'sh.")


@router.message(Command("kurs"))
async def kurs_cmd(m: Message, pool: asyncpg.Pool) -> None:
    """/kurs — joriy va oldingi kurslar; /kurs 12000 — bugundan; /kurs 12000 2026-10-05 (yoki 05.10.2026) — sanadan."""
    if not (await get_access(pool, m.from_user.id)).can_view:
        return
    parts = (m.text or "").split()[1:]
    if parts:
        if m.from_user.id not in config.ADMIN_IDS:
            await m.answer("⛔ Kursni faqat admin o'zgartiradi.")
            return
        try:
            rate = float(parts[0].replace(" ", "").replace(",", "."))
            if not 100 <= rate <= 1_000_000:
                raise ValueError
        except ValueError:
            await m.answer("Masalan: <code>/kurs 12000</code> yoki <code>/kurs 12000 05.10.2026</code>")
            return
        if len(parts) > 1:
            from loaders_sales import _to_date
            d = _to_date(parts[1])
            if d is None:
                await m.answer("Sana noto'g'ri. Masalan: <code>/kurs 12000 05.10.2026</code>")
                return
        else:
            d = await pool.fetchval("SELECT (now() AT TIME ZONE 'Asia/Tashkent')::date")
        await pool.execute(
            """INSERT INTO fx_rates(valid_from, rate, set_by) VALUES ($1, $2, $3)
               ON CONFLICT (valid_from) DO UPDATE SET rate = EXCLUDED.rate, set_by = EXCLUDED.set_by, set_at = now()""",
            d, rate, m.from_user.id)
        await ensure_fact(pool)              # marjalar yangi kurs bilan qayta hisoblanadi
        await m.answer(f"✅ Kurs: <b>{rate:,.0f}</b> so'm/$ — {d.strftime('%d.%m.%Y')} dan boshlab.\n"
                       "Shu sanadan keyingi savdoning front va gross marjasi yangi kurs bilan hisoblanadi.".replace(",", " "))
    rows = await pool.fetch("SELECT valid_from, valid_to, rate FROM fx_periods ORDER BY valid_from DESC LIMIT 10")
    lines = ["<b>💱 Dollar kursi</b> (Кирим нархи dollarda bo'lsa, tannarx = Сони × Кирим нархи × kurs)"]
    for r in rows:
        frm = "boshidan" if r["valid_from"].year < 2001 else r["valid_from"].strftime("%d.%m.%Y") + " dan"
        lines.append(f"  • {float(r['rate']):,.0f}".replace(",", " ") + f" — {frm}")
    if m.from_user.id in config.ADMIN_IDS:
        lines.append("\nO'zgartirish: <code>/kurs 12000</code> (bugundan) yoki <code>/kurs 12000 05.10.2026</code>")
    await m.answer("\n".join(lines))


# ---------------------------------------------------------------------------
# /xabar — yuklashdan keyingi xabarlarni o'chirish/yoqish (faqat admin). Ko'p fayl ketma-ket yuklanganda
# foydalanuvchilarni bezovta qilmaslik uchun. Holat bazada (app_settings) saqlanadi.
async def _xabar_view(pool) -> tuple[str, InlineKeyboardMarkup]:
    muted = await is_muted(pool)
    a, b = await get_setting(pool, "muted_from"), await get_setting(pool, "muted_to")
    if muted:
        text = ("🔕 <b>Yuklashdan keyingi xabarlar O'CHIRILGAN</b>\n"
                "Fayl yuklanganda foydalanuvchilarga ham, boshqa adminlarga ham xabar bormaydi "
                "(yuklagan odamning o'ziga hisobot boradi).")
        if a:
            text += f"\n\nO'chirilgan paytda yuklangan savdo: <b>{period_text([a, b])}</b>"
        kb = [[InlineKeyboardButton(text="🔔 Yoqish", callback_data="ntf:on")]]
        if a:
            kb.append([InlineKeyboardButton(text="🔔 Yoqish + hammaga bitta xabar yuborish", callback_data="ntf:on_send")])
    else:
        text = ("🔔 <b>Yuklashdan keyingi xabarlar YOQILGAN</b>\n"
                "Savdo yuklanganda hammaga «Savdo ma'lumoti yangilandi», adminlarga — to'liq hisobot boradi.\n\n"
                "Ko'p fayl ketma-ket yuklayotgan bo'lsangiz, vaqtincha o'chirib qo'ying.")
        kb = [[InlineKeyboardButton(text="🔕 O'chirish", callback_data="ntf:off")]]
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


@router.message(Command("xabar"))
async def xabar_cmd(m: Message, pool: asyncpg.Pool) -> None:
    if not (await get_access(pool, m.from_user.id)).is_admin:
        await m.answer("⛔ Xabarlarni faqat admin boshqaradi.")
        return
    arg = ((m.text or "").split() + [""])[1].lower()
    if arg in ("off", "ochir", "o'chir", "0"):
        await set_setting(pool, "notify_muted", "1", m.from_user.id)
        await set_setting(pool, "muted_from", None)
        await set_setting(pool, "muted_to", None)
    elif arg in ("on", "yoq", "1"):
        await set_setting(pool, "notify_muted", "0", m.from_user.id)
        await set_setting(pool, "muted_from", None)
        await set_setting(pool, "muted_to", None)
    text, kb = await _xabar_view(pool)
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("ntf:"))
async def xabar_cb(c: CallbackQuery, pool: asyncpg.Pool, bot: Bot) -> None:
    if not (await get_access(pool, c.from_user.id)).is_admin:
        await c.answer("Faqat admin uchun", show_alert=True)
        return
    act = c.data.split(":", 1)[1]
    note = ""
    if act == "off":
        await set_setting(pool, "notify_muted", "1", c.from_user.id)
        await set_setting(pool, "muted_from", None)
        await set_setting(pool, "muted_to", None)
    else:
        a, b = await get_setting(pool, "muted_from"), await get_setting(pool, "muted_to")
        await set_setting(pool, "notify_muted", "0", c.from_user.id)
        if act == "on_send" and a:
            n = await send_users_update(pool, bot, period_text([a, b]), {c.from_user.id})
            note = f"\n\n📨 {n} kishiga «Savdo ma'lumoti yangilandi: {period_text([a, b])}» yuborildi."
        await set_setting(pool, "muted_from", None)
        await set_setting(pool, "muted_to", None)
    text, kb = await _xabar_view(pool)
    try:
        await c.message.edit_text(text + note, reply_markup=kb)
    except Exception:  # noqa: BLE001
        await c.message.answer(text + note, reply_markup=kb)
    await c.answer("Saqlandi")


# ---------------------------------------------------------------------------
# /kutayotgan — hamma yuklangan fayllardan spravochnikda yo'q SKU/filiallar (to'planib borgan) + Excel shablon
async def _send_pending(pool: asyncpg.Pool, msg: Message) -> None:
    async with pool.acquire() as con:
        rep = await pending_report(con)
        xlsx = await build_missing_xlsx(con, rep) if has_missing(rep) else None
    await send_long(msg, pending_text(rep))
    if xlsx:
        await msg.answer_document(BufferedInputFile(xlsx, filename=missing_file_name(rep)), caption=missing_caption(rep))


@router.message(Command("kutayotgan"))
async def pending_cmd(m: Message, pool: asyncpg.Pool) -> None:
    if not (await get_access(pool, m.from_user.id)).can_upload:
        await m.answer("⛔ Bu buyruq faqat fayl yuklovchilar uchun.")
        return
    await _send_pending(pool, m)


@router.callback_query(F.data == "pend:all")
async def pending_cb(c: CallbackQuery, pool: asyncpg.Pool) -> None:
    if not (await get_access(pool, c.from_user.id)).can_upload:
        await c.answer("Faqat fayl yuklovchilar uchun", show_alert=True)
        return
    await c.answer("Tayyorlanmoqda…")
    await _send_pending(pool, c.message)


@router.message(Command("status"))
async def status(m: Message, pool: asyncpg.Pool) -> None:
    if not (await get_access(pool, m.from_user.id)).can_view:
        return
    r = await pool.fetchrow(
        """SELECT (SELECT count(*) FROM products) skus, (SELECT count(*) FROM categories) cats,
                  (SELECT count(*) FROM branches) brs, (SELECT count(*) FROM regions) regs,
                  (SELECT count(*) FROM clusters) cls,
                  (SELECT min(sale_date) FROM sales_daily) d1, (SELECT max(sale_date) FROM sales_daily) d2,
                  (SELECT count(DISTINCT sale_date) FROM sales_daily) days""")
    last = await pool.fetch(
        """SELECT id, kind, status, file_name, to_char(started_at AT TIME ZONE 'Asia/Tashkent','DD.MM HH24:MI') t
           FROM uploads ORDER BY id DESC LIMIT 5""")
    lines = [
        f"<b>Tovarlar:</b> {r['skus']} SKU, {r['cats']} kategoriya",
        f"<b>Filiallar:</b> {r['brs']} ({r['regs']} hudud, {r['cls']} klaster)",
        f"<b>Savdo:</b> {r['d1'] or '—'} — {r['d2'] or '—'} ({r['days']} kun)",
        "", "<b>Oxirgi yuklashlar:</b>",
        *[f"  #{u['id']} {u['t']} {u['kind']} — {u['status']} ({html.escape(u['file_name'] or '')})" for u in last],
    ]
    await m.answer("\n".join(lines))


@router.message(F.document)
async def on_document(m: Message, bot: Bot, pool: asyncpg.Pool) -> None:
    acc = await get_access(pool, m.from_user.id)
    if not acc.can_upload:
        await m.answer("⛔ Fayl yuklash huquqi yo'q." if acc.can_view else "⛔ Ruxsat yo'q — /start bosib, kirish so'rang.")
        return
    doc = m.document
    if not (doc.file_name or "").lower().endswith((".xlsx", ".xlsm")):
        await m.answer("Faqat .xlsx fayl qabul qilinadi.")
        return
    if doc.file_size and doc.file_size > config.MAX_FILE_MB * 1024 * 1024:
        kb = (InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="📥 Mini App'da yuklash", web_app=WebAppInfo(url=config.WEBAPP_URL))]]) if config.WEBAPP_URL else None)
        await m.answer(f"Fayl {config.MAX_FILE_MB} MB dan katta — Telegram bot bunday faylni qabul qilmaydi.\n"
                       f"Mini App → <b>📥 Yuklash</b> bo'limidan yuklang ({config.MAX_UPLOAD_MB} MB gacha).",
                       reply_markup=kb)
        return

    wait = await m.answer("⏳ Fayl tekshirilmoqda…")
    kind = upload_id = None
    try:
        buf = io.BytesIO()
        await bot.download(doc, destination=buf)
        data = buf.getvalue()
        from api_upload import _DB_LOCK   # Mini App yuklashi bilan bir vaqtda ketmasligi uchun
        async with _DB_LOCK:
            kind, text, upload_id = await load_any_ex(pool, data, doc.file_name, m.from_user.id)
    except LoaderError as e:
        text = f"❌ Yuklanmadi: {html.escape(str(e), quote=False)}"
    except Exception:  # noqa: BLE001
        log.exception("Yuklashda xato")
        text = "❌ Kutilmagan xato. Loglarni tekshiring — ma'lumot o'zgartirilmadi."
    try:
        await wait.delete()
    except Exception:  # noqa: BLE001
        pass
    await send_long(m, text)
    if kind and upload_id:
        # adminlarga to'liq hisobot, savdo bo'lsa — hammaga "yangilandi" xabari (fonda)
        _bg(broadcast_upload(pool, kind, text, upload_id, m.from_user.id,
                                             m.from_user.full_name, "bot", doc.file_name, bot))
        if kind in ("sales", "stock_branch", "stock_wh", "branches", "products"):
            # spravochnikda yo'q filial / tanilmagan ombor bo'limi — adminlarga tugmali so'rov
            _bg(prompt_new(pool, bot))
        if kind in ("stock_branch", "stock_wh"):
            schedule_summary(pool)       # qoldiq xulosasi — oxirgi qoldiq yuklashidan ~2,5 daqiqa keyin bitta
    if kind == "sales" and upload_id:
        # spravochnikda yo'q SKU/filiallar — to'ldirish uchun tayyor Excel shablon
        try:
            async with pool.acquire() as con:
                rep = await report_of(con, upload_id)
                xlsx = await build_missing_xlsx(con, rep) if has_missing(rep) else None
            if xlsx:
                await m.answer_document(BufferedInputFile(xlsx, filename=missing_file_name(rep)),
                                        caption=missing_caption(rep), reply_markup=all_pending_kb())
        except Exception:  # noqa: BLE001
            log.exception("Spravochnikda yo'qlar Excel'ini yuborib bo'lmadi")


_DP: Dispatcher | None = None


async def run_bot(pool: asyncpg.Pool) -> None:
    """Botni long-polling rejimida ishga tushiradi (bitta nusxa bo'lishi shart)."""
    global _DP
    if _DP is None:                  # router faqat bir marta ulanadi (qayta urinishlarda ham)
        _DP = Dispatcher()
        _DP.include_router(router)
        _DP.include_router(bot_stock.router)
    dp = _DP
    dp["pool"] = pool
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        me = await bot.get_me()          # token noto'g'ri bo'lsa shu yerda aniq xato chiqadi
        log.info("Bot ulandi: @%s", me.username)
        if config.WEBAPP_URL:
            # chat pastidagi "Menu" tugmasi Mini App'ni ochadi (xato bo'lsa bot baribir ishlayveradi)
            try:
                await bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(text="Savdo", web_app=WebAppInfo(url=config.WEBAPP_URL)))
            except Exception as e:  # noqa: BLE001
                log.warning("Menu tugmasini o'rnatib bo'lmadi (WEBAPP_URL=%r): %s", config.WEBAPP_URL, e)
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, handle_signals=False)
    finally:
        await bot.session.close()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.BOT_TOKEN or not config.DATABASE_URL:
        raise SystemExit("BOT_TOKEN va DATABASE_URL o'rnatilmagan (.env)")
    pool = await create_pool()
    applied = await migrate(pool)
    if applied:
        log.info("Qo'llangan migratsiyalar: %s", applied)
    try:
        await run_bot(pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
