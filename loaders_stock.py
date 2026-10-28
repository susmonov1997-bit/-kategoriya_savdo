"""Qoldiq loaderlari (011_stock.sql): filial qoldig'i va hududiy ombor qoldig'i.

Filial qoldig'i (Tovar_qoldigi_17.10.xlsx):
    majburiy: Filial, Tovar Id, Qoldiq
    ixtiyoriy: Hudud (yoki Худуд), Kirim narxi + Valyutasi, Tovar Variant nomi, Tovar kategoriyasi, Brand
    (registr farqi muhim emas: «Tovar ID» = «Tovar Id»). Tannarx = Kirim narxi × bot kursi (so'mda bo'lsa — o'zi).
    (Limit, Vitrina, Harakatsiz muddati … hozircha o'qilmaydi — bo'lmasa ham muammo yo'q)
Ombor qoldig'i (Ombor qoldig'i 17-10-2026.xlsx):
    majburiy: ID, Омбор, Мижоз учун қолдиқ (rezervsiz)
    ixtiyoriy: A сектор учун жорий сотиш нархи ($), Товар, Товар категорияси
    Bo'limlar («G'uzor Logist ombor», «G'uzor Artel ombor», «G'uzor citystar ombor») bitta omborga yig'iladi:
    nomning boshidagi so'z(lar)i warehouse_aliases dan qidiriladi. Topilmasa — qoldiq kutadi, bot so'raydi.

Sana faylda yo'q — fayl nomidan olinadi (17.10 / 17.10.2026 / 17-10-2026). Topilmasa — bugungi sana.
Bir sana qayta yuklansa, FAYLDAGI filial/omborlar uchun shu sana almashtiriladi (boshqalariga tegilmaydi).
Spravochnikda yo'q SKU / filial / ombor bo'limi qatorlari stock_pending da kutadi va keyin avtomatik ulanadi.
"""
from __future__ import annotations

import asyncio
import html
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
import pandas as pd

from loaders_common import EXCEL_ENGINE, LoaderError, clean_text, name_key, read_sheet_with_header, update_product_names

# --- filial qoldig'i ---
B_BRANCH, B_ID, B_QTY = "Filial", "Tovar Id", "Qoldiq"
B_REGION, B_COST, B_CUR = "Hudud", "Kirim narxi", "Valyutasi"
B_NAME, B_NAME0, B_CAT, B_BRAND = "Tovar Variant nomi", "Tovar nomi", "Tovar kategoriyasi", "Brand"
B_REQ = [B_BRANCH, B_ID, B_QTY]
B_OPT = [B_REGION, B_COST, B_CUR, B_NAME, B_NAME0, B_CAT, B_BRAND]   # «Sotish narxi» o'qilmaydi

# --- ombor qoldig'i ---
W_ID, W_WH, W_QTY = "ID", "Омбор", "Мижоз учун қолдиқ"
W_PRICE, W_NAME, W_CAT = "A сектор учун жорий сотиш нархи", "Товар", "Товар категорияси"
W_REQ = [W_ID, W_WH, W_QTY]
W_OPT = [W_PRICE, W_NAME, W_CAT]

KEEP_DAILY_DAYS = 92      # shundan eski qoldiqdan har haftaning faqat oxirgi sanasi qoladi
TZ = ZoneInfo("Asia/Tashkent")


# Ustun nomining boshqa yozilishlari (registr va apostrof farqi name_key bilan o'zi tenglashadi: «Tovar ID» = «Tovar Id»)
B_ALIASES = {
    B_REGION: ["Худуд"],
}
_B_CANON = {name_key(c): c for c in B_REQ + B_OPT}
_B_CANON.update({name_key(a): c for c, al in B_ALIASES.items() for a in al})


def _bcanon(h) -> str | None:
    """Sarlavha → standart ustun nomi (filial qoldig'i uchun); tanilmasa None."""
    return _B_CANON.get(name_key(h) or "")


def is_branch_stock_file(headers: set[str]) -> bool:
    return set(B_REQ) <= {_bcanon(h) for h in headers}


