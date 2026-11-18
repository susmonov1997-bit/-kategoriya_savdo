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
from typing import Literal
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request

from api_auth import User, current_user
from api_schemas import Filters

router = APIRouter(prefix="/api/matrix")


_MINI = re.compile(r"\b(mini|мини)\b", re.I)


def is_mini(name: str | None) -> bool:
    """Mini do'kon — nomida «mini» / «мини» so'zi bor («Andijon-Navro'z-Mall mini do'koni»)."""
    return bool(_MINI.search(name or ""))


def _M():
    import api_main
    return api_main


class MxReq(Filters):
    days: Literal[7, 10, 14, 21, 30] = 14          # zakaz: necha kunlik savdo zahirasi
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


def _pf(f: Filters) -> bool:
    """Tovar filtri bormi (brend, status, SKU, mas'ul, xususiyat) — bunda vitrina va ▲▼ solishtirib bo'lmaydi."""
    return bool(f.brands or f.statuses or f.product_ids or f.owners or any(v for v in f.attrs.values()))


async def _flt(con, f: Filters, user: User):
    """Filtr SQL yig'uvchisi (api_stock._SQL): pd — tovarlar (doira + barcha tovar filtrlari), bb — filiallar."""
    from api_stock import _SQL
    cats, attrs, _ = await _M().scope_ctx(con, f, user)
    return _SQL(f, cats, attrs)


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

    q = await _flt(con, f, user)
    w_ = q.ctes()
    D, W = q.p(sn["b"]), q.p(sn["w"])
    rows = await con.fetch(f"""
WITH {w_},
m AS (SELECT mr.branch_id, mr.product_id, mr.category_id, max(mr.need_qty) need
      FROM matrix_rows mr JOIN bb USING (branch_id) JOIN pd USING (product_id)
      WHERE mr.category_id IS NOT NULL GROUP BY 1, 2, 3),
j AS (SELECT m.branch_id, m.category_id, m.need, COALESCE(s.qty, 0) have, COALESCE(w.qty, 0) whq
      FROM m JOIN bb USING (branch_id)
      LEFT JOIN stock_branch s ON s.snap_date = {D} AND s.branch_id = m.branch_id AND s.product_id = m.product_id
      LEFT JOIN stock_wh w ON w.snap_date = {W} AND w.warehouse_id = bb.warehouse_id AND w.product_id = m.product_id)
SELECT branch_id, category_id, count(*) pos, count(*) FILTER (WHERE have >= need) ok,
       count(*) FILTER (WHERE have <= 0) miss, count(*) FILTER (WHERE have > 0 AND have < need) low,
       count(*) FILTER (WHERE have < need AND whq > 0) inwh,
       count(*) FILTER (WHERE have <= 0 AND whq > 0) missw, count(*) FILTER (WHERE have > 0) onm
FROM j GROUP BY 1, 2""", *q.params)
    cov = {r[0] for r in await con.fetch("SELECT DISTINCT branch_id FROM stock_branch WHERE snap_date = $1", sn["b"])}
    vit = await _vitrina(con, cats, sn["b"])
    prev_d = await con.fetchval("SELECT max(snap_date) FROM matrix_daily WHERE snap_date < $1", sn["b"])
    prev, pc = {}, {}
    if prev_d:
        for r in await con.fetch("""SELECT branch_id, category_id, pos, ok FROM matrix_daily
                                    WHERE snap_date = $1 AND category_id = ANY($2::int[])""", prev_d, cats):
            pc[(r["branch_id"], r["category_id"])] = (r["pos"], r["ok"])
            o = prev.get(r["branch_id"], (0, 0))
            prev[r["branch_id"]] = (o[0] + r["pos"], o[1] + r["ok"])
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
    KZ = lambda: {"pos": 0, "ok": 0, "miss": 0, "low": 0, "inwh": 0, "vit_full": 0, "vit_total": 0,  # noqa: E731
                  "ppos": 0, "pok": 0, "branches": 0, "nodata": 0}
    KS = {"f": KZ(), "m": KZ()}
    branches, regions = [], {"f": {}, "m": {}}
    for bid, a in agg.items():
        b = meta.get(bid)
        if not b:
            continue
        g = "m" if is_mini(b["name"]) else "f"
        K = KS[g]
        x = {"id": bid, "name": b["name"], "region_id": b["region_id"], "region": b["region"], "g": g,
             "cluster": b["cluster"], "wh": b["wh"], "pos": a["pos"]}
        regions[g][b["region_id"]] = b["region"]
        K["branches"] += 1
        if bid not in cov:
            x["nodata"] = True
            K["nodata"] += 1
            branches.append(x)
            continue
        vf = sum(1 for c in a["cats"] if (v := vit.get((bid, c))) and v[0] and v[1] >= v[0])
        vt = sum(1 for c in a["cats"] if (v := vit.get((bid, c))) and v[0])
        x.update(ok=a["ok"], miss=a["miss"], low=a["low"], inwh=a["inwh"], vit_full=vf, vit_total=vt,
                 pct=a["ok"] / a["pos"] * 100 if a["pos"] else None, d=None)
        if bid in prev and prev[bid][0] and not _pf(f):
            x["d"] = x["pct"] - prev[bid][1] / prev[bid][0] * 100
            K["ppos"] += prev[bid][0]
            K["pok"] += prev[bid][1]
        for k in ("pos", "ok", "miss", "low", "inwh"):
            K[k] += a[k]
        K["vit_full"] += vf
        K["vit_total"] += vt
        branches.append(x)
    branches.sort(key=lambda x: (x.get("nodata", False), x.get("pct") if x.get("pct") is not None else 999, x["name"]))
    for K in KS.values():
        K["pct"] = K["ok"] / K["pos"] * 100 if K["pos"] else None
        K["d"] = (K["pct"] - K["pok"] / K["ppos"] * 100) if K["ppos"] and K["pct"] is not None else None
    # filial × kategoriya (Mini App kategoriya kesimi va vitrina tahlili uchun):
    # [filial, kategoriya, pos, ok, yo'q, kam, omborda(yo'q+kam), omborda(yo'q), turgan matritsa SKU,
    #  vitrina sig'imi, turgan model, oldingi pos, oldingi ok]
    out["bc"] = [[r["branch_id"], r["category_id"], r["pos"], r["ok"], r["miss"], r["low"], r["inwh"], r["missw"],
                  r["onm"], *(vit.get((r["branch_id"], r["category_id"])) or (None, 0)),
                  *((pc.get((r["branch_id"], r["category_id"])) if not _pf(f) else None) or (None, None))]
                 for r in rows if r["branch_id"] in cov and r["branch_id"] in meta]
    out["cats"] = [{"id": r["id"], "name": r["name"]} for r in await con.fetch(
        "SELECT id, name FROM categories WHERE id = ANY($1::int[]) ORDER BY name", list({r["category_id"] for r in rows}))]
    out.update(kpi=KS["f"], kpi_by=KS, branches=branches, prev=prev_d.isoformat() if prev_d else None,
               regions={g: [{"id": k, "name": v} for k, v in sorted(r.items(), key=lambda kv: kv[1])]
                        for g, r in regions.items()})
    return out


