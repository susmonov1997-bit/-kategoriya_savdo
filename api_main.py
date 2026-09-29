"""FastAPI backend — Mini App uchun ma'lumot.

Ishga tushirish:  uvicorn api_main:app --host 0.0.0.0 --port 8000

Endpointlar (hammasi Authorization: tma <initData> talab qiladi):
  GET  /api/meta        kategoriyalar, xususiyatlar, diapazonlar, ma'lumot davri, drill-down tartibi
  POST /api/options     filtr qiymatlari (faceted: har bir filtr qolgan filtrlarga moslashadi)
  POST /api/summary     KPI kartalar: joriy davr, solishtirma davr, o'zgarish
  POST /api/daily       kunlik dinamika (joriy va solishtirma davr kunma-kun)
  POST /api/breakdown   qirqim jadvali: qatorlar o'lchovi (+ ixtiyoriy ustunlar o'lchovi), ulush, ulush o'zgarishi (pp)
  POST /api/share-daily kunlar bo'yicha ulush: tanlangan o'lchovning TOP-N qiymati + "Boshqalar"
  POST /api/attributes  xususiyatlar bloklari: har bir xususiyat va brend bo'yicha barcha qiymatlar, ulush va pp
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

import config
from db import create_pool, migrate
from api_auth import User, allowed_categories, current_user
from api_queries import (JOINS, Q, compare_period, delta, dimension, kpi_from, load_attrs, metrics_sql)
from api_schemas import BreakdownRequest, Filters, Kpi

log = logging.getLogger(__name__)

LOCATION_PATH = ["region", "cluster", "branch"]
MAX_PIVOT_COLS = 30


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool()
    await migrate(app.state.pool)
    yield
    await app.state.pool.close()


app = FastAPI(title="Savdo Mini App API", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(GZipMiddleware, minimum_size=1000)
if config.CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


async def _check_category(user: User, category_id: int) -> None:
    allowed = await allowed_categories(user)
    if allowed is not None and category_id not in allowed:
        raise HTTPException(403, "Bu kategoriya sizga ochiq emas")


@app.get("/api/health")
async def health(request: Request):
    return {"ok": True, "db": await request.app.state.pool.fetchval("SELECT 1") == 1}


# ---------------------------------------------------------------------------
@app.get("/api/meta")
async def meta(request: Request, user: User = Depends(current_user)):
    pool = request.app.state.pool
    allowed = await allowed_categories(user)
    cats = await pool.fetch(
        """SELECT c.id, c.grp, c.name, count(p.*) skus,
                  (SELECT sum(s.amount) FROM sales_daily s JOIN products p2 USING (product_id)
                   WHERE p2.category_id = c.id AND s.sale_date > current_date - 90) recent
           FROM categories c LEFT JOIN products p ON p.category_id = c.id
           GROUP BY c.id ORDER BY recent DESC NULLS LAST, c.name""")
    attrs = await pool.fetch(
        """SELECT category_id, slot, name, data_type, unit, sort_order FROM category_attributes
           WHERE is_filter ORDER BY category_id, sort_order, slot""")
    ranges = await pool.fetch(
        "SELECT category_id, slot, sort_order, label, lo, hi FROM attribute_ranges ORDER BY category_id, slot, sort_order")
    span = await pool.fetchrow("SELECT min(sale_date) d1, max(sale_date) d2 FROM sales_daily")
    last = await pool.fetchrow(
        "SELECT finished_at FROM uploads WHERE kind='sales' AND status='done' ORDER BY id DESC LIMIT 1")

    out = []
    for c in cats:
        if allowed is not None and c["id"] not in allowed:
            continue
        a_list = [{
            "slot": a["slot"], "dim": f"attr:{a['slot']}", "name": a["name"], "type": a["data_type"], "unit": a["unit"],
            "ranges": [{"label": r["label"], "lo": r["lo"], "hi": r["hi"]} for r in ranges
                       if r["category_id"] == c["id"] and r["slot"] == a["slot"]],
        } for a in attrs if a["category_id"] == c["id"]]
        out.append({
            "id": c["id"], "group": c["grp"], "name": c["name"], "skus": c["skus"], "attributes": a_list,
            # drill-down: Tovar — xususiyatlar → brend → SKU
            "product_path": [a["dim"] for a in a_list] + ["brand", "sku"],
            # to'liq drill-down: Hudud → Klaster → Filial → xususiyatlar → Brend → SKU
            "drill_path": LOCATION_PATH + [a["dim"] for a in a_list] + ["brand", "sku"],
        })
    return {
        "user": {"id": user.id, "name": user.first_name},
        "categories": out,
        "location_path": LOCATION_PATH,
        "dimensions": {"region": "Hudud", "cluster": "Klaster", "branch": "Filial", "brand": "Brend",
                       "sku": "SKU", "status": "Status"},
        "data_from": span["d1"], "data_to": span["d2"],
        "last_upload": last["finished_at"] if last else None,
    }


# ---------------------------------------------------------------------------
@app.post("/api/options")
async def options(f: Filters, request: Request, user: User = Depends(current_user)):
    """Har bir filtr uchun qiymatlar ro'yxati (joriy davrdagi savdo bilan, kamayish tartibida).
    Faceted: masalan hudud tanlansa, filial ro'yxati shu hududnikiga qisqaradi, hudud ro'yxati esa to'liq qoladi."""
    await _check_category(user, f.category_id)
    pool = request.app.state.pool
    async with pool.acquire() as con:
        attrs = await load_attrs(con, f.category_id)
        dims = ["region", "cluster", "branch", "brand", "status"] + [f"attr:{s}" for s in attrs]
        result = {}
        for dim in dims:
            q = Q(f, attrs)
            d = dimension(dim, attrs)
            sql = (q.base_cte(exclude=dim, with_cmp=False) +
                   f"\nSELECT {d['key']} AS key, {d['label']} AS label, {d['sort']} AS sort, sum(x.amount) amount "
                   f"FROM x {JOINS} GROUP BY 1, 2, 3")
            rows = await con.fetch(sql, *q.params)
            if dim.startswith("attr:"):
                rows = sorted(rows, key=lambda r: (r["sort"] is None, r["sort"] or 0, r["label"]))
            else:
                rows = sorted(rows, key=lambda r: -(r["amount"] or 0))
            result[dim] = [{"key": r["key"], "label": r["label"], "amount": float(r["amount"] or 0)} for r in rows]
    return result


