"""Kutayotgan savdo (009_pending.sql): spravochnikda yo'q SKU/filial qatorlari keyinroq avtomatik ulanadi."""
from __future__ import annotations

import html

import asyncpg

from fact import refresh_dates


async def resolve_pending(con: asyncpg.Connection) -> dict:
    """Spravochnikda endi BOR SKU va filial qatorlarini sales_daily / company_daily ga ko'chiradi.
    Chaqiruvchining tranzaksiyasida ishlaydi. Qaytaradi: {rows, amount, dates, skus, comp_rows}."""
    moved = await con.fetch(
        """WITH m AS (
               DELETE FROM sales_pending sp
               USING branches b, products p
               WHERE b.name_key = sp.branch_key AND p.product_id = sp.product_id
               RETURNING sp.sale_date, b.id branch_id, sp.product_id, sp.is_bonus, sp.qty, sp.amount, sp.cost,
                         sp.cost_usd, sp.cost_uzs, sp.cost_split, sp.upload_id)
           , ins AS (
           INSERT INTO sales_daily AS s (sale_date, branch_id, product_id, is_bonus, qty, amount, cost,
                                         cost_usd, cost_uzs, cost_split, upload_id)
           SELECT sale_date, branch_id, product_id, is_bonus, sum(qty), sum(amount), sum(cost), sum(cost_usd),
                  sum(cost_uzs), bool_or(cost_split), max(upload_id)
           FROM m GROUP BY sale_date, branch_id, product_id, is_bonus
           ON CONFLICT (sale_date, branch_id, product_id, is_bonus) DO UPDATE
           SET qty = s.qty + EXCLUDED.qty, amount = s.amount + EXCLUDED.amount, cost = s.cost + EXCLUDED.cost,
               cost_usd = s.cost_usd + EXCLUDED.cost_usd, cost_uzs = s.cost_uzs + EXCLUDED.cost_uzs
           RETURNING 1)
           SELECT sale_date, product_id, amount FROM m""")
    comp = await con.fetch(
        """WITH m AS (
               DELETE FROM company_pending cp USING branches b WHERE b.name_key = cp.branch_key
               RETURNING cp.sale_date, b.id branch_id, cp.qty, cp.amount, cp.cost, cp.cost_usd, cp.cost_uzs,
                         cp.cost_split, cp.upload_id)
           , ins AS (
           INSERT INTO company_daily AS c (sale_date, branch_id, qty, amount, cost, cost_usd, cost_uzs, cost_split, upload_id)
           SELECT sale_date, branch_id, sum(qty), sum(amount), sum(cost), sum(cost_usd), sum(cost_uzs),
                  bool_or(cost_split), max(upload_id)
           FROM m GROUP BY sale_date, branch_id
           ON CONFLICT (sale_date, branch_id) DO UPDATE
           SET qty = c.qty + EXCLUDED.qty, amount = c.amount + EXCLUDED.amount, cost = c.cost + EXCLUDED.cost,
               cost_usd = c.cost_usd + EXCLUDED.cost_usd, cost_uzs = c.cost_uzs + EXCLUDED.cost_uzs
           RETURNING 1)
           SELECT DISTINCT sale_date, branch_id FROM m""")
    dates = sorted({r["sale_date"] for r in moved} | {r["sale_date"] for r in comp})
    if moved:
        await refresh_dates(con, sorted({r["sale_date"] for r in moved}))
    left = await con.fetchrow("SELECT count(*) n, coalesce(sum(amount), 0) amount, count(DISTINCT product_id) skus "
                              "FROM sales_pending")
    return {"rows": len(moved), "amount": float(sum(r["amount"] or 0 for r in moved)),
            "skus": len({r["product_id"] for r in moved}), "comp_rows": len(comp),
            "dates": [dates[0].isoformat(), dates[-1].isoformat()] if dates else [],
            "left_rows": left["n"], "left_amount": float(left["amount"]), "left_skus": left["skus"]}


def format_resolved(r: dict) -> str:
    """Spravochnik hisobotiga qo'shiladigan qism (bo'sh bo'lsa — "")."""
    if not r or not (r["rows"] or r["comp_rows"]):
        return ""
    d = r["dates"]
    per = (d[0] if d[0] == d[-1] else f"{d[0]} — {d[-1]}") if d else ""
    out = [f"\n🔗 <b>Kutayotgan savdo ulandi:</b> {r['skus']} ta SKU · {r['rows']} qator · "
           f"{r['amount'] / 1e6:,.1f} mln".replace(",", " ") + (f" ({html.escape(per)})" if per else "")]
    if r["comp_rows"]:
        out.append(f"   set jamiga ham qo'shildi: {r['comp_rows']} ta sana×filial")
    if r["left_rows"]:
        out.append(f"   hali kutmoqda: {r['left_skus']} ta SKU · {r['left_amount'] / 1e6:,.1f} mln".replace(",", " "))
    return "\n".join(out)
