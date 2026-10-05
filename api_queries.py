"""SQL yig'uvchi: filtrlar, o'lchovlar (qirqimlar), joriy va solishtirma davr.

Barcha foydalanuvchi qiymatlari faqat $N parametrlar orqali uzatiladi; SQL ichiga faqat
oq ro'yxatdagi o'lchov ifodalari va tekshirilgan slot raqamlari (int) qo'yiladi.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import asyncpg

from api_schemas import Filters, Kpi


# ---------------------------------------------------------------------------
# Davrlar
# ---------------------------------------------------------------------------
def _minus_year(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:          # 29-fevral
        return d.replace(year=d.year - 1, day=28)


def _month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def _minus_months(d: date, k: int, to_end: bool = False) -> date:
    """k oy orqaga, kun oy oxiriga qisqartiriladi; to_end=True — natija oyning oxirgi kuni."""
    y, m = divmod(d.year * 12 + d.month - 1 - k, 12)
    first = date(y, m + 1, 1)
    last = _month_end(first)
    return last if to_end else min(d.replace(year=y, month=m + 1, day=1) + timedelta(days=d.day - 1), last)


def mom_period(d1: date, d2: date) -> tuple[date, date]:
    """O'tgan oyning shu kunlari: 01–05.10 → 01–05.09; 01–30.09 (to'liq oy) → 01–31.08.
    Davr bir oydan uzun bo'lsa, ustma-ust tushmasligi uchun kerakli oy soniga suriladi."""
    end = d2 == _month_end(d2)
    k = 1
    while _minus_months(d2, k, end) >= d1:
        k += 1
    return _minus_months(d1, k), _minus_months(d2, k, end)


def compare_period(f: Filters) -> tuple[date, date] | None:
    if f.compare == "none":
        return None
    if f.compare == "yoy":
        return _minus_year(f.date_from), _minus_year(f.date_to)
    if f.compare == "mom":
        return mom_period(f.date_from, f.date_to)
    days = (f.date_to - f.date_from).days + 1
    return f.date_from - timedelta(days=days), f.date_from - timedelta(days=1)


# ---------------------------------------------------------------------------
# Kategoriya xususiyatlari
# ---------------------------------------------------------------------------
@dataclass
class AttrDef:
    slot: int
    name: str
    data_type: str
    unit: str | None
    has_ranges: bool


async def load_attrs(con: asyncpg.Connection, category_id: int) -> dict[int, AttrDef]:
    rows = await con.fetch(
        """SELECT a.slot, a.name, a.data_type, a.unit,
                  EXISTS (SELECT 1 FROM attribute_ranges r WHERE r.category_id = a.category_id AND r.slot = a.slot) has_ranges
           FROM category_attributes a WHERE a.category_id = $1 AND a.is_filter ORDER BY a.sort_order, a.slot""",
        category_id)
    return {r["slot"]: AttrDef(r["slot"], r["name"], r["data_type"], r["unit"], r["has_ranges"]) for r in rows}


def _attr_cols(attrs: dict[int, AttrDef]) -> str:
    """pd CTE uchun: har bir xususiyat → d_aN (ko'rinadigan qiymat) va s_aN (saralash kaliti)."""
    parts = []
    for s, a in attrs.items():
        raw = f"(p.attrs->>'a{s}')"
        is_num = f"jsonb_typeof(p.attrs->'a{s}') = 'number'"
        if a.data_type == "number" and a.has_ranges:
            rng = (f"(SELECT r.{{c}} FROM attribute_ranges r WHERE r.category_id = p.category_id AND r.slot = {s} "
                   f"AND (r.lo IS NULL OR {raw}::numeric > r.lo) AND (r.hi IS NULL OR {raw}::numeric <= r.hi) "
                   f"ORDER BY r.sort_order LIMIT 1)")
            label = f"COALESCE(CASE WHEN {is_num} THEN {rng.format(c='label')} END, {raw}, '—')"
            sort = f"CASE WHEN {is_num} THEN {rng.format(c='sort_order')}::numeric END"
        elif a.data_type == "number":
            label = f"COALESCE({raw}, '—')"
            sort = f"CASE WHEN {is_num} THEN {raw}::numeric END"
        else:
            label = f"COALESCE({raw}, '—')"
            sort = "NULL::numeric"
        parts.append(f"{label} AS d_a{s}, {sort} AS s_a{s}")
    return (", " + ", ".join(parts)) if parts else ""