# ---------------------------------------------------------------------------
@app.post("/api/summary")
async def summary(f: Filters, request: Request, user: User = Depends(current_user)):
    await _check_category(user, f.category_id)
    async with request.app.state.pool.acquire() as con:
        attrs = await load_attrs(con, f.category_id)
        q = Q(f, attrs)
        row = await con.fetchrow(q.base_cte() + f"\nSELECT {metrics_sql('c')}, {metrics_sql('p')} FROM x", *q.params)
    cur = kpi_from(row, "c")
    prev = kpi_from(row, "p") if q.cmp else None
    return {
        "period": {"from": f.date_from, "to": f.date_to},
        "compare_period": {"from": q.cmp[0], "to": q.cmp[1]} if q.cmp else None,
        "current": cur, "previous": prev, "delta": delta(cur, prev),
    }


# ---------------------------------------------------------------------------
@app.post("/api/daily")
async def daily(f: Filters, request: Request, user: User = Depends(current_user)):
    await _check_category(user, f.category_id)
    async with request.app.state.pool.acquire() as con:
        attrs = await load_attrs(con, f.category_id)
        q = Q(f, attrs)
        rows = await con.fetch(
            q.base_cte() + """
            SELECT x.sale_date, x.per, sum(x.amount) amount, sum(x.qty) qty, sum(x.margin) margin,
                   sum(x.gross_margin) gross
            FROM x GROUP BY 1, 2""", *q.params)
    by = {(r["sale_date"], r["per"]): r for r in rows}
    n = (f.date_to - f.date_from).days + 1
    cmp = compare_period(f)
    series = []
    for i in range(n):
        d = f.date_from + timedelta(days=i)
        c = by.get((d, "c"))
        item = {"date": d, "amount": float(c["amount"]) if c else 0, "qty": float(c["qty"]) if c else 0,
                "margin": float(c["margin"]) if c else 0, "gross": float(c["gross"]) if c else 0}
        if cmp:
            pd_ = cmp[0] + timedelta(days=i)
            p = by.get((pd_, "p")) if pd_ <= cmp[1] else None
            item |= {"prev_date": pd_ if pd_ <= cmp[1] else None,
                     "prev_amount": float(p["amount"]) if p else 0, "prev_margin": float(p["margin"]) if p else 0,
                     "prev_qty": float(p["qty"]) if p else 0}
        series.append(item)
    return {"series": series}


