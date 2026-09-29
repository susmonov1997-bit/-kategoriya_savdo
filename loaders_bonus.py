"""Brend × kategoriya bo'yicha qo'shimcha daromad % spravochnigi (dop doxod + retro + kompensatsiya).

Ustunlar:
  Категория               — ixtiyoriy qiymat: bo'sh bo'lsa, brendning barcha kategoriyalariga qo'llanadi
  Бренд                   — majburiy
  Қўшимча даромад %       — sarlavhasida '%' bo'lgan ustun; 3.5 = 3.5%
                            (Excel foiz formatidagi 0.035 ham tushuniladi — hisobotda aytiladi)

Qoidalar:
  * Har yuklashda jadval TO'LIQ almashtiriladi (fayl — to'liq amaldagi ro'yxat).
  * % hisob paytida qo'llanadi, shuning uchun butun tarix yangi % bilan qayta hisoblanadi.
  * Foizi bo'sh qator — o'rnatilmagan (0%) hisoblanadi.
"""
from __future__ import annotations

import asyncio
import html
import json
from dataclasses import dataclass, field

import asyncpg

from loaders_common import LoaderError, clean_text, name_key, read_sheet_with_header, to_number

COL_CAT = "Категория"
COL_BRAND = "Бренд"
MAX_PCT = 100


def pct_column(headers) -> str | None:
    return next((h for h in headers if "%" in h), None)


def is_bonus_file(headers: set[str]) -> bool:
    return COL_BRAND in headers and pct_column(headers) is not None and "Товар Ид" not in headers


@dataclass
class BonusLoadReport:
    upload_id: int
    rows_total: int
    loaded: int
    empty_pct: int
    fraction_mode: bool
    by_category: dict[str, int] = field(default_factory=dict)
    unknown_brands: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    no_pct_brands: list[dict] = field(default_factory=list)    # savdosi bor, faylda umuman yo'q (0% yozilgani emas)
    totals: dict = field(default_factory=dict)                 # front / qo'shimcha / gross marja


def _parse(data: bytes) -> tuple[list[dict], int, int, bool, list[str]]:
    df, _ = read_sheet_with_header(data, must_have=[COL_BRAND])
    pcol = pct_column(df.columns)
    if pcol is None:
        raise LoaderError("Foiz ustuni topilmadi (sarlavhasida '%' bo'lishi kerak)")
    rows, errors, empty = [], [], 0
    for rec in df.to_dict("records"):
        r = rec["_excel_row"]
        brand = clean_text(rec.get(COL_BRAND))
        cat = clean_text(rec.get(COL_CAT)) if COL_CAT in df.columns else None
        raw = rec.get(pcol)
        if isinstance(raw, str):
            raw = raw.replace("%", "")
        pct = to_number(raw)
        if not brand:
            errors.append(f"{r}-qator: «Бренд» bo'sh")
            continue
        if pct is None:
            if clean_text(rec.get(pcol)):
                errors.append(f"{r}-qator: foiz noto'g'ri ({rec.get(pcol)!r})")
            else:
                empty += 1
            continue
        rows.append({"row": r, "brand": brand, "cat": cat, "pct": float(pct)})

    nonzero = [x["pct"] for x in rows if x["pct"]]
    fraction = bool(nonzero) and max(nonzero) <= 1
    if fraction:
        for x in rows:
            x["pct"] = round(x["pct"] * 100, 3)
    for x in list(rows):
        if not 0 <= x["pct"] <= MAX_PCT:
            errors.append(f"{x['row']}-qator: foiz {x['pct']} — 0…{MAX_PCT} oralig'ida bo'lishi kerak")
            rows.remove(x)
    return rows, len(df), empty, fraction, errors


