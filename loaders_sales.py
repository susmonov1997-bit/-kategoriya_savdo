"""Kunlik savdo fayli loaderi ("Chiqim tovarlar ….xlsx").

Kelishilgan qoidalar:
  * Savdo summasi = Жами(Чиқим нархи).
  * Tannarx = Сони × Кирим нархи × kurs (Кирим нархи(Валюта тури) = Доллар) yoki Сони × Кирим нархи (Сум).
    Kurs — fx_rates jadvalidan, sana bo'yicha (admin /kurs bilan o'zgartiradi); bazada dollar va so'm
    qismlari alohida saqlanadi, kurs hisoblash paytida qo'llanadi.
    Agar faylda Кирим нархи / Валюта тури ustunlari bo'lmasa — eski usul: tannarx = Жами(Кирим нархи).
  * Front marja = summa − tannarx;  gross marja = front marja + tannarx × brend %.
  * Бонус qatorlari (narx 0) donaga qo'shiladi, tannarxi marjadan ayriladi (is_bonus belgisi bilan saqlanadi).
  * Faqat bazadagi (spravochnikdagi) kategoriyalar yuklanadi; "К"/"M"/"Z" va boshqalar tashlanadi.
  * Qaytarishlar hisobga olinmaydi (faylda yo'q).
  * Spravochnikda yo'q SKU / filial qatorlari yuklanmaydi va ro'yxat qilib qaytariladi.
  * Fayldagi har bir sana uchun bazadagi eski savdo to'liq almashtiriladi (dublikat yo'q).
  * Mijoz, xodim, shartnoma ma'lumotlari o'qilmaydi va saqlanmaydi.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

import asyncpg
import pandas as pd

from fact import refresh_dates
from loaders_common import LoaderError, clean_text, name_key, read_sheet_with_header, update_product_names

COL_BRANCH = "Филиал"
COL_CAT = "Категория"
COL_ID = "Товар ИД"
COL_NAME = "Товар номи"
COL_VAR = "Товар Вариант номи"      # to'liq nom (rang/hajm bilan) — spravochnikda yo'qlar ro'yxatida shu ko'rsatiladi
COL_DATE = "Сана"
COL_BONUS = "Бонус"
COL_QTY = "Сони"
COL_COST = "Жами(Кирим нархи)"
COL_AMOUNT = "Жами(Чиқим нархи)"
COL_UNIT = "Кирим нархи"
COL_CUR = "Кирим нархи(Валюта тури)"
COL_BRAND = "Бранд"
COL_SUPPLIER = "Таъминотчи"
COL_SHOP = "Дукон номи"
EXCLUDE_COLS = [COL_SUPPLIER, COL_SHOP]   # shu ustunlardan birida biror qiymat bo'lsa — qator umuman hisobga olinmaydi
USD_MAX_UNIT = 10_000    # «Доллар» deb yozilgan, lekin kirim narxi bundan katta — so'm deb olinadi (fayldagi xato)
OPTIONAL = [COL_BONUS, COL_UNIT, COL_CUR, COL_BRAND, COL_VAR] + EXCLUDE_COLS
_USD = {"доллар", "долл", "dollar", "usd", "$", "у.е.", "уе"}
_UZS = {"сум", "сўм", "сом", "so'm", "som", "sum", "uzs"}


# «К» bilan belgilangan kategoriyalar (masalan «"К" Б/У Смартфонлар») — kompaniya jamisiga kirmaydi.
# Faqat alohida «К» belgisi: «Кондиционерлар», «Кулерлар» kabi oddiy nomlar ushlanmaydi.
_K_RE = re.compile(r'^\s*["«“„\'`]?\s*[КK]\s*(["»”“\'`]|\s)')


def is_k_category(v) -> bool:
    t = clean_text(v)
    return bool(t and _K_RE.match(t))


def norm_currency(v) -> str | None:
    t = clean_text(v)
    if not t:
        return None
    t = t.lower().replace("ʻ", "'").replace("’", "'").replace("`", "'").strip(" .")
    if t in _USD or t.startswith("долл") or t.startswith("dol"):
        return "USD"
    if t in _UZS or t.startswith("сўм") or t.startswith("сум"):
        return "UZS"
    return None
REQUIRED = [COL_BRANCH, COL_CAT, COL_ID, COL_NAME, COL_DATE, COL_QTY, COL_COST, COL_AMOUNT]


def is_sales_file(headers: set[str]) -> bool:
    return {COL_ID, COL_DATE, COL_BRANCH, COL_AMOUNT} <= headers


def _to_date(v) -> date | None:
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    t = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    return None


def _parse_dates(s: pd.Series) -> pd.Series:
    """'2026-09-01', '01.09.2026' yoki Excel sanasi → date (noto'g'ri → None).
    Faylda noyob sanalar kam — har bir noyob qiymat bir marta o'giriladi."""
    cache = {v: _to_date(v) for v in s.dropna().unique()}
    return s.map(lambda v: cache.get(v) if v is not None and v == v else None)


