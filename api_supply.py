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
        return await con.fetch(f"""WITH {w_}
SELECT r.product_id, r.warehouse_id, r.supplier{oc}, sum(r.qty)::float8 qty, coalesce(sum(r.value_usd), 0)::float8 usd
FROM wh_receipts r WHERE r.rec_date BETWEEN {A} AND {B} AND r.product_id IN (SELECT product_id FROM pd) {wcond}
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
    out["rows"] = [[r["product_id"], r["warehouse_id"], si(r["supplier"]), oi(r["order_code"]), r["qty"], r["usd"]] for r in cur]
    out["prev"] = [[r["product_id"], r["warehouse_id"], si(r["supplier"]), r["qty"], r["usd"]] for r in prev]
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


@router.get("/signals")
async def signals(request: Request, status: str = "all", user: User = Depends(current_user)):
    allowed = await allowed_categories(user)
    cond = {"new": "s.seen_at IS NULL", "seen": "s.seen_at IS NOT NULL"}.get(status, "TRUE")
    async with request.app.state.pool.acquire() as con:
        rows = await con.fetch(
            f"""SELECT s.id, s.kind, s.data, s.created_at, s.seen_at, s.note, s.product_id, p.name pname, p.brand,
                       c.name cname, p.category_id, w.name wname
                FROM supply_signals s LEFT JOIN products p ON p.product_id = s.product_id
                LEFT JOIN categories c ON c.id = p.category_id LEFT JOIN warehouses w ON w.id = s.warehouse_id
                WHERE {cond} AND ($1::int[] IS NULL OR p.category_id = ANY($1::int[]))
                ORDER BY s.seen_at IS NOT NULL, s.created_at DESC, (s.data->>'usd')::float8 DESC NULLS LAST
                LIMIT 3000""", list(allowed) if allowed is not None else None)
        cnt = await con.fetchrow(
            """SELECT count(*) n, count(*) FILTER (WHERE s.seen_at IS NULL) new FROM supply_signals s
               LEFT JOIN products p ON p.product_id = s.product_id
               WHERE $1::int[] IS NULL OR p.category_id = ANY($1::int[])""",
            list(allowed) if allowed is not None else None)
    return {"count": {"all": cnt["n"], "new": cnt["new"]},
            "items": [{"id": r["id"], "kind": r["kind"], "pid": r["product_id"], "name": r["pname"], "brand": r["brand"],
                       "cat": r["cname"], "wh": r["wname"], "created": r["created_at"], "seen": r["seen_at"], "note": r["note"],
                       "data": r["data"] if isinstance(r["data"], dict) else json.loads(r["data"] or "{}")} for r in rows]}


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
