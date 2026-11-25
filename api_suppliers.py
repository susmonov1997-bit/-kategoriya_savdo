"""Ta'minotchilar API (022_suppliers.sql): Ta'minot → Ta'minotchi (Guruhlar, Qoidalar).

GET  /api/supply/sup/groups              guruhlar va ta'minotchilar (kirim summasi, brendlar)
POST /api/supply/sup/confirm             guruhlarni tasdiqlash (ids — bo'lmasa hammasi)
POST /api/supply/sup/{sid}/group         ta'minotchini boshqa / yangi guruhga o'tkazish
POST /api/supply/sup/group/{gid}/rename  guruh nomi (mavjud nom — guruhlar birlashadi)
GET  /api/supply/sup/rules               qoidalar: brend + kategoriya → ta'minotchi; bir nechta ta'minotchili tovarlar
POST /api/supply/sup/rule                qo'lda qoida (supplier_id = null — o'chirish, avtomatikka qaytadi)
POST /api/supply/sup/product             tovar istisnosi (supplier_id = null — o'chirish)
POST /api/supply/sup/export              qoidalar Excel'i botga (tuzatib qayta yuklasa — qabul qilinadi)
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import suppliers as S
from api_auth import User, allowed_categories, current_user

router = APIRouter(prefix="/api/supply/sup")


def _M():
    import api_main
    return api_main


def _need_edit(user: User) -> None:
    if not (user.can_upload or user.is_admin):
        raise HTTPException(403, "O'zgartirish huquqi yo'q")


async def _cats(con, user: User) -> list[int]:
    allowed = await allowed_categories(user)
    ids = [r[0] for r in await con.fetch("SELECT id FROM categories")]
    return [c for c in ids if allowed is None or c in allowed]


# ---------------------------------------------------------------------------
# Guruhlar
async def groups_data(con, user: User) -> dict:
    cats = await _cats(con, user)
    span = await con.fetchrow("SELECT min(rec_date) d1, max(rec_date) d2 FROM wh_receipts")
    rows = await con.fetch(
        """WITH k AS (SELECT r.supplier, p.brand, p.category_id, r.product_id, r.qty, coalesce(r.value_usd, 0) u
                      FROM wh_receipts r JOIN products p USING (product_id) WHERE p.category_id = ANY($1::int[])),
                b AS (SELECT supplier, brand, sum(u) u FROM k GROUP BY 1, 2),
                t AS (SELECT supplier, sum(u)::float8 usd, sum(qty)::float8 qty, count(DISTINCT product_id) skus,
                             count(DISTINCT category_id) ncat FROM k GROUP BY 1)
           SELECT s.id, s.name, s.group_id, s.confirmed, coalesce(t.usd, 0) usd, coalesce(t.qty, 0) qty,
                  coalesce(t.skus, 0) skus, coalesce(t.ncat, 0) ncat,
                  (SELECT array_agg(brand ORDER BY u DESC) FROM (SELECT brand, u FROM b WHERE b.supplier = s.name
                                                                 ORDER BY u DESC LIMIT 5) bb) brands
           FROM suppliers s LEFT JOIN t ON t.supplier = s.name ORDER BY usd DESC, s.name""", cats)
    gnames = {r["id"]: r["name"] for r in await con.fetch("SELECT id, name FROM sup_groups")}
    groups: dict = {}
    sups = []
    for r in rows:
        gid = r["group_id"] or 0
        g = groups.setdefault(gid, {"id": gid, "name": gnames.get(gid, "Guruhsiz"), "usd": 0.0, "qty": 0.0,
                                    "skus": 0, "sups": [], "new": 0})
        g["usd"] += r["usd"]
        g["qty"] += r["qty"]
        g["skus"] += r["skus"]
        g["sups"].append(r["id"])
        g["new"] += 0 if r["confirmed"] else 1
        sups.append({"id": r["id"], "name": r["name"], "group_id": gid, "confirmed": r["confirmed"], "usd": r["usd"],
                     "qty": r["qty"], "skus": r["skus"], "ncat": r["ncat"], "brands": [b for b in (r["brands"] or []) if b]})
    return {"groups": sorted(groups.values(), key=lambda g: (g["id"] != 0, -g["usd"])),
            "sups": sups, "unconfirmed": sum(1 for s in sups if not s["confirmed"]),
            "all_groups": [{"id": k, "name": v} for k, v in sorted(gnames.items(), key=lambda x: x[1].lower())],
            "span": [span["d1"], span["d2"]] if span and span["d1"] else None,
            "can_edit": bool(user.can_upload or user.is_admin)}


@router.get("/groups")
async def groups(request: Request, user: User = Depends(current_user)):
    async with request.app.state.pool.acquire() as con:
        await S.sync(con)
        return await groups_data(con, user)


class ConfirmReq(BaseModel):
    ids: list[int] = []


@router.post("/confirm")
async def confirm(req: ConfirmReq, request: Request, user: User = Depends(current_user)):
    _need_edit(user)
    async with request.app.state.pool.acquire() as con:
        if req.ids:
            await con.execute("UPDATE suppliers SET confirmed = true WHERE id = ANY($1::int[]) AND NOT confirmed", req.ids)
        else:
            await con.execute("UPDATE suppliers SET confirmed = true WHERE NOT confirmed")
        return await groups_data(con, user)


class MoveReq(BaseModel):
    group_id: int | None = None
    new_name: str | None = Field(None, max_length=80)


async def _drop_empty(con) -> None:
    await con.execute("DELETE FROM sup_groups g WHERE NOT EXISTS (SELECT 1 FROM suppliers s WHERE s.group_id = g.id)")


@router.post("/{sid}/group")
async def move(sid: int, req: MoveReq, request: Request, user: User = Depends(current_user)):
    _need_edit(user)
    async with request.app.state.pool.acquire() as con:
        async with con.transaction():
            gid = req.group_id
            name = (req.new_name or "").strip()
            if name:
                gid = await con.fetchval("SELECT id FROM sup_groups WHERE lower(name) = lower($1)", name) or \
                    await con.fetchval("INSERT INTO sup_groups(name) VALUES ($1) RETURNING id", name)
            if gid is None or not await con.fetchval("SELECT 1 FROM sup_groups WHERE id = $1", gid):
                raise HTTPException(422, "Guruh topilmadi")
            r = await con.execute("UPDATE suppliers SET group_id = $2, confirmed = true WHERE id = $1", sid, gid)
            if r.endswith(" 0"):
                raise HTTPException(404, "Ta'minotchi topilmadi")
            await _drop_empty(con)
            await S.refresh(con)
        return await groups_data(con, user)


class RenameReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


@router.post("/group/{gid}/rename")
async def rename(gid: int, req: RenameReq, request: Request, user: User = Depends(current_user)):
    _need_edit(user)
    name = req.name.strip()
    if not name:
        raise HTTPException(422, "Nom bo'sh")
    async with request.app.state.pool.acquire() as con:
        async with con.transaction():
            other = await con.fetchval("SELECT id FROM sup_groups WHERE lower(name) = lower($1) AND id <> $2", name, gid)
            if other:                     # shu nomli guruh bor — birlashtiriladi
                await con.execute("UPDATE suppliers SET group_id = $2 WHERE group_id = $1", gid, other)
                await _drop_empty(con)
            else:
                r = await con.execute("UPDATE sup_groups SET name = $2 WHERE id = $1", gid, name)
                if r.endswith(" 0"):
                    raise HTTPException(404, "Guruh topilmadi")
            await S.refresh(con)
        return await groups_data(con, user)


# ---------------------------------------------------------------------------
# Qoidalar
async def rules_data(con, user: User) -> dict:
    cats = await _cats(con, user)
    last = await con.fetchval("SELECT max(sale_date) FROM sales_daily") or date.today()
    combos = await con.fetch(f"""
