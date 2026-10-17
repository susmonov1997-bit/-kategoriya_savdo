"""Yangi filial va noma'lum ombor bo'limi — bot tugmalar bilan so'raydi (faqat admin).

* Savdo yoki qoldiq faylida spravochnikda yo'q filial chiqsa → «🆕 Yangi filial» xabari:
  ombor → klaster → hudud (hudud faylda bo'lsa va bazada bor bo'lsa — so'ralmaydi). Saqlangach
  kutayotgan savdo va qoldiq avtomatik ulanadi.
* Ombor qoldig'ida tanilmagan bo'lim (masalan «Termiz Logist ombor») → qaysi omborga ekanini so'raydi va
  nomning birinchi so'zini eslab qoladi (keyingi fayllarda Logist/Artel/citystar — hammasi o'zi taniladi).
* Yangi ombor yoki klaster — filial spravochnigi orqali (Омбор / Кластер ustunlari) qo'shiladi.

/yangi — ochiq so'rovlarni qayta ko'rsatadi.
"""
from __future__ import annotations

import html
import logging

import asyncpg
from aiogram import Bot, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

import config
from access import get_access
from fact import ensure_fact
from loaders_common import name_key
from pending import format_resolved, resolve_pending, resolve_stock

log = logging.getLogger(__name__)
router = Router()
esc = lambda s: html.escape(str(s or ""), quote=False)  # noqa: E731


def _n(v: float, d: int = 0) -> str:
    return f"{v:,.{d}f}".replace(",", " ").replace(".", ",")


