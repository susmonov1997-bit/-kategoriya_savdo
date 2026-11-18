"""Matritsa API — Mini App «Matritsa» tabi (013_matrix.sql, 014_matrix_daily.sql).

Hisob (bog'langan matritsa qatorlari: filial × SKU → Turishi):
  * qoldiq — filial qoldig'ining eng so'nggi sanasi (stock_branch);
  * pozitsiya «bajarilgan» — qoldiq ≥ Turishi; «kam» — 0 < qoldiq < Turishi; «yo'q» — qoldiq ≤ 0;
  * bajarilish % = bajarilgan / jami pozitsiya;
  * «omborda bor» — filial biriktirilgan omborda (branches.warehouse_id) shu SKU qoldig'i > 0 (stock_wh, so'nggi sana);
  * vitrina: filial × kategoriya bo'yicha sig'im (Подкатегория'lar sig'imi yig'indisi) va turgan model soni —
    filialda qoldig'i > 0 bo'lgan shu kategoriya SKU'lari (matritsadan tashqarisi ham joy egallaydi);
  * ▲▼ — oldingi qoldiq sanasidagi bajarilish (matrix_daily) bilan farq, pp;
  * 30 kun savdo — oxirgi savdo sanasigacha, filial × SKU.
Qoldiq yuklanmagan filial (shu sanada qoldiq faylida yo'q) — «qoldiq yo'q» bo'lib alohida chiqadi, foizga kirmaydi.

POST /api/matrix/overview   KPI + filiallar ro'yxati
POST /api/matrix/branch     filial tafsiloti: kategoriyalar, yetishmagan SKU'lar, matritsadan tashqari qoldiq
"""
from __future__ import annotations

import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request

from api_auth import User, current_user
from api_schemas import Filters

router = APIRouter(prefix="/api/matrix")


def _M():
    import api_main
    return api_main


class MxReq(Filters):
    branch_id: int | None = None


async def _snaps(con) -> dict:
    return {"b": await con.fetchval("SELECT max(snap_date) FROM stock_branch"),
            "w": await con.fetchval("SELECT max(snap_date) FROM stock_wh"),
            "s": await con.fetchval("SELECT max(sale_date) FROM sales_fact")}


def _where_branches(f: Filters, params: list) -> str:
    c = []
    for col, v in (("region_id", f.region_ids), ("cluster_id", f.cluster_ids), ("id", f.branch_ids)):
        if v:
            params.append(v)
            c.append(f"b.{col} = ANY(${len(params)}::int[])")
    return " AND ".join(c) or "TRUE"


async def _cats(con, f: Filters, user: User) -> list[int]:
    cats, _, _ = await _M().scope_ctx(con, f, user)
    if f.category_ids:
        cats = [c for c in cats if c in set(f.category_ids)]
    return cats


# ---------------------------------------------------------------------------
# Kunlik tarix (▲▼)
_POS_SQL = """
SELECT m.branch_id, m.category_id, count(*) pos, count(*) FILTER (WHERE COALESCE(s.qty, 0) >= m.need) ok
FROM (SELECT branch_id, product_id, category_id, max(need_qty) need FROM matrix_rows
      WHERE branch_id IS NOT NULL AND product_id IS NOT NULL AND category_id IS NOT NULL GROUP BY 1, 2, 3) m
JOIN (SELECT DISTINCT branch_id FROM stock_branch WHERE snap_date = $1) cov USING (branch_id)
LEFT JOIN stock_branch s ON s.snap_date = $1 AND s.branch_id = m.branch_id AND s.product_id = m.product_id
GROUP BY 1, 2"""


async def refresh_daily(con, d) -> None:
    """Joriy qoldiq sanasi uchun bajarilish tarixini yozadi (matritsa yoki qoldiq yangilangan bo'lsa)."""
    if d is None:
        return
    last = await con.fetchval("SELECT max(computed_at) FROM matrix_daily WHERE snap_date = $1", d)
    src = await con.fetchval("""SELECT greatest((SELECT max(finished_at) FROM matrix_syncs WHERE status = 'ok'),
                                                (SELECT max(started_at) FROM uploads WHERE kind = 'stock_branch'))""")
    if last is not None and (src is None or last >= src):
        return
    rows = await con.fetch(_POS_SQL, d)
    async with con.transaction():
        await con.execute("DELETE FROM matrix_daily WHERE snap_date = $1", d)
        await con.executemany("INSERT INTO matrix_daily(snap_date, branch_id, category_id, pos, ok) VALUES ($1,$2,$3,$4,$5)",
                              [(d, r["branch_id"], r["category_id"], r["pos"], r["ok"]) for r in rows])


