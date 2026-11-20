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
    mode: Literal["full", "mx"] = "full"          # zakaz turi: matritsa + zahira | faqat matritsa
    branch_id: int | None = None
    hd: Literal[14, 30, 90] = 30                  # kunlik chartlar: necha kun


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
    payload = req.model_dump(mode="json", exclude={"date_from", "date_to", "compare", "share_by", "branch_id", "hd"})
    payload["_adm"] = user.is_admin
    return await _M().cached(request, user, "mx_overview", payload, run)


@router.post("/branch")
async def branch(req: MxReq, request: Request, user: User = Depends(current_user)):
    async def run():
        async with request.app.state.pool.acquire() as con:
            return await _branch(con, req, user)
    payload = req.model_dump(mode="json", exclude={"date_from", "date_to", "compare", "share_by", "hd"})
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


async def order_data(con, q, days: int = ORDER_DAYS, mode: str = "full") -> dict:
    """q — _flt() natijasi (tovar va filial filtrlari bilan).
    Zahira = kunlik savdo × days; kunlik savdo — oxirgi 30 kunlik va 7 kunlik o'rtachaning kattasi
    (7 kunda kamida MIN_Q7 dona sotilgan bo'lsa), qoldiq tabidagi qoida bilan bir xil."""
    sn = await _snaps(con)
    out = {"snap": {k: (v.isoformat() if v else None) for k, v in sn.items()}, "whs": [], "days": days, "mode": mode}
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
                                             "zm": 0.0, "usdm": 0.0,
                                             "pos": 0, "items": []})
        rate = max(r["q30"] / 30, r["q7"] / 7 if r["q7"] >= MIN_Q7 else 0.0)   # kunlik savdo
        sv = rate * days if mode == "full" else 0.0     # «faqat matritsa» — zahirasiz
        z = max(0.0, round(r["m"] + sv - r["h"]))
        zm = max(0.0, round(r["m"] - r["h"]))              # ma'lumot uchun: faqat matritsani to'ldirishga
        if zm > 0:
            pm = price.get(r["product_id"], (None, None))[0]
            w["zm"] = w.get("zm", 0.0) + zm
            w["usdm"] = w.get("usdm", 0.0) + (zm * pm if pm is not None else 0.0)
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
    # kategoriya → brend (har ombor bo'yicha dona ham)
    C: dict[str, dict] = {}
    for w in out["whs"]:
        for i in w["items"]:
            c = C.setdefault(i["cat"], {"name": i["cat"], "z": 0.0, "usd": 0.0, "pos": 0, "wh": {}, "brands": {}})
            b = c["brands"].setdefault(i["brand"] or "—", {"name": i["brand"] or "—", "z": 0.0, "usd": 0.0, "pos": 0, "wh": {},
                                                           "skus": {}})
            k = b["skus"].setdefault(i["id"], {"id": i["id"], "name": i["name"], "z": 0.0, "usd": 0.0, "m": 0.0, "wh": {}})
            k["m"] += i["m"]
            for x in (c, b, k):
                x["z"] += i["z"]
                x["usd"] += i["usd"] or 0.0
                x["wh"][w["short"]] = x["wh"].get(w["short"], 0.0) + i["z"]
            c["pos"] += 1
            b["pos"] += 1
    for c in C.values():
        for b in c["brands"].values():
            b["skus"] = sorted(b["skus"].values(), key=lambda k: -k["z"])
        c["brands"] = sorted(c["brands"].values(), key=lambda b: -b["z"])
    out["cats"] = sorted(C.values(), key=lambda c: -c["z"])
    out["total"] = {k: sum(w[k] for w in W.values()) for k in ("m", "s", "h", "z", "pos", "usd", "nop", "zm", "usdm")}
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
k AS MATERIALIZED (       -- yetishmayotgan va omborda bor (kichik to'plam) — savdo faqat shular uchun olinadi
    SELECT wb.warehouse_id, wb.region, wb.bname, m.branch_id, m.product_id, m.need,
           COALESCE(s.qty, 0)::float8 have, w.qty::float8 whq
    FROM m JOIN wb USING (branch_id)
    JOIN stock_wh w ON w.snap_date = {W} AND w.warehouse_id = wb.warehouse_id AND w.product_id = m.product_id AND w.qty > 0
    LEFT JOIN stock_branch s ON s.snap_date = {D} AND s.branch_id = m.branch_id AND s.product_id = m.product_id
    WHERE COALESCE(s.qty, 0) < m.need),
sl AS (SELECT e.branch_id, e.product_id, sum(e.qty) q FROM sales_fact e JOIN k USING (branch_id, product_id)
       WHERE e.sale_date BETWEEN {S1} AND {S2} GROUP BY 1, 2)
SELECT k.warehouse_id, k.region, k.bname, k.product_id, p.name, p.brand, c.name cname, k.need, k.have, k.whq,
       COALESCE(sl.q, 0)::float8 s14
FROM k LEFT JOIN sl ON sl.branch_id = k.branch_id AND sl.product_id = k.product_id
JOIN products p ON p.product_id = k.product_id JOIN categories c ON c.id = p.category_id
ORDER BY k.warehouse_id, k.product_id, s14 DESC, k.bname""", *q.params)
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
    whn = [w["short"] for w in d["whs"]]
    cb = []
    for c in d.get("cats", []):
        for b in c["brands"]:
            cb.append([c["name"], b["name"], b["pos"], round(b["z"]), round(b["usd"], 2)] + [round(b["wh"].get(x, 0)) for x in whn])
    sheet("Kategoriya × brend", ["Kategoriya", "Brend", "Pozitsiya", "Zakaz, dona", "Zakaz, $"] + [f"{x}, dona" for x in whn], cb)
    sheet("Ombordan yuborish", ["Ombor", "Hudud", "Filial", "Kategoriya", "Brend", "Tovar ID", "Tovar", "Turishi",
                                "Filialda bor", "Yuborish, dona"], ship)
    ws = wb.create_sheet("Izoh")
    s = d.get("sales") or ["", ""]
    mx_only = d.get("mode") == "mx"
    for line in [("ZAKAZ TURI: FAQAT MATRITSA — zakaz = matritsa uchun − ombor qoldig'i (zahirasiz)" if mx_only else
                  f"ZAKAZ TURI: MATRITSA + {n} KUNLIK ZAHIRA"),
                 f"Zakaz = matritsa uchun + {n} kunlik zahira − ombor qoldig'i (manfiy bo'lsa — 0)",
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


_XP = {"date_from", "date_to", "compare", "share_by", "branch_id", "hd"}


async def _order_full(req: MxReq, request, user: User) -> dict:
    """To'liq zakaz (keshda) — Mini App ham, Excel ham shundan foydalanadi."""
    async def run():
        async with request.app.state.pool.acquire() as con:
            return await order_data(con, await _flt(con, req, user), req.days, req.mode)
    return await _M().cached(request, user, "mx_order_full", req.model_dump(mode="json", exclude=_XP), run)


async def _ship(req: MxReq, request, user: User) -> list:
    async def run():
        async with request.app.state.pool.acquire() as con:
            return await send_list(con, await _flt(con, req, user))
    return await _M().cached(request, user, "mx_ship", req.model_dump(mode="json", exclude=_XP | {"days", "mode"}), run)


@router.post("/order")
async def order(req: MxReq, request: Request, user: User = Depends(current_user)):
    d = await _order_full(req, request, user)
    # Mini App'ga har ombordan eng katta 100 tasi (keshdagi to'liq natija o'zgartirilmaydi — nusxa)
    return {**d, "whs": [{**w, "items": w["items"][:100], "more": max(0, len(w["items"]) - 100)} for w in d["whs"]]}


@router.post("/order_export")
async def order_export(req: MxReq, request: Request, user: User = Depends(current_user)):
    import config
    from aiogram import Bot
    from aiogram.types import BufferedInputFile
    d = await _order_full(req, request, user)
    ship = await _ship(req, request, user)
    if not d["whs"]:
        raise HTTPException(404, "Zakaz uchun ma'lumot yo'q")
    if not config.BOT_TOKEN:
        raise HTTPException(503, "Bot sozlanmagan")
    bot = Bot(config.BOT_TOKEN)
    try:
        await bot.send_document(user.id, BufferedInputFile(order_xlsx(d, ship), filename=order_fname(d.get("mode", "full"))),
                                caption="🛒 Zakaz: har ombor alohida varaq + «Ombordan yuborish»")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"Botga yuborib bo'lmadi — botga /start bosilganini tekshiring ({e})")
    finally:
        await bot.session.close()
    return {"ok": True}