@dataclass
class SalesLoadReport:
    upload_id: int
    rows_total: int
    dates: list[str]
    rows_in_scope: int
    rows_loaded: int
    out_of_scope_rows: int
    out_of_scope_amount: float
    by_category: list[dict]      # [{category, amount, qty, bonus_qty, gross, gross_pct, margin, margin_pct}]
    replaced: dict = field(default_factory=dict)  # {"rows":…, "amount":…} — almashtirilgan eski ma'lumot
    unknown_skus: list[dict] = field(default_factory=list)
    unknown_branches: list[dict] = field(default_factory=list)
    category_mismatch: int = 0
    errors: list[str] = field(default_factory=list)
    cost_mode: str = "split"                  # split — Сони×Кирим нархи×kurs; legacy — Жами(Кирим нархи)
    fx: list[dict] = field(default_factory=list)   # fayl sanalariga qo'llangan kurslar
    company: dict = field(default_factory=dict)    # kompaniya jami: amount, rows, k_excluded, unknown_branch_rows
    pending: dict = field(default_factory=dict)    # kutayotgan savdo: rows, amount, skus
    names_updated: int = 0                         # tovar nomi (variant nomi) yangilangan SKU soni
    excluded: dict = field(default_factory=dict)   # «Таъминотчи»/«Дукон номи» to'ldirilgan, hisobga olinmagan: rows, amount
    cur_fixed: dict = field(default_factory=dict)  # «Доллар» → so'm tuzatilgan qatorlar: rows, skus, examples


def _fmt_mln(v: float) -> str:
    return f"{v / 1e6:,.1f}".replace(",", " ")


async def load_sales(
    pool: asyncpg.Pool, data: bytes, file_name: str | None = None, tg_user_id: int | None = None
) -> SalesLoadReport:
    async with pool.acquire() as con:
        upload_id = await con.fetchval(
            "INSERT INTO uploads(kind, file_name, tg_user_id) VALUES ('sales',$1,$2) RETURNING id",
            file_name, tg_user_id,
        )
        try:
            rep = await _load(con, data, upload_id)
        except Exception as e:
            await con.execute(
                "UPDATE uploads SET status='failed', finished_at=now(), report=$2 WHERE id=$1",
                upload_id, json.dumps({"error": str(e)}, ensure_ascii=False))
            raise
        await con.execute(
            "UPDATE uploads SET status='done', finished_at=now(), rows_total=$2, rows_loaded=$3, report=$4 WHERE id=$1",
            upload_id, rep.rows_total, rep.rows_loaded, json.dumps(rep.__dict__, ensure_ascii=False, default=str))
        return rep