def _read_branch(data: bytes) -> pd.DataFrame:
    """Filial qoldig'i varag'ini o'qiydi: sarlavha qatorini topadi, ustunlarni standart nomlarga o'giradi."""
    buf = io.BytesIO(data)
    try:
        head = pd.read_excel(buf, header=None, dtype=object, nrows=15, engine=EXCEL_ENGINE)
    except Exception as e:  # noqa: BLE001
        raise LoaderError(f"Excel faylni o'qib bo'lmadi: {e}") from e
    hi = next((i for i in range(len(head)) if set(B_REQ) <= {_bcanon(c) for c in head.iloc[i].tolist()}), None)
    if hi is None:
        raise LoaderError("Sarlavha qatori topilmadi. Birinchi 15 qatorda quyidagi ustunlar bo'lishi kerak: "
                          + ", ".join(B_REQ))
    buf.seek(0)
    try:
        df = pd.read_excel(buf, header=hi, dtype=object, engine=EXCEL_ENGINE, usecols=lambda c: _bcanon(c) is not None)
    except Exception as e:  # noqa: BLE001
        raise LoaderError(f"Excel faylni o'qib bo'lmadi: {e}") from e
    df.columns = [_bcanon(c) for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]           # ikkala yozilish bo'lsa — birinchisi olinadi
    df["_excel_row"] = [hi + 2 + k for k in range(len(df))]
    df = df.dropna(how="all", subset=[c for c in df.columns if c != "_excel_row"])
    return df.reset_index(drop=True)


def is_wh_stock_file(headers: set[str]) -> bool:
    return set(W_REQ) <= headers


def today() -> date:
    return datetime.now(TZ).date()


_ISO_RE = re.compile(r"(?<!\d)(20\d\d)[.\-_](\d{1,2})[.\-_](\d{1,2})(?!\d)")
_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[.\-_](\d{1,2})(?:[.\-_](\d{4}|\d{2}))?(?!\d)")


def date_from_name(file_name: str | None, ref: date | None = None) -> date | None:
    """«Tovar_qoldigi_17.10.xlsx» → 17.10.<joriy yil>; «… 17-10-2026.xlsx» → 2026-10-17. Oxirgi moslik olinadi.
    Yil yozilmagan bo'lsa — joriy yil (sana kelajakda bo'lib qolsa — o'tgan yil)."""
    if not file_name:
        return None
    ref = ref or today()
    for m in _ISO_RE.finditer(file_name):
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    found = None
    # har bir pozitsiyadan tekshiriladi («shablon2_19.10.2026»: «2_19» noto'g'ri sana — keyingi moslik yo'qolmasin)
    ms = (_DATE_RE.match(file_name, i) for i in range(len(file_name)))
    for m in (m for m in ms if m):
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        yy = (int(y) + 2000 if len(y) == 2 else int(y)) if y else ref.year
        try:
            v = date(yy, mo, d)
        except ValueError:
            continue
        if not y and v > ref + timedelta(days=1):
            v = date(yy - 1, mo, d)
        found = v
    return found


def _num(s: pd.Series) -> pd.Series:
    """'1 234,5' kabi matnlarni ham songa o'giradi."""
    if s.dtype == object:
        s = s.map(lambda v: str(v).replace(" ", "").replace(" ", "").replace(",", ".") if isinstance(v, str) else v)
    return pd.to_numeric(s, errors="coerce")


def _fmt(v: float, d: int = 0) -> str:
    return f"{v:,.{d}f}".replace(",", " ")


def _mlrd(v: float) -> str:
    return f"{v / 1e9:,.2f} mlrd".replace(",", " ").replace(".", ",") if abs(v) >= 1e9 else \
        f"{v / 1e6:,.1f} mln".replace(",", " ").replace(".", ",")


