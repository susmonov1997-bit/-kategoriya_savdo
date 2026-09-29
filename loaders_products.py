"""Tovar spravochnigi (хусусиятлар.xlsx) loaderi.

Kutilgan ustunlar (nomi bo'yicha qidiriladi, joylashuvi muhim emas):
  majburiy : Категория, Товар Ид, Товар номи, Бренд
  ixtiyoriy: Группа, Статус
  xususiyat: Подкатегория (slot 0), "1-Субкатегория" (slot 1), "2-Субкатегория" (slot 2) ...

Qoidalar:
  * Kalit — "Товар Ид". Takrorlansa: statusi 'матричный' bo'lgan qator ustun, teng bo'lsa faylda oxirgisi.
  * Faylda yo'q, bazada bor tovarlar o'chirilmaydi (savdo tarixi uchun) — faqat soni hisobotda.
  * Yangi kategoriya / yangi xususiyat darajasi avtomatik qo'shiladi (nomi = Excel ustun nomi),
    keyin category_attributes jadvalida nomini o'zgartirish mumkin.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import pandas as pd

from loaders_common import LoaderError, clean_text, read_sheet_with_header, to_number

COL_GROUP = "Группа"
COL_CAT = "Категория"
COL_ID = "Товар Ид"
COL_NAME = "Товар номи"
COL_BRAND = "Бренд"
COL_STATUS = "Статус"
REQUIRED = [COL_CAT, COL_ID, COL_NAME, COL_BRAND]

PRIORITY_STATUS = "матричный"
DEFAULT_GROUP = "—"
NUMERIC_SHARE_FOR_AUTO = 0.9      # yangi xususiyat: qiymatlarning 90%+ son bo'lsa → number

_SUB_RE = re.compile(r"^(\d+)-Субкатегория$", re.IGNORECASE)


def attr_slot(col: str) -> int | None:
    if col.lower() == "подкатегория":
        return 0
    m = _SUB_RE.match(col)
    return int(m.group(1)) if m else None


def is_products_file(headers: list[str]) -> bool:
    hs = set(headers)
    return set(REQUIRED) <= hs and any(attr_slot(h) is not None for h in hs)


# ---------------------------------------------------------------------------
# 1) Parse + validatsiya (bazasiz, sof funksiya)
# ---------------------------------------------------------------------------
@dataclass
class ParsedProduct:
    product_id: int
    name: str
    grp: str
    category: str
    brand: str
    status: str | None
    raw_attrs: dict[int, Any]          # slot → xom qiymat
    excel_row: int


@dataclass
class ProductsParseResult:
    products: dict[int, ParsedProduct]
    attr_cols: dict[int, str]          # slot → Excel ustun nomi
    rows_total: int
    errors: list[str] = field(default_factory=list)          # yuklanmagan qatorlar
    duplicates: list[dict] = field(default_factory=list)     # takrorlangan Ид lar


def parse_products(data: bytes) -> ProductsParseResult:
    df, _ = read_sheet_with_header(data, must_have=[COL_ID, COL_CAT])

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise LoaderError("Majburiy ustun(lar) yo'q: " + ", ".join(missing))

    attr_cols = {s: c for c in df.columns if (s := attr_slot(c)) is not None}
    if not attr_cols:
        raise LoaderError("Xususiyat ustunlari topilmadi (Подкатегория, 1-Субкатегория, ...)")

    res = ProductsParseResult(products={}, attr_cols=dict(sorted(attr_cols.items())), rows_total=len(df))
    seen_rows: dict[int, list[ParsedProduct]] = {}

    for rec in df.to_dict("records"):
        r = rec["_excel_row"]
        pid = to_number(rec.get(COL_ID))
        if pid is None or not isinstance(pid, int) or pid <= 0:
            res.errors.append(f"{r}-qator: «Товар Ид» noto'g'ri ({rec.get(COL_ID)!r})")
            continue
        name, cat, brand = clean_text(rec.get(COL_NAME)), clean_text(rec.get(COL_CAT)), clean_text(rec.get(COL_BRAND))
        empty = [n for n, v in ((COL_NAME, name), (COL_CAT, cat), (COL_BRAND, brand)) if not v]
        if empty:
            res.errors.append(f"{r}-qator (Ид {pid}): bo'sh — {', '.join(empty)}")
            continue

        raw_attrs: dict[int, Any] = {}
        for slot, col in attr_cols.items():
            v = rec.get(col)
            num = to_number(v) if not isinstance(v, str) else None
            val = num if num is not None else clean_text(v)
            if val is not None:
                raw_attrs[slot] = val

        p = ParsedProduct(
            product_id=pid, name=name, grp=clean_text(rec.get(COL_GROUP)) or DEFAULT_GROUP,
            category=cat, brand=brand, status=clean_text(rec.get(COL_STATUS)),
            raw_attrs=raw_attrs, excel_row=r,
        )
        seen_rows.setdefault(pid, []).append(p)

    for pid, rows in seen_rows.items():
        if len(rows) == 1:
            res.products[pid] = rows[0]
            continue
        matrix = [p for p in rows if (p.status or "").lower() == PRIORITY_STATUS]
        chosen = (matrix or rows)[-1]
        res.products[pid] = chosen
        res.duplicates.append({
            "product_id": pid,
            "rows": [p.excel_row for p in rows],
            "chosen_row": chosen.excel_row,
            "differs": len({(p.category, p.brand, json.dumps(p.raw_attrs, sort_keys=True, default=str)) for p in rows}) > 1,
        })
    return res


# ---------------------------------------------------------------------------
# 2) Bazaga yozish
# ---------------------------------------------------------------------------
@dataclass
class ProductsLoadReport:
    upload_id: int
    rows_total: int
    loaded: int
    inserted: int
    updated: int
    unchanged: int
    missing_in_file: int
    new_categories: list[str]
    new_attrs: list[str]
    non_numeric: list[str]               # son kutilgan joyda matn (masalan 'Витрина')
    errors: list[str]
    duplicates: list[dict]
    by_category: dict[str, int]

    def as_json(self) -> dict:
        return self.__dict__.copy()


def _auto_type(values: list[Any]) -> str:
    if not values:
        return "text"
    nums = sum(1 for v in values if to_number(v) is not None)
    return "number" if nums / len(values) >= NUMERIC_SHARE_FOR_AUTO else "text"


async def load_products(
    pool: asyncpg.Pool, data: bytes, file_name: str | None = None, tg_user_id: int | None = None
) -> ProductsLoadReport:
    async with pool.acquire() as con:
        upload_id = await con.fetchval(
            "INSERT INTO uploads(kind, file_name, tg_user_id) VALUES ('products',$1,$2) RETURNING id",
            file_name, tg_user_id,
        )
        try:
            parsed = await asyncio.to_thread(parse_products, data)
            report = await _write(con, parsed, upload_id)
        except Exception as e:
            await con.execute(
                "UPDATE uploads SET status='failed', finished_at=now(), report=$2 WHERE id=$1",
                upload_id, json.dumps({"error": str(e)}, ensure_ascii=False),
            )
            raise
        await con.execute(
            """UPDATE uploads SET status='done', finished_at=now(), rows_total=$2, rows_loaded=$3, report=$4
               WHERE id=$1""",
            upload_id, report.rows_total, report.loaded,
            json.dumps(report.as_json(), ensure_ascii=False, default=str),
        )
        return report


async def _write(con: asyncpg.Connection, parsed: ProductsParseResult, upload_id: int) -> ProductsLoadReport:
    prods = list(parsed.products.values())
    async with con.transaction():
        # --- kategoriyalar ---
        existing = {r["name"]: r["id"] for r in await con.fetch("SELECT id, name FROM categories")}
        new_cats: list[str] = []
        for p in prods:
            if p.category not in existing:
                existing[p.category] = await con.fetchval(
                    "INSERT INTO categories(grp, name) VALUES ($1,$2) RETURNING id", p.grp, p.category
                )
                new_cats.append(p.category)

        # --- xususiyat darajalari ---
        attr_defs: dict[tuple[int, int], str] = {
            (r["category_id"], r["slot"]): r["data_type"]
            for r in await con.fetch("SELECT category_id, slot, data_type FROM category_attributes")
        }
        values_by_key: dict[tuple[int, int], list[Any]] = {}
        for p in prods:
            cid = existing[p.category]
            for slot, v in p.raw_attrs.items():
                values_by_key.setdefault((cid, slot), []).append(v)

        cat_name = {v: k for k, v in existing.items()}
        new_attrs: list[str] = []
        for (cid, slot), vals in sorted(values_by_key.items()):
            if (cid, slot) in attr_defs:
                continue
            dtype = _auto_type(vals)
            col = parsed.attr_cols[slot]
            await con.execute(
                """INSERT INTO category_attributes(category_id, slot, source_col, name, data_type, sort_order)
                   VALUES ($1,$2,$3,$3,$4,$2)""",
                cid, slot, col, dtype,
            )
            attr_defs[(cid, slot)] = dtype
            new_attrs.append(f"{cat_name[cid]} → {col} ({dtype})")

        # --- attrs JSON (tur bo'yicha) ---
        non_numeric: dict[tuple[str, str], set[str]] = {}
        rows = []
        by_cat: dict[str, int] = {}
        for p in prods:
            cid = existing[p.category]
            attrs: dict[str, Any] = {}
            for slot, v in p.raw_attrs.items():
                if attr_defs.get((cid, slot)) == "number":
                    n = to_number(v)
                    if n is None:
                        non_numeric.setdefault((p.category, parsed.attr_cols[slot]), set()).add(str(v))
                        attrs[f"a{slot}"] = str(v)
                    else:
                        attrs[f"a{slot}"] = n
                else:
                    attrs[f"a{slot}"] = str(v)   # to_number allaqachon 185.0 → 185 qilgan
            rows.append((p.product_id, p.name, cid, p.brand, p.status, json.dumps(attrs, ensure_ascii=False)))
            by_cat[p.category] = by_cat.get(p.category, 0) + 1

        # --- upsert (temp jadval orqali) ---
        await con.execute(
            """CREATE TEMP TABLE _p (product_id BIGINT, name TEXT, category_id INT, brand TEXT,
                                     status TEXT, attrs TEXT) ON COMMIT DROP"""
        )
        await con.copy_records_to_table("_p", records=rows)
        stats = await con.fetchrow(
            """
            WITH cmp AS (
                SELECT t.*, p.product_id IS NULL AS is_new,
                       p.product_id IS NOT NULL AND (p.name, p.category_id, p.brand, p.status, p.attrs)
                           IS DISTINCT FROM (t.name, t.category_id, t.brand, t.status, t.attrs::jsonb) AS is_changed
                FROM _p t LEFT JOIN products p USING (product_id)
            ), up AS (
                INSERT INTO products AS p (product_id, name, category_id, brand, status, attrs, upload_id)
                SELECT product_id, name, category_id, brand, status, attrs::jsonb, $1
                FROM cmp WHERE is_new OR is_changed
                ON CONFLICT (product_id) DO UPDATE SET
                    name = EXCLUDED.name, category_id = EXCLUDED.category_id, brand = EXCLUDED.brand,
                    status = EXCLUDED.status, attrs = EXCLUDED.attrs, upload_id = EXCLUDED.upload_id,
                    updated_at = now()
                RETURNING 1
            )
            SELECT (SELECT count(*) FROM cmp WHERE is_new)     AS inserted,
                   (SELECT count(*) FROM cmp WHERE is_changed) AS updated,
                   (SELECT count(*) FROM up)                   AS written,
                   (SELECT count(*) FROM products p WHERE NOT EXISTS
                        (SELECT 1 FROM _p t WHERE t.product_id = p.product_id)) AS missing
            """,
            upload_id,
        )

    return ProductsLoadReport(
        upload_id=upload_id,
        rows_total=parsed.rows_total,
        loaded=len(rows),
        inserted=stats["inserted"],
        updated=stats["updated"],
        unchanged=len(rows) - stats["inserted"] - stats["updated"],
        missing_in_file=stats["missing"],
        new_categories=new_cats,
        new_attrs=new_attrs,
        non_numeric=[f"{c} → {col}: {', '.join(sorted(v))}" for (c, col), v in sorted(non_numeric.items())],
        errors=parsed.errors,
        duplicates=parsed.duplicates,
        by_category=dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
    )


# ---------------------------------------------------------------------------
# 3) Telegram uchun qisqa hisobot
# ---------------------------------------------------------------------------
def format_report(r: ProductsLoadReport, max_items: int = 15) -> str:
    def lst(items: list[str]) -> str:
        head = "\n".join(f"  • {html.escape(str(x), quote=False)}" for x in items[:max_items])
        return head + (f"\n  … yana {len(items) - max_items} ta" if len(items) > max_items else "")

    out = [
        f"✅ <b>Tovar spravochnigi yuklandi</b> (#{r.upload_id})",
        f"Qatorlar: {r.rows_total} → yuklandi: <b>{r.loaded}</b> SKU",
        f"Yangi: {r.inserted} · O'zgargan: {r.updated} · O'zgarmagan: {r.unchanged}",
        "",
        "<b>Kategoriyalar:</b>",
        *[f"  • {html.escape(c)}: {n}" for c, n in r.by_category.items()],
    ]
    if r.missing_in_file:
        out.append(f"\nℹ️ Bazada bor, faylda yo'q SKU: {r.missing_in_file} (o'chirilmadi)")
    if r.new_categories:
        out += ["\n🆕 <b>Yangi kategoriyalar:</b>", lst(r.new_categories)]
    if r.new_attrs:
        out += ["\n🆕 <b>Yangi xususiyatlar</b> (nomini keyin o'zgartiring):", lst(r.new_attrs)]
    if r.duplicates:
        d = [
            f"Ид {x['product_id']}: qatorlar {x['rows']} → olindi {x['chosen_row']}"
            + (" ⚠️ ma'lumot farq qiladi" if x["differs"] else "")
            for x in r.duplicates
        ]
        out += [f"\n⚠️ <b>Takrorlangan Ид: {len(d)}</b>", lst(d)]
    if r.non_numeric:
        out += ["\n⚠️ <b>Son kutilgan joyda matn</b> (matn sifatida saqlandi):", lst(r.non_numeric)]
    if r.errors:
        out += [f"\n❌ <b>Yuklanmagan qatorlar: {len(r.errors)}</b>", lst(r.errors)]
    return "\n".join(out)