# ---------------------------------------------------------------------------
def _sort_key(item: dict, metric: str):
    if metric == "name":
        return (item["sort"] is None, item["sort"] or 0, item["label"] or "")
    k: Kpi = item["current"]
    v = k.amount if metric == "share" else getattr(k, metric)
    return v if v is not None else float("-inf")


@app.post("/api/breakdown")
async def breakdown(req: BreakdownRequest, request: Request, user: User = Depends(current_user)):
    """Qirqim jadvali. rows — qator o'lchovi, cols — ixtiyoriy ustun o'lchovi (kesma).
    Ulush (share) — joriy filtrlar bo'yicha jami savdoga nisbatan."""
    await _check_category(user, req.category_id)
    async with request.app.state.pool.acquire() as con:
        attrs = await load_attrs(con, req.category_id)
        try:
            rd = dimension(req.rows, attrs)
            cd = dimension(req.cols, attrs) if req.cols else None
        except ValueError as e:
            raise HTTPException(422, str(e))
        if cd and req.cols == req.rows:
            raise HTTPException(422, "Qator va ustun o'lchovi bir xil bo'lmasin")

        q = Q(req, attrs)
        base = q.base_cte()
        m = f"{metrics_sql('c')}, {metrics_sql('p')}"
        rows = await con.fetch(
            base + f"\nSELECT {rd['key']} AS key, {rd['label']} AS label, {rd['sort']} AS sort, {rd['sub']} AS sub, "
                   f"{m} FROM x {JOINS} GROUP BY 1, 2, 3, 4", *q.params)
        cells_raw = []
        if cd:
            cells_raw = await con.fetch(
                base + f"\nSELECT {rd['key']} AS rkey, {cd['key']} AS ckey, {cd['label']} AS clabel, "
                       f"{cd['sort']} AS csort, {m} FROM x {JOINS} GROUP BY 1, 2, 3, 4", *q.params)

    has_cmp = q.cmp is not None
    items = []
    for r in rows:
        cur = kpi_from(r, "c")
        prev = kpi_from(r, "p") if has_cmp else None
        if cur.amount == 0 and cur.qty == 0 and (prev is None or prev.amount == 0):
            continue
        items.append({"key": r["key"], "label": r["label"], "sub": r["sub"], "sort": r["sort"], "current": cur,
                      "previous": prev, "delta": delta(cur, prev)})
    total_cur = Kpi(**{k: sum(getattr(i["current"], k) for i in items)
                       for k in ("amount", "qty", "bonus_qty", "gross", "income", "margin")})
    total_cur.gross_pct = round(total_cur.gross / total_cur.amount * 100, 2) if total_cur.amount else None
    total_cur.margin_pct = round(total_cur.margin / total_cur.amount * 100, 2) if total_cur.amount else None
    total_prev = sum(i["previous"].amount for i in items) if has_cmp else 0
    for i in items:
        i["share"] = round(i["current"].amount / total_cur.amount * 100, 2) if total_cur.amount else None
        i["prev_share"] = round(i["previous"].amount / total_prev * 100, 2) if has_cmp and total_prev else None
        i["share_pp"] = (round(i["share"] - i["prev_share"], 2)
                         if i["share"] is not None and i["prev_share"] is not None else None)

    items.sort(key=lambda i: _sort_key(i, req.sort), reverse=req.desc and req.sort != "name")
    if req.sort == "name" and req.desc:
        items.reverse()
    total_rows = len(items)
    items = items[: req.limit]

    result = {
        "rows_dim": req.rows, "rows_title": rd["title"],
        "cols_dim": req.cols, "cols_title": cd["title"] if cd else None,
        "total": total_cur, "total_rows": total_rows,
        "rows": [{k: v for k, v in i.items() if k != "sort"} for i in items],
    }

    if cd:
        # ustunlar: joriy davr savdosi bo'yicha TOP-N, qolganlari "Boshqalar"
        col_amt: dict[str, dict] = {}
        for c in cells_raw:
            e = col_amt.setdefault(c["ckey"], {"key": c["ckey"], "label": c["clabel"], "sort": c["csort"], "amount": 0.0})
            e["amount"] += float(c["c_amount"] or 0)
        cols = sorted(col_amt.values(), key=lambda c: -c["amount"])
        if cd["sort"] != "NULL::numeric":      # diapazonli/raqamli xususiyat — tabiiy tartib
            cols = sorted(cols, key=lambda c: (c["sort"] is None, c["sort"] or 0, c["label"] or ""))
        keep = {c["key"] for c in sorted(col_amt.values(), key=lambda c: -c["amount"])[:MAX_PIVOT_COLS]}
        other = len(col_amt) > MAX_PIVOT_COLS
        cols_out = [{"key": c["key"], "label": c["label"], "amount": c["amount"]} for c in cols if c["key"] in keep]
        if other:
            cols_out.append({"key": "__other__", "label": "Boshqalar",
                             "amount": sum(c["amount"] for c in col_amt.values() if c["key"] not in keep)})
        row_keys = {i["key"] for i in items}
        acc: dict[str, dict[str, dict]] = {}
        for c in cells_raw:
            if c["rkey"] not in row_keys:
                continue
            ck = c["ckey"] if c["ckey"] in keep else "__other__"
            a = acc.setdefault(c["rkey"], {}).setdefault(ck, {"amount": 0.0, "qty": 0.0, "margin": 0.0, "gross": 0.0,
                                                             "prev_amount": 0.0})
            a["amount"] += float(c["c_amount"] or 0)
            a["qty"] += float(c["c_qty"] or 0)
            a["margin"] += float(c["c_margin"] or 0)
            a["gross"] += float(c["c_gross"] or 0)
            a["prev_amount"] += float(c["p_amount"] or 0)
        for row in acc.values():
            for a in row.values():
                a["margin_pct"] = round(a["margin"] / a["amount"] * 100, 2) if a["amount"] else None
                a["delta_amount"] = (round((a["amount"] - a["prev_amount"]) / a["prev_amount"] * 100, 1)
                                     if has_cmp and a["prev_amount"] else None)
        result["cols"] = cols_out
        result["cells"] = acc
    return result