def order_fname(mode: str = "full") -> str:
    from datetime import datetime, timezone
    return f"zakaz{'_matritsa' if mode == 'mx' else ''}_{datetime.now(timezone(timedelta(hours=5))):%d.%m.%Y}.xlsx"


async def bot_digest(con) -> dict:
    """06:00 xabari uchun: filiallar va mini do'konlar bo'yicha holat + zakaz (barcha kategoriyalar)."""
    from datetime import date
    u = User(id=0, is_admin=True, level="admin")
    f = MxReq(scope="a:", date_from=date.today(), date_to=date.today())
    ov = await _overview(con, f, u)
    has = ov.get("snap", {}).get("b")
    od = await order_data(con, await _flt(con, f, u)) if has else {"whs": [], "total": None}
    return {"ov": ov, "order": od}


# ---------------------------------------------------------------------------
# Matritsadan tashqari qoldiq (filial va ombor) — faqat matritsa kategoriyalari
#   filial: filial qoldig'idagi SKU shu filial matritsasida yo'q;
#   ombor:  ombor qoldig'idagi SKU shu omborga bog'langan birorta filial matritsasida yo'q.
#   Qiymat $: filial — tannarx (so'm) ÷ kurs; ombor — A sektor narxi (10 000 dan katta bo'lsa so'm deb ÷ kurs).
#   Harakatsiz — 30 kunda sotilmagan (filial: shu filialda; ombor: shu ombor filiallarida).
async def outside_data(con, q) -> dict:
    sn = await _snaps(con)
    out = {"snap": {k: (v.isoformat() if v else None) for k, v in sn.items()}}
    if not sn["b"] and not sn["w"]:
        return out
    s_to = sn["s"] or sn["b"] or sn["w"]
    rate_b = float(await con.fetchval("SELECT rate FROM fx_periods WHERE $1 >= valid_from AND $1 < valid_to", sn["b"]) or 0) if sn["b"] else 0.0
    rate_w = float(await con.fetchval("SELECT rate FROM fx_periods WHERE $1 >= valid_from AND $1 < valid_to", sn["w"]) or 0) if sn["w"] else 0.0
    w_ = q.ctes()
    D, S1, S2 = q.p(sn["b"]), q.p(s_to - timedelta(days=29)), q.p(s_to)
    base = f"""WITH {w_},
mc AS (SELECT DISTINCT category_id FROM matrix_rows WHERE category_id IS NOT NULL),
pdm AS (SELECT pd.* FROM pd JOIN mc USING (category_id)),
mx AS (SELECT DISTINCT branch_id, product_id FROM matrix_rows WHERE branch_id IS NOT NULL AND product_id IS NOT NULL)"""
    # avval nomatritsa qoldiq (kichik to'plam), keyin faqat shu filial×SKU savdosi (sales_fact katta — to'liq yig'ilmaydi)
    brows = await con.fetch(base + f""",
c AS MATERIALIZED (
    SELECT s.branch_id, s.product_id, s.qty, s.cost_uzs FROM stock_branch s JOIN bb USING (branch_id)
    JOIN pdm p USING (product_id)
    LEFT JOIN mx ON mx.branch_id = s.branch_id AND mx.product_id = s.product_id
    WHERE s.snap_date = {D} AND s.qty > 0 AND mx.product_id IS NULL),
sl AS (SELECT e.branch_id, e.product_id, sum(e.qty) q FROM sales_fact e JOIN c USING (branch_id, product_id)
       WHERE e.sale_date BETWEEN {S1} AND {S2} GROUP BY 1, 2)
SELECT c.branch_id, c.product_id, c.qty::float8 qty, COALESCE(c.cost_uzs, 0)::float8 cost, COALESCE(sl.q, 0)::float8 s30,
       p.pname, p.brand, p.cname
FROM c JOIN pdm p USING (product_id)
LEFT JOIN sl ON sl.branch_id = c.branch_id AND sl.product_id = c.product_id""", *q.params) if sn["b"] else []
    q2 = q.__class__(q.f, q.cats, q.attrs)
    w2 = q2.ctes()
    W2, T1, T2 = q2.p(sn["w"]), q2.p(s_to - timedelta(days=29)), q2.p(s_to)
    wrows = await con.fetch(f"""WITH {w2},
mc AS (SELECT DISTINCT category_id FROM matrix_rows WHERE category_id IS NOT NULL),
pdm AS (SELECT pd.* FROM pd JOIN mc USING (category_id)),
whs AS (SELECT DISTINCT warehouse_id FROM bb WHERE warehouse_id IS NOT NULL),
wmx AS (SELECT DISTINCT b.warehouse_id, m.product_id FROM matrix_rows m JOIN branches b ON b.id = m.branch_id
        WHERE m.product_id IS NOT NULL AND b.warehouse_id IS NOT NULL),
c AS MATERIALIZED (
    SELECT s.warehouse_id, s.product_id, s.qty, s.value_usd FROM stock_wh s JOIN whs USING (warehouse_id)
    JOIN pdm p USING (product_id)
    LEFT JOIN wmx ON wmx.warehouse_id = s.warehouse_id AND wmx.product_id = s.product_id
    WHERE s.snap_date = {W2} AND s.qty > 0 AND wmx.product_id IS NULL),
sl AS (SELECT b.warehouse_id, e.product_id, sum(e.qty) q FROM sales_fact e
       JOIN (SELECT DISTINCT product_id FROM c) cp USING (product_id) JOIN branches b ON b.id = e.branch_id
       WHERE e.sale_date BETWEEN {T1} AND {T2} AND b.warehouse_id IS NOT NULL GROUP BY 1, 2)
SELECT c.warehouse_id, c.product_id, c.qty::float8 qty, c.value_usd::float8 v, COALESCE(sl.q, 0)::float8 s30,
       p.pname, p.brand, p.cname
FROM c JOIN pdm p USING (product_id)
LEFT JOIN sl ON sl.warehouse_id = c.warehouse_id AND sl.product_id = c.product_id""", *q2.params) if sn["w"] else []

    meta = {r["id"]: r for r in await con.fetch(
        """SELECT b.id, b.name, b.region_id, rg.name region FROM branches b JOIN regions rg ON rg.id = b.region_id""")}
    wn = {r["id"]: r["name"].replace(" омбори", "") for r in await con.fetch("SELECT id, name FROM warehouses")}

    def wusd(r):
        if r["v"] is None or not r["qty"]:
            return 0.0
        u = r["v"] / r["qty"]
        if u > USD_MAX_UNIT:
            u = u / rate_w if rate_w else 0.0
        return u * r["qty"]

    Z = lambda: {"bq": 0.0, "busd": 0.0, "bidle": 0.0, "bidle_usd": 0.0, "wq": 0.0, "wusd": 0.0, "widle": 0.0,  # noqa: E731
                 "widle_usd": 0.0}
    K = Z()
    C: dict[str, dict] = {}
    BR: dict[int, dict] = {}
    WH: dict[int, dict] = {}

    def node(cat, brand, pid, name):
        c = C.setdefault(cat, {"name": cat, **Z(), "brands": {}})
        b = c["brands"].setdefault(brand or "—", {"name": brand or "—", **Z(), "skus": {}})
        k = b["skus"].setdefault(pid, {"id": pid, "name": name, **Z(), "s30": 0.0, "nbr": 0, "wh": {}})
        return c, b, k

    for r in brows:
        usd_ = r["cost"] / rate_b if rate_b else 0.0
        idle = r["s30"] <= 0
        c, b, k = node(r["cname"], r["brand"], r["product_id"], r["pname"])
        k["nbr"] += 1
        k["s30"] += r["s30"]
        for x in (K, c, b, k):
            x["bq"] += r["qty"]
            x["busd"] += usd_
            if idle:
                x["bidle"] += r["qty"]
                x["bidle_usd"] += usd_
        m = meta.get(r["branch_id"])
        if m:
            x = BR.setdefault(r["branch_id"], {"id": r["branch_id"], "name": m["name"], "region_id": m["region_id"],
                                               "region": m["region"], "g": "m" if is_mini(m["name"]) else "f",
                                               "q": 0.0, "usd": 0.0, "idle": 0.0, "skus": 0})
            x["q"] += r["qty"]
            x["usd"] += usd_
            x["skus"] += 1
            if idle:
                x["idle"] += r["qty"]
    for r in wrows:
        usd_ = wusd(r)
        idle = r["s30"] <= 0
        c, b, k = node(r["cname"], r["brand"], r["product_id"], r["pname"])
        nm = wn.get(r["warehouse_id"], "?")
        k["wh"][nm] = k["wh"].get(nm, 0.0) + r["qty"]
        for x in (K, c, b, k):
            x["wq"] += r["qty"]
            x["wusd"] += usd_
            if idle:
                x["widle"] += r["qty"]
                x["widle_usd"] += usd_
        x = WH.setdefault(r["warehouse_id"], {"id": r["warehouse_id"], "name": nm, "q": 0.0, "usd": 0.0, "idle": 0.0,
                                              "idle_usd": 0.0, "items": []})
        x["q"] += r["qty"]
        x["usd"] += usd_
        if idle:
            x["idle"] += r["qty"]
            x["idle_usd"] += usd_
        x["items"].append({"id": r["product_id"], "name": r["pname"], "cat": r["cname"], "q": r["qty"], "usd": usd_,
                           "s30": r["s30"]})
    tot = lambda x: x["busd"] + x["wusd"]  # noqa: E731
    for c in C.values():
        for b in c["brands"].values():
            b["skus"] = sorted(b["skus"].values(), key=lambda k: -tot(k))
        c["brands"] = sorted(c["brands"].values(), key=lambda b: -tot(b))
    for w in WH.values():
        w["items"].sort(key=lambda i: -i["usd"])
    out.update(kpi=K, cats=sorted(C.values(), key=lambda c: -tot(c)),
               whs=sorted(WH.values(), key=lambda w: -w["usd"]),
               branches=sorted(BR.values(), key=lambda b: -b["usd"]), rates={"b": rate_b, "w": rate_w})
    out["_brows"], out["_wrows"] = brows, wrows
    return out