@dataclass
class StockReport:
    kind: str                         # stock_branch | stock_wh
    upload_id: int
    snap_date: str
    date_source: str                  # file_name | today
    rows_total: int = 0
    rows_loaded: int = 0
    qty: float = 0
    skus: int = 0
    locations: int = 0
    cost_uzs: float = 0
    price_uzs: float = 0
    value_usd: float = 0
    rate: float | None = None
    no_rate: bool = False
    by_loc: list[dict] = field(default_factory=list)       # [{name, qty, skus, value, parts}]
    replaced: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)            # {rows, qty, skus}
    names_updated: int = 0                                 # tovar nomi (variant nomi) yangilangan SKU soni
    unknown_skus: int = 0
    new_branches: list[dict] = field(default_factory=list)  # [{name, region, rows, qty}]
    unknown_parts: list[dict] = field(default_factory=list)  # [{name, key, rows, qty}]
    errors: list[str] = field(default_factory=list)
    zero_rows: int = 0
    out_of_scope_rows: int = 0        # spravochnikda yo'q va kategoriyasi bizniki emas (smartfon, mebel …) — tashlandi
    dates: list[str] = field(default_factory=list)


def _bad_rows(df: pd.DataFrame, checks: list[tuple[pd.Series, str]]) -> tuple[pd.Series, list[str]]:
    errors: list[str] = []
    bad = pd.Series(False, index=df.index)
    for mask, msg in checks:
        new = mask & ~bad
        errors += [f"{r}-qator: {msg}" for r in df.loc[new, "_excel_row"].head(30)]
        if new.sum() > 30:
            errors.append(f"… «{msg}» yana {int(new.sum()) - 30} qator")
        bad |= mask
    return bad, errors


async def _start(con, kind: str, file_name: str | None, tg_user_id: int | None) -> int:
    return await con.fetchval("INSERT INTO uploads(kind, file_name, tg_user_id) VALUES ($1,$2,$3) RETURNING id",
                              kind, file_name, tg_user_id)


async def _finish(con, rep: StockReport) -> None:
    await con.execute(
        "UPDATE uploads SET status='done', finished_at=now(), rows_total=$2, rows_loaded=$3, report=$4 WHERE id=$1",
        rep.upload_id, rep.rows_total, rep.rows_loaded, json.dumps(rep.__dict__, ensure_ascii=False, default=str))


async def _fail(con, upload_id: int, e: Exception) -> None:
    await con.execute("UPDATE uploads SET status='failed', finished_at=now(), report=$2 WHERE id=$1",
                      upload_id, json.dumps({"error": str(e)}, ensure_ascii=False))


async def _own_keys(con) -> set[str]:
    """Bizning kategoriyalar: tovar spravochnigi + mas'ullar ro'yxati (savdo loaderidagi kabi)."""
    return ({name_key(r["name"]) for r in await con.fetch("SELECT name FROM categories")}
            | {r["name_key"] for r in await con.fetch("SELECT name_key FROM category_owner_list")})


def _scope_mask(cats: pd.Series, known_p: pd.Series, own: set[str]) -> pd.Series:
    """True — saqlanadi: tovar spravochnikda bor YOKI kategoriyasi bizniki (yoki kategoriya ko'rsatilmagan)."""
    ck = cats.map(lambda v: name_key(v) if isinstance(v, str) else None)
    return known_p | ck.isna() | ck.isin(own)


async def _prune(con) -> None:
    """Eski qoldiq: KEEP_DAILY_DAYS dan eskisidan har haftaning oxirgi sanasi qoladi (baza kattalashib ketmasin)."""
    for t in ("stock_branch", "stock_wh"):
        await con.execute(
            f"""DELETE FROM {t} WHERE snap_date < CURRENT_DATE - {KEEP_DAILY_DAYS}
                AND snap_date NOT IN (SELECT max(snap_date) FROM {t} WHERE snap_date < CURRENT_DATE - {KEEP_DAILY_DAYS}
                                      GROUP BY date_trunc('week', snap_date))""")


def _snap(file_name: str | None) -> tuple[date, str]:
    d = date_from_name(file_name)
    return (d, "file_name") if d else (today(), "today")


