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
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Message,
                           WebAppInfo)

import config
from db import create_pool, migrate
from loaders_common import LoaderError
from loaders_dispatch import load_any

log = logging.getLogger(__name__)
router = Router()
TG_LIMIT = 4000  # xabar uzunligi zaxira bilan


def allowed(m: Message) -> bool:
    return bool(m.from_user) and m.from_user.id in config.ADMIN_IDS


async def send_long(m: Message, text: str) -> None:
    while text:
        cut = text if len(text) <= TG_LIMIT else text[: text.rfind("\n", 0, TG_LIMIT)]
        await m.answer(cut)
        text = text[len(cut):].lstrip("\n")


@router.message(CommandStart())
async def start(m: Message) -> None:
    if not allowed(m):
        await m.answer(f"⛔ Ruxsat yo'q. ID: <code>{m.from_user.id}</code> — adminga yuboring.")
        return
    kb = (InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="📊 Savdoni ochish", web_app=WebAppInfo(url=config.WEBAPP_URL))]]) if config.WEBAPP_URL else None)
    await m.answer(
        "Salom! Savdoni ko'rish uchun pastdagi tugmani bosing.\n\n"
        "Yuklash uchun Excel faylni shu yerga tashlang:\n"
        "• <b>Kunlik savdo</b> (Chiqim tovarlar …xlsx)\n"
        "• <b>Tovar spravochnigi</b> (Товар Ид, Категория, Бренд, Подкатегория…)\n"
        "• <b>Filial spravochnigi</b> (Территория, Филиал, Кластер)\n"
        "• <b>Qo'shimcha daromad %</b> (Категория, Бренд, Қўшимча даромад %)\n"
        "Fayl turi sarlavhalar bo'yicha avtomatik aniqlanadi. Tartib: avval spravochniklar, keyin savdo.\n\n"
        "/attrs — kategoriyalar va xususiyatlar\n/status — bazadagi ma'lumotlar holati",
        reply_markup=kb,
    )


@router.message(Command("attrs"))
async def attrs(m: Message, pool: asyncpg.Pool) -> None:
    if not allowed(m):
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
    if not allowed(m):
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
    if not allowed(m):
        await m.answer("⛔ Ruxsat yo'q.")
        return
    doc = m.document
    if not (doc.file_name or "").lower().endswith((".xlsx", ".xlsm")):
        await m.answer("Faqat .xlsx fayl qabul qilinadi.")
        return
    if doc.file_size and doc.file_size > config.MAX_FILE_MB * 1024 * 1024:
        await m.answer(f"Fayl juda katta (> {config.MAX_FILE_MB} MB — Telegram limiti). "
                       "Kunlarga bo'lib yuboring yoki serverda <code>python load_file.py</code> bilan yuklang.")
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
