"""Ta'minotchidan omborga kirim (021_receipts.sql) — «kirim_tovarlar_01-11-2026-24-11-2026.xlsx».

Ustunlar: Буюртма коди · Таъминотчи · Омбор · Товар коди · Товар · Категорияси · Бренд · Кирим нархи · Валюта ·
          Миқдори · Серия рақами · Aсл белги · Кабул вақти · Валюта курси · ID
  * har dona — alohida qator (seriya, marka) → sana × ombor × tovar × zakaz × ta'minotchi bo'yicha yig'iladi;
    Серия рақами, Aсл белги, ID (qator/dona raqami) o'qilmaydi;
  * faylda Товар Ид yo'q — tovar NOM bo'yicha: product_aliases (qo'lda bog'langan nom / Товар коди) → aynan nom
    (name_key) → taxminiy (loose_key, faqat bitta tovar topilsa). Topilmasa — receipt_pending'da kutadi;
  * Омбор — qoldiq faylidagi bo'limlar («XorazmBaza Logist ombor») → warehouse_aliases (loaders_stock.resolve_alias);
  * narx — Валюта da (USD / UZS); UZS → qatordagi Валюта курси (yo'q bo'lsa — bot kursi) bilan $ ga;
  * fayldagi sanalar to'liq almashtiriladi (dublikat yo'q);
  * bizning kategoriyalardan tashqari (smartfon, maishiy kimyo …) — tashlanadi.
Signal: matritsa kategoriyasidagi tovar shu ombor filiallarining birortasi matritsasida yo'q bo'lsa — «kirim_nomx».
"""
from __future__ import annotations

import asyncio
import html
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

import asyncpg
import pandas as pd

from loaders_common import LoaderError, clean_text, name_key, read_sheet_with_header

C_ORDER, C_SUP, C_WH, C_CODE, C_NAME = "Буюртма коди", "Таъминотчи", "Омбор", "Товар коди", "Товар"
C_CAT, C_BRAND, C_PRICE, C_CUR, C_QTY, C_DATE, C_RATE = ("Категорияси", "Бренд", "Кирим нархи", "Валюта", "Миқдори",
                                                         "Кабул вақти", "Валюта курси")
REQ = [C_SUP, C_WH, C_NAME, C_QTY, C_DATE]
OPT = [C_ORDER, C_CODE, C_CAT, C_BRAND, C_PRICE, C_CUR, C_RATE]
_SUP_TAIL = re.compile(r"\s*\((?:та[ьъ]?минотчи|ta['ʼ`’]?minotchi)\)\s*$", re.I)


def is_receipts_file(headers) -> bool:
    h = {name_key(x) for x in headers if x is not None}
    return {name_key(c) for c in (C_SUP, C_WH, C_NAME, C_DATE, C_QTY)} <= h


def _num(s: pd.Series) -> pd.Series:
    if s.dtype == object:
        s = s.map(lambda v: str(v).replace(" ", "").replace(" ", "").replace(",", ".") if isinstance(v, str) else v)
    return pd.to_numeric(s, errors="coerce")


_D1 = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_D2 = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})")


def _date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        s = v.strip()
        try:
            if m := _D1.match(s):
                return date(int(m[1]), int(m[2]), int(m[3]))
            if m := _D2.match(s):
                return date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            return None
    return None


def supplier_name(v) -> str:
    s = clean_text(v) or ""
    return _SUP_TAIL.sub("", s).strip()


# ---------------------------------------------------------------------------
# Tovarni nom bo'yicha topish
class Matcher:
    """product_aliases → aynan nom → taxminiy nom. match(code, name) → (pid | None, tier: 1 aynan/eslab qolingan, 2 taxminiy)."""

    def __init__(self, products, aliases):
        from matrix_sync import loose_key
        self.lk = loose_key
        self.alias = aliases
        self.exact: dict[str, set[int]] = defaultdict(set)
        self.loose: dict[str, set[int]] = defaultdict(set)
        for pid, name in products:
            self.exact[name_key(name) or ""].add(pid)
            if (k := loose_key(name)):
                self.loose[k].add(pid)

    @classmethod
    async def load(cls, con) -> "Matcher":
        prods = [(r["product_id"], r["name"]) for r in await con.fetch("SELECT product_id, name FROM products")]
        al = {r["alias_key"]: r["product_id"] for r in await con.fetch("SELECT alias_key, product_id FROM product_aliases")}
        return cls(prods, al)

    def match(self, code: str | None, name: str | None) -> tuple[int | None, int | None]:
        if code and (p := self.alias.get("c:" + code)):
            return p, 1
        nk = name_key(name) or ""
        if nk and (p := self.alias.get("n:" + nk)):
            return p, 1
        ids = self.exact.get(nk)
        if ids and len(ids) == 1:
            return next(iter(ids)), 1
        lk = self.lk(name) or ""
        ids = self.loose.get(lk) if lk else None
        if ids and len(ids) == 1:
            return next(iter(ids)), 2
        return None, None


