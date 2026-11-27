"""Ta'minot → Zakaz va Yo'lda (023_shipments.sql).

POST /api/supply/ship/orders   zakaz: ta'minotchida «в работе» (hali jo'natilmagan)
POST /api/supply/ship/transit  yo'lda: jo'natilgan − shu omborga shu ta'minotchi (guruhi)dan kelgan kirim (FIFO)

Yo'lda hisobi (ombor × tovar): jo'natmalar sanasi bo'yicha tartiblanadi; har biriga o'z sanasidan keyingi kirim
birinchi kelgan birinchi yopiladi. Yopilmagan qoldiq — yo'lda. Odatdagi muddat — shu omborda to'liq yopilgan
jo'natmalarning mediana kuni (yetarli bo'lmasa 4 kun). Kechikmoqda — yo'lda kun > odatdagi + 2.
Filialning o'ziga (direct) jo'natmalar kirim faylida ko'rinmaydi — oxirgi 14 kundagilari alohida ko'rsatiladi.
Davr filtri ishlatilmaydi: bu — hozirgi holat (oxirgi 60 kun jo'natmalari).
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request

from api_auth import User, current_user
from api_schemas import Filters

router = APIRouter(prefix="/api/supply/ship")
WINDOW = 60
LATE_EXTRA = 2
DEFAULT_LEAD = 4
DIRECT_DAYS = 14


def _M():
    import api_main
    return api_main


def _today() -> date:
    return datetime.now(timezone(timedelta(hours=5))).date()


async def _base(con, f: Filters, user: User):
    from api_stock import _SQL
    cats, attrs, _ = await _M().scope_ctx(con, f, user)
    q = _SQL(f, cats, attrs, sup=False)
    w_ = q.ctes()
    locf = bool(f.region_ids or f.cluster_ids or f.branch_ids)
    cond = "AND s.warehouse_id IN (SELECT warehouse_id FROM bb WHERE warehouse_id IS NOT NULL)" if locf else ""
    cond += " " + q.sup_cond("s.supplier")
    return q, w_, cond, locf


async def _prods(con, pids) -> dict:
    return {str(r["product_id"]): [r["name"], r["cname"], r["brand"]] for r in await con.fetch(
        """SELECT p.product_id, p.name, c.name cname, p.brand FROM products p JOIN categories c ON c.id = p.category_id
           WHERE p.product_id = ANY($1::bigint[])""", list(pids))}


async def _unbound(con, f: Filters) -> dict:
    """Bog'lanmagan Artel kodlari (tovar filtri bo'lmasa ko'rsatiladi)."""
    r = await con.fetchrow("""SELECT count(DISTINCT material) n, coalesce(sum(value_usd), 0)::float8 usd FROM sup_ship
                              WHERE product_id IS NULL AND doc_date > $1::date - $2::int""", _today(), WINDOW)
    return {"n": r["n"], "usd": r["usd"]}


async def _orders(con, f: Filters, user: User) -> dict:
    q, w_, cond, locf = await _base(con, f, user)
    T, Wn = q.p(_today()), q.p(WINDOW)
    rows = await con.fetch(f"""WITH {w_},
mc AS (SELECT DISTINCT category_id FROM matrix_rows WHERE category_id IS NOT NULL),
wmx AS (SELECT DISTINCT b.warehouse_id, m.product_id FROM matrix_rows m JOIN branches b ON b.id = m.branch_id
        WHERE m.product_id IS NOT NULL AND b.warehouse_id IS NOT NULL)
SELECT s.doc_no, s.doc_date, s.warehouse_id, s.direct, s.dest_key, s.base, s.product_id,
       sum(s.qty)::float8 qty, coalesce(sum(s.value_usd), 0)::float8 usd,
       CASE WHEN s.warehouse_id IS NULL THEN 2
            WHEN EXISTS (SELECT 1 FROM wmx WHERE wmx.warehouse_id = s.warehouse_id AND wmx.product_id = s.product_id) THEN 1
            WHEN max(p.category_id) IN (SELECT category_id FROM mc) THEN 0 ELSE 2 END m
FROM sup_ship s JOIN pd p USING (product_id)
WHERE s.status = 'work' AND s.doc_date > {T}::date - {Wn}::int {cond}
GROUP BY 1, 2, 3, 4, 5, 6, 7""", *q.params)
    out = {"has": bool(await con.fetchval("SELECT 1 FROM sup_ship LIMIT 1")),
           "last": await con.fetchval("SELECT max(doc_date) FROM sup_ship"), "today": _today(),
           "rows": [[r["doc_no"], r["doc_date"].isoformat(), r["warehouse_id"], 1 if r["direct"] else 0, r["dest_key"],
                     r["base"], r["product_id"], r["qty"], r["usd"], r["m"]] for r in rows]}
    out["prods"] = await _prods(con, {r["product_id"] for r in rows})
    out["whs"] = {str(r["id"]): r["name"] for r in await con.fetch("SELECT id, name FROM warehouses")}
    out["unbound"] = await _unbound(con, f)
    return out