def outside_xlsx(d: dict, con_meta: dict) -> bytes:
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

    rb, rw = d["rates"]["b"], d["rates"]["w"]
    cb = []
    for c in d.get("cats", []):
        for b in c["brands"]:
            cb.append([c["name"], b["name"], len(b["skus"]), round(b["bq"]), round(b["busd"], 2), round(b["bidle"]),
                       round(b["wq"]), round(b["wusd"], 2), round(b["widle"])])
    sheet("Kategoriya × brend", ["Kategoriya", "Brend", "SKU", "Filiallarda, dona", "Filiallarda, $",
                                 "shundan harakatsiz, dona", "Omborlarda, dona", "Omborlarda, $",
                                 "shundan harakatsiz, dona"], cb, first=True)
    br = con_meta["branches"]
    sheet("Filiallar (SKU)", ["Hudud", "Filial", "Kategoriya", "Brend", "Tovar ID", "Tovar", "Qoldiq, dona", "Tannarx, $",
                              "30 kun savdo", "Harakatsiz"],
          [[br.get(r["branch_id"], ("", ""))[0], br.get(r["branch_id"], ("", ""))[1], r["cname"], r["brand"], r["product_id"],
            r["pname"], r["qty"], round(r["cost"] / rb, 2) if rb else None, r["s30"], "ha" if r["s30"] <= 0 else ""]
           for r in sorted(d["_brows"], key=lambda r: -r["cost"])])
    wn = con_meta["whs"]
    rows = []
    for r in sorted(d["_wrows"], key=lambda r: -(r["v"] or 0)):
        u = (r["v"] or 0) / r["qty"] if r["qty"] else 0
        if u > USD_MAX_UNIT:
            u = u / rw if rw else 0
        rows.append([wn.get(r["warehouse_id"], "?"), r["cname"], r["brand"], r["product_id"], r["pname"], r["qty"],
                     round(u * r["qty"], 2), r["s30"], "ha" if r["s30"] <= 0 else ""])
    sheet("Omborlar (SKU)", ["Ombor", "Kategoriya", "Brend", "Tovar ID", "Tovar", "Qoldiq, dona", "Qiymati, $",
                             "30 kun savdo (ombor filiallari)", "Harakatsiz"], rows)
    ws = wb.create_sheet("Izoh")
    for line in ["Matritsadan tashqari qoldiq — faqat matritsadagi kategoriyalar bo'yicha.",
                 "Filial: SKU shu filial matritsasida yo'q. Qiymati — tannarx (kirim narxi) ÷ bot kursi.",
                 "Ombor: SKU shu omborga bog'langan birorta filial matritsasida yo'q. Qiymati — A sektor narxi (so'mdagisi ÷ kurs).",
                 "Harakatsiz — oxirgi 30 kunda sotilmagan (filial — shu filialda; ombor — shu ombor filiallarida).",
                 f"Filial qoldig'i: {d['snap'].get('b')} · ombor qoldig'i: {d['snap'].get('w')} · savdo {d['snap'].get('s')} gacha"]:
        ws.append([line])
    ws.column_dimensions["A"].width = 110
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _outside_full(req: MxReq, request, user: User) -> dict:
    async def run():
        async with request.app.state.pool.acquire() as con:
            return await outside_data(con, await _flt(con, req, user))
    return await _M().cached(request, user, "mx_outside_full", req.model_dump(mode="json", exclude=_XP | {"days", "mode"}), run)