# ---------------------------------------------------------------------------
@app.post("/api/share-daily")
async def share_daily(req: BreakdownRequest, request: Request, user: User = Depends(current_user)):
    """Kunlar bo'yicha savdo ulushi (100% ustunlar grafigi uchun). req.rows — o'lchov, req.limit — TOP-N (sukut 5)."""
    await _check_category(user, req.category_id)
    top_n = min(req.limit, 8) if req.limit != 200 else 5
    async with request.app.state.pool.acquire() as con:
        attrs = await load_attrs(con, req.category_id)
        try:
            rd = dimension(req.rows, attrs)
        except ValueError as e:
            raise HTTPException(422, str(e))
        q = Q(req, attrs)
        rows = await con.fetch(
            q.base_cte(with_cmp=False) + f"\nSELECT x.sale_date, {rd['key']} AS key, {rd['label']} AS label, "
            f"sum(x.amount) amount FROM x {JOINS} GROUP BY 1, 2, 3", *q.params)
    totals: dict[str, dict] = {}
    for r in rows:
        t = totals.setdefault(r["key"], {"key": r["key"], "label": r["label"], "amount": 0.0})
        t["amount"] += float(r["amount"] or 0)
    top = sorted(totals.values(), key=lambda t: -t["amount"])[:top_n]
    keys = [t["key"] for t in top]
    n = (req.date_to - req.date_from).days + 1
    days = [{"date": req.date_from + timedelta(days=i), "total": 0.0, "values": [0.0] * (len(keys) + 1)} for i in range(n)]
    idx = {k: i for i, k in enumerate(keys)}
    for r in rows:
        d = days[(r["sale_date"] - req.date_from).days]
        v = float(r["amount"] or 0)
        d["values"][idx.get(r["key"], len(keys))] += v
        d["total"] += v
    for d in days:
        d["shares"] = [round(v / d["total"] * 100, 2) if d["total"] else 0 for v in d["values"]]
    return {"series": [{"key": t["key"], "label": t["label"]} for t in top] + [{"key": "__other__", "label": "Boshqalar"}],
            "days": days}