def _cap(subs: list[tuple]) -> float | None:
    """Kategoriya vitrina sig'imi. Jadvalda sig'im ba'zan kategoriyaga (har Подкатегория qatorida bir xil son —
    Совутгичлар 70/70/70), ba'zan Подкатегория'ga (Сплит 12, Устунли 2) yozilgan.
    Sonlar har xil → yig'indi; hammasi bir xil → bitta son yoki yig'indidan qaysi biri matritsa
    pozitsiyalari soniga yaqin bo'lsa (sig'im odatda pozitsiyalar soniga yaqin)."""
    caps = [c for c, _ in subs if c is not None]
    if not caps:
        return None
    total, n = sum(caps), sum(k for _, k in subs)
    if len(caps) == 1 or len(set(caps)) > 1:
        return total
    one = caps[0]
    return total if abs(total - n) < abs(one - n) else one


async def _vitrina(con, cats: list[int], d, branch_id: int | None = None) -> dict:
    """(branch_id, category_id) → (sig'im, turgan model soni)."""
    sub = await con.fetch(f"""
        SELECT branch_id, category_id, COALESCE(sub0, '') sub0, max(vitrina_cap)::float8 c, count(*) n FROM matrix_rows
        WHERE branch_id IS NOT NULL AND category_id = ANY($1::int[]) {'AND branch_id = $2' if branch_id else ''}
        GROUP BY 1, 2, 3""", *([cats, branch_id] if branch_id else [cats]))
    grp: dict[tuple, list] = {}
    for r in sub:
        grp.setdefault((r[0], r[1]), []).append((r["c"], r["n"]))
    cap = [(k[0], k[1], _cap(v)) for k, v in grp.items()]
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
    q = await _flt(con, f, user)
    w_ = q.ctes()
    B, D, W, WH, S1, S2 = (q.p(f.branch_id), q.p(sn["b"]), q.p(sn["w"]), q.p(b["warehouse_id"]), q.p(s_from), q.p(s_to))
    items = await con.fetch(f"""
WITH {w_},
m AS (SELECT mr.product_id, mr.category_id, max(mr.need_qty)::float8 need, max(mr.product_name) mname
      FROM matrix_rows mr JOIN pd USING (product_id)
      WHERE mr.branch_id = {B} AND mr.category_id IS NOT NULL GROUP BY 1, 2),
sl AS (SELECT product_id, sum(qty) q FROM sales_fact WHERE branch_id = {B} AND sale_date BETWEEN {S1} AND {S2} GROUP BY 1)
SELECT m.product_id, m.category_id, c.name cname, COALESCE(p.name, m.mname) name, m.need,
       COALESCE(s.qty, 0)::float8 have, COALESCE(w.qty, 0)::float8 whq, COALESCE(sl.q, 0)::float8 s30
FROM m JOIN categories c ON c.id = m.category_id LEFT JOIN products p ON p.product_id = m.product_id
LEFT JOIN stock_branch s ON s.snap_date = {D} AND s.branch_id = {B} AND s.product_id = m.product_id
LEFT JOIN stock_wh w ON w.snap_date = {W} AND w.warehouse_id = {WH}::int AND w.product_id = m.product_id
LEFT JOIN sl ON sl.product_id = m.product_id""", *q.params)
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

    q = await _flt(con, f, user)
    w_ = q.ctes()
    B, D, S1, S2 = q.p(f.branch_id), q.p(sn["b"]), q.p(s_from), q.p(s_to)
    ex = await con.fetch(f"""
WITH {w_},
mc AS (SELECT DISTINCT category_id FROM matrix_rows WHERE branch_id = {B} AND category_id IS NOT NULL),
mp AS (SELECT DISTINCT product_id FROM matrix_rows WHERE branch_id = {B} AND product_id IS NOT NULL),
sl AS (SELECT product_id, sum(qty) q FROM sales_fact WHERE branch_id = {B} AND sale_date BETWEEN {S1} AND {S2} GROUP BY 1)
SELECT p.product_id, p.pname AS name, c.name cname, s.qty::float8 qty, COALESCE(s.cost_uzs, 0)::float8 cost,
       COALESCE(sl.q, 0)::float8 s30
FROM stock_branch s JOIN pd p USING (product_id) JOIN mc ON mc.category_id = p.category_id
JOIN categories c ON c.id = p.category_id LEFT JOIN sl ON sl.product_id = s.product_id
WHERE s.snap_date = {D} AND s.branch_id = {B} AND s.qty > 0 AND s.product_id NOT IN (SELECT product_id FROM mp)
ORDER BY s.cost_uzs DESC NULLS LAST""", *q.params)
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