@router.post("/outside")
async def outside(req: MxReq, request: Request, user: User = Depends(current_user)):
    d = await _outside_full(req, request, user)
    out = {k: v for k, v in d.items() if not k.startswith("_")}
    # Mini App'ga — har brenddan va ombordan eng qimmat 50 SKU (nusxa)
    out["cats"] = [{**c, "brands": [{**b, "nsku": len(b["skus"]), "skus": b["skus"][:50]} for b in c["brands"]]}
                   for c in d.get("cats", [])]
    out["whs"] = [{**w, "nsku": len(w["items"]), "items": w["items"][:50]} for w in d.get("whs", [])]
    return out


@router.post("/outside_export")
async def outside_export(req: MxReq, request: Request, user: User = Depends(current_user)):
    import config
    from datetime import datetime, timezone
    from aiogram import Bot
    from aiogram.types import BufferedInputFile
    d = await _outside_full(req, request, user)
    async with request.app.state.pool.acquire() as con:
        meta = {"branches": {r["id"]: (r["region"], r["name"]) for r in await con.fetch(
                    "SELECT b.id, b.name, rg.name region FROM branches b JOIN regions rg ON rg.id = b.region_id")},
                "whs": {r["id"]: r["name"] for r in await con.fetch("SELECT id, name FROM warehouses")}}
    if not d.get("cats"):
        raise HTTPException(404, "Matritsadan tashqari qoldiq topilmadi")
    if not config.BOT_TOKEN:
        raise HTTPException(503, "Bot sozlanmagan")
    fname = f"nematritsa_{datetime.now(timezone(timedelta(hours=5))):%d.%m.%Y}.xlsx"
    bot = Bot(config.BOT_TOKEN)
    try:
        await bot.send_document(user.id, BufferedInputFile(outside_xlsx(d, meta), filename=fname),
                                caption="📦 Nematritsa qoldiq: kategoriya × brend, filiallar va omborlar (SKU)")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"Botga yuborib bo'lmadi — botga /start bosilganini tekshiring ({e})")
    finally:
        await bot.session.close()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Kunlik chartlar (matrix_hist — matritsa versiyalari; versiyadan oldingi kunlar — birinchi versiya bilan)