# ---------------------------------------------------------------------------
# O'lchovlar: kalit, nom, saralash, qo'shimcha join
# ---------------------------------------------------------------------------
# key — guruhlash kaliti, label — nom, sort — tabiiy tartib, sub — qo'shimcha izoh (filial → hudud, SKU → brend)
DIMENSIONS: dict[str, dict[str, str]] = {
    "region":  {"key": "x.region_id::text",  "label": "rg.name", "sort": "NULL::numeric", "sub": "NULL::text",
                "title": "Hudud"},
    "cluster": {"key": "x.cluster_id::text", "label": "cl.name", "sort": "NULL::numeric", "sub": "NULL::text",
                "title": "Klaster"},
    "branch":  {"key": "x.branch_id::text",  "label": "br.name", "sort": "NULL::numeric", "sub": "rg.name",
                "title": "Filial"},
    "brand":   {"key": "x.brand",            "label": "x.brand", "sort": "NULL::numeric", "sub": "NULL::text",
                "title": "Brend"},
    "status":  {"key": "COALESCE(x.status,'—')", "label": "COALESCE(x.status,'—')", "sort": "NULL::numeric",
                "sub": "NULL::text", "title": "Status"},
    "category": {"key": "x.category_id::text", "label": "x.cname", "sort": "NULL::numeric", "sub": "x.owner",
                 "title": "Kategoriya"},
    "owner":   {"key": "x.owner", "label": "x.owner", "sort": "NULL::numeric", "sub": "NULL::text",
                "title": "Mas'ul"},
    "sku":     {"key": "x.product_id::text", "label": "x.pname", "sort": "NULL::numeric", "sub": "x.brand",
                "title": "SKU"},
}


def dimension(dim: str, attrs: dict[int, AttrDef]) -> dict[str, str]:
    if dim in DIMENSIONS:
        return DIMENSIONS[dim]
    if dim.startswith("attr:"):
        try:
            slot = int(dim[5:])
        except ValueError:
            slot = -1
        if slot in attrs:
            return {"key": f"x.d_a{slot}", "label": f"x.d_a{slot}", "sort": f"x.s_a{slot}",
                    "sub": "NULL::text", "title": attrs[slot].name}
    raise ValueError(f"Noma'lum o'lchov: {dim}")


JOINS = """
LEFT JOIN regions rg  ON rg.id = x.region_id
LEFT JOIN clusters cl ON cl.id = x.cluster_id
LEFT JOIN branches br ON br.id = x.branch_id
"""


# ---------------------------------------------------------------------------
# So'rov quruvchi
# ---------------------------------------------------------------------------
class Q:
    def __init__(self, f: Filters, attrs: dict[int, AttrDef], cats: list[int]):
        self.f, self.attrs, self.cats = f, attrs, cats
        self.params: list[Any] = []
        self.cmp = compare_period(f)

    def p(self, v: Any) -> str:
        self.params.append(v)
        return f"${len(self.params)}"

    def base_cte(self, exclude: str | None = None, with_cmp: bool = True) -> str:
        """pd (kategoriya tovarlari + xususiyat qiymatlari) va x (filtrlangan savdo, per='c'|'p')."""
        f = self.f
        cat = self.p(self.cats)
        c1, c2 = self.p(f.date_from), self.p(f.date_to)
        if with_cmp and self.cmp:
            p1, p2 = self.p(self.cmp[0]), self.p(self.cmp[1])
            period = f"(e.sale_date BETWEEN {c1} AND {c2} OR e.sale_date BETWEEN {p1} AND {p2})"
        else:
            period = f"e.sale_date BETWEEN {c1} AND {c2}"
        where = [period] + self.filter_conds(exclude)
        return f"""
WITH pd AS (
    SELECT p.product_id, p.name AS pname, p.brand, p.status, p.category_id, c.name AS cname,
           COALESCE(c.owner, '—') AS owner{_attr_cols(self.attrs)}
    FROM products p JOIN categories c ON c.id = p.category_id WHERE p.category_id = ANY({cat}::int[])
), x AS (
    SELECT e.sale_date, e.is_bonus, e.qty, e.amount, e.gross_margin, e.supplier_income, e.margin,
           e.branch_id, b.region_id, b.cluster_id, pd.*,
           CASE WHEN e.sale_date BETWEEN {c1} AND {c2} THEN 'c' ELSE 'p' END AS per
    FROM sales_enriched e
    JOIN pd USING (product_id)
    JOIN branches b ON b.id = e.branch_id
    WHERE {' AND '.join(where)}
)"""

    def filter_conds(self, exclude: str | None = None, alias_b: str = "b", alias_p: str = "pd") -> list[str]:
        f, c = self.f, []
        if f.region_ids and exclude != "region":
            c.append(f"{alias_b}.region_id = ANY({self.p(f.region_ids)}::int[])")
        if f.cluster_ids and exclude != "cluster":
            c.append(f"{alias_b}.cluster_id = ANY({self.p(f.cluster_ids)}::int[])")
        if f.branch_ids and exclude != "branch":
            c.append(f"{alias_b}.id = ANY({self.p(f.branch_ids)}::int[])")
        if f.brands and exclude != "brand":
            c.append(f"{alias_p}.brand = ANY({self.p(f.brands)}::text[])")
        if f.statuses and exclude != "status":
            c.append(f"COALESCE({alias_p}.status,'—') = ANY({self.p(f.statuses)}::text[])")
        if f.category_ids and exclude != "category":
            c.append(f"{alias_p}.category_id = ANY({self.p(f.category_ids)}::int[])")
        if f.owners and exclude != "owner":
            c.append(f"{alias_p}.owner = ANY({self.p(f.owners)}::text[])")
        if f.product_ids and exclude != "sku":
            c.append(f"{alias_p}.product_id = ANY({self.p(f.product_ids)}::bigint[])")
        for slot, vals in f.attrs.items():
            if vals and slot in self.attrs and exclude != f"attr:{slot}":
                c.append(f"{alias_p}.d_a{slot} = ANY({self.p(vals)}::text[])")
        return c