# ---------------------------------------------------------------------------
async def _overview(con, f: Filters, user: User) -> dict:
    cats = await _cats(con, f, user)
    sn = await _snaps(con)
    sync = await con.fetchrow("SELECT finished_at, stats FROM matrix_syncs WHERE status = 'ok' ORDER BY id DESC LIMIT 1")
    out = {"has": bool(sync), "snap": {k: (v.isoformat() if v else None) for k, v in sn.items()}}
    if not sync:
        return out
    st = sync["stats"] if isinstance(sync["stats"], dict) else json.loads(sync["stats"] or "{}")
    out["sync"] = {"at": sync["finished_at"].isoformat(), "rows": st.get("rows")}
    if user.is_admin:
        out["sync"]["unbound"] = {"branches": st.get("branches", 0) - st.get("branches_ok", 0),
                                  "skus": st.get("skus_miss", 0)}
    if not sn["b"]:
        return out
    await refresh_daily(con, sn["b"])

    params: list = [cats, sn["b"], sn["w"]]
    bw = _where_branches(f, params)
    brand = ""
    if f.brands:
        params.append(f.brands)
        brand = f"AND p.brand = ANY(${len(params)}::text[])"
    rows = await con.fetch(f"""
WITH bb AS (SELECT b.id branch_id, b.warehouse_id FROM branches b WHERE {bw}),
m AS (SELECT mr.branch_id, mr.product_id, mr.category_id, max(mr.need_qty) need
      FROM matrix_rows mr JOIN bb USING (branch_id) JOIN products p USING (product_id)
      WHERE mr.category_id = ANY($1::int[]) {brand} GROUP BY 1, 2, 3),
j AS (SELECT m.branch_id, m.category_id, m.need, COALESCE(s.qty, 0) have, COALESCE(w.qty, 0) whq
      FROM m JOIN bb USING (branch_id)
      LEFT JOIN stock_branch s ON s.snap_date = $2 AND s.branch_id = m.branch_id AND s.product_id = m.product_id
      LEFT JOIN stock_wh w ON w.snap_date = $3 AND w.warehouse_id = bb.warehouse_id AND w.product_id = m.product_id)
SELECT branch_id, category_id, count(*) pos, count(*) FILTER (WHERE have >= need) ok,
       count(*) FILTER (WHERE have <= 0) miss, count(*) FILTER (WHERE have > 0 AND have < need) low,
       count(*) FILTER (WHERE have < need AND whq > 0) inwh
FROM j GROUP BY 1, 2""", *params)
    cov = {r[0] for r in await con.fetch("SELECT DISTINCT branch_id FROM stock_branch WHERE snap_date = $1", sn["b"])}
    vit = await _vitrina(con, cats, sn["b"])
    prev_d = await con.fetchval("SELECT max(snap_date) FROM matrix_daily WHERE snap_date < $1", sn["b"])
    prev = {}
    if prev_d:
        for r in await con.fetch("""SELECT branch_id, sum(pos) pos, sum(ok) ok FROM matrix_daily
                                    WHERE snap_date = $1 AND category_id = ANY($2::int[]) GROUP BY 1""", prev_d, cats):
            prev[r["branch_id"]] = (r["pos"], r["ok"])
    meta = {r["id"]: r for r in await con.fetch(
        """SELECT b.id, b.name, b.region_id, rg.name region, cl.name cluster, w.name wh
           FROM branches b JOIN regions rg ON rg.id = b.region_id LEFT JOIN clusters cl ON cl.id = b.cluster_id
           LEFT JOIN warehouses w ON w.id = b.warehouse_id""")}

    agg: dict[int, dict] = {}
    for r in rows:
        a = agg.setdefault(r["branch_id"], {"pos": 0, "ok": 0, "miss": 0, "low": 0, "inwh": 0, "cats": set()})
        for k in ("pos", "ok", "miss", "low", "inwh"):
            a[k] += r[k]
        a["cats"].add(r["category_id"])
    K = {"pos": 0, "ok": 0, "miss": 0, "low": 0, "inwh": 0, "vit_full": 0, "vit_total": 0, "ppos": 0, "pok": 0}
    branches, regions = [], {}
    for bid, a in agg.items():
        b = meta.get(bid)
        if not b:
            continue
        x = {"id": bid, "name": b["name"], "region_id": b["region_id"], "region": b["region"],
             "cluster": b["cluster"], "wh": b["wh"], "pos": a["pos"]}
        regions[b["region_id"]] = b["region"]
        if bid not in cov:
            x["nodata"] = True
            branches.append(x)
            continue
        vf = sum(1 for c in a["cats"] if (v := vit.get((bid, c))) and v[0] and v[1] >= v[0])
        vt = sum(1 for c in a["cats"] if (v := vit.get((bid, c))) and v[0])
        x.update(ok=a["ok"], miss=a["miss"], low=a["low"], inwh=a["inwh"], vit_full=vf, vit_total=vt,
                 pct=a["ok"] / a["pos"] * 100 if a["pos"] else None, d=None)
        if bid in prev and prev[bid][0] and not f.brands:
            x["d"] = x["pct"] - prev[bid][1] / prev[bid][0] * 100
            K["ppos"] += prev[bid][0]
            K["pok"] += prev[bid][1]
        for k in ("pos", "ok", "miss", "low", "inwh"):
            K[k] += a[k]
        K["vit_full"] += vf
        K["vit_total"] += vt
        branches.append(x)
    branches.sort(key=lambda x: (x.get("nodata", False), x.get("pct") if x.get("pct") is not None else 999, x["name"]))
    K["pct"] = K["ok"] / K["pos"] * 100 if K["pos"] else None
    K["d"] = (K["pct"] - K["pok"] / K["ppos"] * 100) if K["ppos"] and K["pct"] is not None else None
    out.update(kpi=K, branches=branches, prev=prev_d.isoformat() if prev_d else None,
               regions=[{"id": k, "name": v} for k, v in sorted(regions.items(), key=lambda kv: kv[1])])
    return out