# ---------------------------------------------------------------------------
@dataclass
class ReceiptReport:
    upload_id: int
    date_from: str = ""
    date_to: str = ""
    days: int = 0
    rows_total: int = 0
    rows_loaded: int = 0
    records: int = 0
    out_of_scope_rows: int = 0
    qty: float = 0
    value_usd: float = 0
    skus: int = 0
    exact: int = 0
    loose: int = 0
    miss: int = 0
    miss_qty: float = 0
    by_loc: list[dict] = field(default_factory=list)
    unknown_parts: list[dict] = field(default_factory=list)
    replaced: dict = field(default_factory=dict)
    signals: int = 0
    errors: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)


def _parse(data: bytes) -> tuple[pd.DataFrame, int, list[str]]:
    df, _ = read_sheet_with_header(data, REQ, 15, REQ + OPT)
    miss = [c for c in REQ if c not in df.columns]
    if miss:
        raise LoaderError("Majburiy ustun(lar) yo'q: " + ", ".join(miss))
    total = len(df)
    for c in OPT:
        if c not in df.columns:
            df[c] = None
    df["d"] = df[C_DATE].map(_date)
    df["qty"] = _num(df[C_QTY])
    df["wh_name"] = df[C_WH].map(clean_text)
    df["name"] = df[C_NAME].map(clean_text)
    errors = []
    bad = df["d"].isna() | df["qty"].isna() | df["wh_name"].isna() | df["name"].isna()
    for r in df.loc[bad, "_excel_row"].head(20):
        errors.append(f"{r}-qator: sana, miqdor, ombor yoki tovar nomi bo'sh/noto'g'ri")
    df = df[~bad & (df["qty"] != 0)].copy()
    df["code"] = df[C_CODE].map(clean_text)
    df["sup"] = df[C_SUP].map(supplier_name)
    df["order"] = df[C_ORDER].map(lambda v: clean_text(v) or "")
    df["cat"] = df[C_CAT].map(clean_text)
    df["brand"] = df[C_BRAND].map(clean_text)
    price, rate = _num(df[C_PRICE]), _num(df[C_RATE])
    cur = df[C_CUR].map(lambda v: (clean_text(v) or "USD").upper())
    df["value"] = price * df["qty"]
    uzs = cur.isin(["UZS", "СУМ", "SUM", "SO'M"])
    df["uzs"] = uzs
    df["rate"] = rate
    return df, total, errors


