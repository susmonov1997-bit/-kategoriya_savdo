"""Ta'minot tabi API (021_receipts.sql): omborga kirim va signallar.

POST /api/supply/kirim              davr kirimi (Filters: davr, solishtirish, doira, brend/xususiyat, joy → ombor)
GET  /api/supply/signals            signallar (status=all|new|seen)
POST /api/supply/signals/{id}/seen  «✓ Ko'rildi» (+ izoh)
"""
from __future__ import annotations

import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api_auth import User, allowed_categories, current_user
from api_queries import compare_period
from api_schemas import Filters

router = APIRouter(prefix="/api/supply")


def _M():
    import api_main
    return api_main


async def _kirim(con, f: Filters, user: User) -> dict:
    from api_stock import _SQL
    cats, attrs, _ = await _M().scope_ctx(con, f, user)
    last = await con.fetchval("SELECT max(rec_date) FROM wh_receipts")
    pend = await con.fetchrow("SELECT count(DISTINCT prod_key) n, coalesce(sum(qty), 0)::float8 q FROM receipt_pending "
                              "WHERE product_id IS NULL")
    out = {"has": last is not None, "last": last, "from": f.date_from, "to": f.date_to,
           "pending": {"skus": pend["n"], "qty": pend["q"]}}
    if last is None:
        return out
    cmp = compare_period(f)
    out["cmp"] = list(cmp) if cmp else None
    locf = bool(f.region_ids or f.cluster_ids or f.branch_ids)

    async def fetch(d1, d2, by_order: bool):
        q = _SQL(f, cats, attrs)
        w_ = q.ctes()
        A, B = q.p(d1), q.p(d2)
        wcond = "AND r.warehouse_id IN (SELECT warehouse_id FROM bb WHERE warehouse_id IS NOT NULL)" if locf else ""
        oc = ", r.order_code" if by_order else ""
        # m: 1 — shu ombor filiallaridan birortasining matritsasida bor, 0 — nematritsa, 2 — kategoriyasi matritsada yo'q
        return await con.fetch(f"""WITH {w_},
mc AS (SELECT DISTINCT category_id FROM matrix_rows WHERE category_id IS NOT NULL),
wmx AS (SELECT DISTINCT b.warehouse_id, m.product_id FROM matrix_rows m JOIN branches b ON b.id = m.branch_id
        WHERE m.product_id IS NOT NULL AND b.warehouse_id IS NOT NULL)
SELECT r.product_id, r.warehouse_id, r.supplier{oc}, sum(r.qty)::float8 qty, coalesce(sum(r.value_usd), 0)::float8 usd,
       CASE WHEN EXISTS (SELECT 1 FROM wmx WHERE wmx.warehouse_id = r.warehouse_id AND wmx.product_id = r.product_id) THEN 1
            WHEN max(p.category_id) IN (SELECT category_id FROM mc) THEN 0 ELSE 2 END m
FROM wh_receipts r JOIN pd p USING (product_id) WHERE r.rec_date BETWEEN {A} AND {B} {wcond}
GROUP BY 1, 2, 3{', 4' if by_order else ''}""", *q.params)

    async def daily(d1, d2):
        q = _SQL(f, cats, attrs)
        w_ = q.ctes()
        A, B = q.p(d1), q.p(d2)
        wcond = "AND r.warehouse_id IN (SELECT warehouse_id FROM bb WHERE warehouse_id IS NOT NULL)" if locf else ""
        return await con.fetch(f"""WITH {w_}
SELECT r.rec_date d, sum(r.qty)::float8 qty, coalesce(sum(r.value_usd), 0)::float8 usd
FROM wh_receipts r WHERE r.rec_date BETWEEN {A} AND {B} AND r.product_id IN (SELECT product_id FROM pd) {wcond}
GROUP BY 1 ORDER BY 1""", *q.params)

    cur = await fetch(f.date_from, f.date_to, True)
    prev = await fetch(cmp[0], cmp[1], False) if cmp else []
    dd = {r["d"]: r for r in await daily(f.date_from, f.date_to)}
    sups, orders = {}, {}
    si = lambda s: sups.setdefault(s, len(sups))  # noqa: E731
    oi = lambda s: orders.setdefault(s, len(orders))  # noqa: E731
    out["rows"] = [[r["product_id"], r["warehouse_id"], si(r["supplier"]), oi(r["order_code"]), r["qty"], r["usd"], r["m"]]
                   for r in cur]
    out["prev"] = [[r["product_id"], r["warehouse_id"], si(r["supplier"]), r["qty"], r["usd"], r["m"]] for r in prev]
    n = (f.date_to - f.date_from).days + 1
    out["daily"] = [[(f.date_from + timedelta(days=i)).isoformat(),
                     dd[f.date_from + timedelta(days=i)]["usd"] if f.date_from + timedelta(days=i) in dd else 0,
                     dd[f.date_from + timedelta(days=i)]["qty"] if f.date_from + timedelta(days=i) in dd else 0]
                    for i in range(n)] if n <= 400 else []
    out["sups"] = list(sups)
    out["orders"] = list(orders)
    pids = list({r[0] for r in out["rows"]} | {r[0] for r in out["prev"]})
    out["prods"] = {str(r["product_id"]): [r["name"], r["category_id"], r["cname"], r["brand"]] for r in await con.fetch(
        """SELECT p.product_id, p.name, p.category_id, c.name cname, p.brand FROM products p
           JOIN categories c ON c.id = p.category_id WHERE p.product_id = ANY($1::bigint[])""", pids)}
    out["whs"] = {str(r["id"]): r["name"] for r in await con.fetch("SELECT id, name FROM warehouses")}
    return out