_DATES = """WITH RECURSIVE t AS (
    SELECT (SELECT min(snap_date) FROM {t} WHERE snap_date >= $1 AND snap_date <= $2) d
    UNION ALL SELECT (SELECT min(snap_date) FROM {t} WHERE snap_date > t.d AND snap_date <= $2) FROM t WHERE t.d IS NOT NULL)
SELECT d FROM t WHERE d IS NOT NULL"""
_DV = """dv AS (SELECT d, COALESCE((SELECT max(ver) FROM matrix_hist WHERE ver <= d),
                               (SELECT min(ver) FROM matrix_hist)) ver FROM unnest({DT}::date[]) d)"""


async def _hist_ready(con) -> None:
    if not await con.fetchval("SELECT EXISTS (SELECT 1 FROM matrix_hist)"):
        from matrix_sync import snapshot_hist
        await snapshot_hist(con)


async def _dates(con, table: str, end, hd: int) -> list:
    if end is None:
        return []
    return [r["d"] for r in await con.fetch(_DATES.format(t=table), end - timedelta(days=hd - 1), end)]


async def _daily_live(con, q, hd: int) -> dict:
    """(jonli) Matritsa bajarilishi kunlar bo'yicha: [kun, g(0 filial/1 mini), hudud, kategoriya, pos, ok, yo'q]."""
    await _hist_ready(con)
    dts = await _dates(con, "stock_branch", await con.fetchval("SELECT max(snap_date) FROM stock_branch"), hd)
    if not dts:
        return {"dates": [], "rows": []}
    w_ = q.ctes()
    DT = q.p(dts)
    rows = await con.fetch(f"""
WITH {w_},
{_DV.format(DT=DT)},
m AS MATERIALIZED (
    SELECT dv.d, h.branch_id, h.product_id, h.category_id, h.need FROM dv JOIN matrix_hist h ON h.ver = dv.ver
    WHERE h.branch_id IN (SELECT branch_id FROM bb) AND h.product_id IN (SELECT product_id FROM pd)),
cov AS (SELECT d, branch_id FROM (SELECT DISTINCT d, branch_id FROM m) x
        WHERE EXISTS (SELECT 1 FROM stock_branch c WHERE c.snap_date = x.d AND c.branch_id = x.branch_id))
SELECT m.d, m.branch_id, m.category_id, count(*)::int pos,
       count(*) FILTER (WHERE COALESCE(s.qty, 0) >= m.need)::int ok,
       count(*) FILTER (WHERE COALESCE(s.qty, 0) <= 0)::int miss
FROM m JOIN cov ON cov.d = m.d AND cov.branch_id = m.branch_id
LEFT JOIN stock_branch s ON s.snap_date = m.d AND s.branch_id = m.branch_id AND s.product_id = m.product_id
GROUP BY 1, 2, 3""", *q.params)
    return await _daily_pack(con, dts, rows)