# ---------------------------------------------------------------------------
# Zakaz: ombor × SKU (faqat matritsa SKU'lari)
#   matritsa uchun = Σ filiallar (shu ombor) max(0, Turishi − filial qoldig'i)   — qoldiq fayli bor filiallar
#   2 hafta savdo  = shu ombor filiallarining oxirgi 14 kunlik savdosi (oxirgi savdo sanasigacha)
#   zakaz          = max(0, matritsa uchun + 2 hafta savdo − ombor qoldig'i)
ORDER_DAYS = 14          # zahira (kun), standart
MIN_Q7 = 3               # 7 kunda kamida shuncha sotilsa — 7 kunlik sur'at hisobga olinadi (qoldiq tabidagidek)


USD_MAX_UNIT = 10_000   # birlik narxi bundan katta bo'lsa — so'mda yozilgan deb olinadi (savdo faylidagi qoida kabi)


async def unit_prices(con, sn: dict, pids: list[int]) -> dict[int, tuple[float, str]]:
    """SKU birlik narxi, $ (zakaz summasi uchun):
    1) ombor qoldig'idagi «A сектор учун жорий сотиш нархи» (= ta'minotchi narxi), so'nggi sana, omborlar o'rtachasi;
       narx 10 000 dan katta bo'lsa — so'mda deb olinadi va bot kursi bilan $ ga o'tkaziladi;
    2) bo'lmasa — filial qoldig'idagi kirim narxi (tannarx, so'm) ÷ bot kursi."""
    if not pids:
        return {}
    out: dict[int, tuple[float, str]] = {}
    if sn.get("w"):
        rate = float(await con.fetchval("SELECT rate FROM fx_periods WHERE $1 >= valid_from AND $1 < valid_to", sn["w"]) or 0)
        for r in await con.fetch("""SELECT product_id, qty::float8 q, value_usd::float8 v FROM stock_wh
                                    WHERE snap_date = $1 AND product_id = ANY($2::bigint[]) AND qty > 0 AND value_usd > 0""",
                                 sn["w"], pids):
            u = r["v"] / r["q"]
            if u > USD_MAX_UNIT:
                if not rate:
                    continue
                u /= rate
            a = out.setdefault(r["product_id"], [0.0, 0.0])
            a[0] += u * r["q"]
            a[1] += r["q"]
        out = {k: (v[0] / v[1], "w") for k, v in out.items() if v[1]}
    rest = [p for p in pids if p not in out]
    if rest and sn.get("b"):
        rate = float(await con.fetchval("SELECT rate FROM fx_periods WHERE $1 >= valid_from AND $1 < valid_to", sn["b"]) or 0)
        if rate:
            for r in await con.fetch("""SELECT product_id, sum(cost_uzs)::float8 c, sum(qty)::float8 q FROM stock_branch
                                        WHERE snap_date = $1 AND product_id = ANY($2::bigint[]) AND qty > 0 AND cost_uzs > 0
                                        GROUP BY 1""", sn["b"], rest):
                if r["q"]:
                    out[r["product_id"]] = (r["c"] / r["q"] / rate, "b")
    return out


