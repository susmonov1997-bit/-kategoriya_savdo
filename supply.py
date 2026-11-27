"""Ta'minot signallari: yangi signallarni adminlarga botda yuborish (yuklashdan keyin)."""
from __future__ import annotations

import html
import logging

import asyncpg

import config

log = logging.getLogger(__name__)

SIG_NAMES = {"kirim_nomx": "🚫 Matritsada yo'q tovar kirim qilindi",
             "ship_nomx": "🚫 Matritsada yo'q tovar zakaz qilindi / jo'natildi (hali kelmagan)"}


def _f(v) -> str:
    return f"{float(v or 0):,.0f}".replace(",", " ")


def _usd(v) -> str:
    v = float(v or 0)
    return f"{v / 1e6:,.2f} mln $".replace(",", " ").replace(".", ",") if v >= 1e6 else f"{v / 1e3:,.1f} ming $".replace(",", " ").replace(".", ",")


async def recheck_signals(con) -> int:
    """Matritsa yangilangach (yoki tovar qayta bog'langach): endi shu ombor filiallari matritsasida bor tovarning
    ochiq signallari avtomatik yopiladi. Yopilganlar soni. (UPDATE faqat kerak bo'lsa — kesh bekorga eskirmasin.)"""
    ids = [r[0] for r in await con.fetch(
        """SELECT s.id FROM supply_signals s WHERE s.kind IN ('kirim_nomx', 'ship_nomx') AND s.seen_at IS NULL
           AND EXISTS (SELECT 1 FROM matrix_rows m JOIN branches b ON b.id = m.branch_id
                       WHERE m.product_id = s.product_id AND b.warehouse_id = s.warehouse_id)""")]
    if ids:
        await con.execute("UPDATE supply_signals SET seen_at = now(), note = COALESCE(note, $2) WHERE id = ANY($1::int[])",
                          ids, "✓ matritsaga qo'shildi — avtomatik yopildi")
    return len(ids)


async def notify_signals(pool: asyncpg.Pool, bot=None) -> int:
    """Hali yuborilmagan signallar — bitta xabar (kind bo'yicha), adminlarga. Yuborilganlar belgilanadi."""
    if not config.BOT_TOKEN:
        return 0
    rows = await pool.fetch(
        """SELECT s.id, s.kind, s.data, p.name pname, w.name wname
           FROM supply_signals s LEFT JOIN products p ON p.product_id = s.product_id
           LEFT JOIN warehouses w ON w.id = s.warehouse_id
           WHERE s.notified_at IS NULL ORDER BY s.kind, (s.data->>'usd')::float8 DESC NULLS LAST""")
    if not rows:
        return 0
    import json
    e = lambda s: html.escape(str(s or "—"), quote=False)  # noqa: E731
    lines = [f"⚠️ <b>Yangi signallar: {len(rows)}</b>"]
    kind = None
    for i, r in enumerate(rows):
        d = r["data"] if isinstance(r["data"], dict) else json.loads(r["data"] or "{}")
        if r["kind"] != kind:
            kind = r["kind"]
            lines += ["", f"<b>{SIG_NAMES.get(kind, kind)}</b>"]
        if i >= 15:
            lines.append(f"… yana {len(rows) - 15} ta — Mini App'da")
            break
        dt = str(d.get("date") or "")
        lines.append(f"• {e(r['pname'])} · {e(r['wname'])} · {_f(d.get('qty'))} dona"
                     + (f" · {e(d['stage'])}" if d.get("stage") else "")
                     + (f" · {_usd(d['usd'])}" if d.get("usd") else "")
                     + (f" · {dt[8:10]}.{dt[5:7]}" if dt else "")
                     + (f" · {e(d.get('order'))}" if d.get("order") else ""))
    lines += ["", "<i>Shu ombor filiallarining birortasi matritsasida yo'q. Mini App → Ta'minot → ⚠</i>"]
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📱 Ta'minot → Signallar",
                                                                     web_app=WebAppInfo(url=config.WEBAPP_URL))]]) \
        if config.WEBAPP_URL else None
    own = bot is None
    if own:
        bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        for uid in sorted(config.ADMIN_IDS):
            try:
                await bot.send_message(uid, "\n".join(lines), reply_markup=kb)
            except Exception as ex:  # noqa: BLE001
                log.warning("Signal xabari %s ga yuborilmadi: %s", uid, ex)
        await pool.execute("UPDATE supply_signals SET notified_at = now() WHERE id = ANY($1::int[])", [r["id"] for r in rows])
    finally:
        if own:
            await bot.session.close()
    return len(rows)