@router.post("/kirim")
async def kirim(f: Filters, request: Request, user: User = Depends(current_user)):
    async def run():
        async with request.app.state.pool.acquire() as con:
            return await _kirim(con, f, user)
    return await _M().cached(request, user, "supply_kirim",
                             f.model_dump(mode="json", exclude={"share_by", "cur"}), run)


async def _signals(con, f: Filters, user: User, status: str = "all") -> dict:
    """Signallar tepadagi filtrlar bilan: doira (kategoriyalar, ruxsat), brend/status/xususiyat/SKU/mas'ul,
    joy filtri (hudud/klaster/filial) — shu filiallar biriktirilgan omborlar."""
    from api_stock import _SQL
    cats, attrs, _ = await _M().scope_ctx(con, f, user)
    q = _SQL(f, cats, attrs)
    w_ = q.ctes()
    locf = bool(f.region_ids or f.cluster_ids or f.branch_ids)
    wcond = "AND s.warehouse_id IN (SELECT warehouse_id FROM bb WHERE warehouse_id IS NOT NULL)" if locf else ""
    rows = await con.fetch(f"""WITH {w_}
SELECT s.id, s.kind, s.data, s.created_at, s.seen_at, s.note, s.product_id, p.name pname, p.brand,
       c.name cname, p.category_id, w.name wname
FROM supply_signals s JOIN products p ON p.product_id = s.product_id
JOIN categories c ON c.id = p.category_id LEFT JOIN warehouses w ON w.id = s.warehouse_id
WHERE s.product_id IN (SELECT product_id FROM pd) {wcond}
ORDER BY s.seen_at IS NOT NULL, s.created_at DESC, (s.data->>'usd')::float8 DESC NULLS LAST
LIMIT 5000""", *q.params)
    items = [{"id": r["id"], "kind": r["kind"], "pid": r["product_id"], "name": r["pname"], "brand": r["brand"],
              "cat": r["cname"], "wh": r["wname"], "created": r["created_at"], "seen": r["seen_at"], "note": r["note"],
              "data": r["data"] if isinstance(r["data"], dict) else json.loads(r["data"] or "{}")} for r in rows]
    cnt = {"all": len(items), "new": sum(1 for x in items if not x["seen"])}
    if status == "new":
        items = [x for x in items if not x["seen"]]
    elif status == "seen":
        items = [x for x in items if x["seen"]]
    return {"count": cnt, "items": items}


@router.post("/signals")
async def signals(f: Filters, request: Request, status: str = "all", user: User = Depends(current_user)):
    async with request.app.state.pool.acquire() as con:
        return await _signals(con, f, user, status)


class SeenReq(BaseModel):
    note: str | None = None
    unseen: bool = False


@router.post("/signals/{sid}/seen")
async def signal_seen(sid: int, req: SeenReq, request: Request, user: User = Depends(current_user)):
    async with request.app.state.pool.acquire() as con:
        r = await con.execute(
            "UPDATE supply_signals SET seen_at = CASE WHEN $4 THEN NULL ELSE now() END, seen_by = $2, "
            "note = COALESCE($3, note) WHERE id = $1", sid, user.id, (req.note or "").strip() or None, req.unseen)
    if r.endswith(" 0"):
        raise HTTPException(404, "Signal topilmadi")
    return {"ok": True}


class BulkSeenReq(BaseModel):
    ids: list[int]
    unseen: bool = False


@router.post("/signals/seen")
async def signals_seen(req: BulkSeenReq, request: Request, user: User = Depends(current_user)):
    """Bir nechta signal birdan «✓ Ko'rildi» (kategoriya / brend bo'yicha)."""
    if not req.ids:
        return {"ok": True, "n": 0}
    async with request.app.state.pool.acquire() as con:
        r = await con.execute(
            "UPDATE supply_signals SET seen_at = CASE WHEN $3 THEN NULL ELSE now() END, seen_by = $2 "
            "WHERE id = ANY($1::int[])", req.ids[:2000], user.id, req.unseen)
    return {"ok": True, "n": int(r.split()[-1])}