async def order_data(con, q, days: int = ORDER_DAYS) -> dict:
    """q — _flt() natijasi (tovar va filial filtrlari bilan).
    Zahira = kunlik savdo × days; kunlik savdo — oxirgi 30 kunlik va 7 kunlik o'rtachaning kattasi
    (7 kunda kamida MIN_Q7 dona sotilgan bo'lsa), qoldiq tabidagi qoida bilan bir xil."""
    sn = await _snaps(con)
    out = {"snap": {k: (v.isoformat() if v else None) for k, v in sn.items()}, "whs": [], "days": days}
    if not sn["b"]:
        return out
    s_to = sn["s"] or sn["b"]
    s_from = s_to - timedelta(days=29)
    out["sales"] = [s_from.isoformat(), s_to.isoformat()]
    w_ = q.ctes()
    D, W, S1, S2, S7 = q.p(sn["b"]), q.p(sn["w"]), q.p(s_from), q.p(s_to), q.p(s_to - timedelta(days=6))
    rows = await con.fetch(f"""
WITH {w_},
wb AS (SELECT branch_id, warehouse_id FROM bb WHERE warehouse_id IS NOT NULL),
cov AS (SELECT DISTINCT branch_id FROM stock_branch WHERE snap_date = {D}),
m AS (SELECT mr.branch_id, mr.product_id, max(mr.need_qty) need FROM matrix_rows mr
      JOIN wb USING (branch_id) JOIN cov USING (branch_id) JOIN pd USING (product_id)
      WHERE mr.category_id IS NOT NULL GROUP BY 1, 2),
f AS (SELECT wb.warehouse_id, m.product_id, sum(GREATEST(0, m.need - COALESCE(s.qty, 0))) fill,
             count(*) FILTER (WHERE m.need > COALESCE(s.qty, 0)) nbr
      FROM m JOIN wb USING (branch_id)
      LEFT JOIN stock_branch s ON s.snap_date = {D} AND s.branch_id = m.branch_id AND s.product_id = m.product_id
      GROUP BY 1, 2),
mp AS (SELECT DISTINCT wb.warehouse_id, m.product_id FROM m JOIN wb USING (branch_id)),
sl AS (SELECT wb.warehouse_id, e.product_id, sum(e.qty) q, sum(e.qty) FILTER (WHERE e.sale_date >= {S7}) q7
       FROM sales_fact e JOIN wb USING (branch_id)
       JOIN mp ON mp.warehouse_id = wb.warehouse_id AND mp.product_id = e.product_id
       WHERE e.sale_date BETWEEN {S1} AND {S2} GROUP BY 1, 2),
w AS (SELECT warehouse_id, product_id, qty FROM stock_wh WHERE snap_date = {W})
SELECT mp.warehouse_id, mp.product_id, COALESCE(f.fill, 0)::float8 m, COALESCE(f.nbr, 0) nbr,
       GREATEST(0, COALESCE(sl.q, 0))::float8 q30, GREATEST(0, COALESCE(sl.q7, 0))::float8 q7,
       GREATEST(0, COALESCE(w.qty, 0))::float8 h,
       p.name, p.brand, c.name cname
FROM mp LEFT JOIN f USING (warehouse_id, product_id) LEFT JOIN sl USING (warehouse_id, product_id)
LEFT JOIN w USING (warehouse_id, product_id)
JOIN products p ON p.product_id = mp.product_id JOIN categories c ON c.id = p.category_id""", *q.params)
    names = {r["id"]: r["name"] for r in await con.fetch("SELECT id, name FROM warehouses")}
    price = await unit_prices(con, sn, list({r["product_id"] for r in rows}))
    q2 = q.__class__(q.f, q.cats, q.attrs)
    nbr = {r["warehouse_id"]: r["n"] for r in await con.fetch(
        f"WITH {q2.ctes()} SELECT warehouse_id, count(*) n FROM bb WHERE warehouse_id IS NOT NULL GROUP BY 1", *q2.params)}
    W: dict[int, dict] = {}
    for r in rows:
        w = W.setdefault(r["warehouse_id"], {"id": r["warehouse_id"], "name": names.get(r["warehouse_id"], "?"),
                                             "short": names.get(r["warehouse_id"], "?").replace(" омбори", ""),
                                             "branches": nbr.get(r["warehouse_id"], 0),
                                             "m": 0.0, "s": 0.0, "h": 0.0, "z": 0.0, "usd": 0.0, "nop": 0,
                                             "pos": 0, "items": []})
        rate = max(r["q30"] / 30, r["q7"] / 7 if r["q7"] >= MIN_Q7 else 0.0)   # kunlik savdo
        sv = rate * days
        z = max(0.0, round(r["m"] + sv - r["h"]))
        w["m"] += r["m"]
        w["s"] += sv
        w["h"] += min(r["h"], r["m"] + sv)               # zakazni kamaytirgan qismi
        if z > 0:
            pr, src = price.get(r["product_id"], (None, None))
            w["z"] += z
            w["pos"] += 1
            if pr is None:
                w["nop"] += 1
            else:
                w["usd"] += z * pr
            w["items"].append({"id": r["product_id"], "name": r["name"], "brand": r["brand"], "cat": r["cname"],
                               "m": r["m"], "s": sv, "r": rate, "h": r["h"], "z": z, "nbr": r["nbr"],
                               "p": pr, "src": src, "usd": z * pr if pr is not None else None})
    for w in W.values():
        w["items"].sort(key=lambda i: (-i["z"], i["name"]))
    out["whs"] = sorted(W.values(), key=lambda w: -w["z"])
    out["total"] = {k: sum(w[k] for w in W.values()) for k in ("m", "s", "h", "z", "pos", "usd", "nop")}
    return out