WITH {S._K},
{S._AUTO},
sd AS (SELECT DISTINCT product_id FROM sales_daily WHERE sale_date > $2::date - 90),
act AS (SELECT p.product_id, p.category_id, COALESCE(p.brand, '—') brand FROM products p
        WHERE p.category_id = ANY($1::int[]) AND (
              p.product_id IN (SELECT product_id FROM sd) OR p.product_id IN (SELECT product_id FROM k)
              OR EXISTS (SELECT 1 FROM sup_rules r WHERE r.category_id = p.category_id AND r.brand = COALESCE(p.brand, '—')))),
cmb AS (SELECT category_id, brand, count(*) skus FROM act GROUP BY 1, 2)
SELECT cmb.category_id cid, c.name cname, cmb.brand, cmb.skus,
       (SELECT array_agg(ARRAY[sid::float8, u::float8, q::float8] ORDER BY u DESC, q DESC) FROM cb
        WHERE cb.category_id = cmb.category_id AND cb.brand = cmb.brand) auto,
       au.sid asid, au.src asrc, r.supplier_id manual
FROM cmb JOIN categories c ON c.id = cmb.category_id
LEFT JOIN auto au ON au.category_id = cmb.category_id AND au.brand = cmb.brand
LEFT JOIN sup_rules r ON r.category_id = cmb.category_id AND r.brand = cmb.brand""", cats, last)
    rules = []
    for r in combos:
        auto = [[int(a[0]), a[1], a[2]] for a in (r["auto"] or [])]
        # m — qo'lda, k — kirimdan (bitta), d — kirimdan (bir nechta, eng kattasi), b — brenddan, n — aniqlanmadi
        st = "m" if r["manual"] else "k" if len(auto) == 1 else "d" if auto else "b" if r["asid"] else "n"
        rules.append({"cid": r["cid"], "cname": r["cname"], "brand": r["brand"], "skus": r["skus"], "auto": auto,
                      "manual": r["manual"], "eff": r["manual"] or r["asid"], "st": st,
                      "usd": sum(a[1] for a in auto)})
    rules.sort(key=lambda x: (-x["usd"], x["brand"], x["cname"]))
    multi = await con.fetch(f"""
