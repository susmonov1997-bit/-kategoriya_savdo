"""Kutayotgan tovarlar (Mini App «📥 → ⏳ Kutayotgan»): spravochnikda yo'q Товар ИД'larni qo'lda to'ldirish.

Manba — export_missing.pending_report (savdo, qoldiq va matritsadagi ID). Saqlanganda tovar products'ga
qo'shiladi va shu zahoti kutayotgan savdo/qoldiq (pending.resolve_pending) va matritsa (rebind_products) ulanadi.
Excel orqali to'ldirish ham saqlanib qoladi (spravochnik formatida yuklash).

GET  /api/pending/list            ro'yxat (summa bo'yicha)
GET  /api/pending/item/{pid}      forma uchun: tovar, kategoriyalar, taklif kategoriya, o'xshash tovarlar, statuslar
GET  /api/pending/cat/{cid}       kategoriya xususiyatlari (+ bazadagi qiymatlar) va brendlar
POST /api/pending/save            saqlash va ulash
"""
from __future__ import annotations

import difflib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api_auth import User
from api_upload import uploader
from loaders_common import clean_text, name_key, to_number

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pending")


async def _report(con) -> dict:
    from export_missing import pending_report
    return await pending_report(con)


def _item(s: dict) -> dict:
    return {"id": s["product_id"], "name": s.get("name"), "category": s.get("category"), "brand": s.get("brand"),
            "sales_days": s.get("days") or 0, "sales_amount": s.get("amount") or 0, "sales_rows": s.get("rows") or 0,
            "stock_qty": s.get("stock_qty") or 0,
            "stock_amount": s.get("stock_amount") or 0, "mx_branches": s.get("mx_branches") or 0}


@router.get("/list")
async def pending_list(request: Request, user: User = Depends(uploader)):
    async with request.app.state.pool.acquire() as con:
        rep = await _report(con)
    items = [_item(s) for s in rep.get("unknown_skus") or []]
    # savdo summasi bo'yicha; savdosi yo'qlar — qoldiq summasi bo'yicha (kamayish tartibida)
    items.sort(key=lambda x: (0 if x["sales_amount"] > 0 else 1, -x["sales_amount"], -x["stock_amount"], -x["stock_qty"],
                              -x["mx_branches"]))
    return {"items": items, "branches": len(rep.get("unknown_branches") or []),
            "sum": {"amount": sum(x["sales_amount"] for x in items), "stock": sum(x["stock_qty"] for x in items),
                    "mx": sum(1 for x in items if x["mx_branches"])}}


def _lk(v) -> str:
    from matrix_sync import loose_key
    return loose_key(v) or ""


@router.get("/item/{pid}")
async def pending_item(pid: int, request: Request, kirim: str | None = None, user: User = Depends(uploader)):
    async with request.app.state.pool.acquire() as con:
        if await con.fetchval("SELECT 1 FROM products WHERE product_id = $1", pid):
            raise HTTPException(409, "Bu tovar allaqachon spravochnikda bor")
        rep = await _report(con)
        s = next((x for x in rep.get("unknown_skus") or [] if x["product_id"] == pid), None)
        if s is None and kirim:                 # kirim faylidagi nomi topilmagan tovar → yangi tovar sifatida
            k = await con.fetchrow("""SELECT max(product_name) name, max(category) cat, max(brand) brand
                                      FROM receipt_pending WHERE prod_key = $1 AND product_id IS NULL""", kirim)
            if k and k["name"]:
                s = {"product_id": pid, "name": k["name"], "category": k["cat"], "brand": k["brand"]}
        if s is None:
            raise HTTPException(404, "Kutayotganlar ro'yxatida bunday tovar yo'q")
        cats = [dict(r) for r in await con.fetch("SELECT id, name, grp FROM categories ORDER BY grp, name")]
        ck = name_key(s.get("category"))
        cat_id = next((c["id"] for c in cats if name_key(c["name"]) == ck), None)
        # o'xshash tovarlar: shu brend (yo'q bo'lsa — taklif kategoriya) ichidan nom bo'yicha
        brand = clean_text(s.get("brand"))
        if brand:
            cand = await con.fetch("SELECT product_id, name, category_id, brand, status, attrs FROM products "
                                   "WHERE lower(brand) = lower($1) LIMIT 5000", brand)
        else:
            cand = []
        if not cand and cat_id:
            cand = await con.fetch("SELECT product_id, name, category_id, brand, status, attrs FROM products "
                                   "WHERE category_id = $1 LIMIT 5000", cat_id)
        k = _lk(s.get("name"))
        sim = []
        if k:
            for r in cand:
                sc = difflib.SequenceMatcher(None, k, _lk(r["name"])).ratio()
                if sc >= 0.6:
                    sim.append((sc, r))
        sim.sort(key=lambda x: -x[0])
        statuses = [r[0] for r in await con.fetch(
            "SELECT status FROM products WHERE status IS NOT NULL GROUP BY 1 ORDER BY count(*) DESC LIMIT 8")]
    return {"item": _item(s), "categories": cats, "cat_id": cat_id or (sim[0][1]["category_id"] if sim else None),
            "statuses": statuses,
            "similar": [{"id": r["product_id"], "name": r["name"], "category_id": r["category_id"], "brand": r["brand"],
                         "status": r["status"], "score": round(sc * 100),
                         "attrs": r["attrs"] if isinstance(r["attrs"], dict) else json.loads(r["attrs"] or "{}")}
                        for sc, r in sim[:3]]}