async def send_list(con, q) -> list:
    """Ombordan yuborish: filialdagi yetishmovchilik (Turishi − qoldiq), ombor qoldig'i yetguncha —
    avval 14 kunda ko'p sotgan filialga."""
    sn = await _snaps(con)
    if not sn["b"] or not sn["w"]:
        return []
    s_to = sn["s"] or sn["b"]
    w_ = q.ctes()
    D, W, S1, S2 = q.p(sn["b"]), q.p(sn["w"]), q.p(s_to - timedelta(days=ORDER_DAYS - 1)), q.p(s_to)
    rows = await con.fetch(f"""
WITH {w_},
wb AS (SELECT b.id branch_id, b.name bname, b.warehouse_id, rg.name region FROM branches b
       JOIN bb ON bb.branch_id = b.id JOIN regions rg ON rg.id = b.region_id WHERE b.warehouse_id IS NOT NULL),
cov AS (SELECT DISTINCT branch_id FROM stock_branch WHERE snap_date = {D}),
m AS (SELECT mr.branch_id, mr.product_id, max(mr.need_qty)::float8 need FROM matrix_rows mr
      JOIN cov USING (branch_id) JOIN pd USING (product_id) WHERE mr.category_id IS NOT NULL GROUP BY 1, 2),
sl AS (SELECT branch_id, product_id, sum(qty) q FROM sales_fact WHERE sale_date BETWEEN {S1} AND {S2} GROUP BY 1, 2)
SELECT wb.warehouse_id, wb.region, wb.bname, m.product_id, p.name, p.brand, c.name cname, m.need,
       COALESCE(s.qty, 0)::float8 have, w.qty::float8 whq, COALESCE(sl.q, 0)::float8 s14
FROM m JOIN wb USING (branch_id)
JOIN stock_wh w ON w.snap_date = {W} AND w.warehouse_id = wb.warehouse_id AND w.product_id = m.product_id AND w.qty > 0
LEFT JOIN stock_branch s ON s.snap_date = {D} AND s.branch_id = m.branch_id AND s.product_id = m.product_id
LEFT JOIN sl ON sl.branch_id = m.branch_id AND sl.product_id = m.product_id
JOIN products p ON p.product_id = m.product_id JOIN categories c ON c.id = p.category_id
WHERE COALESCE(s.qty, 0) < m.need
ORDER BY wb.warehouse_id, m.product_id, s14 DESC, wb.bname""", *q.params)
    names = {r["id"]: r["name"] for r in await con.fetch("SELECT id, name FROM warehouses")}
    left: dict[tuple, float] = {}
    out = []
    for r in rows:
        k = (r["warehouse_id"], r["product_id"])
        rest = left.setdefault(k, r["whq"])
        q = min(r["need"] - r["have"], rest)
        if q <= 0:
            continue
        left[k] = rest - q
        out.append([names.get(r["warehouse_id"], "?"), r["region"], r["bname"], r["cname"], r["brand"], r["product_id"],
                    r["name"], r["need"], r["have"], q])
    return out