async def _load(con: asyncpg.Connection, data: bytes, upload_id: int) -> SalesLoadReport:
    df, _ = await asyncio.to_thread(read_sheet_with_header, data, [COL_ID, COL_DATE, COL_BRANCH], 15,
                                    REQUIRED + OPTIONAL)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise LoaderError("Majburiy ustun(lar) yo'q: " + ", ".join(missing))

    split = COL_UNIT in df.columns and COL_CUR in df.columns
    cur_fixed: dict = {}
    keep = REQUIRED + [c for c in OPTIONAL if c in df.columns] + ["_excel_row"]
    df = df[keep].copy()
    rows_total = len(df)
    # «Таъминотчи» yoki «Дукон номи» to'ldirilgan qatorlar — savdo emas: na bizning savdoga, na kompaniya jamiga kiradi
    excl = pd.Series(False, index=df.index)
    for c in EXCLUDE_COLS:
        if c in df.columns:
            excl |= df[c].map(lambda v: clean_text(v) is not None)
    excluded = {}
    if excl.any():
        excluded = {"rows": int(excl.sum()),
                    "amount": float(pd.to_numeric(df.loc[excl, COL_AMOUNT], errors="coerce").fillna(0).sum())}
        df = df[~excl].copy()
        if df.empty:
            raise LoaderError("Fayldagi hamma qatorda «Таъминотчи» yoki «Дукон номи» to'ldirilgan — yuklanadigan savdo yo'q")
    # nom: «Товар Вариант номи» (bo'lsa), aks holda «Товар номи»
    df["_pname"] = df[COL_VAR].map(clean_text) if COL_VAR in df.columns else None
    df["_pname"] = df["_pname"].fillna(df[COL_NAME].map(clean_text)) if COL_VAR in df.columns else df[COL_NAME].map(clean_text)

    # --- turlarni o'girish va xato qatorlar ---
    df["d"] = _parse_dates(df[COL_DATE])
    df["pid"] = pd.to_numeric(df[COL_ID], errors="coerce")
    for src, dst in ((COL_QTY, "qty"), (COL_AMOUNT, "amount"), (COL_COST, "cost")):
        df[dst] = pd.to_numeric(df[src], errors="coerce")
    df["is_bonus"] = df[COL_BONUS].notna() if COL_BONUS in df.columns else False
    if split:
        df["unit"] = pd.to_numeric(df[COL_UNIT], errors="coerce")
        cache = {v: norm_currency(v) for v in df[COL_CUR].dropna().unique()}
        df["cur"] = df[COL_CUR].map(lambda v: cache.get(v) if v is not None and v == v else None)
        # valyuta «Доллар», lekin kirim narxi USD_MAX_UNIT dan katta — fayldagi xato: aslida so'mda
        wrong = (df["cur"] == "USD") & (df["unit"] > USD_MAX_UNIT)
        if wrong.any():
            df.loc[wrong, "cur"] = "UZS"
            ex = df[wrong].drop_duplicates("pid").head(5)
            cur_fixed = {"rows": int(wrong.sum()), "skus": int(df.loc[wrong, "pid"].nunique()),
                         "examples": [f"{int(r['pid']) if r['pid'] == r['pid'] else '—'} · {r['_pname'] or ''} — {r['unit']:,.0f}".replace(",", " ")
                                      for _, r in ex.iterrows()]}
    df["cat_key"] = df[COL_CAT].map(name_key)
    df["br_key"] = df[COL_BRANCH].map(name_key)

    errors: list[str] = []
    checks = [
        (df["d"].isna(), "«Сана» noto'g'ri"),
        (df["pid"].isna() | (df["pid"] <= 0) | (df["pid"] % 1 != 0), "«Товар ИД» noto'g'ri"),
        (df["qty"].isna() | (df["qty"] <= 0), "«Сони» bo'sh yoki ≤0"),
        (df["amount"].isna() | (df["amount"] < 0), "«Жами(Чиқим нархи)» noto'g'ri"),
        (df["cost"].isna() | (df["cost"] < 0), "«Жами(Кирим нархи)» noto'g'ri"),
        (df["br_key"].isna(), "«Филиал» bo'sh"),
    ]
    if split:
        checks += [
            (df["unit"].isna() | (df["unit"] < 0), "«Кирим нархи» bo'sh yoki noto'g'ri"),
            (df["cur"].isna(), "«Кирим нархи(Валюта тури)» noma'lum (Доллар yoki Сум bo'lishi kerak)"),
        ]
    bad = pd.Series(False, index=df.index)
    for mask, msg in checks:
        new = mask & ~bad
        errors += [f"{r}-qator: {msg}" for r in df.loc[new, "_excel_row"].head(50)]
        if new.sum() > 50:
            errors.append(f"… «{msg}» yana {int(new.sum()) - 50} qator")
        bad |= mask
    good = df[~bad].copy()
    if good.empty:
        raise LoaderError("Faylda birorta ham to'g'ri qator yo'q")
    good["pid"] = good["pid"].astype("int64")
    if split:
        good["cost_usd"] = (good["qty"] * good["unit"]).where(good["cur"] == "USD", 0.0)
        good["cost_uzs"] = (good["qty"] * good["unit"]).where(good["cur"] == "UZS", 0.0)
    else:
        good["cost_usd"] = 0.0
        good["cost_uzs"] = 0.0
    file_dates: list[date] = sorted(good["d"].unique())

    # --- kompaniya jami (ulush uchun): barcha kategoriyalar, «К» belgililaridan tashqari ---
    brs = {r["name_key"]: r["id"] for r in await con.fetch("SELECT id, name_key FROM branches")}
    good["branch_id_all"] = good["br_key"].map(brs)
    good["is_k"] = good[COL_CAT].map(is_k_category)
    k_rows = good[good["is_k"]]
    k_excluded = [{"category": clean_text(c), "rows": len(g), "amount": float(g["amount"].sum())}
                  for c, g in k_rows.groupby(COL_CAT)]
    comp_src = good[~good["is_k"] & good["branch_id_all"].notna()]
    comp = (comp_src.groupby(["d", "branch_id_all"], as_index=False)
            .agg(qty=("qty", "sum"), amount=("amount", "sum"), cost=("cost", "sum"),
                 cost_usd=("cost_usd", "sum"), cost_uzs=("cost_uzs", "sum")))
    comp_records = [(r.d, int(r.branch_id_all), float(r.qty), float(r.amount), float(r.cost), float(r.cost_usd),
                     float(r.cost_uzs), split, upload_id) for r in comp.itertuples(index=False)]
    company_info = {
        "amount": float(comp_src["amount"].sum()), "rows": int(len(comp_src)),
        "k_excluded": sorted(k_excluded, key=lambda x: -x["amount"]),
        "unknown_branch_rows": int((~good["is_k"] & good["branch_id_all"].isna()).sum()),
    }

    # --- qamrov: faqat bazadagi kategoriyalar ---
    cats = {name_key(r["name"]): r["id"] for r in await con.fetch("SELECT id, name FROM categories")}
    in_scope = good["cat_key"].isin(cats.keys())
    out_rows, out_amount = int((~in_scope).sum()), float(good.loc[~in_scope, "amount"].sum())
    sc = good[in_scope].copy()
    if sc.empty:
        raise LoaderError(
            "Faylda spravochnikdagi kategoriyalar bo'yicha birorta qator yo'q — "
            "boshqa fayl yuborilgan bo'lishi mumkin. Hech narsa o'zgartirilmadi.")

    # --- spravochnik bilan solishtirish ---
    prods = {r["product_id"]: r["category_id"] for r in await con.fetch("SELECT product_id, category_id FROM products")}
    sc["branch_id"] = sc["br_key"].map(brs)
    sc["prod_cat"] = sc["pid"].map(prods)

    unk_sku = sc[sc["prod_cat"].isna()]
    unknown_skus = [
        {"product_id": int(pid), "name": g["_pname"].iloc[0], "category": clean_text(g[COL_CAT].iloc[0]),
         "brand": clean_text(g[COL_BRAND].iloc[0]) if COL_BRAND in g.columns else None,
         "rows": len(g), "amount": float(g["amount"].sum()), "qty": float(g["qty"].sum())}
        for pid, g in unk_sku.groupby("pid")
    ]
    unk_br = sc[sc["branch_id"].isna()]
    unknown_branches = [
        {"name": clean_text(g[COL_BRANCH].iloc[0]), "rows": len(g), "amount": float(g["amount"].sum())}
        for _, g in unk_br.groupby("br_key")
    ]
    ok = sc[sc["prod_cat"].notna() & sc["branch_id"].notna()].copy()

    # --- kutayotgan savdo: spravochnikda yo'q SKU/filial qatorlari (bizning kategoriyalar — spravochnikdagi
    #     yoki mas'ullar ro'yxatidagi) saqlanadi; spravochnik yuklanganda avtomatik ulanadi ---
    own_keys = set(cats) | {r["name_key"] for r in await con.fetch("SELECT name_key FROM category_owner_list")}
    pend_src = good[~good["is_k"] & good["cat_key"].isin(own_keys) & ~good.index.isin(ok.index)].copy()
    pend_src["brand_txt"] = pend_src[COL_BRAND].map(clean_text) if COL_BRAND in pend_src.columns else None
    pend = (pend_src.groupby(["d", "br_key", "pid", "is_bonus"], as_index=False)
            .agg(branch_name=(COL_BRANCH, "first"), pname=("_pname", "first"), category=(COL_CAT, "first"),
                 brand=("brand_txt", "first"), qty=("qty", "sum"), amount=("amount", "sum"), cost=("cost", "sum"),
                 cost_usd=("cost_usd", "sum"), cost_uzs=("cost_uzs", "sum"))) if len(pend_src) else pend_src
    pend_records = [(r.d, r.br_key, clean_text(r.branch_name), int(r.pid), clean_text(r.pname), clean_text(r.category),
                     r.brand if isinstance(r.brand, str) else None, bool(r.is_bonus), float(r.qty), float(r.amount),
                     float(r.cost), float(r.cost_usd), float(r.cost_uzs), split, upload_id)
                    for r in pend.itertuples(index=False)] if len(pend_src) else []
    cpend_src = good[~good["is_k"] & good["branch_id_all"].isna()]
    cpend = (cpend_src.groupby(["d", "br_key"], as_index=False)
             .agg(branch_name=(COL_BRANCH, "first"), qty=("qty", "sum"), amount=("amount", "sum"), cost=("cost", "sum"),
                  cost_usd=("cost_usd", "sum"), cost_uzs=("cost_uzs", "sum"))) if len(cpend_src) else cpend_src
    cpend_records = [(r.d, r.br_key, clean_text(r.branch_name), float(r.qty), float(r.amount), float(r.cost),
                      float(r.cost_usd), float(r.cost_uzs), split, upload_id)
                     for r in cpend.itertuples(index=False)] if len(cpend_src) else []
    new_cat = pend_src["cat_key"].isin(own_keys - set(cats)) if len(pend_src) else pend_src.get("cat_key")
    pending_info = {"rows": len(pend_records), "amount": float(pend_src["amount"].sum()) if len(pend_src) else 0.0,
                    "skus": int(pend_src["pid"].nunique()) if len(pend_src) else 0,
                    # mas'ullar ro'yxatida bor, lekin tovar spravochnigida hali umuman yo'q kategoriyalar
                    "new_cat_amount": float(pend_src.loc[new_cat, "amount"].sum()) if len(pend_src) else 0.0,
                    "new_cats": sorted({clean_text(c) for c in pend_src.loc[new_cat, COL_CAT]}) if len(pend_src) else []}
    mismatch = int((ok["cat_key"].map(cats) != ok["prod_cat"]).sum())   # spravochnik kategoriyasi ustun

    agg = (
        ok.groupby(["d", "branch_id", "pid", "is_bonus"], as_index=False)
          .agg(qty=("qty", "sum"), amount=("amount", "sum"), cost=("cost", "sum"),
               cost_usd=("cost_usd", "sum"), cost_uzs=("cost_uzs", "sum"))
    )
    records = [
        (r.d, int(r.branch_id), int(r.pid), bool(r.is_bonus), float(r.qty), float(r.amount), float(r.cost),
         float(r.cost_usd), float(r.cost_uzs), split, upload_id)
        for r in agg.itertuples(index=False)
    ]

    # --- yozish: fayldagi sanalar to'liq almashtiriladi ---
    async with con.transaction():
        old = await con.fetchrow(
            "SELECT count(*) n, coalesce(sum(amount),0) amount FROM sales_daily WHERE sale_date = ANY($1::date[])",
            file_dates)
        await con.execute("DELETE FROM sales_daily WHERE sale_date = ANY($1::date[])", file_dates)
        await con.copy_records_to_table(
            "sales_daily", records=records,
            columns=["sale_date", "branch_id", "product_id", "is_bonus", "qty", "amount", "cost",
                     "cost_usd", "cost_uzs", "cost_split", "upload_id"])
        await con.execute("DELETE FROM company_daily WHERE sale_date = ANY($1::date[])", file_dates)
        if comp_records:
            await con.copy_records_to_table(
                "company_daily", records=comp_records,
                columns=["sale_date", "branch_id", "qty", "amount", "cost", "cost_usd", "cost_uzs", "cost_split",
                         "upload_id"])
        await con.execute("DELETE FROM sales_pending WHERE sale_date = ANY($1::date[])", file_dates)
        if pend_records:
            await con.copy_records_to_table(
                "sales_pending", records=pend_records,
                columns=["sale_date", "branch_key", "branch_name", "product_id", "product_name", "category", "brand",
                         "is_bonus", "qty", "amount", "cost", "cost_usd", "cost_uzs", "cost_split", "upload_id"])
        await con.execute("DELETE FROM company_pending WHERE sale_date = ANY($1::date[])", file_dates)
        if cpend_records:
            await con.copy_records_to_table(
                "company_pending", records=cpend_records,
                columns=["sale_date", "branch_key", "branch_name", "qty", "amount", "cost", "cost_usd", "cost_uzs",
                         "cost_split", "upload_id"])
        await refresh_dates(con, file_dates)          # tayyor hisob-kitob jadvali (sales_fact) — shu sanalar
        # tovar nomi = «Товар Вариант номи» (bo'lmasa «Товар номи»); bir SKU bir necha kunda — eng oxirgi sanadagisi
        known = sc[sc["prod_cat"].notna()].sort_values("d")
        names_updated = await update_product_names(con, known.groupby("pid")["_pname"].last().to_dict())
        by_cat = await con.fetch(
            """SELECT c.name category, sum(e.amount) amount, sum(e.qty) qty,
                      sum(e.qty) FILTER (WHERE e.is_bonus) bonus_qty,
                      sum(e.gross_margin) gross, sum(e.margin) margin
               FROM sales_enriched e JOIN categories c ON c.id = e.category_id
               WHERE e.upload_id = $1 GROUP BY c.name ORDER BY amount DESC""", upload_id)
        fx = await con.fetch(
            """SELECT valid_from, valid_to, rate FROM fx_periods
               WHERE valid_to > $1::date AND valid_from <= $2::date ORDER BY valid_from""",
            file_dates[0], file_dates[-1])

    return SalesLoadReport(
        upload_id=upload_id,
        rows_total=rows_total,
        dates=[d.isoformat() for d in file_dates],
        rows_in_scope=len(sc),
        rows_loaded=len(ok),
        out_of_scope_rows=out_rows,
        out_of_scope_amount=out_amount,
        by_category=[{
            "category": r["category"], "amount": float(r["amount"]), "qty": float(r["qty"]),
            "bonus_qty": float(r["bonus_qty"] or 0), "gross": float(r["gross"]), "margin": float(r["margin"]),
            "gross_pct": round(float(r["gross"]) / float(r["amount"]) * 100, 1) if r["amount"] else None,
            "margin_pct": round(float(r["margin"]) / float(r["amount"]) * 100, 1) if r["amount"] else None,
        } for r in by_cat],
        replaced={"rows": old["n"], "amount": float(old["amount"])},
        unknown_skus=sorted(unknown_skus, key=lambda x: -x["amount"]),
        unknown_branches=sorted(unknown_branches, key=lambda x: -x["amount"]),
        category_mismatch=mismatch,
        errors=errors,
        cost_mode="split" if split else "legacy",
        fx=[{"from": max(r["valid_from"], file_dates[0]).isoformat(), "rate": float(r["rate"])} for r in fx],
        company=company_info,
        pending=pending_info,
        names_updated=names_updated,
        cur_fixed=cur_fixed,
        excluded=excluded,
    )


