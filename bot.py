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
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp,
                           Message, WebAppInfo)

import config
from access import ROLE_NAMES, get_access, invalidate
from db import create_pool, migrate
from loaders_common import LoaderError
from loaders_dispatch import load_any

log = logging.getLogger(__name__)
router = Router()
TG_LIMIT = 4000  # xabar uzunligi zaxira bilan


async def send_long(m: Message, text: str) -> None:
    while text:
        cut = text if len(text) <= TG_LIMIT else text[: text.rfind("\n", 0, TG_LIMIT)]
        await m.answer(cut)
        text = text[len(cut):].lstrip("\n")


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
    "Fayl turi avtomatik aniqlanadi.")


@router.message(CommandStart())
async def start(m: Message, pool: asyncpg.Pool) -> None:
    acc = await get_access(pool, m.from_user.id)
    if acc.can_view:
        text = f"Salom! Siz: <b>{ROLE_NAMES[acc.level]}</b>.\nSavdoni ko'rish uchun pastdagi tugmani bosing."
        if acc.can_upload:
            text += WELCOME_UPLOAD
        text += "\n\n/status — bazadagi ma'lumotlar holati\n/attrs — kategoriyalar va xususiyatlar"
        if acc.is_admin:
            text += "\n/users — foydalanuvchilar va kirish so'rovlari"
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
    await c.message.edit_text("✅ So'rovingiz adminga yuborildi. Tasdiqlanishi bilan xabar keladi."
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
        await c.message.edit_text(f"✅ <b>Ruxsat berildi</b> — {ROLE_NAMES[role]}\n{who}\n<i>Tasdiqladi: {by}</i>")
        msg = f"✅ Kirish tasdiqlandi! Siz: <b>{ROLE_NAMES[role]}</b>.\nSavdoni ko'rish uchun tugmani bosing."
        if role == "uploader":
            msg += WELCOME_UPLOAD
        try:
            await bot.send_message(tg_id, msg, reply_markup=_app_kb())
        except Exception as e:  # noqa: BLE001
            log.warning("Foydalanuvchiga xabar yuborilmadi: %s", e)
    else:
        await c.message.edit_text(f"❌ <b>Rad etildi</b>\n{who}\n<i>Rad etdi: {by}</i>")
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
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer(note)


@router.message(Command("attrs"))
async def attrs(m: Message, pool: asyncpg.Pool) -> None:
    if not (await get_access(pool, m.from_user.id)).can_view:
        return
    rows = await pool.fetch(
        """SELECT c.name cat, a.slot, a.source_col, a.name, a.data_type, a.unit,
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
            out.append(f"  {r['slot']}. {html.escape(r['name'])}{unit} <i>({html.escape(r['source_col'])}, {r['data_type']})</i>")
    await send_long(m, "\n".join(out).strip() or "Hozircha bo'sh.")


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
    buf = io.BytesIO()
    await bot.download(doc, destination=buf)
    data = buf.getvalue()

    try:
        text = await load_any(pool, data, doc.file_name, m.from_user.id)
    except LoaderError as e:
        text = f"❌ Yuklanmadi: {e}"
    except Exception:  # noqa: BLE001
        log.exception("Yuklashda xato")
        text = "❌ Kutilmagan xato. Loglarni tekshiring — ma'lumot o'zgartirilmadi."
    await wait.delete()
    await send_long(m, text)


_DP: Dispatcher | None = None


async def run_bot(pool: asyncpg.Pool) -> None:
    """Botni long-polling rejimida ishga tushiradi (bitta nusxa bo'lishi shart)."""
    global _DP
    if _DP is None:                  # router faqat bir marta ulanadi (qayta urinishlarda ham)
        _DP = Dispatcher()
        _DP.include_router(router)
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