def order_xlsx(d: dict, ship: list) -> bytes:
    import io
    from openpyxl import Workbook

    from export_missing import BOLD, HEAD_FILL, _autowidth

    wb = Workbook()

    def sheet(title, head, rows, first=False):
        ws = wb.active if first else wb.create_sheet(title[:31])
        ws.title = title[:31]
        ws.append(head)
        for i in range(1, len(head) + 1):
            c = ws.cell(row=1, column=i)
            c.font, c.fill = BOLD, HEAD_FILL
        for r in rows:
            ws.append(list(r))
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = ws.dimensions
        _autowidth(ws)
        return ws

    n = d.get("days", ORDER_DAYS)
    zl = f"{n} kunlik zahira"
    SRC = {"w": "ombor (A sektor)", "b": "filial (kirim)"}
    sheet("Jami", ["Ombor", "Pozitsiya", "Matritsa uchun", zl, "Omborda (hisobga olingan)", "Zakaz, dona",
                   "Zakaz, $", "Narxi yo'q (poz)"],
          [[w["name"], w["pos"], round(w["m"]), round(w["s"]), round(w["h"]), round(w["z"]), round(w["usd"], 2), w["nop"]]
           for w in d["whs"]], first=True)
    for w in d["whs"]:
        sheet(w["short"], ["Kategoriya", "Brend", "Tovar ID", "Tovar", "Matritsa uchun", "Filiallar (yetishmaydi)",
                           "Kunlik savdo", zl, "Omborda", "Zakaz, dona", "Narx, $", "Narx manbai", "Zakaz, $"],
              [[i["cat"], i["brand"], i["id"], i["name"], round(i["m"]), i["nbr"], round(i["r"], 2), round(i["s"]),
                round(i["h"]), round(i["z"]),
                round(i["p"], 2) if i["p"] is not None else None, SRC.get(i["src"], "yo'q"),
                round(i["usd"], 2) if i["usd"] is not None else None] for i in w["items"]])
    sheet("Ombordan yuborish", ["Ombor", "Hudud", "Filial", "Kategoriya", "Brend", "Tovar ID", "Tovar", "Turishi",
                                "Filialda bor", "Yuborish, dona"], ship)
    ws = wb.create_sheet("Izoh")
    s = d.get("sales") or ["", ""]
    for line in [f"Zakaz = matritsa uchun + {n} kunlik zahira − ombor qoldig'i (manfiy bo'lsa — 0)",
                 f"{n} kunlik zahira = kunlik savdo × {n}; kunlik savdo — oxirgi 30 kunlik va 7 kunlik o'rtachaning "
                 f"kattasi (7 kunda kamida {MIN_Q7} dona sotilgan bo'lsa)",
                 "Matritsa uchun = shu ombor filiallarida Turishi − filial qoldig'i (yetishmagan qismi)",
                 f"Savdo (30 kun): {s[0]} — {s[1]} · filial qoldig'i: {d['snap'].get('b')} · ombor qoldig'i: {d['snap'].get('w')}",
                 "Faqat matritsadagi SKU'lar; ombori biriktirilmagan va qoldiq faylida yo'q filiallar hisobga olinmaydi.",
                 "«Ombordan yuborish» — ombor qoldig'i yetguncha, avval ko'p sotgan filialga.",
                 "Narx, $: ombor qoldig'idagi «A сектор учун жорий сотиш нархи»; bo'lmasa — filial qoldig'idagi kirim narxi. "
                 "So'mdagi narx (10 000 dan katta) bot kursi bilan $ ga o'tkazilgan."]:
        ws.append([line])
    ws.column_dimensions["A"].width = 110
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def all_cats(con) -> list[int]:
    return [r[0] for r in await con.fetch("SELECT id FROM categories")]