@router.get("/cat/{cid}")
async def pending_cat(cid: int, request: Request, user: User = Depends(uploader)):
    async with request.app.state.pool.acquire() as con:
        attrs = []
        for a in await con.fetch("SELECT slot, name, data_type, unit FROM category_attributes "
                                 "WHERE category_id = $1 ORDER BY sort_order, slot", cid):
            vals = [r[0] for r in await con.fetch(
                "SELECT attrs->>$2 v FROM products WHERE category_id = $1 AND attrs ? $2 "
                "GROUP BY 1 ORDER BY count(*) DESC LIMIT 40", cid, f"a{a['slot']}") if r[0] not in (None, "")]
            attrs.append({"slot": a["slot"], "name": a["name"], "type": a["data_type"], "unit": a["unit"], "values": vals})
        brands = [r[0] for r in await con.fetch(
            "SELECT brand FROM products WHERE category_id = $1 GROUP BY 1 ORDER BY count(*) DESC LIMIT 80", cid)]
    return {"attrs": attrs, "brands": brands}


class SaveReq(BaseModel):
    product_id: int
    name: str
    category_id: int
    brand: str
    status: str | None = None
    attrs: dict[int, str | float | None] = {}
    kirim_key: str | None = None        # kirim faylidagi nom (prod_key) — shu tovarga bog'lanadi


@router.post("/save")
async def pending_save(req: SaveReq, request: Request, user: User = Depends(uploader)):
    from fact import ensure_fact
    from matrix_sync import rebind_products
    from pending import resolve_pending

    name, brand = clean_text(req.name), clean_text(req.brand)
    if not name or not brand:
        raise HTTPException(400, "Tovar nomi va brend majburiy")
    pool = request.app.state.pool
    async with pool.acquire() as con:
        types = {r["slot"]: r["data_type"] for r in await con.fetch(
            "SELECT slot, data_type FROM category_attributes WHERE category_id = $1", req.category_id)}
        if not await con.fetchval("SELECT 1 FROM categories WHERE id = $1", req.category_id):
            raise HTTPException(400, "Kategoriya topilmadi")
        attrs = {}
        for slot, v in req.attrs.items():
            if slot not in types or v is None or (isinstance(v, str) and not v.strip()):
                continue
            if types[slot] == "number":
                n = to_number(v)
                attrs[f"a{slot}"] = n if n is not None else str(v).strip()
            else:
                attrs[f"a{slot}"] = clean_text(v)
        async with con.transaction():
            ok = await con.fetchval(
                """INSERT INTO products (product_id, name, category_id, brand, status, attrs)
                   VALUES ($1, $2, $3, $4, $5, $6::jsonb) ON CONFLICT (product_id) DO NOTHING RETURNING 1""",
                req.product_id, name, req.category_id, brand, clean_text(req.status),
                json.dumps(attrs, ensure_ascii=False))
            if not ok:
                raise HTTPException(409, "Bu tovar allaqachon spravochnikda bor")
            if req.kirim_key:
                await _alias(con, req.kirim_key, req.product_id, user.id)
            res = await resolve_pending(con)
        mx = 0
        try:
            mx = await rebind_products(con)
        except Exception:  # noqa: BLE001
            log.exception("Matritsa ID bo'yicha qayta bog'lanmadi")
    try:
        await ensure_fact(pool)
    except Exception:  # noqa: BLE001
        log.exception("sales_fact qayta qurilmadi — API keyingi so'rovda quradi")
    log.info("Kutayotgan tovar qo'lda qo'shildi: %s (%s) — user %s", req.product_id, name, user.id)
    return {"ok": True, "sales_rows": res["rows"], "sales_amount": res["amount"], "stock_rows": res.get("stock_rows", 0),
            "stock_qty": res.get("stock_qty", 0), "mx_rows": mx, "rec_rows": res.get("rec_rows", 0),
            "rec_qty": res.get("rec_qty", 0)}