# =====================================================================
# Filial qoldig'i
# =====================================================================
def _parse_branch(data: bytes) -> tuple[pd.DataFrame, int, list[str], int]:
    df = _read_branch(data)
    missing = [c for c in B_REQ if c not in df.columns]
    if missing:
        raise LoaderError("Majburiy ustun(lar) yo'q: " + ", ".join(missing))
    total = len(df)
    df["pid"] = _num(df[B_ID])
    df["qty"] = _num(df[B_QTY])
    df["br_key"] = df[B_BRANCH].map(name_key)
    bad, errors = _bad_rows(df, [
        (df["br_key"].isna(), "«Filial» bo'sh"),
        (df["pid"].isna() | (df["pid"] <= 0) | (df["pid"] % 1 != 0), "«Tovar Id» noto'g'ri"),
        (df["qty"].isna(), "«Qoldiq» bo'sh yoki noto'g'ri"),
    ])
    df = df[~bad].copy()
    zero = int((df["qty"] == 0).sum())
    df = df[df["qty"] != 0]
    df["pid"] = df["pid"].astype("int64")
    # tannarx: «Kirim narxi» × bot kursi (qoldiq sanasidagi). «Valyutasi» so'm bo'lsa — narx o'zi (kurssiz).
    cost = _num(df[B_COST]) if B_COST in df.columns else pd.Series(float("nan"), index=df.index)
    cur = df[B_CUR].map(lambda v: name_key(v) or "") if B_CUR in df.columns else pd.Series("", index=df.index)
    is_sum = cur.str.contains("сум|сўм|so'm|som|sum|uzs", regex=True)
    df["cost_usd"] = (df["qty"] * cost).where(~is_sum)
    df["cost_sum"] = (df["qty"] * cost).where(is_sum)
    df["price_uzs"] = float("nan")                        # sotish narxi ishlatilmaydi (bazada NULL)
    for c in (B_REGION, B_NAME, B_NAME0, B_CAT, B_BRAND):
        df[c] = df[c].map(clean_text) if c in df.columns else None
    df[B_NAME] = df[B_NAME].where(df[B_NAME].notna(), df[B_NAME0])    # variant nomi bo'lmasa — «Tovar nomi»
    df["br_name"] = df[B_BRANCH].map(clean_text)
    g = (df.groupby(["br_key", "pid"], sort=False)
           .agg(qty=("qty", "sum"), cost_usd=("cost_usd", "sum"), usd_n=("cost_usd", "count"),
                cost_sum=("cost_sum", "sum"), sum_n=("cost_sum", "count"),
                price_uzs=("price_uzs", "sum"), price_n=("price_uzs", "count"),
                br_name=("br_name", "first"), region=(B_REGION, "first"), name=(B_NAME, "first"),
                cat=(B_CAT, "first"), brand=(B_BRAND, "first"))
           .reset_index())
    g["cost_usd"] = g["cost_usd"].where(g["usd_n"] > 0)       # narx yo'q bo'lsa — NULL (0 emas)
    g["cost_sum"] = g["cost_sum"].where(g["sum_n"] > 0)
    g["price_uzs"] = g["price_uzs"].where(g["price_n"] > 0)
    return g, total, errors, zero


def _nn(v):
    return None if v is None or (isinstance(v, float) and v != v) else v