# ---------------------------------------------------------------------------
@app.post("/api/attributes")
async def attributes(f: Filters, request: Request, user: User = Depends(current_user)):
    """Xususiyatlar bloklari: har bir xususiyat (va brend) bo'yicha barcha qiymatlar — savdo, dona, marja,
    ulush, o'tgan davr ulushi, pp. Faceted: blokning o'z filtri hisobga olinmaydi, tanlangan qiymatlar selected=true."""
    await _check_category(user, f.category_id)
    out = []
    async with request.app.state.pool.acquire() as con:
        attrs = await load_attrs(con, f.category_id)
        for dim in [f"attr:{s}" for s in attrs] + ["brand"]:
            d = dimension(dim, attrs)
            q = Q(f, attrs)
            rows = await con.fetch(
                q.base_cte(exclude=dim) + f"\nSELECT {d['key']} AS key, {d['label']} AS label, {d['sort']} AS sort, "
                f"{metrics_sql('c')}, {metrics_sql('p')} FROM x {JOINS} GROUP BY 1, 2, 3", *q.params)
            has_cmp = q.cmp is not None
            items = [{"key": r["key"], "label": r["label"], "sort": r["sort"], "current": kpi_from(r, "c"),
                      "previous": kpi_from(r, "p") if has_cmp else None} for r in rows]
            items = [i for i in items if i["current"].amount or i["current"].qty]
            tc = sum(i["current"].amount for i in items)
            tp = sum(i["previous"].amount for i in items) if has_cmp else 0
            selected = set()
            if dim == "brand":
                selected = set(f.brands)
            elif dim.startswith("attr:"):
                selected = set(f.attrs.get(int(dim[5:]), []))
            for i in items:
                i["share"] = round(i["current"].amount / tc * 100, 2) if tc else None
                i["prev_share"] = round(i["previous"].amount / tp * 100, 2) if has_cmp and tp else None
                i["share_pp"] = (round(i["share"] - i["prev_share"], 2)
                                 if i["share"] is not None and i["prev_share"] is not None else None)
                i["delta"] = delta(i["current"], i["previous"])
                i["selected"] = i["key"] in selected
            if any(i["sort"] is not None for i in items):
                items.sort(key=lambda i: (i["sort"] is None, i["sort"] or 0, -i["current"].amount))
            else:
                items.sort(key=lambda i: -i["current"].amount)
            out.append({"dim": dim, "title": d["title"],
                        "unit": attrs[int(dim[5:])].unit if dim.startswith("attr:") else None,
                        "total": tc, "items": [{k: v for k, v in i.items() if k != "sort"} for i in items]})
    return {"blocks": out}


# ---------------------------------------------------------------------------
# Mini App statik fayllari: /app/  (Telegram'da WEBAPP_URL = https://<domen>/app/)
# Faqat 3 ta fayl beriladi — papkadagi boshqa fayllar (.py, .env) tashqariga ochilmaydi.
from fastapi.responses import FileResponse

BASE_DIR = Path(__file__).resolve().parent
WEBAPP_FILES = {"": ("index.html", "text/html"), "index.html": ("index.html", "text/html"),
                "app.css": ("app.css", "text/css"), "app.js": ("app.js", "application/javascript")}


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/app/")


@app.get("/app", include_in_schema=False)
async def app_noslash():
    return RedirectResponse("/app/")


@app.get("/app/{name}", include_in_schema=False)
@app.get("/app/", include_in_schema=False)
async def webapp_file(name: str = ""):
    if name not in WEBAPP_FILES:
        raise HTTPException(404)
    fname, media = WEBAPP_FILES[name]
    return FileResponse(BASE_DIR / fname, media_type=media, headers={"Cache-Control": "no-cache"})
