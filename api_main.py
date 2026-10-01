"""FastAPI backend — Mini App uchun ma'lumot.

Ishga tushirish:  uvicorn api_main:app --host 0.0.0.0 --port 8000

Endpointlar (hammasi Authorization: tma <initData> talab qiladi):
  GET  /api/meta        kategoriyalar, xususiyatlar, diapazonlar, ma'lumot davri, drill-down tartibi
  POST /api/options     filtr qiymatlari (faceted: har bir filtr qolgan filtrlarga moslashadi)
  POST /api/summary     KPI kartalar: joriy davr, solishtirma davr, o'zgarish
  POST /api/daily       kunlik dinamika (joriy va solishtirma davr kunma-kun)
  POST /api/breakdown   qirqim jadvali: qatorlar o'lchovi (+ ixtiyoriy ustunlar o'lchovi), ulush, ulush o'zgarishi (pp)
  POST /api/share-daily kunlar bo'yicha ulush: tanlangan o'lchovning TOP-N qiymati + "Boshqalar"
  GET  /api/product/{id} SKU kartasi (xususiyatlar)
  POST /api/attributes  xususiyatlar bloklari: har bir xususiyat va brend bo'yicha barcha qiymatlar, ulush va pp
"""
from __future__ import annotations

import json
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
from api_upload import can_upload, router as upload_router

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
app.include_router(upload_router)
app.add_middleware(GZipMiddleware, minimum_size=1000)
if config.CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


async def _check_category(user: User, category_id: int) -> None:
    allowed = await allowed_categories(user)
    if allowed is not None and category_id not in allowed:
        raise HTTPException(403, "Bu kategoriya sizga ochiq emas")


async def scope_ctx(con, f: Filters, user: User) -> tuple[list[int], dict, bool]:
    """Ko'rinish doirasini kategoriyalar ro'yxatiga aylantiradi: c:<id> | g:<guruh> | o:<mas'ul> | a:"""
    kind, _, val = (f.scope or "").partition(":")
    if kind == "c":
        try:
            cats = [r["id"] for r in await con.fetch("SELECT id FROM categories WHERE id = $1", int(val))]
        except ValueError:
            raise HTTPException(422, "Noto'g'ri kategoriya")
    elif kind == "g":
        cats = [r["id"] for r in await con.fetch("SELECT id FROM categories WHERE grp = $1", val)]
    elif kind == "o":
        cats = [r["id"] for r in await con.fetch("SELECT id FROM categories WHERE owner = $1", val)]
    elif kind == "a":
        cats = [r["id"] for r in await con.fetch("SELECT id FROM categories")]
    else:
        raise HTTPException(422, "Noto'g'ri doira (scope)")
    allowed = await allowed_categories(user)
    if allowed is not None:
        if kind == "c" and cats and cats[0] not in allowed:
            raise HTTPException(403, "Bu kategoriya sizga ochiq emas")
        cats = [c for c in cats if c in allowed]
    if not cats:
        raise HTTPException(404, "Kategoriya topilmadi")
    multi = kind != "c"
    attrs = {} if multi else await load_attrs(con, cats[0])
    return cats, attrs, multi


@app.get("/api/health")
async def health(request: Request):
    return {"ok": True, "db": await request.app.state.pool.fetchval("SELECT 1") == 1}