# ---------------------------------------------------------------------------
# Kirim faylida nomi topilmagan tovarlar (receipt_pending): bog'lash
async def _alias(con, key: str, pid: int, uid: int) -> None:
    """Kirimdagi nom va uning «Товар коди»lari → tovar (keyingi fayllarda o'zi bog'lanadi)."""
    codes = [r[0] for r in await con.fetch("SELECT DISTINCT product_code FROM receipt_pending WHERE prod_key = $1 "
                                           "AND product_code IS NOT NULL", key)]
    await con.executemany(
        """INSERT INTO product_aliases (alias_key, product_id, created_by) VALUES ($1, $2, $3)
           ON CONFLICT (alias_key) DO UPDATE SET product_id = EXCLUDED.product_id, created_by = EXCLUDED.created_by,
             created_at = now()""", [("n:" + key, pid, uid)] + [("c:" + c, pid, uid) for c in codes])


@router.get("/kirim")
async def kirim_list(request: Request, user: User = Depends(uploader)):
    async with request.app.state.pool.acquire() as con:
        rows = await con.fetch(
            """SELECT prod_key, max(product_name) name, max(category) cat, max(brand) brand,
                      string_agg(DISTINCT product_code, ', ') codes, sum(qty)::float8 qty,
                      coalesce(sum(value_usd), 0)::float8 usd, count(DISTINCT nullif(order_code, '')) orders,
                      count(DISTINCT loc_key) locs, min(rec_date) d1, max(rec_date) d2
               FROM receipt_pending WHERE product_id IS NULL GROUP BY prod_key ORDER BY usd DESC, qty DESC""")
    return {"items": [{"key": r["prod_key"], "name": r["name"], "cat": r["cat"], "brand": r["brand"], "codes": r["codes"],
                       "qty": r["qty"], "usd": r["usd"], "orders": r["orders"], "locs": r["locs"],
                       "d1": r["d1"], "d2": r["d2"]} for r in rows]}


@router.get("/kirim/cand")
async def kirim_cand(key: str, request: Request, user: User = Depends(uploader)):
    async with request.app.state.pool.acquire() as con:
        k = await con.fetchrow("""SELECT max(product_name) name, max(brand) brand, max(category) cat
                                  FROM receipt_pending WHERE prod_key = $1 AND product_id IS NULL""", key)
        if not k or not k["name"]:
            raise HTTPException(404, "Bu tovar endi kutayotganlar ro'yxatida yo'q")
        cand = []
        if k["brand"]:
            cand = await con.fetch("SELECT p.product_id, p.name, c.name cname FROM products p JOIN categories c "
                                   "ON c.id = p.category_id WHERE lower(p.brand) = lower($1) LIMIT 8000", k["brand"])
        if not cand:
            cid = await con.fetchval("SELECT id FROM categories WHERE lower(name) = lower($1)", k["cat"] or "")
            if cid:
                cand = await con.fetch("SELECT p.product_id, p.name, c.name cname FROM products p JOIN categories c "
                                       "ON c.id = p.category_id WHERE p.category_id = $1 LIMIT 8000", cid)
    kk = _lk(k["name"])
    sc = sorted(((difflib.SequenceMatcher(None, kk, _lk(r["name"])).ratio(), r) for r in cand), key=lambda x: -x[0])
    return {"name": k["name"], "similar": [{"id": r["product_id"], "name": r["name"], "cat": r["cname"],
                                            "score": round(s * 100)} for s, r in sc[:3] if s >= 0.5]}


@router.get("/search")
async def product_search(q: str, request: Request, user: User = Depends(uploader)):
    q = (q or "").strip()
    if len(q) < 2:
        return {"items": []}
    async with request.app.state.pool.acquire() as con:
        pid = int(q) if q.isdigit() else None
        words = [w for w in q.split() if w][:6]
        cond = " AND ".join(f"p.name ILIKE ${i + 2}" for i in range(len(words))) or "FALSE"
        rows = await con.fetch(
            f"""SELECT p.product_id, p.name, c.name cname FROM products p JOIN categories c ON c.id = p.category_id
                WHERE p.product_id = $1 OR ({cond}) ORDER BY p.product_id = $1 DESC, length(p.name) LIMIT 20""",
            pid, *[f"%{w}%" for w in words])
    return {"items": [{"id": r["product_id"], "name": r["name"], "cat": r["cname"]} for r in rows]}


class BindReq(BaseModel):
    key: str
    product_id: int


@router.post("/kirim/bind")
async def kirim_bind(req: BindReq, request: Request, user: User = Depends(uploader)):
    from pending import resolve_pending
    async with request.app.state.pool.acquire() as con:
        if not await con.fetchval("SELECT 1 FROM products WHERE product_id = $1", req.product_id):
            raise HTTPException(404, "Tovar spravochnikda topilmadi")
        async with con.transaction():
            await _alias(con, req.key, req.product_id, user.id)
            res = await resolve_pending(con)
    log.info("Kirim nomi qo'lda bog'landi: %s → %s (user %s)", req.key, req.product_id, user.id)
    return {"ok": True, "rec_rows": res.get("rec_rows", 0), "rec_qty": res.get("rec_qty", 0)}