async def load_receipts(pool: asyncpg.Pool, data: bytes, file_name: str | None = None,
                        tg_user_id: int | None = None) -> ReceiptReport:
    from loaders_stock import _own_keys, resolve_alias
    async with pool.acquire() as con:
        upload_id = await con.fetchval("INSERT INTO uploads(kind, file_name, tg_user_id) VALUES ('receipts',$1,$2) RETURNING id",
                                       file_name, tg_user_id)
        try:
            df, total, errors = await asyncio.to_thread(_parse, data)
            if df.empty:
                raise LoaderError("Faylda birorta ham to'g'ri kirim qatori yo'q")
            rep = ReceiptReport(upload_id, rows_total=total, errors=errors)
            async with con.transaction():
                # so'mdagi narx → $: qatordagi kurs, bo'lmasa bot kursi
                need = df["uzs"] & ~(df["rate"] > 0)
                if need.any():
                    rates = {}
                    for d in df.loc[need, "d"].unique():
                        rates[d] = float(await con.fetchval(
                            "SELECT rate FROM fx_periods WHERE $1 >= valid_from AND $1 < valid_to", d) or 0) or None
                    df.loc[need, "rate"] = df.loc[need, "d"].map(rates)
                df["value_usd"] = df["value"].where(~df["uzs"], df["value"] / df["rate"].where(df["rate"] > 0))
                # tovar
                m = await Matcher.load(con)
                keys = df[["code", "name"]].drop_duplicates()
                res = {(r.code, r.name): m.match(r.code, r.name) for r in keys.itertuples()}
                df["pid"] = [res[(c, n)][0] for c, n in zip(df["code"], df["name"])]
                df["tier"] = [res[(c, n)][1] for c, n in zip(df["code"], df["name"])]
                own = await _own_keys(con)
                ck = df["cat"].map(lambda v: name_key(v) if v else None)
                scope = df["pid"].notna() | ck.isin(own)
                rep.out_of_scope_rows = int((~scope).sum())
                all_dates = sorted(set(df["d"]))
                df = df[scope].copy()
                # ombor
                aliases = {r["alias_key"]: r["warehouse_id"] for r in await con.fetch(
                    "SELECT alias_key, warehouse_id FROM warehouse_aliases")}
                wnames = {r["id"]: r["name"] for r in await con.fetch("SELECT id, name FROM warehouses")}
                wres = {n: resolve_alias(n, aliases) for n in df["wh_name"].unique()}
                df["wid"] = df["wh_name"].map(lambda n: wres[n][0])
                df["loc_key"] = df["wh_name"].map(lambda n: wres[n][1])
                df["prod_key"] = df["name"].map(lambda v: name_key(v) or "")
                okm = df["pid"].notna() & df["wid"].notna()
                ok = (df[okm].groupby(["d", "wid", "pid", "order", "sup"], sort=False)
                      .agg(qty=("qty", "sum"), value_usd=("value_usd", lambda s: s.sum(min_count=1))).reset_index())
                pend = (df[~okm].groupby(["d", "loc_key", "prod_key", "order", "sup"], sort=False)
                        .agg(qty=("qty", "sum"), value_usd=("value_usd", lambda s: s.sum(min_count=1)),
                             loc_name=("wh_name", "first"), code=("code", "first"), name=("name", "first"),
                             cat=("cat", "first"), brand=("brand", "first"), pid=("pid", "first"))
                        .reset_index())
                rr = await con.fetchrow("""WITH x AS (DELETE FROM wh_receipts WHERE rec_date = ANY($1::date[]) RETURNING qty)
                                           SELECT count(*) n, coalesce(sum(qty), 0) q FROM x""", all_dates)
                rep.replaced = {"rows": rr["n"], "qty": float(rr["q"])}
                await con.execute("DELETE FROM receipt_pending WHERE rec_date = ANY($1::date[])", all_dates)
                nn = lambda v: None if v is None or (isinstance(v, float) and v != v) else v  # noqa: E731
                if not ok.empty:
                    await con.copy_records_to_table(
                        "wh_receipts", columns=["rec_date", "warehouse_id", "product_id", "order_code", "supplier",
                                                "qty", "value_usd", "upload_id"],
                        records=[(r.d, int(r.wid), int(r.pid), r.order, r.sup, float(r.qty), nn(r.value_usd), upload_id)
                                 for r in ok.itertuples()])
                if not pend.empty:
                    await con.copy_records_to_table(
                        "receipt_pending", columns=["rec_date", "loc_key", "loc_name", "prod_key", "product_code",
                                                    "product_name", "category", "brand", "order_code", "supplier",
                                                    "qty", "value_usd", "product_id", "upload_id"],
                        records=[(r.d, r.loc_key, nn(r.loc_name), r.prod_key, nn(r.code), nn(r.name), nn(r.cat), nn(r.brand), r.order, r.sup,
                                  float(r.qty), nn(r.value_usd), int(r.pid) if nn(r.pid) is not None else None, upload_id)
                                 for r in pend.itertuples()])
                rep.signals = await make_signals(con, all_dates, upload_id)
                from supply import recheck_signals
                await recheck_signals(con)

            rep.dates = [d.isoformat() for d in all_dates]
            rep.date_from, rep.date_to, rep.days = rep.dates[0], rep.dates[-1], len(all_dates)
            rep.rows_loaded = int(okm.sum())
            rep.records = len(ok)
            rep.qty = float(ok["qty"].sum()) if not ok.empty else 0.0
            rep.value_usd = float(ok["value_usd"].sum()) if not ok.empty else 0.0
            u = df.drop_duplicates("prod_key")
            rep.skus = int(u["pid"].notna().sum())
            rep.exact = int((u["tier"] == 1).sum())
            rep.loose = int((u["tier"] == 2).sum())
            rep.miss = int(u["pid"].isna().sum())
            rep.miss_qty = float(df.loc[df["pid"].isna(), "qty"].sum())
            parts = df[df["wid"].notna()].groupby("wid")["wh_name"].unique()
            for wid, part in ok.groupby("wid"):
                rep.by_loc.append({"name": wnames[int(wid)], "qty": float(part["qty"].sum()),
                                   "value_usd": float(part["value_usd"].sum()), "parts": sorted(parts.get(wid, []))})
            rep.by_loc.sort(key=lambda x: -x["qty"])
            for k, part in df[df["wid"].isna()].groupby("loc_key"):
                rep.unknown_parts.append({"key": k, "name": ", ".join(sorted(part["wh_name"].unique())),
                                          "rows": len(part), "qty": float(part["qty"].sum())})
        except Exception as e:
            await con.execute("UPDATE uploads SET status='failed', finished_at=now(), report=$2 WHERE id=$1",
                              upload_id, json.dumps({"error": str(e)}, ensure_ascii=False))
            raise
        await con.execute(
            "UPDATE uploads SET status='done', finished_at=now(), rows_total=$2, rows_loaded=$3, report=$4 WHERE id=$1",
            upload_id, rep.rows_total, rep.rows_loaded, json.dumps(rep.__dict__, ensure_ascii=False, default=str))
        return rep