async def load_branch_stock(pool: asyncpg.Pool, data: bytes, file_name: str | None = None,
                            tg_user_id: int | None = None) -> StockReport:
    async with pool.acquire() as con:
        upload_id = await _start(con, "stock_branch", file_name, tg_user_id)
        try:
            d, src = _snap(file_name)
            g, total, errors, zero = await asyncio.to_thread(_parse_branch, data)
            if g.empty:
                raise LoaderError("Faylda birorta ham to'g'ri qoldiq qatori yo'q")
            rep = StockReport("stock_branch", upload_id, d.isoformat(), src, rows_total=total, errors=errors,
                              zero_rows=zero, dates=[d.isoformat()])
            rate = await con.fetchval("SELECT rate FROM fx_periods WHERE $1 >= valid_from AND $1 < valid_to", d)
            rep.rate = float(rate) if rate else None
            usd = g["cost_usd"] * (float(rate) if rate else float("nan"))
            g["cost_uzs"] = usd.fillna(0) + g["cost_sum"].fillna(0)
            g["cost_uzs"] = g["cost_uzs"].where(usd.notna() | g["cost_sum"].notna())
            rep.no_rate = bool(not rate and g["cost_usd"].notna().any())
            async with con.transaction():
                brs = {r["name_key"]: (r["id"], r["wh"]) for r in await con.fetch(
                    """SELECT b.name_key, b.id, w.name wh FROM branches b LEFT JOIN warehouses w ON w.id = b.warehouse_id""")}
                prods = {r["product_id"] for r in await con.fetch("SELECT product_id FROM products")}
                g["bid"] = g["br_key"].map(lambda k: brs[k][0] if k in brs else None)
                g["known_p"] = g["pid"].isin(prods)
                # tovar nomi = «Tovar Variant nomi» (bo'lmasa «Tovar nomi»)
                rep.names_updated = await update_product_names(
                    con, g[g["known_p"]].groupby("pid")["name"].first().to_dict())
                scope = _scope_mask(g["cat"], g["known_p"], await _own_keys(con))
                rep.out_of_scope_rows = int((~scope).sum())
                g_all, g = g, g[scope]
                ok = g[g["bid"].notna() & g["known_p"]]
                pend = g[~(g["bid"].notna() & g["known_p"])]
                file_bids = sorted({int(b) for b in g_all["bid"].dropna()})
                rr = await con.fetchrow(
                    """WITH x AS (DELETE FROM stock_branch WHERE snap_date=$1 AND branch_id = ANY($2::int[]) RETURNING qty)
                       SELECT count(*) n, coalesce(sum(qty),0) q FROM x""", d, file_bids)
                rep.replaced = {"rows": rr["n"], "qty": float(rr["q"])}
                await con.execute("DELETE FROM stock_pending WHERE kind='b' AND snap_date=$1 AND loc_key = ANY($2::text[])",
                                  d, sorted(set(g_all["br_key"])))
                if not ok.empty:
                    await con.copy_records_to_table(
                        "stock_branch", columns=["snap_date", "branch_id", "product_id", "qty", "cost_uzs", "price_uzs", "upload_id"],
                        records=[(d, int(r.bid), int(r.pid), float(r.qty), _nn(r.cost_uzs), _nn(r.price_uzs), upload_id)
                                 for r in ok.itertuples()])
                if not pend.empty:
                    await con.copy_records_to_table(
                        "stock_pending", columns=["kind", "snap_date", "loc_key", "loc_name", "region", "product_id",
                                                  "product_name", "category", "brand", "qty", "cost_uzs", "price_uzs", "upload_id"],
                        records=[("b", d, r.br_key, r.br_name, _nn(r.region), int(r.pid), _nn(r.name), _nn(r.cat), _nn(r.brand),
                                  float(r.qty), _nn(r.cost_uzs), _nn(r.price_uzs), upload_id) for r in pend.itertuples()])
                await _prune(con)

            rep.rows_loaded = len(ok)
            rep.qty = float(ok["qty"].sum())
            rep.skus = int(ok["pid"].nunique())
            rep.locations = int(ok["bid"].nunique())
            rep.cost_uzs = float(ok["cost_uzs"].sum())
            rep.price_uzs = float(ok["price_uzs"].sum())
            okw = ok.assign(wh=ok["br_key"].map(lambda k: brs[k][1] or "ombori ko'rsatilmagan"))
            for wh, part in okw.groupby("wh"):
                rep.by_loc.append({"name": wh, "qty": float(part["qty"].sum()), "skus": int(part["pid"].nunique()),
                                   "branches": int(part["bid"].nunique()), "value": float(part["cost_uzs"].sum())})
            rep.by_loc.sort(key=lambda x: -x["value"])
            if not pend.empty:
                rep.pending = {"rows": len(pend), "qty": float(pend["qty"].sum()), "skus": int(pend["pid"].nunique())}
                rep.unknown_skus = int(pend.loc[pend["bid"].notna(), "pid"].nunique())
                nb = pend[pend["bid"].isna()]
                for k, part in nb.groupby("br_key"):
                    rep.new_branches.append({"name": part["br_name"].iloc[0], "region": _nn(part["region"].iloc[0]),
                                             "rows": len(part), "qty": float(part["qty"].sum())})
        except Exception as e:
            await _fail(con, upload_id, e)
            raise
        await _finish(con, rep)
        return rep