# ---------------------------------------------------------------------------
@app.get("/api/meta")
async def meta(request: Request, user: User = Depends(current_user)):
    pool = request.app.state.pool
    allowed = await allowed_categories(user)
    cats = await pool.fetch(
        """SELECT c.id, c.grp, c.name, c.owner, count(p.*) skus,
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
    rate = await pool.fetchrow("SELECT rate, valid_from FROM fx_rates ORDER BY valid_from DESC LIMIT 1")

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
            "key": f"c:{c['id']}", "kind": "cat", "id": c["id"], "group": c["grp"], "owner": c["owner"],
            "name": c["name"], "skus": c["skus"], "attributes": a_list, "extra_dims": [], "cat_ids": [c["id"]],
            # drill-down: Tovar — xususiyatlar → brend → SKU
            "product_path": [a["dim"] for a in a_list] + ["brand", "sku"],
            # to'liq drill-down: Hudud → Klaster → Filial → xususiyatlar → Brend → SKU
            "drill_path": LOCATION_PATH + [a["dim"] for a in a_list] + ["brand", "sku"],
        })

    def multi_scope(key: str, kind: str, name: str, members: list[dict]) -> dict:
        owners = {m["owner"] for m in members if m["owner"]}
        extra = ["category"] + (["owner"] if len(owners) > 1 else [])
        return {"key": key, "kind": kind, "id": None, "name": name, "group": None, "owner": None,
                "skus": sum(m["skus"] for m in members), "attributes": [], "extra_dims": extra,
                "cat_ids": [m["id"] for m in members], "n_cats": len(members),
                "product_path": ["category", "brand", "sku"],
                "drill_path": LOCATION_PATH + ["category", "brand", "sku"]}

    groups, owners = {}, {}
    for c in out:
        if c["group"]:
            groups.setdefault(c["group"], []).append(c)
        if c["owner"]:
            owners.setdefault(c["owner"], []).append(c)
    scopes = ([multi_scope("a:", "all", "Barcha kategoriyalar", out)] if len(out) > 1 else [])
    scopes += [multi_scope(f"g:{g}", "group", f"Butun {g}", m) for g, m in sorted(groups.items())]
    scopes += [multi_scope(f"o:{o}", "owner", o, m) for o, m in sorted(owners.items())]
    return {
        "user": {"id": user.id, "name": user.first_name},
        "can_upload": can_upload(user), "max_upload_mb": config.MAX_UPLOAD_MB, "is_admin": user.is_admin,
        "categories": out, "scopes": scopes,
        "location_path": LOCATION_PATH,
        "dimensions": {"region": "Hudud", "cluster": "Klaster", "branch": "Filial", "brand": "Brend",
                       "sku": "SKU", "status": "Status", "category": "Kategoriya", "owner": "Mas'ul"},
        "data_from": span["d1"], "data_to": span["d2"],
        "last_upload": last["finished_at"] if last else None,
        "fx_rate": float(rate["rate"]) if rate else None, "fx_from": rate["valid_from"] if rate else None,
    }


# ---------------------------------------------------------------------------
@app.post("/api/options")
async def options(f: Filters, request: Request, user: User = Depends(current_user)):
    """Har bir filtr uchun qiymatlar ro'yxati (joriy davrdagi savdo bilan, kamayish tartibida).
    Faceted: masalan hudud tanlansa, filial ro'yxati shu hududnikiga qisqaradi, hudud ro'yxati esa to'liq qoladi."""
    pool = request.app.state.pool
    async with pool.acquire() as con:
        cats, attrs, multi = await scope_ctx(con, f, user)
        dims = (["region", "cluster", "branch"] + (["category", "owner"] if multi else []) + ["brand", "status"]
                + [f"attr:{s}" for s in attrs])
        result = {}
        for dim in dims:
            q = Q(f, attrs, cats)
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
    async with request.app.state.pool.acquire() as con:
        cats, attrs, multi = await scope_ctx(con, f, user)
        q = Q(f, attrs, cats)
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
    async with request.app.state.pool.acquire() as con:
        cats, attrs, multi = await scope_ctx(con, f, user)
        q = Q(f, attrs, cats)
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
    async with request.app.state.pool.acquire() as con:
        cats, attrs, multi = await scope_ctx(con, req, user)
        try:
            rd = dimension(req.rows, attrs)
            cd = dimension(req.cols, attrs) if req.cols else None
        except ValueError as e:
            raise HTTPException(422, str(e))
        if cd and req.cols == req.rows:
            raise HTTPException(422, "Qator va ustun o'lchovi bir xil bo'lmasin")

        q = Q(req, attrs, cats)
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
    top_n = min(req.limit, 8) if req.limit != 200 else 5
    async with request.app.state.pool.acquire() as con:
        cats, attrs, multi = await scope_ctx(con, req, user)
        try:
            rd = dimension(req.rows, attrs)
        except ValueError as e:
            raise HTTPException(422, str(e))
        q = Q(req, attrs, cats)
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
    out = []
    async with request.app.state.pool.acquire() as con:
        cats, attrs, multi = await scope_ctx(con, f, user)
        for dim in (["category", "owner"] if multi else [f"attr:{s}" for s in attrs]) + ["brand"]:
            d = dimension(dim, attrs)
            q = Q(f, attrs, cats)
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
            elif dim == "category":
                selected = {str(c) for c in f.category_ids}
            elif dim == "owner":
                selected = set(f.owners)
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

@app.get("/api/product/{product_id}")
async def product_card(product_id: int, request: Request, user: User = Depends(current_user)):
    """SKU kartasi: nomi, brend, status va kategoriya xususiyatlari (nomi + qiymati)."""
    async with request.app.state.pool.acquire() as con:
        p = await con.fetchrow(
            """SELECT p.product_id, p.name, p.brand, p.status, p.category_id, p.attrs::text AS attrs, c.name AS category
               FROM products p JOIN categories c ON c.id = p.category_id WHERE p.product_id = $1""", product_id)
        if not p:
            raise HTTPException(404, "Tovar topilmadi")
        await _check_category(user, p["category_id"])
        defs = await con.fetch(
            "SELECT slot, name, unit FROM category_attributes WHERE category_id = $1 AND is_filter ORDER BY sort_order, slot",
            p["category_id"])
    raw = json.loads(p["attrs"]) if p["attrs"] else {}
    attrs = []
    for d in defs:
        v = raw.get(f"a{d['slot']}")
        if v is None or v == "":
            continue
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        val = str(v)
        if d["unit"] and isinstance(v, (int, float)):
            val = f"{val} {d['unit']}"
        attrs.append({"name": d["name"], "value": val})
    return {"product_id": p["product_id"], "name": p["name"], "brand": p["brand"], "status": p["status"],
            "category": p["category"], "attrs": attrs}


# ---------------------------------------------------------------------------
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