# ---------------------------------------------------------------------------
async def make_signals(con, dates: list, upload_id: int | None = None) -> int:
    """Matritsada yo'q tovar kirimi: tovar kategoriyasi matritsada bor, lekin shu ombor filiallarining birortasi
    matritsasida bu tovar yo'q. Bitta signal — ombor × tovar × oy (qayta yuklansa yoki oy ichida yana kelsa — summasi
    yangilanadi, qayta yuborilmaydi). Yangi signallar soni qaytadi."""
    if not dates:
        return 0
    months = sorted({d.replace(day=1) for d in dates})
    rows = await con.fetch(
        """WITH mc AS (SELECT DISTINCT category_id FROM matrix_rows WHERE category_id IS NOT NULL),
                wmx AS (SELECT DISTINCT b.warehouse_id, m.product_id FROM matrix_rows m JOIN branches b ON b.id = m.branch_id
                        WHERE m.product_id IS NOT NULL AND b.warehouse_id IS NOT NULL),
                r AS (SELECT date_trunc('month', r.rec_date)::date mon, r.warehouse_id, r.product_id,
                             max(r.rec_date) last_date, string_agg(DISTINCT nullif(r.order_code, ''), ', ') orders,
                             string_agg(DISTINCT nullif(r.supplier, ''), ', ') suppliers,
                             sum(r.qty)::float8 qty, sum(r.value_usd)::float8 usd
                      FROM wh_receipts r JOIN products p USING (product_id)
                      WHERE date_trunc('month', r.rec_date)::date = ANY($1::date[])
                        AND p.category_id IN (SELECT category_id FROM mc)
                        AND NOT EXISTS (SELECT 1 FROM wmx WHERE wmx.warehouse_id = r.warehouse_id AND wmx.product_id = r.product_id)
                        -- zakaz / jo'natma bosqichida signal berilgan — kirimda takrorlanmaydi
                        AND NOT EXISTS (SELECT 1 FROM supply_signals g WHERE g.skey = 'sn:' || to_char(r.rec_date, 'YYYY-MM')
                                        || ':' || r.warehouse_id || ':' || r.product_id)
                      GROUP BY 1, 2, 3)
           INSERT INTO supply_signals AS s (kind, skey, warehouse_id, product_id, data, upload_id)
           SELECT 'kirim_nomx', 'kn:' || to_char(mon, 'YYYY-MM') || ':' || warehouse_id || ':' || product_id,
                  warehouse_id, product_id,
                  jsonb_build_object('month', mon, 'date', last_date, 'order', orders, 'supplier', suppliers,
                                     'qty', qty, 'usd', usd), $2
           FROM r
           ON CONFLICT (skey) DO UPDATE SET data = EXCLUDED.data
           RETURNING (xmax = 0) AS ins""", months, upload_id)
    return sum(1 for r in rows if r["ins"])