async def _transit(con, f: Filters, user: User) -> dict:
    q, w_, cond, locf = await _base(con, f, user)
    T, Wn = q.p(_today()), q.p(WINDOW)
    ships = await con.fetch(f"""WITH {w_}
SELECT s.doc_no, s.doc_date, s.warehouse_id, s.direct, s.dest_key, s.base, s.product_id, s.supplier,
       sum(s.qty)::float8 qty, coalesce(sum(s.value_usd), 0)::float8 usd
FROM sup_ship s WHERE s.status <> 'work' AND s.doc_date > {T}::date - {Wn}::int
  AND s.product_id IN (SELECT product_id FROM pd) {cond}
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8 ORDER BY s.doc_date, s.doc_no""", *q.params)
    today = _today()
    out = {"has": bool(await con.fetchval("SELECT 1 FROM sup_ship LIMIT 1")),
           "last": await con.fetchval("SELECT max(doc_date) FROM sup_ship"), "today": today,
           "kirim_last": await con.fetchval("SELECT max(rec_date) FROM wh_receipts"),
           "docs": [], "lead": {}, "direct": []}
    wh_ships = [r for r in ships if r["warehouse_id"] is not None]
    if wh_ships:
        sups = sorted({r["supplier"] for r in wh_ships})
        # shu ta'minotchi guruhining barcha ta'minotchilari kirimi (КБТ / МБТ — bitta guruh)
        names = [r[0] for r in await con.fetch(
            """SELECT s2.name FROM suppliers s1 JOIN suppliers s2 ON s2.group_id = s1.group_id
               WHERE s1.name = ANY($1::text[])""", sups)] or sups
        d0 = min(r["doc_date"] for r in wh_ships)
        recs = await con.fetch(
            """SELECT warehouse_id, product_id, rec_date, sum(qty)::float8 qty FROM wh_receipts
               WHERE supplier = ANY($1::text[]) AND rec_date >= $2 AND product_id = ANY($3::bigint[])
               GROUP BY 1, 2, 3 ORDER BY 3""", names, d0, list({r["product_id"] for r in wh_ships}))
        R = defaultdict(list)
        for r in recs:
            R[(r["warehouse_id"], r["product_id"])].append([r["rec_date"], r["qty"]])
        S = defaultdict(list)
        for r in wh_ships:
            S[(r["warehouse_id"], r["product_id"])].append(r)
        lead_days = defaultdict(list)
        rem = []                     # (ship row, received, remaining)
        for k, lst in S.items():
            rl = R.get(k, [])
            for s in lst:            # sana bo'yicha
                need, got, done_at = s["qty"], 0.0, None
                for x in rl:
                    if need <= 1e-9:
                        break
                    if x[0] < s["doc_date"] or x[1] <= 0:
                        continue
                    t = min(need, x[1])
                    x[1] -= t
                    need -= t
                    got += t
                    done_at = x[0]
                if need <= 1e-9 and done_at:
                    lead_days[k[0]].append((done_at - s["doc_date"]).days)
                rem.append((s, got, max(need, 0.0)))
        lead = {w: (round(statistics.median(v), 1) if len(v) >= 3 else None) for w, v in lead_days.items()}
        out["lead"] = {str(w): v for w, v in lead.items()}
        docs = defaultdict(lambda: {"items": [], "qty": 0.0, "got": 0.0, "rem": 0.0, "usd": 0.0, "rem_usd": 0.0})
        for s, got, need in rem:
            d = docs[(s["doc_no"], s["warehouse_id"])]
            d.update(doc=s["doc_no"], date=s["doc_date"], wid=s["warehouse_id"], base=s["base"])
            pu = s["usd"] / s["qty"] if s["qty"] else 0
            d["qty"] += s["qty"]
            d["got"] += got
            d["rem"] += need
            d["usd"] += s["usd"]
            d["rem_usd"] += need * pu
            if need > 1e-9:
                d["items"].append([s["product_id"], s["qty"], got, need, need * pu])
        for d in docs.values():
            if d["rem"] <= 1e-9:
                continue
            age = (today - d["date"]).days
            ld = lead.get(d["wid"]) or DEFAULT_LEAD
            out["docs"].append({"doc": d["doc"], "date": d["date"], "wid": d["wid"], "base": d["base"], "age": age,
                                "late": age > ld + LATE_EXTRA, "qty": d["qty"], "got": d["got"], "rem": d["rem"],
                                "usd": d["usd"], "rem_usd": d["rem_usd"],
                                "items": sorted(d["items"], key=lambda x: -x[4])})
        out["docs"].sort(key=lambda d: (d["date"], d["doc"]))
    for r in ships:
        if r["direct"] and (today - r["doc_date"]).days <= DIRECT_DAYS:
            out["direct"].append([r["doc_no"], r["doc_date"].isoformat(), r["dest_key"], r["product_id"], r["qty"], r["usd"]])
    pids = {i[0] for d in out["docs"] for i in d["items"]} | {r[3] for r in out["direct"]}
    out["prods"] = await _prods(con, pids)
    out["whs"] = {str(r["id"]): r["name"] for r in await con.fetch("SELECT id, name FROM warehouses")}
    out["unbound"] = await _unbound(con, f)
    out["default_lead"], out["late_extra"] = DEFAULT_LEAD, LATE_EXTRA
    return out


@router.post("/orders")
async def orders(f: Filters, request: Request, user: User = Depends(current_user)):
    async def run():
        async with request.app.state.pool.acquire() as con:
            return await _orders(con, f, user)
    return await _M().cached(request, user, "ship_orders",
                             [_today(), f.model_dump(mode="json", exclude={"share_by", "cur", "date_from", "date_to", "compare"})], run)


@router.post("/transit")
async def transit(f: Filters, request: Request, user: User = Depends(current_user)):
    async def run():
        async with request.app.state.pool.acquire() as con:
            return await _transit(con, f, user)
    return await _M().cached(request, user, "ship_transit",
                             [_today(), f.model_dump(mode="json", exclude={"share_by", "cur", "date_from", "date_to", "compare"})], run)