async def _vitrina(con, cats: list[int], d, branch_id: int | None = None) -> dict:
    """(branch_id, category_id) → (sig'im, turgan model soni)."""
    cap = await con.fetch(f"""
        SELECT branch_id, category_id, sum(c)::float8 cap FROM (
            SELECT branch_id, category_id, COALESCE(sub0, ''), max(vitrina_cap) c FROM matrix_rows
            WHERE branch_id IS NOT NULL AND category_id = ANY($1::int[]) {'AND branch_id = $2' if branch_id else ''}
            GROUP BY 1, 2, 3) t GROUP BY 1, 2""", *([cats, branch_id] if branch_id else [cats]))
    on = await con.fetch(f"""
        SELECT s.branch_id, p.category_id, count(DISTINCT s.product_id) n FROM stock_branch s
        JOIN products p USING (product_id)
        WHERE s.snap_date = $2 AND s.qty > 0 AND p.category_id = ANY($1::int[]) {'AND s.branch_id = $3' if branch_id else ''}
        GROUP BY 1, 2""", *([cats, d, branch_id] if branch_id else [cats, d]))
    onm = {(r[0], r[1]): r[2] for r in on}
    return {(r[0], r[1]): (r[2], onm.get((r[0], r[1]), 0)) for r in cap}