async def _daily_pack_impl(con, dts, rows) -> dict:
    meta = {r["id"]: (1 if is_mini(r["name"]) else 0, r["region_id"])
            for r in await con.fetch("SELECT id, name, region_id FROM branches")}
    di = {d: i for i, d in enumerate(dts)}
    agg: dict[tuple, list] = {}
    for r in rows:
        g, rg = meta.get(r["branch_id"], (0, None))
        a = agg.setdefault((di[r["d"]], g, rg, r["category_id"]), [0, 0, 0])
        a[0] += r["pos"]
        a[1] += r["ok"]
        a[2] += r["miss"]
    return {"dates": [d.isoformat() for d in dts],
            "rows": [[*k, *v] for k, v in sorted(agg.items(), key=lambda kv: kv[0][0])]}


async def _outside_daily_live(con, q, hd: int) -> dict:
    """(jonli) Nematritsa qoldig'i kunlar bo'yicha: b — filiallarda, w — omborlarda: [sana, dona, $]."""
    await _hist_ready(con)
    out = {"b": [], "w": []}
    bd = await _dates(con, "stock_branch", await con.fetchval("SELECT max(snap_date) FROM stock_branch"), hd)
    wd = await _dates(con, "stock_wh", await con.fetchval("SELECT max(snap_date) FROM stock_wh"), hd)
    fx = "(SELECT rate FROM fx_periods WHERE dv.d >= valid_from AND dv.d < valid_to LIMIT 1)"
    if bd:
        w_ = q.ctes()
        DT = q.p(bd)
        rows = await con.fetch(f"""
WITH {w_},
{_DV.format(DT=DT)},
mc AS (SELECT DISTINCT category_id FROM matrix_rows WHERE category_id IS NOT NULL),
pdm AS (SELECT pd.product_id FROM pd JOIN mc USING (category_id))
SELECT dv.d, sum(s.qty)::float8 q, (sum(COALESCE(s.cost_uzs, 0)) / NULLIF(max({fx}), 0))::float8 usd
FROM dv JOIN stock_branch s ON s.snap_date = dv.d
WHERE s.qty > 0 AND s.branch_id IN (SELECT branch_id FROM bb) AND s.product_id IN (SELECT product_id FROM pdm)
  AND NOT EXISTS (SELECT 1 FROM matrix_hist h WHERE h.ver = dv.ver AND h.branch_id = s.branch_id
                  AND h.product_id = s.product_id)
GROUP BY 1 ORDER BY 1""", *q.params)
        out["b"] = [[r["d"].isoformat(), r["q"] or 0, r["usd"] or 0] for r in rows]
    if wd:
        q2 = q.__class__(q.f, q.cats, q.attrs)
        w2 = q2.ctes()
        DT = q2.p(wd)
        rows = await con.fetch(f"""
WITH {w2},
{_DV.format(DT=DT)},
mc AS (SELECT DISTINCT category_id FROM matrix_rows WHERE category_id IS NOT NULL),
pdm AS (SELECT pd.product_id FROM pd JOIN mc USING (category_id)),
whs AS (SELECT DISTINCT warehouse_id FROM bb WHERE warehouse_id IS NOT NULL),
wmx AS MATERIALIZED (SELECT DISTINCT h.ver, b.warehouse_id, h.product_id FROM matrix_hist h
        JOIN branches b ON b.id = h.branch_id
        WHERE b.warehouse_id IS NOT NULL AND h.ver IN (SELECT ver FROM dv))
SELECT dv.d, sum(s.qty)::float8 q,
       sum(CASE WHEN s.value_usd / s.qty > {USD_MAX_UNIT} THEN s.value_usd / NULLIF({fx}, 0)
                ELSE s.value_usd END)::float8 usd
FROM dv JOIN stock_wh s ON s.snap_date = dv.d
WHERE s.qty > 0 AND s.warehouse_id IN (SELECT warehouse_id FROM whs) AND s.product_id IN (SELECT product_id FROM pdm)
  AND NOT EXISTS (SELECT 1 FROM wmx WHERE wmx.ver = dv.ver AND wmx.warehouse_id = s.warehouse_id
                  AND wmx.product_id = s.product_id)
GROUP BY 1 ORDER BY 1""", *q2.params)
        out["w"] = [[r["d"].isoformat(), r["q"] or 0, r["usd"] or 0] for r in rows]
    return out


# --- oldindan hisoblangan kunlik yig'indilar (019_matrix_dsum.sql): har sana bir marta hisoblanadi ---
_DSUM_LOCK = None