def _kb(rows: list[list[Btn]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _grid(btns: list[Btn], n: int) -> list[list[Btn]]:
    return [btns[i:i + n] for i in range(0, len(btns), n)]


# ---------------------------------------------------------------------------
# So'rovlarni kutayotgan jadvallardan yig'ish
async def sync_requests(con: asyncpg.Connection) -> None:
    await con.execute(
        """WITH u AS (
               SELECT branch_key k, max(branch_name) n, NULL::text reg FROM sales_pending GROUP BY 1
               UNION ALL SELECT branch_key, max(branch_name), NULL FROM company_pending GROUP BY 1
               UNION ALL SELECT loc_key, max(loc_name), max(region) FROM stock_pending WHERE kind = 'b' GROUP BY 1)
           INSERT INTO branch_requests (name_key, name, region_hint)
           SELECT k, max(n), max(reg) FROM u
           WHERE k IS NOT NULL AND NOT EXISTS (SELECT 1 FROM branches b WHERE b.name_key = u.k)
           GROUP BY k
           ON CONFLICT (name_key) DO UPDATE
           SET region_hint = COALESCE(branch_requests.region_hint, EXCLUDED.region_hint)""")
    await con.execute("UPDATE branch_requests r SET done_at = now() WHERE done_at IS NULL "
                      "AND EXISTS (SELECT 1 FROM branches b WHERE b.name_key = r.name_key)")
    await con.execute(
        """INSERT INTO warehouse_requests (alias_key, sample_name)
           SELECT loc_key, max(loc_name) FROM stock_pending sp
           WHERE kind = 'w' AND NOT EXISTS (SELECT 1 FROM warehouse_aliases a WHERE a.alias_key = sp.loc_key)
           GROUP BY loc_key
           ON CONFLICT (alias_key) DO NOTHING""")
    await con.execute("UPDATE warehouse_requests r SET done_at = now() WHERE done_at IS NULL "
                      "AND EXISTS (SELECT 1 FROM warehouse_aliases a WHERE a.alias_key = r.alias_key)")


async def _branch_stats(con, key: str) -> str:
    r = await con.fetchrow(
        """SELECT (SELECT count(*) FROM sales_pending WHERE branch_key = $1) s_rows,
                  (SELECT coalesce(sum(amount), 0) FROM sales_pending WHERE branch_key = $1) s_amt,
                  (SELECT coalesce(sum(amount), 0) FROM company_pending WHERE branch_key = $1) c_amt,
                  (SELECT count(*) FROM stock_pending WHERE kind = 'b' AND loc_key = $1) q_rows,
                  (SELECT coalesce(sum(qty), 0) FROM stock_pending WHERE kind = 'b' AND loc_key = $1) q_qty""", key)
    parts = []
    if r["s_rows"]:
        parts.append(f"savdo {r['s_rows']} qator · {_n(float(r['s_amt']) / 1e6, 1)} mln")
    elif r["c_amt"]:
        parts.append(f"set jamida {_n(float(r['c_amt']) / 1e6, 1)} mln")
    if r["q_rows"]:
        parts.append(f"qoldiq {r['q_rows']} qator · {_n(float(r['q_qty']))} dona")
    return "; ".join(parts) or "—"


async def _branch_intro(con, req) -> tuple[str, InlineKeyboardMarkup]:
    stats = await _branch_stats(con, req["name_key"])
    hint = f"\nFayldagi hudud: {esc(req['region_hint'])}" if req["region_hint"] else ""
    text = (f"🆕 <b>Yangi filial:</b> «{esc(req['name'])}»\n"
            f"Spravochnikda yo'q — kutmoqda: {stats}{hint}\n\n"
            "Ombor, klaster va hududni tanlang — shundan keyin ma'lumoti avtomatik ulanadi.")
    return text, _kb([[Btn(text="📝 Hozir to'ldirish", callback_data=f"nb:s:{req['id']}"),
                       Btn(text="Keyinroq", callback_data=f"nb:x:{req['id']}")]])


async def _wh_intro(con, req) -> tuple[str, InlineKeyboardMarkup]:
    r = await con.fetchrow("SELECT count(*) n, coalesce(sum(qty), 0) q FROM stock_pending WHERE kind = 'w' AND loc_key = $1",
                           req["alias_key"])
    whs = await con.fetch("SELECT id, name FROM warehouses ORDER BY name")
    text = (f"❓ <b>Noma'lum ombor bo'limi:</b> «{esc(req['sample_name'])}»\n"
            f"Kutmoqda: {r['n']} qator · {_n(float(r['q']))} dona\n\n"
            + f"Qaysi omborga qo'shilsin? (nomi «{esc(req['alias_key'])}…» bilan boshlanadigan barcha bo'limlar shu omborga)")
    btns = [Btn(text=w["name"], callback_data=f"wa:{req['id']}:{w['id']}") for w in whs]
    rows = _grid(btns, 2) + [[Btn(text="➕ Yangi ombor", callback_data=f"wa:{req['id']}:new")]]
    return text, _kb(rows)


async def prompt_new(pool: asyncpg.Pool, bot: Bot | None = None, only_new: bool = True,
                     chat_ids: list[int] | None = None) -> int:
    """Ochiq so'rovlarni adminlarga yuboradi (only_new — faqat hali yuborilmaganlarini). Xatolar yutiladi."""
    if not config.BOT_TOKEN:
        return 0
    own = bot is None
    sent = 0
    try:
        async with pool.acquire() as con:
            await sync_requests(con)
            cond = "done_at IS NULL" + (" AND notified_at IS NULL" if only_new else "")
            breqs = await con.fetch(f"SELECT * FROM branch_requests WHERE {cond} ORDER BY id LIMIT 20")
            wreqs = await con.fetch(f"SELECT * FROM warehouse_requests WHERE {cond} ORDER BY id LIMIT 20")
            msgs = [await _wh_intro(con, r) for r in wreqs] + [await _branch_intro(con, r) for r in breqs]
            if not msgs:
                return 0
            if own:
                bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            for cid in (chat_ids or sorted(config.ADMIN_IDS)):
                for text, kb in msgs:
                    try:
                        await bot.send_message(cid, text, reply_markup=kb)
                        sent += 1
                    except Exception as e:  # noqa: BLE001
                        log.warning("So'rov (%s) ga yuborilmadi: %s", cid, e)
            if breqs:
                await con.execute("UPDATE branch_requests SET notified_at = now() WHERE id = ANY($1::int[])",
                                  [r["id"] for r in breqs])
            if wreqs:
                await con.execute("UPDATE warehouse_requests SET notified_at = now() WHERE id = ANY($1::int[])",
                                  [r["id"] for r in wreqs])
    except Exception:  # noqa: BLE001
        log.exception("Yangi filial/ombor so'rovlarini yuborib bo'lmadi")
    finally:
        if own and bot is not None:
            await bot.session.close()
    return sent


@router.message(Command("yangi"))
async def yangi_cmd(m: Message, pool: asyncpg.Pool, bot: Bot) -> None:
    if not (await get_access(pool, m.from_user.id)).is_admin:
        await m.answer("⛔ Faqat admin uchun.")
        return
    n = await prompt_new(pool, bot, only_new=False, chat_ids=[m.from_user.id])
    if not n:
        await m.answer("✅ To'ldirilmagan yangi filial yoki ombor bo'limi yo'q.")


# ---------------------------------------------------------------------------
# Yangi filial: nb:s|x|w|c|r|ra|rn
async def _req(con, rid: int):
    return await con.fetchrow("SELECT * FROM branch_requests WHERE id = $1", rid)


async def _step_wh(con, req) -> tuple[str, InlineKeyboardMarkup]:
    whs = await con.fetch("SELECT id, name FROM warehouses ORDER BY name")
    btns = [Btn(text=("✓ " if w["id"] == req["warehouse_id"] else "") + w["name"],
                callback_data=f"nb:w:{req['id']}:{w['id']}") for w in whs]
    return (f"<b>{esc(req['name'])} · 1/3</b>\nQaysi <b>ombordan</b> tovar oladi?",
            _kb(_grid(btns, 2)))


async def _step_cl(con, req) -> tuple[str, InlineKeyboardMarkup]:
    cls = await con.fetch("SELECT id, name FROM clusters ORDER BY name")
    btns = [Btn(text=c["name"], callback_data=f"nb:c:{req['id']}:{c['id']}") for c in cls]
    return (f"<b>{esc(req['name'])} · 2/3</b>\nQaysi <b>klaster</b>?",
            _kb(_grid(btns, 3) + [[Btn(text="‹ Orqaga", callback_data=f"nb:s:{req['id']}")]]))


async def _step_rg(con, req, all_: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    rows = await con.fetch(
        """SELECT r.id, r.name, count(b.id) FILTER (WHERE b.warehouse_id = $1) n
           FROM regions r LEFT JOIN branches b ON b.region_id = r.id GROUP BY r.id ORDER BY n DESC, r.name""",
        req["warehouse_id"])
    near = [r for r in rows if r["n"]]
    shown = rows if all_ or not near else near[:8]
    btns = [Btn(text=r["name"], callback_data=f"nb:r:{req['id']}:{r['id']}") for r in shown]
    kb = _grid(btns, 2)
    if len(shown) < len(rows):
        kb.append([Btn(text="Boshqa hudud ▾", callback_data=f"nb:ra:{req['id']}")])
    hint = req["region_hint"]
    if hint and not any(name_key(r["name"]) == name_key(hint) for r in rows):
        kb.append([Btn(text=f"➕ «{hint[:30]}» — yangi hudud", callback_data=f"nb:rn:{req['id']}")])
    kb.append([Btn(text="‹ Orqaga", callback_data=f"nb:w:{req['id']}:{req['warehouse_id']}")])
    wh = await con.fetchval("SELECT name FROM warehouses WHERE id = $1", req["warehouse_id"])
    note = f"\n<i>{esc(wh)} hududlari birinchi</i>" if near and not all_ else ""
    src = "Faylda hudud yo'q" if not hint else f"Fayldagi «{esc(hint)}» bazada yo'q"
    return f"<b>{esc(req['name'])} · 3/3</b>\n{src} — qaysi <b>hudud</b>?{note}", _kb(kb)


async def _finish(pool, con, req, by: int) -> str:
    async with con.transaction():
        await con.execute(
            """INSERT INTO branches (name, name_key, region_id, cluster_id, warehouse_id)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (name_key) DO UPDATE SET region_id = EXCLUDED.region_id, cluster_id = EXCLUDED.cluster_id,
                 warehouse_id = EXCLUDED.warehouse_id, updated_at = now()""",
            req["name"], req["name_key"], req["region_id"], req["cluster_id"], req["warehouse_id"])
        res = await resolve_pending(con)
        await con.execute("UPDATE branch_requests SET done_at = now(), done_by = $2 WHERE id = $1", req["id"], by)
    try:
        await ensure_fact(pool)
    except Exception:  # noqa: BLE001
        log.exception("sales_fact qayta qurilmadi — API keyingi so'rovda quradi")
    r = await con.fetchrow(
        """SELECT rg.name rg, cl.name cl, w.name wh FROM regions rg, clusters cl, warehouses w
           WHERE rg.id = $1 AND cl.id = $2 AND w.id = $3""", req["region_id"], req["cluster_id"], req["warehouse_id"])
    return (f"✅ <b>Saqlandi:</b> {esc(req['name'])}\n{esc(r['rg'])} · {esc(r['cl'])} · {esc(r['wh'])}"
            + (format_resolved(res) or "\nKutayotgan ma'lumot yo'q edi."))


@router.callback_query(F.data.startswith("nb:"))
async def nb_cb(c: CallbackQuery, pool: asyncpg.Pool) -> None:
    if not (await get_access(pool, c.from_user.id)).is_admin:
        await c.answer("Faqat admin uchun", show_alert=True)
        return
    p = c.data.split(":")
    act, rid = p[1], int(p[2])
    async with pool.acquire() as con:
        req = await _req(con, rid)
        if req is None:
            await c.answer("So'rov topilmadi", show_alert=True)
            return
        if req["done_at"] or await con.fetchval("SELECT 1 FROM branches WHERE name_key = $1", req["name_key"]):
            await c.answer("Bu filial allaqachon saqlangan", show_alert=True)
            try:
                await c.message.edit_reply_markup(reply_markup=None)
            except Exception:  # noqa: BLE001
                pass
            return
        if act == "x":
            await c.message.edit_reply_markup(reply_markup=None)
            await c.answer("Keyinroq — /yangi buyrug'i bilan qaytasiz")
            return
        if act == "s":
            text, kb = await _step_wh(con, req)
        elif act == "w":
            await con.execute("UPDATE branch_requests SET warehouse_id = $2 WHERE id = $1", rid, int(p[3]))
            text, kb = await _step_cl(con, await _req(con, rid))
        elif act in ("c", "ra", "rn", "r"):
            if act == "c":
                await con.execute("UPDATE branch_requests SET cluster_id = $2 WHERE id = $1", rid, int(p[3]))
                req = await _req(con, rid)
                if req["region_hint"]:   # fayldagi hudud bazada bo'lsa — so'ralmaydi
                    hk = name_key(req["region_hint"])
                    rg = next((r["id"] for r in await con.fetch("SELECT id, name FROM regions")
                               if name_key(r["name"]) == hk), None)
                    if rg:
                        await con.execute("UPDATE branch_requests SET region_id = $2 WHERE id = $1", rid, rg)
                        text = await _finish(pool, con, await _req(con, rid), c.from_user.id)
                        await c.message.edit_text(text)
                        await c.answer("Saqlandi")
                        return
                text, kb = await _step_rg(con, req)
            elif act == "ra":
                text, kb = await _step_rg(con, req, all_=True)
            else:
                if act == "rn":
                    rg = await con.fetchval(
                        "INSERT INTO regions(name) VALUES ($1) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name "
                        "RETURNING id", req["region_hint"])
                else:
                    rg = int(p[3])
                await con.execute("UPDATE branch_requests SET region_id = $2 WHERE id = $1", rid, rg)
                req = await _req(con, rid)
                if not (req["warehouse_id"] and req["cluster_id"]):
                    text, kb = await _step_wh(con, req)
                else:
                    text = await _finish(pool, con, req, c.from_user.id)
                    await c.message.edit_text(text)
                    await c.answer("Saqlandi")
                    return
        else:
            await c.answer()
            return
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


# ---------------------------------------------------------------------------
# Ombor bo'limi: wa:<id>:<warehouse_id|new>
@router.callback_query(F.data.startswith("wa:"))
async def wa_cb(c: CallbackQuery, pool: asyncpg.Pool) -> None:
    if not (await get_access(pool, c.from_user.id)).is_admin:
        await c.answer("Faqat admin uchun", show_alert=True)
        return
    _, rid, wid = c.data.split(":")
    async with pool.acquire() as con:
        req = await con.fetchrow("SELECT * FROM warehouse_requests WHERE id = $1", int(rid))
        if req is None:
            await c.answer("So'rov topilmadi", show_alert=True)
            return
        if req["done_at"]:
            await c.answer("Allaqachon tanlangan", show_alert=True)
            return
        if wid == "new":
            await c.answer("Yangi omborni filial spravochnigidagi «Омбор» ustuniga yozib yuklang — "
                           "keyin /yangi bosib shu bo'limni tanlaysiz.", show_alert=True)
            return
        async with con.transaction():
            await con.execute("INSERT INTO warehouse_aliases (alias_key, warehouse_id, created_by) VALUES ($1, $2, $3) "
                              "ON CONFLICT (alias_key) DO UPDATE SET warehouse_id = EXCLUDED.warehouse_id",
                              req["alias_key"], int(wid), c.from_user.id)
            st = await resolve_stock(con)
            await con.execute("UPDATE warehouse_requests SET done_at = now(), done_by = $2 WHERE id = $1",
                              req["id"], c.from_user.id)
        wname = await con.fetchval("SELECT name FROM warehouses WHERE id = $1", int(wid))
    await c.message.edit_text(
        f"✅ «{esc(req['sample_name'])}» → <b>{esc(wname)}</b>. Keyingi fayllarda o'zi taniydi."
        + (f"\n📦 Kutayotgan qoldiq ulandi: {st['stock_rows']} qator · {_n(st['stock_qty'])} dona"
           if st["stock_rows"] else ""))
    await c.answer("Saqlandi")