def metrics_sql(per: str) -> str:
    w = f"x.per = '{per}'"
    return (f"sum(x.amount) FILTER (WHERE {w}) AS {per}_amount, "
            f"sum(x.qty) FILTER (WHERE {w}) AS {per}_qty, "
            f"sum(x.qty) FILTER (WHERE {w} AND x.is_bonus) AS {per}_bonus_qty, "
            f"sum(x.qty) FILTER (WHERE {w} AND NOT x.is_bonus) AS {per}_sold_qty, "
            f"sum(x.gross_margin) FILTER (WHERE {w}) AS {per}_gross, "
            f"sum(x.supplier_income) FILTER (WHERE {w}) AS {per}_income, "
            f"sum(x.margin) FILTER (WHERE {w}) AS {per}_margin, "
            f"count(DISTINCT x.product_id) FILTER (WHERE {w}) AS {per}_skus, "
            f"count(DISTINCT x.branch_id) FILTER (WHERE {w}) AS {per}_branches")


def kpi_from(row: asyncpg.Record | dict, per: str) -> Kpi:
    g = lambda k: float(row[f"{per}_{k}"] or 0)  # noqa: E731
    amount, sold = g("amount"), g("sold_qty")
    return Kpi(
        amount=amount, qty=g("qty"), bonus_qty=g("bonus_qty"), gross=g("gross"), income=g("income"),
        margin=g("margin"),
        gross_pct=round(g("gross") / amount * 100, 2) if amount else None,
        margin_pct=round(g("margin") / amount * 100, 2) if amount else None,
        avg_price=round(amount / sold) if sold else None,
        skus=int(row[f"{per}_skus"] or 0), branches=int(row[f"{per}_branches"] or 0),
    )


def delta(cur: Kpi, prev: Kpi | None) -> dict[str, float | None]:
    """Nisbiy o'zgarish (%) summalar uchun, foiz punktlari (pp) marja% uchun."""
    if prev is None:
        return {}
    rel = lambda a, b: round((a - b) / abs(b) * 100, 1) if b else None  # noqa: E731
    pp = lambda a, b: round(a - b, 2) if a is not None and b is not None else None  # noqa: E731
    return {
        "amount": rel(cur.amount, prev.amount), "qty": rel(cur.qty, prev.qty),
        "gross": rel(cur.gross, prev.gross), "margin": rel(cur.margin, prev.margin),
        "income": rel(cur.income, prev.income),
        "avg_price": rel(cur.avg_price or 0, prev.avg_price or 0) if cur.avg_price and prev.avg_price else None,
        "gross_pct_pp": pp(cur.gross_pct, prev.gross_pct), "margin_pct_pp": pp(cur.margin_pct, prev.margin_pct),
    }
