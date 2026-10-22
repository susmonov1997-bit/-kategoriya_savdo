"""Yuklashdan keyingi xabarlar.

  * Adminlarga (ADMIN_IDS) — HAR QANDAY muvaffaqiyatli yuklashdan keyin to'liq hisobot: kim, qayerdan
    (bot / Mini App), qaysi fayl, fayl turi va loaderning to'liq izohlari (spravochnikda yo'qlar, xatolar…).
    Savdo yuklanib, spravochnikda yo'q SKU/filiallar bo'lsa — Excel shablon ham ilova qilinadi.
  * Savdo yuklanganda barcha tasdiqlangan foydalanuvchilarga — qisqa "Savdo ma'lumoti yangilandi" xabari
    va Mini App'ni ochish tugmasi.
  * Yuklagan odamning o'ziga hisobot avvalgidek alohida boradi — unga bu xabarlar takror yuborilmaydi.
"""
from __future__ import annotations

import asyncio
import html
import logging

import asyncpg
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

import config
from export_missing import build_missing_xlsx, file_name, has_missing, missing_caption, report_of
from loaders_dispatch import KIND_NAMES

log = logging.getLogger(__name__)
TG_LIMIT = 3900


def period_text(dates: list[str] | None) -> str:
    if not dates:
        return ""
    f = lambda d: f"{d[8:10]}.{d[5:7]}.{d[0:4]}"  # noqa: E731
    return f(dates[0]) if len(dates) == 1 else f"{f(dates[0])} — {f(dates[-1])}"


async def _who(pool: asyncpg.Pool, tg_id: int, fallback: str = "") -> str:
    r = await pool.fetchrow("SELECT first_name, last_name, username FROM users WHERE tg_id = $1", tg_id)
    name = " ".join(x for x in ((r["first_name"], r["last_name"]) if r else ()) if x) or fallback or "—"
    un = f" @{r['username']}" if r and r["username"] else ""
    return f"{html.escape(name)}{html.escape(un)} (ID <code>{tg_id}</code>)"


def split_text(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Matnni qatorlar bo'yicha bo'laklarga bo'ladi; juda uzun qator bo'lsa — qattiq kesadi (cheksiz sikl bo'lmaydi)."""
    parts = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break
        i = text.rfind("\n", 0, limit)
        cut = text[:i] if i > 0 else text[:limit]
        parts.append(cut)
        text = text[len(cut):].lstrip("\n")
    return parts


async def _chunks(bot: Bot, chat_id: int, text: str, kb=None) -> None:
    parts = split_text(text)
    for i, p in enumerate(parts):
        await bot.send_message(chat_id, p, reply_markup=kb if i == len(parts) - 1 else None)


async def get_setting(pool, key: str) -> str | None:
    return await pool.fetchval("SELECT value FROM app_settings WHERE key = $1", key)


async def set_setting(pool, key: str, value: str | None, by: int | None = None) -> None:
    await pool.execute("""INSERT INTO app_settings(key, value, updated_by) VALUES ($1, $2, $3)
                          ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by,
                                                          updated_at = now()""", key, value, by)


async def is_muted(pool) -> bool:
    return (await get_setting(pool, "notify_muted")) == "1"


async def send_users_update(pool, bot: Bot, period: str, skip: set[int]) -> int:
    """Tasdiqlangan foydalanuvchilarga qisqa "Savdo ma'lumoti yangilandi" xabari. Yuborilganlar sonini qaytaradi."""
    users = [r["tg_id"] for r in await pool.fetch("SELECT tg_id FROM users WHERE status = 'approved'")]
    kb = (InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="📊 Savdoni ochish", web_app=WebAppInfo(url=config.WEBAPP_URL))]]) if config.WEBAPP_URL else None)
    msg = f"📊 <b>Savdo ma'lumoti yangilandi</b>" + (f": {period}" if period else "")
    n = 0
    for u in users:
        if u in skip:
            continue
        try:
            await bot.send_message(u, msg, reply_markup=kb)
            n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("Foydalanuvchiga (%s) xabar yuborilmadi: %s", u, e)
        await asyncio.sleep(0.05)
    return n


async def broadcast_upload(pool: asyncpg.Pool, kind: str, report_text: str, upload_id: int | None,
                           uploader_id: int, uploader_name: str = "", source: str = "bot",
                           file_name_: str | None = None, bot: Bot | None = None) -> None:
    """Xatolar yutiladi — xabar yuborilmasa ham yuklash natijasi o'zgarmaydi."""
    if not config.BOT_TOKEN:
        return
    # admin /xabar bilan o'chirgan bo'lsa — foydalanuvchilarga «yangilandi» xabari bormaydi, adminlarga to'liq
    # hisobot boradi; savdo sanalari eslab qolinadi — yoqilganda bitta umumiy xabar yuborish mumkin
    muted = False
    try:
        if await is_muted(pool):
            muted = True
            if kind == "sales" and upload_id:
                async with pool.acquire() as con:
                    rep = await report_of(con, upload_id)
                d = (rep or {}).get("dates") or []
                if d:
                    a, b = await get_setting(pool, "muted_from"), await get_setting(pool, "muted_to")
                    await set_setting(pool, "muted_from", min(filter(None, [a, d[0]])))
                    await set_setting(pool, "muted_to", max(filter(None, [b, d[-1]])))
    except Exception:  # noqa: BLE001
        log.exception("Xabar sozlamasini o'qib bo'lmadi — xabarlar odatdagidek yuboriladi")
    own = bot is None
    if own:
        bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        rep = None
        if kind == "sales" and upload_id:
            async with pool.acquire() as con:
                rep = await report_of(con, upload_id)
        period = period_text(rep.get("dates") if rep else None)
        who = await _who(pool, uploader_id, uploader_name)

        # --- 1. Adminlarga to'liq hisobot ---
        head = (f"🔔 <b>Yangi yuklash</b> — {html.escape(KIND_NAMES.get(kind, kind))}\n"
                f"👤 {who} · {'Mini App' if source == 'miniapp' else 'bot'}\n"
                + (f"📄 {html.escape(file_name_)}\n" if file_name_ else "")
                + "\n")
        xlsx = None
        if has_missing(rep):
            async with pool.acquire() as con:
                xlsx = await build_missing_xlsx(con, rep)
        admins = [a for a in config.ADMIN_IDS if a != uploader_id]
        for a in admins:
            try:
                await _chunks(bot, a, head + report_text)
                if xlsx:
                    await bot.send_document(a, BufferedInputFile(xlsx, filename=file_name(rep)),
                                            caption=missing_caption(rep))
            except Exception as e:  # noqa: BLE001
                log.warning("Adminga (%s) xabar yuborilmadi: %s", a, e)
            await asyncio.sleep(0.05)

        # --- 2. Savdo yangilansa — hammaga qisqa xabar ---
        if kind != "sales" or muted:
            return
        await send_users_update(pool, bot, period, {uploader_id, *config.ADMIN_IDS})
    except Exception:  # noqa: BLE001
        log.exception("Yuklashdan keyingi xabarlarni yuborib bo'lmadi")
    finally:
        if own:
            await bot.session.close()
