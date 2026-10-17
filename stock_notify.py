"""Qoldiq yuklangandan keyin — foydalanuvchilarga qisqa xulosa (bot).

* Filial va ombor qoldig'i ko'pincha ketma-ket yuklanadi — xulosa oxirgi yuklashdan DELAY soniya keyin bitta boradi.
* /xabar bilan o'chirilgan bo'lsa — yuborilmaydi.
* Hozircha hamma kategoriyalar bo'yicha (foydalanuvchiga kategoriya biriktirish — 4-bosqich).
* «⬇ Excel» tugmasi (stk:xl) — signallar, SKU'lar, ombordan yuborish, ortiqcha, harakatsiz.
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import timedelta

import asyncpg
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

import config

log = logging.getLogger(__name__)
DELAY = 150
_TASK: asyncio.Task | None = None
LV = ["🔴 Tugadi", "🟠 Tugayapti", "🟡 Kamaymoqda"]


def _n(v: float, d: int = 0) -> str:
    return f"{v:,.{d}f}".replace(",", " ").replace(".", ",")


def _money(v: float) -> str:
    a = abs(v)
    if a >= 1e9:
        return f"{_n(v / 1e9, 2)} mlrd"
    if a >= 1e6:
        return f"{_n(v / 1e6, 1)} mln"
    return f"{_n(v / 1e3)} ming"


async def _scope(con):
    """Hamma kategoriyalar bo'yicha filtr (oxirgi qoldiq sanasi)."""
    from api_schemas import Filters
    d = await con.fetchval("SELECT max(d) FROM (SELECT max(snap_date) d FROM stock_branch "
                           "UNION ALL SELECT max(snap_date) FROM stock_wh) x")
    if d is None:
        return None, None
    cats = [r["id"] for r in await con.fetch("SELECT id FROM categories")]
    f = Filters(scope="a:", date_from=d - timedelta(days=29), date_to=d, compare="none")
    return f, cats


async def summary_text(pool: asyncpg.Pool) -> str | None:
    import api_stock as S
    async with pool.acquire() as con:
        f, cats = await _scope(con)
        if f is None:
            return None
        base = await S.base_data(con, f, cats, {})
        lists = await S.lists_data(con, f, cats, {})
    sig = S._signals(base, "all")
    snap = base["snap"]
    cnt = [sum(1 for x in sig if x["level"] == k) for k in (0, 1, 2)]
    new2 = sum(1 for x in sig if x["level"] == 2 and x["new"])
    tb = sum(r[3] for r in base["b"])
    tw = sum(r[3] for r in base["w"])
    # tarmoq savdosi: ombor qoldig'i bo'lsa — omborlar zonasi (barcha filiallar), bo'lmasa — qoldiq faylidagi filiallar
    q = sum(r[5] for r in base["w"]) if base["w"] else sum(r[5] for r in base["b"])
    q7 = sum(r[8] for r in base["w"]) if base["w"] else sum(r[8] for r in base["b"])
    val = sum(r[4] for r in base["b"]) + sum(r[4] for r in base["w"]) * base["rate"]
    days = S._days_eff(tb + tw, q, q7)
    d = snap.get("b") or snap.get("w")
    lines = [f"📦 <b>Qoldiq · {d[8:10]}.{d[5:7]}</b> · barcha kategoriyalar",
             f"Jami: <b>{_money(val)}</b> · {_n(tb + tw)} dona" + (f" · {_n(days)} kunga" if days else ""), "",
             f"{LV[0]}: <b>{cnt[0]}</b> · {LV[1]}: <b>{cnt[1]}</b> · {LV[2]}: <b>{cnt[2]}</b>"
             + (f" ({new2} yangi)" if new2 else "")]
    top = sorted(sig, key=lambda x: -x["risk"])[:3]
    if top:
        lines += ["", "Eng qimmat 3 ta:"]
        for i, x in enumerate(top, 1):
            lines.append(f"{i}. {html.escape(x['name'] or '')} · {html.escape(x['cat'] or '')} · <b>{html.escape(x['brand'] or '')}</b>"
                         f" — {_n(x['days'] or 0)} kun, ≈ {_money(x['risk'])}/kun")
    s = (lists.get("summary") or {}).get("send") or {}
    if s.get("positions"):
        lines += ["", f"🚚 Ombordan yuborish mumkin: {_n(s['positions'])} poz · {_n(s['qty'])} dona"]
    o, i_ = (lists.get("summary") or {}).get("over") or {}, (lists.get("summary") or {}).get("idle") or {}
    if o.get("positions") or i_.get("positions"):
        lines.append(f"📈 Ortiqcha: {_money(o.get('value', 0))} · 💤 Harakatsiz: {_money(i_.get('value', 0))}")
    return "\n".join(lines)


def _kb() -> InlineKeyboardMarkup:
    row = []
    if config.WEBAPP_URL:
        row.append(InlineKeyboardButton(text="📊 Qoldiqni ochish", web_app=WebAppInfo(url=config.WEBAPP_URL)))
    row.append(InlineKeyboardButton(text="⬇ Excel", callback_data="stk:xl"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


async def send_summary(pool: asyncpg.Pool, bot: Bot | None = None) -> int:
    from notify import is_muted
    if not config.BOT_TOKEN or await is_muted(pool):
        return 0
    text = await summary_text(pool)
    if not text:
        return 0
    users = {r["tg_id"] for r in await pool.fetch("SELECT tg_id FROM users WHERE status = 'approved'")} | set(config.ADMIN_IDS)
    own = bot is None
    if own:
        bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    n = 0
    try:
        for u in sorted(users):
            try:
                await bot.send_message(u, text, reply_markup=_kb())
                n += 1
            except Exception as e:  # noqa: BLE001
                log.warning("Qoldiq xulosasi (%s) ga yuborilmadi: %s", u, e)
            await asyncio.sleep(0.05)
    finally:
        if own:
            await bot.session.close()
    return n


def schedule_summary(pool: asyncpg.Pool, delay: int = DELAY) -> None:
    """Qoldiq yuklangach chaqiriladi: oldingi rejalashtirilgan xulosa bekor qilinadi, yangisi delay dan keyin."""
    global _TASK
    if _TASK and not _TASK.done():
        _TASK.cancel()

    async def run():
        try:
            await asyncio.sleep(delay)
            await send_summary(pool)
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            log.exception("Qoldiq xulosasini yuborib bo'lmadi")
    _TASK = asyncio.create_task(run())


async def excel_for(pool: asyncpg.Pool) -> tuple[bytes, str] | None:
    import api_stock as S
    async with pool.acquire() as con:
        f, cats = await _scope(con)
        if f is None:
            return None
        return await S.export_bytes(con, f, cats, {})


async def send_excel(pool: asyncpg.Pool, bot: Bot, chat_id: int) -> bool:
    res = await excel_for(pool)
    if res is None:
        return False
    data, fname = res
    await bot.send_document(chat_id, BufferedInputFile(data, filename=fname),
                            caption="📦 Qoldiq: signallar, SKU'lar, ombordan yuborish, ortiqcha, harakatsiz")
    return True