async def all_flt(con):
    """Bot uchun: barcha kategoriyalar, filtrsiz."""
    from datetime import date
    return await _flt(con, MxReq(scope="a:", date_from=date.today(), date_to=date.today()),
                      User(id=0, is_admin=True, level="admin"))


@router.post("/order")
async def order(req: MxReq, request: Request, user: User = Depends(current_user)):
    async def run():
        async with request.app.state.pool.acquire() as con:
            d = await order_data(con, await _flt(con, req, user), req.days)
            for w in d["whs"]:                        # Mini App'ga har ombordan eng katta 100 tasi
                w["more"] = max(0, len(w["items"]) - 100)
                w["items"] = w["items"][:100]
            return d
    payload = req.model_dump(mode="json", exclude={"date_from", "date_to", "compare", "share_by", "branch_id"})
    return await _M().cached(request, user, "mx_order", payload, run)


@router.post("/order_export")
async def order_export(req: MxReq, request: Request, user: User = Depends(current_user)):
    import config
    from aiogram import Bot
    from aiogram.types import BufferedInputFile
    async with request.app.state.pool.acquire() as con:
        d = await order_data(con, await _flt(con, req, user), req.days)
        ship = await send_list(con, await _flt(con, req, user))
    if not d["whs"]:
        raise HTTPException(404, "Zakaz uchun ma'lumot yo'q")
    if not config.BOT_TOKEN:
        raise HTTPException(503, "Bot sozlanmagan")
    bot = Bot(config.BOT_TOKEN)
    try:
        await bot.send_document(user.id, BufferedInputFile(order_xlsx(d, ship), filename=order_fname()),
                                caption="🛒 Zakaz: har ombor alohida varaq + «Ombordan yuborish»")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"Botga yuborib bo'lmadi — botga /start bosilganini tekshiring ({e})")
    finally:
        await bot.session.close()
    return {"ok": True}


def order_fname() -> str:
    from datetime import datetime, timezone
    return f"zakaz_{datetime.now(timezone(timedelta(hours=5))):%d.%m.%Y}.xlsx"


async def bot_digest(con) -> dict:
    """06:00 xabari uchun: filiallar va mini do'konlar bo'yicha holat + zakaz (barcha kategoriyalar)."""
    from datetime import date
    u = User(id=0, is_admin=True, level="admin")
    f = MxReq(scope="a:", date_from=date.today(), date_to=date.today())
    ov = await _overview(con, f, u)
    od = await order_data(con, await _flt(con, f, u)) if ov.get("snap", {}).get("b") else {"whs": [], "total": None}
    return {"ov": ov, "order": od}