# =====================================================================
# Ombor qoldig'i
# =====================================================================
def resolve_alias(name: str, aliases: dict[str, int]) -> tuple[int | None, str]:
    """«G'uzor Logist ombor» → (Гузор омбори id, «g'uzor»). Avval to'liq nom, keyin boshidagi so'zlar (uzundan qisqaga).
    Topilmasa (None, birinchi so'z kaliti) — shu kalit bo'yicha so'raladi."""
    words = (name_key(name) or "").split()
    for n in range(len(words), 0, -1):
        k = " ".join(words[:n])
        if k in aliases:
            return aliases[k], k
    return None, (words[0] if words else "")


def _parse_wh(data: bytes) -> tuple[pd.DataFrame, int, list[str], int]:
    df, _ = read_sheet_with_header(data, W_REQ, 15, W_REQ + W_OPT)
    missing = [c for c in W_REQ if c not in df.columns]
    if missing:
        raise LoaderError("Majburiy ustun(lar) yo'q: " + ", ".join(missing))
    total = len(df)
    df["pid"] = _num(df[W_ID])
    df["qty"] = _num(df[W_QTY])
    df["wh_name"] = df[W_WH].map(clean_text)
    bad, errors = _bad_rows(df, [
        (df["wh_name"].isna(), "«Омбор» bo'sh"),
        (df["pid"].isna() | (df["pid"] <= 0) | (df["pid"] % 1 != 0), "«ID» noto'g'ri"),
        (df["qty"].isna(), "«Мижоз учун қолдиқ» bo'sh yoki noto'g'ri"),
    ])
    df = df[~bad].copy()
    zero = int((df["qty"] == 0).sum())
    df = df[df["qty"] != 0]
    df["pid"] = df["pid"].astype("int64")
    price = _num(df[W_PRICE]) if W_PRICE in df.columns else pd.Series(float("nan"), index=df.index)
    df["value_usd"] = df["qty"] * price
    for c in (W_NAME, W_CAT):
        df[c] = df[c].map(clean_text) if c in df.columns else None
    return df, total, errors, zero