async def _branch(con, f: MxReq, user: User) -> dict:
    if not f.branch_id:
        raise HTTPException(422, "branch_id kerak")
    cats = await _cats(con, f, user)
    sn = await _snaps(con)
    b = await con.fetchrow("""SELECT b.id, b.name, b.warehouse_id, rg.name region, cl.name cluster, w.name wh
                              FROM branches b JOIN regions rg ON rg.id = b.region_id
                              LEFT JOIN clusters cl ON cl.id = b.cluster_id LEFT JOIN warehouses w ON w.id = b.warehouse_id
                              WHERE b.id = $1""", f.branch_id)
    if not b:
        raise HTTPException(404, "Filial topilmadi")
    out = {"branch": {"id": b["id"], "name": b["name"], "region": b["region"], "cluster": b["cluster"], "wh": b["wh"]},
           "snap": {k: (v.isoformat() if v else None) for k, v in sn.items()}, "cats": [], "extra": None}
    if not sn["b"]:
        return out
    s_to = sn["s"] or sn["b"]
    s_from = s_to - timedelta(days=29)
    params = [cats, f.branch_id, sn["b"], sn["w"], b["warehouse_id"], s_from, s_to]
    brand = ""
    if f.brands:
        params.append(f.brands)
        brand = f"AND p.brand = ANY(${len(params)}::text[])"
    items = await con.fetch(f"""
WITH m AS (SELECT mr.product_id, mr.category_id, max(mr.need_qty)::float8 need, max(mr.product_name) mname
           FROM matrix_rows mr JOIN products p USING (product_id)
           WHERE mr.branch_id = $2 AND mr.category_id = ANY($1::int[]) {brand} GROUP BY 1, 2),
sl AS (SELECT product_id, sum(qty) q FROM sales_fact WHERE branch_id = $2 AND sale_date BETWEEN $6 AND $7 GROUP BY 1)
SELECT m.product_id, m.category_id, c.name cname, COALESCE(p.name, m.mname) name, m.need,
       COALESCE(s.qty, 0)::float8 have, COALESCE(w.qty, 0)::float8 whq, COALESCE(sl.q, 0)::float8 s30
FROM m JOIN categories c ON c.id = m.category_id LEFT JOIN products p ON p.product_id = m.product_id
LEFT JOIN stock_branch s ON s.snap_date = $3 AND s.branch_id = $2 AND s.product_id = m.product_id
LEFT JOIN stock_wh w ON w.snap_date = $4 AND w.warehouse_id = $5 AND w.product_id = m.product_id
LEFT JOIN sl ON sl.product_id = m.product_id""", *params)
    vit = await _vitrina(con, cats, sn["b"], f.branch_id)
    cs: dict[int, dict] = {}
    for r in items:
        c = cs.setdefault(r["category_id"], {"id": r["category_id"], "name": r["cname"], "pos": 0, "ok": 0, "miss": 0,
                                             "low": 0, "items": []})
        c["pos"] += 1
        if r["have"] >= r["need"]:
            c["ok"] += 1
            continue
        c["miss" if r["have"] <= 0 else "low"] += 1
        c["items"].append({"id": r["product_id"], "name": r["name"], "need": r["need"], "have": r["have"],
                           "wh": r["whq"], "s30": r["s30"]})
    for c in cs.values():
        v = vit.get((f.branch_id, c["id"]))
        c["cap"], c["on"] = (v if v else (None, 0))
        c["pct"] = c["ok"] / c["pos"] * 100 if c["pos"] else None
        c["items"].sort(key=lambda i: (i["have"] > 0, -(i["wh"] > 0), -i["s30"], i["name"]))
    out["cats"] = sorted(cs.values(), key=lambda c: (c["pct"] if c["pct"] is not None else 999, c["name"]))
    cov = await con.fetchval("SELECT EXISTS(SELECT 1 FROM stock_branch WHERE snap_date = $1 AND branch_id = $2)",
                             sn["b"], f.branch_id)
    out["nodata"] = not cov

    ep = [cats, f.branch_id, sn["b"], s_from, s_to]
    ebrand = ""
    if f.brands:
        ep.append(f.brands)
        ebrand = f"AND p.brand = ANY(${len(ep)}::text[])"
    ex = await con.fetch(f"""
WITH mc AS (SELECT DISTINCT category_id FROM matrix_rows WHERE branch_id = $2 AND category_id = ANY($1::int[])),
mp AS (SELECT DISTINCT product_id FROM matrix_rows WHERE branch_id = $2 AND product_id IS NOT NULL),
sl AS (SELECT product_id, sum(qty) q FROM sales_fact WHERE branch_id = $2 AND sale_date BETWEEN $4 AND $5 GROUP BY 1)
SELECT p.product_id, p.name, c.name cname, s.qty::float8 qty, COALESCE(s.cost_uzs, 0)::float8 cost,
       COALESCE(sl.q, 0)::float8 s30
FROM stock_branch s JOIN products p USING (product_id) JOIN mc ON mc.category_id = p.category_id
JOIN categories c ON c.id = p.category_id LEFT JOIN sl ON sl.product_id = s.product_id
WHERE s.snap_date = $3 AND s.branch_id = $2 AND s.qty > 0 AND s.product_id NOT IN (SELECT product_id FROM mp)
  {ebrand}
ORDER BY s.cost_uzs DESC NULLS LAST""", *ep)
    out["extra"] = {"qty": sum(r["qty"] for r in ex), "cost": sum(r["cost"] for r in ex), "n": len(ex),
                    "items": [{"id": r["product_id"], "name": r["name"], "cat": r["cname"], "qty": r["qty"],
                               "cost": r["cost"], "s30": r["s30"]} for r in ex[:60]]}
    return out


@router.post("/overview")
async def overview(req: MxReq, request: Request, user: User = Depends(current_user)):
    async def run():
        async with request.app.state.pool.acquire() as con:
            return await _overview(con, req, user)
    payload = req.model_dump(mode="json", exclude={"date_from", "date_to", "compare", "share_by", "branch_id"})
    payload["_adm"] = user.is_admin
    return await _M().cached(request, user, "mx_overview", payload, run)


@router.post("/branch")
async def branch(req: MxReq, request: Request, user: User = Depends(current_user)):
    async def run():
        async with request.app.state.pool.acquire() as con:
            return await _branch(con, req, user)
    payload = req.model_dump(mode="json", exclude={"date_from", "date_to", "compare", "share_by"})
    return await _M().cached(request, user, "mx_branch", payload, run)