def format_report(r: SalesLoadReport, max_items: int = 15) -> str:
    esc = lambda s: html.escape(str(s), quote=False)  # noqa: E731
    d = r.dates
    period = d[0] if len(d) == 1 else f"{d[0]} — {d[-1]} ({len(d)} kun)"
    out = [
        f"✅ <b>Savdo yuklandi</b> (#{r.upload_id})",
        f"Sana: <b>{period}</b>",
        f"Qatorlar: {r.rows_total} → spravochnik kategoriyalari: {r.rows_in_scope} → yuklandi: <b>{r.rows_loaded}</b>",
        "",
    ]
    tot_a = sum(c["amount"] for c in r.by_category)
    tot_m = sum(c["margin"] for c in r.by_category)
    for c in r.by_category:
        bonus = f", bonus {c['bonus_qty']:.0f}" if c["bonus_qty"] else ""
        out.append(f"• <b>{esc(c['category'])}</b>: {_fmt_mln(c['amount'])} mln · {c['qty']:.0f} dona{bonus} · "
                   f"front marja {c['gross_pct']}% · gross marja {c['margin_pct']}%")
    if tot_a:
        tot_g = sum(c["gross"] for c in r.by_category)
        out.append(f"<b>Jami: {_fmt_mln(tot_a)} mln · front marja {tot_g / tot_a * 100:.1f}% · "
                   f"gross marja {tot_m / tot_a * 100:.1f}%</b>")
        out.append("<i>gross marja = front marja + qo'shimcha daromad (brend %)</i>")
    if r.company.get("amount"):
        mine = sum(c["amount"] for c in r.by_category)
        out.append(f"🏢 Kompaniya jami («К»siz): {_fmt_mln(r.company['amount'])} mln · "
                   f"bizning ulush {mine / r.company['amount'] * 100:.1f}%")
        if r.company.get("k_excluded"):
            ks = ", ".join(f"{esc(k['category'])} ({_fmt_mln(k['amount'])} mln)" for k in r.company["k_excluded"][:5])
            out.append(f"<i>jamidan chiqarildi: {ks}</i>")
    if r.cost_mode == "split":
        rates = ", ".join(f"{x['rate']:,.0f}".replace(",", " ") + (f" ({x['from']} dan)" if len(r.fx) > 1 else "")
                          for x in r.fx)
        out.append(f"💱 Tannarx: Сони × Кирим нархи × kurs ({rates} so'm/$)")
        if r.cur_fixed.get("rows"):
            lim = f"{USD_MAX_UNIT:,}".replace(",", " ")
            out.append(f"🔧 Valyuta tuzatildi: «Доллар» yozilgan, lekin kirim narxi {lim} dan katta — so'm deb olindi: "
                       f"{r.cur_fixed['rows']} qator · {r.cur_fixed['skus']} SKU")
            out += [f"   • {esc(x)}" for x in r.cur_fixed.get("examples", [])]
    else:
        out.append("⚠️ Faylda «Кирим нархи» / «Кирим нархи(Валюта тури)» yo'q — tannarx Жами(Кирим нархи) dan olindi")
    if r.replaced.get("rows"):
        out.append(f"\n♻️ Shu sanalardagi eski ma'lumot almashtirildi: {r.replaced['rows']} qator, "
                   f"{_fmt_mln(r.replaced['amount'])} mln")
    out.append(f"\nℹ️ Boshqa kategoriyalar (yuklanmadi): {r.out_of_scope_rows} qator")
    if r.excluded.get("rows"):
        out.append(f"🚫 «Таъминотчи» yoki «Дукон номи» to'ldirilgan — hisobga olinmadi: {r.excluded['rows']} qator · "
                   f"{_fmt_mln(r.excluded['amount'])} mln")
    if r.names_updated:
        out.append(f"✏️ Tovar nomi yangilandi (variant nomi): {r.names_updated} SKU")

    if r.unknown_skus:
        out.append(f"\n⚠️ <b>Spravochnikda yo'q SKU: {len(r.unknown_skus)}</b> (yuklanmadi)")
        out += [f"  • {x['product_id']} — {esc(x['name'])} ({esc(x['category'])}): {x['rows']} qator, "
                f"{_fmt_mln(x['amount'])} mln" for x in r.unknown_skus[:max_items]]
        if len(r.unknown_skus) > max_items:
            out.append(f"  … yana {len(r.unknown_skus) - max_items} ta")
    if r.unknown_branches:
        out.append(f"\n⚠️ <b>Spravochnikda yo'q filial: {len(r.unknown_branches)}</b> (yuklanmadi)")
        out += [f"  • {esc(x['name'])}: {x['rows']} qator, {_fmt_mln(x['amount'])} mln"
                for x in r.unknown_branches[:max_items]]
    if r.unknown_skus or r.unknown_branches:
        p = r.pending or {}
        out.append(f"⏳ Bu savdo ({_fmt_mln(p.get('amount', 0) - p.get('new_cat_amount', 0))} mln) saqlab qo'yildi — "
                   "tovar/filial spravochnigiga qo'shilishi bilan avtomatik ulanadi, savdo faylini qayta yuklash shart emas.")
    p = r.pending or {}
    if p.get("new_cats"):
        out.append(f"\nℹ️ Mas'ullar ro'yxatida bor, tovar spravochnigida hali yo'q kategoriyalar savdosi ham saqlandi: "
                   f"{len(p['new_cats'])} ta, {_fmt_mln(p['new_cat_amount'])} mln — spravochnikka qo'shilganda ulanadi.")
    if r.category_mismatch:
        out.append(f"\n⚠️ Kategoriyasi spravochnikdan farq qilgan qatorlar: {r.category_mismatch} "
                   f"(spravochnik kategoriyasi olindi)")
    if r.errors:
        out.append(f"\n❌ <b>Xato qatorlar</b> (yuklanmadi):")
        out += [f"  • {esc(e)}" for e in r.errors[:max_items]]
        if len(r.errors) > max_items:
            out.append(f"  … yana {len(r.errors) - max_items} ta")
    return "\n".join(out)