async def load_wh_stock(pool: asyncpg.Pool, data: bytes, file_name: str | None = None,
                        tg_user_id: int | None = None) -> StockReport:
    async with pool.acquire() as con:
        upload_id = await _start(con, "stock_wh", file_name, tg_user_id)
        try:
            d, src = _snap(file_name)
            df, total, errors, zero = await asyncio.to_thread(_parse_wh, data)
            if df.empty:
                raise LoaderError("Faylda birorta ham to'g'ri qoldiq qatori yo'q")
            rep = StockReport("stock_wh", upload_id, d.isoformat(), src, rows_total=total, errors=errors,
                              zero_rows=zero, dates=[d.isoformat()])
            async with con.transaction():
                aliases = {r["alias_key"]: r["warehouse_id"] for r in await con.fetch(
                    "SELECT alias_key, warehouse_id FROM warehouse_aliases")}
                wnames = {r["id"]: r["name"] for r in await con.fetch("SELECT id, name FROM warehouses")}
                prods = {r["product_id"] for r in await con.fetch("SELECT product_id FROM products")}
                res = {n: resolve_alias(n, aliases) for n in df["wh_name"].unique()}
                df["wid"] = df["wh_name"].map(lambda n: res[n][0])
                df["loc_key"] = df["wh_name"].map(lambda n: res[n][1])
                df["known_p"] = df["pid"].isin(prods)
                rep.names_updated = await update_product_names(      # «Товар» ustunidagi to'liq nom
                    con, df[df["known_p"]].groupby("pid")[W_NAME].first().to_dict()) if W_NAME in df.columns else 0
                scope = _scope_mask(df[W_CAT], df["known_p"], await _own_keys(con))
                rep.out_of_scope_rows = int((~scope).sum())
                df_all, df = df, df[scope]
                okm = df["wid"].notna() & df["known_p"]
                ok = (df[okm].groupby(["wid", "pid"], sort=False)
                        .agg(qty=("qty", "sum"), value_usd=("value_usd", "sum"), vn=("value_usd", "count")).reset_index())
                ok["value_usd"] = ok["value_usd"].where(ok["vn"] > 0)
                pend = (df[~okm].groupby(["loc_key", "pid"], sort=False)
                          .agg(qty=("qty", "sum"), value_usd=("value_usd", "sum"), vn=("value_usd", "count"),
                               loc_name=("wh_name", "first"), name=(W_NAME, "first"), cat=(W_CAT, "first"),
                               wid=("wid", "first"))
                          .reset_index())
                pend["value_usd"] = pend["value_usd"].where(pend["vn"] > 0)
                file_wids = sorted({int(w) for w in df_all["wid"].dropna()})
                rr = await con.fetchrow(
                    """WITH x AS (DELETE FROM stock_wh WHERE snap_date=$1 AND warehouse_id = ANY($2::int[]) RETURNING qty)
                       SELECT count(*) n, coalesce(sum(qty),0) q FROM x""", d, file_wids)
                rep.replaced = {"rows": rr["n"], "qty": float(rr["q"])}
                await con.execute("DELETE FROM stock_pending WHERE kind='w' AND snap_date=$1 AND loc_key = ANY($2::text[])",
                                  d, sorted(set(df_all["loc_key"])))
                if not ok.empty:
                    await con.copy_records_to_table(
                        "stock_wh", columns=["snap_date", "warehouse_id", "product_id", "qty", "value_usd", "upload_id"],
                        records=[(d, int(r.wid), int(r.pid), float(r.qty), _nn(r.value_usd), upload_id) for r in ok.itertuples()])
                if not pend.empty:
                    await con.copy_records_to_table(
                        "stock_pending", columns=["kind", "snap_date", "loc_key", "loc_name", "product_id", "product_name",
                                                  "category", "qty", "value_usd", "upload_id"],
                        records=[("w", d, r.loc_key, r.loc_name, int(r.pid), _nn(r.name), _nn(r.cat), float(r.qty),
                                  _nn(r.value_usd), upload_id) for r in pend.itertuples()])
                rate = await con.fetchval("SELECT rate FROM fx_periods WHERE $1 >= valid_from AND $1 < valid_to", d)
                await _prune(con)

            rep.rate = float(rate) if rate else None
            rep.rows_loaded = int(okm.sum())
            rep.qty = float(ok["qty"].sum())
            rep.skus = int(ok["pid"].nunique())
            rep.locations = int(ok["wid"].nunique())
            rep.value_usd = float(ok["value_usd"].sum())
            parts = df[df["wid"].notna()].groupby("wid")["wh_name"].unique()
            for wid, part in ok.groupby("wid"):
                rep.by_loc.append({"name": wnames[int(wid)], "qty": float(part["qty"].sum()),
                                   "skus": int(part["pid"].nunique()), "value_usd": float(part["value_usd"].sum()),
                                   "parts": sorted(parts.get(wid, []))})
            rep.by_loc.sort(key=lambda x: -x["qty"])
            if not pend.empty:
                rep.pending = {"rows": len(pend), "qty": float(pend["qty"].sum()), "skus": int(pend["pid"].nunique())}
                rep.unknown_skus = int(pend.loc[pend["wid"].notna(), "pid"].nunique())
                unk = df[df["wid"].isna()]
                for k, part in unk.groupby("loc_key"):
                    rep.unknown_parts.append({"key": k, "name": ", ".join(sorted(part["wh_name"].unique())),
                                              "rows": len(part), "qty": float(part["qty"].sum())})
        except Exception as e:
            await _fail(con, upload_id, e)
            raise
        await _finish(con, rep)
        return rep