WITH {S._K},
m AS (SELECT product_id FROM k GROUP BY 1 HAVING count(*) > 1
      UNION SELECT product_id FROM sup_product)
SELECT p.product_id pid, p.name, p.category_id cid, c.name cname, COALESCE(p.brand, '—') brand,
       (SELECT array_agg(ARRAY[sid::float8, q::float8, u::float8] ORDER BY q DESC, u DESC) FROM k
        WHERE k.product_id = p.product_id) ks,
       x.supplier_id ovr, ps.supplier_id eff, ps.src
FROM m JOIN products p USING (product_id) JOIN categories c ON c.id = p.category_id
LEFT JOIN sup_product x ON x.product_id = p.product_id
LEFT JOIN product_supplier ps ON ps.product_id = p.product_id
WHERE p.category_id = ANY($1::int[])""", cats)
    multi_l = [{"pid": r["pid"], "name": r["name"], "cid": r["cid"], "cname": r["cname"], "brand": r["brand"],
                "ks": [[int(a[0]), a[1], a[2]] for a in (r["ks"] or [])], "ovr": r["ovr"], "eff": r["eff"],
                "src": r["src"]} for r in multi]
    multi_l.sort(key=lambda x: -sum(a[2] for a in x["ks"]))
    sups = {r["id"]: [r["name"], r["group_id"]] for r in await con.fetch("SELECT id, name, group_id FROM suppliers")}
    gnames = {r["id"]: r["name"] for r in await con.fetch("SELECT id, name FROM sup_groups")}
    return {"rules": rules, "multi": multi_l, "sups": sups, "groups": gnames,
            "can_edit": bool(user.can_upload or user.is_admin)}


@router.get("/rules")
async def rules(request: Request, user: User = Depends(current_user)):
    async def run():
        async with request.app.state.pool.acquire() as con:
            return await rules_data(con, user)
    return await _M().cached(request, user, "sup_rules", {"e": bool(user.can_upload or user.is_admin)}, run)


class RuleReq(BaseModel):
    category_id: int
    brand: str
    supplier_id: int | None = None


async def _check_cat(user: User, cid: int) -> None:
    allowed = await allowed_categories(user)
    if allowed is not None and cid not in allowed:
        raise HTTPException(403, "Bu kategoriya sizga ochiq emas")


@router.post("/rule")
async def set_rule(req: RuleReq, request: Request, user: User = Depends(current_user)):
    _need_edit(user)
    await _check_cat(user, req.category_id)
    async with request.app.state.pool.acquire() as con:
        async with con.transaction():
            if req.supplier_id is None:
                await con.execute("DELETE FROM sup_rules WHERE category_id = $1 AND brand = $2", req.category_id, req.brand)
            else:
                if not await con.fetchval("SELECT 1 FROM suppliers WHERE id = $1", req.supplier_id):
                    raise HTTPException(422, "Ta'minotchi topilmadi")
                await con.execute(
                    """INSERT INTO sup_rules(category_id, brand, supplier_id, updated_by) VALUES ($1, $2, $3, $4)
                       ON CONFLICT (category_id, brand) DO UPDATE SET supplier_id = EXCLUDED.supplier_id,
                       updated_by = EXCLUDED.updated_by, updated_at = now()""",
                    req.category_id, req.brand, req.supplier_id, user.id)
            await S.refresh(con)
        return await rules_data(con, user)


class ProdReq(BaseModel):
    product_id: int
    supplier_id: int | None = None


@router.post("/product")
async def set_product(req: ProdReq, request: Request, user: User = Depends(current_user)):
    _need_edit(user)
    async with request.app.state.pool.acquire() as con:
        cid = await con.fetchval("SELECT category_id FROM products WHERE product_id = $1", req.product_id)
        if cid is None:
            raise HTTPException(404, "Tovar topilmadi")
        await _check_cat(user, cid)
        async with con.transaction():
            if req.supplier_id is None:
                await con.execute("DELETE FROM sup_product WHERE product_id = $1", req.product_id)
            else:
                if not await con.fetchval("SELECT 1 FROM suppliers WHERE id = $1", req.supplier_id):
                    raise HTTPException(422, "Ta'minotchi topilmadi")
                await con.execute(
                    """INSERT INTO sup_product(product_id, supplier_id, updated_by) VALUES ($1, $2, $3)
                       ON CONFLICT (product_id) DO UPDATE SET supplier_id = EXCLUDED.supplier_id,
                       updated_by = EXCLUDED.updated_by, updated_at = now()""", req.product_id, req.supplier_id, user.id)
            await S.refresh(con)
        return await rules_data(con, user)


# ---------------------------------------------------------------------------
# Excel: qoidalar (+ guruhlar, tovarlar) — tuzatib botga qayta yuklanadi (loaders_suprules)
SRC_NAMES = {"m": "qo'lda", "k": "kirimdan", "d": "kirimdan (bir nechta)", "b": "brenddan (taxmin)", "n": "aniqlanmadi"}


def rules_xlsx(rd: dict, gd: dict) -> bytes:
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    B, H, E = Font(name="Arial", bold=True), PatternFill("solid", fgColor="DDE7F5"), PatternFill("solid", fgColor="FFF2CC")
    F = Font(name="Arial")
    sn = lambda i: rd["sups"].get(i, [""])[0] if i else ""  # noqa: E731
    wb = Workbook()

    def sheet(ws, head, rows, edit_col, widths, money=()):
        ws.append(head)
        for c in ws[1]:
            c.font, c.fill = B, H
        ws.cell(1, edit_col).fill = E
        for r in rows:
            ws.append(r)
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.font = F
                if c.column in money:
                    c.number_format = "#,##0"
            row[edit_col - 1].fill = E
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = ws.dimensions

    ws = wb.active
    ws.title = "Qoidalar"
    sheet(ws, ["Kategoriya ID", "Kategoriya", "Brend", "Ta'minotchi", "Manba", "Kirim, $", "SKU", "Kirimdagi ta'minotchilar"],
          [[r["cid"], r["cname"], r["brand"], sn(r["eff"]), SRC_NAMES[r["st"]], round(r["usd"]), r["skus"],
            "; ".join(f"{sn(a[0])} ({a[1] / r['usd'] * 100:.0f}%)" if r["usd"] else sn(a[0]) for a in r["auto"])]
           for r in sorted(rd["rules"], key=lambda r: (r["brand"].lower(), r["cname"]))],
          4, [12, 30, 16, 32, 20, 12, 7, 60], money=(6,))
    sheet(wb.create_sheet("Guruhlar"), ["Ta'minotchi", "Guruh", "Kirim, $", "Brendlar"],
          [[s["name"], next((g["name"] for g in gd["groups"] if g["id"] == s["group_id"]), ""), round(s["usd"]),
            ", ".join(s["brands"])] for s in gd["sups"]], 2, [36, 20, 12, 40], money=(3,))
    sheet(wb.create_sheet("Tovarlar"), ["Товар Ид", "Tovar", "Kategoriya", "Brend", "Ta'minotchi", "Kirimdagi ta'minotchilar"],
          [[m["pid"], m["name"], m["cname"], m["brand"], sn(m["ovr"] or m["eff"]),
            "; ".join(f"{sn(a[0])} {a[1]:.0f} dona" for a in m["ks"])] for m in rd["multi"]],
          5, [12, 44, 26, 14, 32, 60])
    wi = wb.create_sheet("Izoh")
    for r in [["Qanday", "Sariq ustunni tuzating va faylni botga qayta yuboring — bot o'zi taniydi."],
              ["Qoidalar", "Brend + kategoriya → ta'minotchi. Bo'sh qoldirilsa — qo'lda qoida o'chadi, kirim tarixidan avtomatik olinadi."],
              ["Guruhlar", "Ta'minotchining guruhi. Yangi nom yozilsa — yangi guruh ochiladi."],
              ["Tovarlar", "Bir nechta ta'minotchidan kelgan tovarning asosiysi (istisno). Bo'sh — istisno o'chadi."],
              ["Ustuvorlik", "tovar istisnosi → qoida (qo'lda) → tovarning o'z kirim tarixi → kirimdan avtomatik qoida"],
              ["Ta'minotchi", "nomi aynan ro'yxatdagidek bo'lsin (Guruhlar varag'idagi «Ta'minotchi» ustuni)"]]:
        wi.append(r)
    wi.column_dimensions["A"].width, wi.column_dimensions["B"].width = 14, 110
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@router.post("/export")
async def export(request: Request, user: User = Depends(current_user)):
    import config
    from datetime import datetime, timezone
    from aiogram import Bot
    from aiogram.types import BufferedInputFile
    async with request.app.state.pool.acquire() as con:
        rd = await rules_data(con, user)
        gd = await groups_data(con, user)
    if not config.BOT_TOKEN:
        raise HTTPException(503, "Bot sozlanmagan")
    fname = f"taminotchi_qoidalar_{datetime.now(timezone(timedelta(hours=5))):%d.%m.%Y}.xlsx"
    bot = Bot(config.BOT_TOKEN)
    try:
        await bot.send_document(user.id, BufferedInputFile(rules_xlsx(rd, gd), filename=fname),
                                caption=f"🏭 Ta'minotchi qoidalari: {len(rd['rules'])} qoida, {len(gd['sups'])} ta'minotchi, "
                                        f"{len(rd['multi'])} tovar. Sariq ustunni tuzatib, faylni shu botga qayta yuboring.")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"Botga yuborib bo'lmadi — botga /start bosilganini tekshiring ({e})")
    finally:
        await bot.session.close()
    return {"ok": True}