async def resolve_receipts(con) -> dict:
    """Kutayotgan kirim: endi topiladigan tovar (yangi spravochnik / qo'lda bog'lash) va ombor bo'limi → wh_receipts."""
    from loaders_stock import resolve_alias
    pend = await con.fetch("SELECT DISTINCT prod_key, product_code, product_name FROM receipt_pending WHERE product_id IS NULL")
    if pend:
        m = await Matcher.load(con)
        upd = [(r["prod_key"], r["product_code"], pid) for r in pend
               if (pid := m.match(r["product_code"], r["product_name"])[0])]
        if upd:
            await con.executemany("UPDATE receipt_pending SET product_id = $3 WHERE prod_key = $1 "
                                  "AND product_code IS NOT DISTINCT FROM $2 AND product_id IS NULL", upd)
    aliases = {r["alias_key"]: r["warehouse_id"] for r in await con.fetch("SELECT alias_key, warehouse_id FROM warehouse_aliases")}
    locs = [r["loc_key"] for r in await con.fetch("SELECT DISTINCT loc_key FROM receipt_pending WHERE product_id IS NOT NULL")]
    lmap = [(k, w) for k in locs if (w := aliases.get(k) or resolve_alias(k, aliases)[0])]
    if not lmap:
        return {"rows": 0, "qty": 0.0}
    moved = await con.fetch(
        """WITH lm AS (SELECT * FROM unnest($1::text[], $2::int[]) AS t(loc_key, wid)),
                m AS (DELETE FROM receipt_pending rp USING lm WHERE rp.loc_key = lm.loc_key AND rp.product_id IS NOT NULL
                      AND EXISTS (SELECT 1 FROM products p WHERE p.product_id = rp.product_id)
                      RETURNING rp.rec_date, lm.wid, rp.product_id, rp.order_code, rp.supplier, rp.qty, rp.value_usd, rp.upload_id),
                ins AS (INSERT INTO wh_receipts AS w (rec_date, warehouse_id, product_id, order_code, supplier, qty, value_usd, upload_id)
                        SELECT rec_date, wid, product_id, order_code, supplier, sum(qty), sum(value_usd), max(upload_id)
                        FROM m GROUP BY 1, 2, 3, 4, 5
                        ON CONFLICT (rec_date, warehouse_id, product_id, order_code, supplier) DO UPDATE
                        SET qty = w.qty + EXCLUDED.qty, value_usd = coalesce(w.value_usd, 0) + coalesce(EXCLUDED.value_usd, 0)
                        RETURNING 1)
           SELECT rec_date, qty FROM m""", [k for k, _ in lmap], [w for _, w in lmap])
    if moved:
        await make_signals(con, sorted({r["rec_date"] for r in moved}))
    return {"rows": len(moved), "qty": float(sum(r["qty"] for r in moved))}


# ---------------------------------------------------------------------------
def _f(v: float, d: int = 0) -> str:
    return f"{v:,.{d}f}".replace(",", " ")


def _usd(v: float) -> str:
    return f"{v / 1e6:,.2f} mln $".replace(",", " ").replace(".", ",") if abs(v) >= 1e6 else \
        f"{v / 1e3:,.0f} ming $".replace(",", " ")


def _dm(s: str) -> str:
    return f"{s[8:10]}.{s[5:7]}"


def format_report(r: ReceiptReport) -> str:
    e = lambda s: html.escape(str(s), quote=False)  # noqa: E731
    per = _dm(r.date_from) if r.date_from == r.date_to else f"{_dm(r.date_from)}–{_dm(r.date_to)}"
    out = [f"📥 <b>Omborga kirim yuklandi</b> (#{r.upload_id}) · {per} ({r.days} kun)",
           f"{_f(r.rows_total)} qator → {_f(r.records)} yozuv (sana × ombor × tovar × zakaz)",
           f"Bizning kategoriyalar: <b>{_f(r.qty)} dona · {_usd(r.value_usd)}</b>"]
    if r.out_of_scope_rows:
        out.append(f"<i>boshqa kategoriyalar (smartfon va h.k.) — {_f(r.out_of_scope_rows)} qator o'tkazib yuborildi</i>")
    tot = r.skus + r.miss
    out += ["", f"<b>Tovarlar nom bo'yicha bog'landi: {r.skus} / {tot}</b>",
            f"✅ aynan nom: {r.exact}" + (f" · 🔤 taxminiy: {r.loose}" if r.loose else "")]
    if r.miss:
        out.append(f"⏳ topilmadi: <b>{r.miss} tovar · {_f(r.miss_qty)} dona</b> — kutayotganlarga qo'shildi "
                   "(📥 → ⏳ Kutayotgan → «Kirim: nom topilmadi»)")
    if r.by_loc:
        out += ["", "🏬 <b>Omborlar:</b>"] + [f"  • {e(x['name'])} — {_f(x['qty'])} dona · {_usd(x['value_usd'])}"
                                             for x in r.by_loc]
    if r.unknown_parts:
        out.append("❓ Noma'lum ombor bo'limi: " + ", ".join(e(x["name"]) for x in r.unknown_parts)
                   + " — bot qaysi omborligini so'raydi")
    if r.replaced.get("rows"):
        out.append(f"🔁 {per} sanalari almashtirildi (dublikat yo'q)")
    if r.signals:
        out += ["", f"⚠️ <b>Yangi signallar: {r.signals}</b> — matritsada yo'q tovar kirimi"]
    if r.errors:
        out += ["", "<b>Xato qatorlar:</b>"] + [e(x) for x in r.errors[:10]]
    return "\n".join(out)