async def load_bonus(
    pool: asyncpg.Pool, data: bytes, file_name: str | None = None, tg_user_id: int | None = None
) -> BonusLoadReport:
    async with pool.acquire() as con:
        upload_id = await con.fetchval(
            "INSERT INTO uploads(kind, file_name, tg_user_id) VALUES ('bonus',$1,$2) RETURNING id",
            file_name, tg_user_id)
        try:
            rows, total, empty, fraction, errors = await asyncio.to_thread(_parse, data)
            rep = BonusLoadReport(upload_id=upload_id, rows_total=total, loaded=0, empty_pct=empty,
                                  fraction_mode=fraction, errors=errors)
            cats = {name_key(r["name"]): (r["id"], r["name"]) for r in await con.fetch("SELECT id, name FROM categories")}
            # brendlar: products.brand ning aniq yozilishiga moslaymiz
            brands_all: dict[str, str] = {}
            brands_by_cat: dict[tuple[int, str], str] = {}
            for r in await con.fetch("SELECT DISTINCT category_id, brand FROM products"):
                brands_all[name_key(r["brand"])] = r["brand"]
                brands_by_cat[(r["category_id"], name_key(r["brand"]))] = r["brand"]

            final: dict[tuple[int | None, str], dict] = {}
            for x in rows:
                cid, cname = None, "Barcha kategoriyalar"
                if x["cat"]:
                    if name_key(x["cat"]) not in cats:
                        rep.errors.append(f"{x['row']}-qator: kategoriya bazada yo'q — {x['cat']}")
                        continue
                    cid, cname = cats[name_key(x["cat"])]
                bkey = name_key(x["brand"])
                brand = brands_by_cat.get((cid, bkey)) if cid else brands_all.get(bkey)
                if brand is None:
                    rep.unknown_brands.append(f"{x['brand']} ({cname})")
                    continue
                k = (cid, brand)
                if k in final:
                    rep.duplicates.append(f"{brand} / {cname}: {final[k]['row']} va {x['row']}-qatorlar → {x['row']} olindi")
                final[k] = {**x, "cname": cname}

            async with con.transaction():
                await con.execute("DELETE FROM brand_bonus")
                await con.executemany(
                    "INSERT INTO brand_bonus(category_id, brand, pct, upload_id) VALUES ($1,$2,$3,$4)",
                    [(cid, brand, v["pct"], upload_id) for (cid, brand), v in final.items()])
            rep.loaded = len(final)
            for v in final.values():
                rep.by_category[v["cname"]] = rep.by_category.get(v["cname"], 0) + 1

            rep.no_pct_brands = [dict(r) | {"amount": float(r["amount"])} for r in await con.fetch(
                """SELECT c.name category, e.brand, sum(e.amount) amount
                   FROM sales_enriched e JOIN categories c ON c.id = e.category_id
                   WHERE NOT EXISTS (SELECT 1 FROM brand_bonus b WHERE b.brand = e.brand
                                     AND (b.category_id = e.category_id OR b.category_id IS NULL))
                   GROUP BY 1, 2 HAVING sum(e.amount) > 0 ORDER BY 3 DESC""")]
            t = await con.fetchrow(
                """SELECT min(sale_date) d1, max(sale_date) d2, sum(amount) amount, sum(gross_margin) gm,
                          sum(supplier_income) si, sum(margin) m FROM sales_enriched""")
            if t["amount"]:
                rep.totals = {"period": f"{t['d1']} — {t['d2']}", "amount": float(t["amount"]),
                              "gross": float(t["gm"]), "income": float(t["si"]), "margin": float(t["m"])}
        except Exception as e:
            await con.execute("UPDATE uploads SET status='failed', finished_at=now(), report=$2 WHERE id=$1",
                              upload_id, json.dumps({"error": str(e)}, ensure_ascii=False))
            raise
        await con.execute(
            "UPDATE uploads SET status='done', finished_at=now(), rows_total=$2, rows_loaded=$3, report=$4 WHERE id=$1",
            upload_id, rep.rows_total, rep.loaded, json.dumps(rep.__dict__, ensure_ascii=False, default=str))
        return rep


def _mln(v: float) -> str:
    return f"{v / 1e6:,.1f}".replace(",", " ")


def format_report(r: BonusLoadReport, max_items: int = 15) -> str:
    esc = lambda s: html.escape(str(s), quote=False)  # noqa: E731

    def lst(items):
        out = [f"  • {esc(x)}" for x in items[:max_items]]
        return out + ([f"  … yana {len(items) - max_items} ta"] if len(items) > max_items else [])

    out = [
        f"✅ <b>Qo'shimcha daromad % yuklandi</b> (#{r.upload_id})",
        f"Qatorlar: {r.rows_total} → o'rnatildi: <b>{r.loaded}</b> (foizi bo'sh: {r.empty_pct})",
        *[f"  • {esc(c)}: {n} brend" for c, n in r.by_category.items()],
    ]
    if r.fraction_mode:
        out.append("ℹ️ Foizlar Excel foiz formatida (0.035) deb tushunildi → 3.5%")
    if r.totals:
        t = r.totals
        p = lambda v: f"{v / t['amount'] * 100:.1f}%"  # noqa: E731
        out += [
            f"\n<b>Butun baza bo'yicha</b> ({t['period']}):",
            f"  Savdo: {_mln(t['amount'])} mln",
            f"  Front marja: {_mln(t['gross'])} mln ({p(t['gross'])})",
            f"  + Qo'shimcha daromad: {_mln(t['income'])} mln ({p(t['income'])})",
            f"  = <b>Gross marja: {_mln(t['margin'])} mln ({p(t['margin'])})</b>",
        ]
    if r.no_pct_brands:
        out.append(f"\n⚠️ <b>Savdosi bor, faylda yo'q brendlar: {len(r.no_pct_brands)}</b> (0% hisoblanadi)")
        out += lst([f"{x['category']} / {x['brand']}: {_mln(x['amount'])} mln" for x in r.no_pct_brands])
    if r.unknown_brands:
        out += [f"\n⚠️ <b>Spravochnikda yo'q brend: {len(r.unknown_brands)}</b> (yuklanmadi)", *lst(r.unknown_brands)]
    if r.duplicates:
        out += [f"\n⚠️ <b>Takrorlangan: {len(r.duplicates)}</b>", *lst(r.duplicates)]
    if r.errors:
        out += [f"\n❌ <b>Xato qatorlar: {len(r.errors)}</b>", *lst(r.errors)]
    return "\n".join(out)