def _dlock():
    global _DSUM_LOCK
    if _DSUM_LOCK is None:
        import asyncio
        _DSUM_LOCK = asyncio.Lock()
    return _DSUM_LOCK


async def _ver_for(con, d):
    return await con.fetchval("SELECT COALESCE((SELECT max(ver) FROM matrix_hist WHERE ver <= $1), "
                              "(SELECT min(ver) FROM matrix_hist))", d)


async def _calc_day(con, kind: str, d) -> None:
    ver = await _ver_for(con, d)
    async with con.transaction():
        if kind == "m":
            await con.execute("DELETE FROM mx_dsum WHERE snap_date = $1", d)
            if ver is not None:
                await con.execute("""
INSERT INTO mx_dsum (snap_date, branch_id, category_id, brand, pos, ok, miss)
SELECT $1, h.branch_id, h.category_id, COALESCE(p.brand, '—'), count(*),
       count(*) FILTER (WHERE COALESCE(s.qty, 0) >= h.need), count(*) FILTER (WHERE COALESCE(s.qty, 0) <= 0)
FROM matrix_hist h JOIN products p USING (product_id)
LEFT JOIN stock_branch s ON s.snap_date = $1 AND s.branch_id = h.branch_id AND s.product_id = h.product_id
WHERE h.ver = $2 AND h.branch_id IN (SELECT DISTINCT branch_id FROM stock_branch WHERE snap_date = $1)
GROUP BY 2, 3, 4""", d, ver)
        else:
            await con.execute("DELETE FROM mx_osum WHERE kind = $1 AND snap_date = $2", kind, d)
            rate = float(await con.fetchval("SELECT rate FROM fx_periods WHERE $1 >= valid_from AND $1 < valid_to", d) or 0)
            if ver is not None and kind == "b":
                await con.execute("""
INSERT INTO mx_osum (kind, snap_date, loc_id, category_id, brand, qty, usd)
SELECT 'b', $1, s.branch_id, p.category_id, COALESCE(p.brand, '—'), sum(s.qty),
       COALESCE(sum(COALESCE(s.cost_uzs, 0)) / NULLIF($3::float8, 0), 0)
FROM stock_branch s JOIN products p USING (product_id)
WHERE s.snap_date = $1 AND s.qty > 0
  AND p.category_id IN (SELECT DISTINCT category_id FROM matrix_hist WHERE ver = $2)
  AND NOT EXISTS (SELECT 1 FROM matrix_hist h WHERE h.ver = $2 AND h.branch_id = s.branch_id
                  AND h.product_id = s.product_id)
GROUP BY 3, 4, 5""", d, ver, rate)
            elif ver is not None:
                await con.execute(f"""
WITH wmx AS MATERIALIZED (SELECT DISTINCT b.warehouse_id, h.product_id FROM matrix_hist h
         JOIN branches b ON b.id = h.branch_id WHERE h.ver = $2 AND b.warehouse_id IS NOT NULL)
INSERT INTO mx_osum (kind, snap_date, loc_id, category_id, brand, qty, usd)
SELECT 'w', $1, s.warehouse_id, p.category_id, COALESCE(p.brand, '—'), sum(s.qty),
       COALESCE(sum(CASE WHEN s.value_usd / s.qty > {USD_MAX_UNIT} THEN s.value_usd / NULLIF($3::float8, 0)
                         ELSE s.value_usd END), 0)
FROM stock_wh s JOIN products p USING (product_id)
WHERE s.snap_date = $1 AND s.qty > 0
  AND p.category_id IN (SELECT DISTINCT category_id FROM matrix_hist WHERE ver = $2)
  AND NOT EXISTS (SELECT 1 FROM wmx WHERE wmx.warehouse_id = s.warehouse_id AND wmx.product_id = s.product_id)
GROUP BY 3, 4, 5""", d, ver, rate)
        await con.execute("""INSERT INTO mx_dsum_meta (kind, snap_date) VALUES ($1, $2)
                             ON CONFLICT (kind, snap_date) DO UPDATE SET computed_at = now()""", kind, d)


async def ensure_dsum(con, kind: str, dates: list) -> None:
    """Hali hisoblanmagan sanalarni hisoblaydi (bir vaqtda bitta — parallel so'rovlar kutadi)."""
    if not dates:
        return
    async with _dlock():
        have = {r[0] for r in await con.fetch("SELECT snap_date FROM mx_dsum_meta WHERE kind = $1 "
                                               "AND snap_date = ANY($2::date[])", kind, dates)}
        for d in sorted(set(dates) - have, reverse=True):   # avval eng yangi kunlar
            await _calc_day(con, kind, d)


def _live_only(f) -> bool:
    """Yig'indida yo'q tovar filtrlari (status, mas'ul, SKU, xususiyat) — jonli hisob."""
    return bool(f.statuses or f.product_ids or f.owners or any(v for v in f.attrs.values()))


def _sum_where(q, loc_col: str, loc_sql: str) -> str:
    f = q.f
    cats = q.cats if not f.category_ids else [c for c in q.cats if c in set(f.category_ids)]
    w = [f"category_id = ANY({q.p(cats)}::int[])", f"{loc_col} IN ({loc_sql})"]
    if f.brands:
        w.append(f"brand = ANY({q.p(f.brands)}::text[])")
    return " AND ".join(w)