# =====================================================================
def format_report(r: StockReport, max_items: int = 15) -> str:
    esc = lambda s: html.escape(str(s), quote=False)  # noqa: E731
    d = datetime.fromisoformat(r.snap_date).strftime("%d.%m.%Y")
    src = "fayl nomidan" if r.date_source == "file_name" else \
        "⚠️ fayl nomida sana topilmadi — bugungi sana olindi (nomga sanani yozing: …_17.10.2026.xlsx)"
    if r.kind == "stock_branch":
        out = [f"✅ <b>Filial qoldig'i yuklandi</b> (#{r.upload_id})",
               f"Sana: <b>{d}</b> ({src})",
               f"Qatorlar: {_fmt(r.rows_total)} → yuklandi: <b>{_fmt(r.rows_loaded)}</b>",
               f"Filiallar: <b>{r.locations}</b> · SKU: {_fmt(r.skus)} · jami <b>{_fmt(r.qty)}</b> dona",
               f"Tannarxda: <b>{_mlrd(r.cost_uzs)}</b> (kirim narxi × kurs "
               f"{_fmt(r.rate) + ' so' + chr(39) + 'm/$' if r.rate else '—'})"]
        if r.no_rate:
            out.append("⚠️ Shu sana uchun kurs topilmadi — dollardagi tannarx hisoblanmadi. Kursni yuklab, qoldiqni qayta yuklang.")
        if r.by_loc:
            out += ["", "<b>Omborlar bo'yicha:</b>"]
            out += [f"  • {esc(x['name'])} — {x['branches']} filial · {_fmt(x['qty'])} dona · {_mlrd(x['value'])}"
                    for x in r.by_loc]
    else:
        rate = f"{_fmt(r.rate)} so'm/$" if r.rate else "—"
        out = [f"✅ <b>Ombor qoldig'i yuklandi</b> (#{r.upload_id})",
               f"Sana: <b>{d}</b> ({src})",
               f"Qatorlar: {_fmt(r.rows_total)} → yuklandi: <b>{_fmt(r.rows_loaded)}</b>",
               f"Omborlar: <b>{r.locations}</b> · SKU: {_fmt(r.skus)} · jami <b>{_fmt(r.qty)}</b> dona (mijoz uchun)",
               f"Qiymati: <b>{_mlrd(r.value_usd * (r.rate or 0))}</b> (A sektor narxi × kurs {rate}) · $ {_fmt(r.value_usd)}"]
        if r.by_loc:
            out += ["", "<b>Omborlar:</b>"]
            for x in r.by_loc:
                out.append(f"  • <b>{esc(x['name'])}</b> — {_fmt(x['qty'])} dona · {x['skus']} SKU · "
                           f"{_mlrd(x['value_usd'] * (r.rate or 0))}")
                if len(x["parts"]) > 1:
                    out.append(f"     <i>{esc(' + '.join(x['parts']))}</i>")
    if r.replaced.get("rows"):
        out.append(f"\n♻️ {d} uchun oldingi qoldiq almashtirildi ({_fmt(r.replaced['rows'])} qator)")
    if r.unknown_skus:
        out.append(f"\n⏳ Tovar spravochnigida yo'q SKU: <b>{r.unknown_skus}</b> — saqlab qo'yildi, "
                   "spravochnik yuklanganda avtomatik ulanadi")
    if r.new_branches:
        out.append(f"\n🆕 <b>Spravochnikda yo'q filial: {len(r.new_branches)}</b> — qoldig'i saqlab qo'yildi, "
                   "bot ombor/klaster/hududni so'raydi")
        out += [f"  • {esc(x['name'])}" + (f" ({esc(x['region'])})" if x.get("region") else "")
                + f" — {_fmt(x['qty'])} dona" for x in r.new_branches[:max_items]]
    if r.unknown_parts:
        out.append(f"\n❓ <b>Noma'lum ombor bo'limi: {len(r.unknown_parts)}</b> — qoldig'i saqlab qo'yildi, "
                   "bot qaysi omborga ekanini so'raydi")
        out += [f"  • {esc(x['name'])} — {_fmt(x['rows'])} qator, {_fmt(x['qty'])} dona" for x in r.unknown_parts[:max_items]]
    if r.out_of_scope_rows:
        out.append(f"\nℹ️ Boshqa kategoriyalar (yuklanmadi): {_fmt(r.out_of_scope_rows)} qator")
    if r.names_updated:
        out.append(f"✏️ Tovar nomi yangilandi (variant nomi): {_fmt(r.names_updated)} SKU")
    if r.zero_rows:
        out.append(f"\nℹ️ Qoldig'i 0 bo'lgan {_fmt(r.zero_rows)} qator o'tkazib yuborildi")
    if r.errors:
        out.append("\n❌ <b>Yuklanmagan qatorlar:</b>")
        out += [f"  • {esc(e)}" for e in r.errors[:max_items]]
    return "\n".join(out)