# ---------------------------------------------------------------------------
# Signallar Excel'i (botga yuboriladi)
def signals_xlsx(items: list[dict], status: str) -> bytes:
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    B, H = Font(name="Arial", bold=True), PatternFill("solid", fgColor="DDE7F5")
    F = Font(name="Arial")
    wb = Workbook()

    def sheet(ws, head, rows, money_cols=(), widths=None):
        ws.append(head)
        for c in ws[1]:
            c.font, c.fill = B, H
        for r in rows:
            ws.append(r)
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.font = F
                if c.column in money_cols:
                    c.number_format = "#,##0"
        for i, w in enumerate(widths or [], 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = ws.dimensions

    # 1. Kategoriya bo'yicha jami
    agg: dict[str, list] = {}
    for x in items:
        d = x["data"]
        a = agg.setdefault(x["cat"] or "—", [x["cat"] or "—", set(), set(), 0, 0.0, 0.0])
        a[1].add(x["brand"] or "—")
        a[2].add(x["pid"])
        a[3] += 1
        a[4] += float(d.get("qty") or 0)
        a[5] += float(d.get("usd") or 0)
    ws = wb.active
    ws.title = "Kategoriya"
    sheet(ws, ["Kategoriya", "Brendlar", "Tovarlar", "Signallar", "Dona", "Summa, $"],
          [[a[0], len(a[1]), len(a[2]), a[3], a[4], round(a[5])] for a in sorted(agg.values(), key=lambda a: -a[5])],
          money_cols=(5, 6), widths=[34, 10, 10, 10, 10, 14])
    # 2. Batafsil: kategoriya → brend → tovar → ombor
    rows = []
    for x in sorted(items, key=lambda x: (x["cat"] or "", x["brand"] or "", x["name"] or "", -(x["data"].get("usd") or 0))):
        d = x["data"]
        rows.append([x["cat"], x["brand"], x["pid"], x["name"], x["wh"], d.get("qty"), round(float(d.get("usd") or 0)),
                     str(d.get("date") or "")[:10], d.get("supplier"), d.get("order"),
                     "ko'rilgan" if x["seen"] else "yangi", x["note"]])
    sheet(wb.create_sheet("Signallar"),
          ["Kategoriya", "Brend", "Товар Ид", "Tovar", "Ombor", "Dona", "Summa, $", "Oxirgi kirim", "Ta'minotchi",
           "Zakazlar", "Holat", "Izoh"], rows, money_cols=(6, 7), widths=[28, 14, 11, 44, 20, 8, 12, 12, 24, 36, 11, 28])
    wi = wb.create_sheet("Izoh")
    for r in [["Signal", "Matritsada yo'q tovar kirimi — tovar kategoriyasi matritsada bor, lekin shu ombor filiallarining birortasi matritsasida bu tovar yo'q."],
              ["Birlik", "bitta signal = ombor × tovar × oy (oy ichida qayta kelsa — dona va summa yangilanadi)"],
              ["Ro'yxat", {"new": "faqat yangi (ko'rilmagan) signallar", "seen": "faqat ko'rilgan signallar"}.get(status, "barcha signallar")]]:
        wi.append(r)
    wi.column_dimensions["A"].width, wi.column_dimensions["B"].width = 12, 110
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@router.post("/signals/export")
async def signals_export(f: Filters, request: Request, status: str = "new", user: User = Depends(current_user)):
    import config
    from datetime import datetime, timezone
    from aiogram import Bot
    from aiogram.types import BufferedInputFile
    async with request.app.state.pool.acquire() as con:
        d = await _signals(con, f, user, status)
    if not d["items"]:
        raise HTTPException(404, "Signal yo'q")
    if not config.BOT_TOKEN:
        raise HTTPException(503, "Bot sozlanmagan")
    fname = f"signallar_{datetime.now(timezone(timedelta(hours=5))):%d.%m.%Y}.xlsx"
    bot = Bot(config.BOT_TOKEN)
    try:
        await bot.send_document(user.id, BufferedInputFile(signals_xlsx(d["items"], status), filename=fname),
                                caption=f"⚠️ Ta'minot signallari ({len(d['items'])}): kategoriya bo'yicha jami + batafsil "
                                        "(kategoriya → brend → tovar → ombor)")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"Botga yuborib bo'lmadi — botga /start bosilganini tekshiring ({e})")
    finally:
        await bot.session.close()
    return {"ok": True}