async def daily_data(con, q, hd: int) -> dict:
    """Matritsa bajarilishi kunlar bo'yicha: [kun, g(0 filial/1 mini), hudud, kategoriya, pos, ok, yo'q]."""
    if _live_only(q.f):
        return await _daily_live(con, q, hd)
    await _hist_ready(con)
    dts = await _dates(con, "stock_branch", await con.fetchval("SELECT max(snap_date) FROM stock_branch"), hd)
    if not dts:
        return {"dates": [], "rows": []}
    await ensure_dsum(con, "m", dts)
    w_ = q.ctes()
    DT = q.p(dts)
    rows = await con.fetch(f"""WITH {w_}
SELECT snap_date d, branch_id, category_id, sum(pos)::int pos, sum(ok)::int ok, sum(miss)::int miss
FROM mx_dsum WHERE snap_date = ANY({DT}::date[]) AND {_sum_where(q, 'branch_id', 'SELECT branch_id FROM bb')}
GROUP BY 1, 2, 3""", *q.params)
    return await _daily_pack(con, dts, rows)


async def outside_daily_data(con, q, hd: int) -> dict:
    """Nematritsa qoldig'i kunlar bo'yicha: b — filiallarda, w — omborlarda: [sana, dona, $]."""
    if _live_only(q.f):
        return await _outside_daily_live(con, q, hd)
    await _hist_ready(con)
    out = {"b": [], "w": []}
    mc = [r[0] for r in await con.fetch("SELECT DISTINCT category_id FROM matrix_rows WHERE category_id IS NOT NULL")]
    for kind, table, loc in (("b", "stock_branch", "SELECT branch_id FROM bb"),
                             ("w", "stock_wh", "SELECT warehouse_id FROM bb WHERE warehouse_id IS NOT NULL")):
        dts = await _dates(con, table, await con.fetchval(f"SELECT max(snap_date) FROM {table}"), hd)
        if not dts:
            continue
        await ensure_dsum(con, kind, dts)
        q2 = q.__class__(q.f, [c for c in q.cats if c in set(mc)], q.attrs)
        w_ = q2.ctes()
        K, DT = q2.p(kind), q2.p(dts)
        rows = await con.fetch(f"""WITH {w_}
SELECT snap_date d, sum(qty)::float8 q, sum(usd)::float8 usd FROM mx_osum
WHERE kind = {K} AND snap_date = ANY({DT}::date[]) AND {_sum_where(q2, 'loc_id', loc)}
GROUP BY 1 ORDER BY 1""", *q2.params)
        out[kind] = [[r["d"].isoformat(), r["q"] or 0, r["usd"] or 0] for r in rows]
    return out


async def _daily_pack(con, dts, rows) -> dict:
    return await _daily_pack_impl(con, dts, rows)


def _dkey(req: MxReq) -> dict:
    return req.model_dump(mode="json", exclude=(_XP - {"hd"}) | {"days", "mode"})


@router.post("/daily")
async def daily(req: MxReq, request: Request, user: User = Depends(current_user)):
    async def run():
        async with request.app.state.pool.acquire() as con:
            return await daily_data(con, await _flt(con, req, user), req.hd)
    return await _M().cached(request, user, "mx_daily", _dkey(req), run)


@router.post("/outside_daily")
async def outside_daily(req: MxReq, request: Request, user: User = Depends(current_user)):
    async def run():
        async with request.app.state.pool.acquire() as con:
            return await outside_daily_data(con, await _flt(con, req, user), req.hd)
    return await _M().cached(request, user, "mx_outside_daily", _dkey(req), run)


# ---------------------------------------------------------------------------
# Oldindan hisoblash: ma'lumot o'zgargach (savdo/qoldiq yuklandi, matritsa o'qildi, kurs o'zgardi) Matritsa tabining
# standart ko'rinishlari (barcha kategoriyalar, filtrsiz) fonda hisoblanib keshga qo'yiladi — birinchi ochish ham tez.
async def prewarm(app) -> float:
    import time
    from datetime import date
    from types import SimpleNamespace
    t0 = time.time()
    req = SimpleNamespace(app=app)
    today = date.today()
    f = MxReq(scope="a:", date_from=today, date_to=today)
    steps = []
    for adm in (True, False):
        t = time.time()
        await overview(f, req, User(id=0, is_admin=adm, level="admin" if adm else "viewer"))
        steps.append(f"overview {time.time() - t:.1f}s")
    adm = User(id=0, is_admin=True, level="admin")
    for name, fn in (("zakaz", order), ("nematritsa", outside), ("yuborish", lambda f_, r_, u_: _ship(f_, r_, u_)),
                     ("kunlik", daily), ("nematritsa kunlik", outside_daily)):
        t = time.time()
        await fn(f, req, adm)
        steps.append(f"{name} {time.time() - t:.1f}s")
    t = time.time()                                    # 90 kunlik tarix (sanalar bir marta hisoblanadi)
    f90 = MxReq(scope="a:", date_from=today, date_to=today, hd=90)
    await daily(f90, req, adm)
    await outside_daily(f90, req, adm)
    steps.append(f"kunlik 90 {time.time() - t:.1f}s")
    import logging
    logging.getLogger(__name__).info("prewarm: %s", ", ".join(steps))
    return time.time() - t0
