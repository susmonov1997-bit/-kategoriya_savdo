"""Tovar spravochnigi (хусусиятлар.xlsx) loaderi.

Kutilgan ustunlar (nomi bo'yicha qidiriladi, joylashuvi muhim emas):
  majburiy : Категория, Товар Ид, Товар номи, Бренд
  ixtiyoriy: Группа, Статус
  xususiyat: QOLGAN BARCHA ustunlar. Ustun nomi = xususiyat nomi, qavsda birlik bo'lishi mumkin:
             "Turi", "Balandlik (sm)", "Umumiy hajm (l)", "Rang" ...
             Eski format ("Подкатегория", "1-Субкатегория", ...) ham qabul qilinadi — u holda nom
             bazadagi shu tartib raqamidan olinadi.

Qoidalar:
  * Kalit — "Товар Ид". Takrorlansa: statusi 'матричный' bo'lgan qator ustun, teng bo'lsa faylda oxirgisi.
  * Faylda yo'q, bazada bor tovarlar o'chirilmaydi (savdo tarixi uchun) — faqat soni hisobotda.
  * Har bir kategoriyaning xususiyatlari = shu kategoriya tovarlarida qiymati bor ustunlar (fayldagi tartibda).
    Xususiyat bazada NOMI bo'yicha topiladi (ustun joyi muhim emas); yangi nom — yangi xususiyat.
  * Faylda kelgan kategoriyaning endi faylda yo'q xususiyati Mini App'dan yashiriladi (ma'lumot o'chirilmaydi;
    ustun qaytsa yana ko'rinadi).
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
_UNIT_RE = re.compile(r"^(.*?)\s*[\(\[]\s*([^\)\]]*?)\s*[\)\]]\s*$")
META_COLS = {"Группа", "Категория", "Товар Ид", "Товар номи", "Бренд", "Статус", "_excel_row"}


def split_name_unit(col: str) -> tuple[str, str | None]:
    """'Balandlik (sm)' → ('Balandlik', 'sm');  'Rang' → ('Rang', None)."""
    m = _UNIT_RE.match(col)
    if m and m.group(1).strip():
        return m.group(1).strip(), (m.group(2).strip() or None)
    return col.strip(), None


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def attr_slot(col: str) -> int | None:
    if col.lower() == "подкатегория":
        return 0
    m = _SUB_RE.match(col)
    return int(m.group(1)) if m else None


def is_products_file(headers: list[str]) -> bool:
    hs = {str(h).strip() for h in headers if h is not None}
    return set(REQUIRED) <= hs


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
    raw_attrs: dict[str, Any]          # ustun nomi → xom qiymat
    excel_row: int


@dataclass
class ProductsParseResult:
    products: dict[int, ParsedProduct]
    attr_cols: list[str]               # xususiyat ustunlari (fayldagi tartibda)
    rows_total: int
    errors: list[str] = field(default_factory=list)          # yuklanmagan qatorlar
    duplicates: list[dict] = field(default_factory=list)     # takrorlangan Ид lar


def parse_products(data: bytes) -> ProductsParseResult:
    df, _ = read_sheet_with_header(data, must_have=[COL_ID, COL_CAT])

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise LoaderError("Majburiy ustun(lar) yo'q: " + ", ".join(missing))

    attr_cols = [c for c in df.columns if c not in META_COLS and not str(c).lower().startswith("unnamed")]
    if not attr_cols:
        raise LoaderError("Xususiyat ustunlari topilmadi — majburiy ustunlardan keyin xususiyat ustunlari "
                          "bo'lishi kerak (masalan: Turi, Balandlik (sm), Rang)")
    names = [_norm(split_name_unit(c)[0]) for c in attr_cols]
    dup = sorted({c for c, n in zip(attr_cols, names) if names.count(n) > 1})
    if dup:
        raise LoaderError("Bir xil nomli xususiyat ustunlari: " + ", ".join(dup))

    res = ProductsParseResult(products={}, attr_cols=attr_cols, rows_total=len(df))
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

        raw_attrs: dict[str, Any] = {}
        for col in attr_cols:
            v = rec.get(col)
            num = to_number(v) if not isinstance(v, str) else None
            val = num if num is not None else clean_text(v)
            if val is not None:
                raw_attrs[col] = val

        p = ParsedProduct(
            product_id=pid, name=name, grp=(clean_text(rec.get(COL_GROUP)) or DEFAULT_GROUP).upper(),
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
    hidden_attrs: list[str]              # faylda endi yo'q → Mini App'dan yashirildi
    restored_attrs: list[str]            # yana ko'rinadigan bo'ldi
    changed_attrs: list[str]             # turi / birligi o'zgardi
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
        # guruh (КБТ/МБТ) — fayldagi qiymat bilan yangilanadi
        file_grp = {}
        for p in prods:
            if p.grp != DEFAULT_GROUP:
                file_grp[p.category] = p.grp
        for cname, g in file_grp.items():
            await con.execute("UPDATE categories SET grp = $2 WHERE id = $1 AND grp IS DISTINCT FROM $2",
                              existing[cname], g)

        # --- xususiyatlar: har kategoriya uchun ustun → slot ---
        defs: dict[int, list[dict]] = {}
        for r in await con.fetch("SELECT category_id, slot, source_col, name, data_type, unit, is_filter "
                                 "FROM category_attributes"):
            defs.setdefault(r["category_id"], []).append(dict(r))
        cat_name = {v: k for k, v in existing.items()}

        values: dict[int, dict[str, list[Any]]] = {}          # cid → ustun → qiymatlar
        for p in prods:
            vc = values.setdefault(existing[p.category], {})
            for col, v in p.raw_attrs.items():
                vc.setdefault(col, []).append(v)

        header_names = {_norm(split_name_unit(c)[0]) for c in parsed.attr_cols if attr_slot(c) is None}
        header_cols = {_norm(c) for c in parsed.attr_cols}
        legacy_slots = {s for c in parsed.attr_cols if (s := attr_slot(c)) is not None}
        slot_of: dict[tuple[int, str], int] = {}               # (cid, ustun) → slot
        dtype_of: dict[tuple[int, int], str] = {}
        new_attrs, hidden, restored, changed = [], [], [], []
        for cid, vc in values.items():
            cdefs = defs.setdefault(cid, [])
            used: set[int] = set()
            cols = [c for c in parsed.attr_cols if c in vc]      # shu kategoriyada qiymati bor ustunlar
            for pos, col in enumerate(cols):
                vals = vc[col]
                legacy = attr_slot(col)
                if legacy is not None:                         # eski format: tartib raqami bo'yicha
                    name, unit = col, None
                    d = next((x for x in cdefs if x["slot"] == legacy), None)
                else:                                          # yangi format: nomi bo'yicha
                    name, unit = split_name_unit(col)
                    d = next((x for x in cdefs if x["slot"] not in used and
                              (_norm(x["name"]) == _norm(name) or _norm(x["source_col"]) == _norm(col))), None)
                auto = _auto_type(vals)
                label = f"{cat_name[cid]} → {name if legacy is None else (d['name'] if d else col)}"
                if d is None:
                    slot = legacy if legacy is not None else max([x["slot"] for x in cdefs] + [-1]) + 1
                    d = {"category_id": cid, "slot": slot, "source_col": col, "name": name, "data_type": auto,
                         "unit": unit, "is_filter": True}
                    await con.execute(
                        """INSERT INTO category_attributes(category_id, slot, source_col, name, data_type, unit, sort_order)
                           VALUES ($1,$2,$3,$4,$5,$6,$7)""", cid, slot, col, name, auto, unit, pos)
                    cdefs.append(d)
                    new_attrs.append(f"{label}{f' ({unit})' if unit else ''} — {'son' if auto == 'number' else 'matn'}")
                else:
                    upd_type, upd_unit = d["data_type"], d["unit"]
                    if legacy is None:
                        if auto != d["data_type"]:
                            upd_type = auto
                            changed.append(f"{label}: turi {d['data_type']} → {auto}")
                        if unit and unit != d["unit"]:
                            upd_unit = unit
                            changed.append(f"{label}: birlik {d['unit'] or '—'} → {unit}")
                        elif not unit and d["unit"] and auto == "text":
                            upd_unit = None
                    elif d["data_type"] == "number" and auto == "text":
                        changed.append(f"{label}: son kutilgan, matn keldi — ustunlar surilgan bo'lishi mumkin!")
                    if not d["is_filter"]:
                        restored.append(label)
                    await con.execute(
                        """UPDATE category_attributes SET source_col=$3, name=$4, data_type=$5, unit=$6,
                                  sort_order=$7, is_filter=TRUE WHERE category_id=$1 AND slot=$2""",
                        cid, d["slot"], col, name if legacy is None else d["name"], upd_type, upd_unit, pos)
                    d.update(data_type=upd_type, unit=upd_unit, is_filter=True)
                used.add(d["slot"])
                slot_of[(cid, col)] = d["slot"]
                dtype_of[(cid, d["slot"])] = d["data_type"]
            for x in cdefs:                                    # faylda endi yo'q xususiyatlar
                # faqat ustunning o'zi faylda bo'lmasa yashiriladi; ustun bor-u bo'sh bo'lsa (masalan,
                # faqat yangi SKU'lar yuklanganda) — xususiyat joyida qoladi
                in_file = (_norm(x["name"]) in header_names or _norm(x["source_col"]) in header_cols
                           or x["slot"] in legacy_slots)
                if x["slot"] not in used and x["is_filter"] and not in_file:
                    await con.execute("UPDATE category_attributes SET is_filter=FALSE WHERE category_id=$1 AND slot=$2",
                                      cid, x["slot"])
                    hidden.append(f"{cat_name[cid]} → {x['name']}")

        # --- attrs JSON (tur bo'yicha) ---
        non_numeric: dict[tuple[str, str], set[str]] = {}
        rows = []
        by_cat: dict[str, int] = {}
        for p in prods:
            cid = existing[p.category]
            attrs: dict[str, Any] = {}
            for col, v in p.raw_attrs.items():
                slot = slot_of[(cid, col)]
                if dtype_of.get((cid, slot)) == "number":
                    n = to_number(v)
                    if n is None:
                        non_numeric.setdefault((p.category, col), set()).add(str(v))
                        attrs[f"a{slot}"] = str(v)
                    else:
                        attrs[f"a{slot}"] = n
                else:
                    attrs[f"a{slot}"] = str(v)
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

        # mas'ullar ro'yxati (yuklangan bo'lsa) yangi kategoriyalarga ham qo'llanadi
        from loaders_owners import apply_owner_list
        await apply_owner_list(con)

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
        hidden_attrs=hidden,
        restored_attrs=restored,
        changed_attrs=changed,
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
        out += ["\n🆕 <b>Yangi xususiyatlar:</b>", lst(r.new_attrs)]
    if r.hidden_attrs:
        out += ["\n🙈 <b>Faylda yo'q — Mini App'dan yashirildi:</b>", lst(r.hidden_attrs)]
    if r.restored_attrs:
        out += ["\n👁 <b>Yana ko'rinadi:</b>", lst(r.restored_attrs)]
    if r.changed_attrs:
        out += ["\n✏️ <b>Xususiyat o'zgarishlari:</b>", lst(r.changed_attrs)]
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
