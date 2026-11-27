"""Ta'minotchi jo'natmalari (023_shipments.sql) — Artel SAP hisoboti («01-24_bi.xlsx», har kuni) va
«Artel kodi ↔ Товар ид» bog'lash jadvali.

Jo'natma fayli ustunlari: ДатаДокум, Материал, Название, КумулКлчЗк (dona), Цена нетто, СтоимНетто ($),
Документ, Поз., Номер заказа на поставку («Биг Ишонч Коканд с Бух баз 1Ф» — manzil va baza), Статус, ВТДк, Вал.,
Торговый дом.
  * «в работе» — zakaz (hali jo'natilmagan), «Исполнено» / «частично поставлено» — jo'natilgan (yo'lda yoki kelgan).
  * YBRE / minus dona (brak qaytarish, tuzatish) — hisobga olinmaydi.
  * 0 $ qatorlar — konditsioner bloklari (-IN / -OUT), komplekt qatori bilan keladi — olinmaydi.
  * Qayta yuklansa — fayldagi hujjat sanalari almashtiriladi (dublikat yo'q).
Bog'lash jadvali: istalgan varaqda «Материал» va «Товар ид» ustunlari → product_aliases ('am:<material>').
"""
from __future__ import annotations

import asyncio
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape

import pandas as pd

from loaders_common import EXCEL_ENGINE, LoaderError, clean_header, clean_text, name_key

SUPPLIER = "Тошкент Artel КБТ"          # kirim faylidagi nom bilan bir xil — Yo'lda shu ta'minotchi guruhi kirimidan
MUST = {"ДатаДокум", "Материал", "КумулКлчЗк", "Статус"}
STATUS = {"исполнено": "done", "частично поставлено": "part", "в работе": "work"}
# manzil (PO matnidagi shahar) → ombor nomining boshi; filialning o'ziga boradiganlar
DEST_WH = {"коканд": "кукон", "қўқон": "кукон", "кашкадарё": "гузор", "кашкадарья": "гузор", "қашқадарё": "гузор",
           "гузор": "гузор", "самарканд": "самарканд", "самаркард": "самарканд", "самарқанд": "самарканд",
           "охангарон": "охангарон", "оҳангарон": "охангарон", "хоразм": "хоразм", "ургенч": "хоразм"}
DIRECT = {"наманган", "фаргона", "фарғона", "фергана"}
_PO = re.compile(r"^\s*(?:биг|big|артчик|artchik|арт\s*чик|климат|climate)?\s*ишонч\s+", re.I)


def is_ship_file(headers: set[str]) -> bool:
    return MUST <= headers and "Номер заказа на поставку" in headers


def parse_dest(po: str | None) -> tuple[str | None, str | None]:
    """«Биг Ишонч Коканд с Бух баз 1Ф» → ('Коканд', 'Бух')."""
    s = clean_text(po)
    if not s:
        return None, None
    s2 = _PO.sub("", s)
    city, _, rest = s2.partition(" с ")
    base = re.match(r"\s*(\S+)\s+баз", rest)
    return (city.strip() or None), (base.group(1) if base else None)


def _date(v):
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 30000 < v < 80000:       # Excel seriya raqami
        return date(1899, 12, 30) + timedelta(days=int(v))
    s = clean_text(v)
    if s:
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(s[:10], fmt).date()
            except ValueError:
                pass
    return None


def _num(v):
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("\xa0", "").replace(" ", "")
    if "," in s and "." in s:                    # 1.234,50
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


@dataclass
class ShipReport:
    upload_id: int
    rows_total: int = 0
    rows_loaded: int = 0
    dates: list = field(default_factory=list)
    docs: int = 0
    done_usd: float = 0.0
    done_qty: float = 0.0
    work_usd: float = 0.0
    work_qty: float = 0.0
    kits: int = 0
    returns_qty: float = 0.0
    mats: int = 0
    mats_bound: int = 0
    miss_usd: float = 0.0
    miss: list = field(default_factory=list)        # [(material, name, usd)]
    unknown_dest: dict = field(default_factory=dict)
    direct_usd: float = 0.0
    replaced: int = 0
    signals: int = 0


def _parse(data: bytes) -> tuple[pd.DataFrame, int]:
    raw = pd.read_excel(io.BytesIO(data), header=None, dtype=object, engine=EXCEL_ENGINE)
    hi = None
    for i in range(min(15, len(raw))):
        if MUST <= {clean_header(c) for c in raw.iloc[i].tolist()}:
            hi = i
            break
    if hi is None:
        raise LoaderError("Sarlavha topilmadi: " + ", ".join(sorted(MUST)))
    head = [clean_header(c) for c in raw.iloc[hi].tolist()]

    def col(name, required=True):
        if name in head:
            return head.index(name)               # birinchisi (fayldagi takroriy nomlar — ikkinchisi bo'sh)
        if required:
            raise LoaderError(f"«{name}» ustuni yo'q")
        return None
    c = {k: col(k, k in MUST) for k in ("ДатаДокум", "Материал", "Название", "КумулКлчЗк", "Цена нетто", "СтоимНетто",
                                         "Документ", "Поз.", "Номер заказа на поставку", "Статус", "ВТДк", "Вал.",
                                         "Торговый дом")}
    body = raw.iloc[hi + 1:]
    g = lambda k: body.iloc[:, c[k]] if c[k] is not None else pd.Series([None] * len(body), index=body.index)  # noqa: E731
    df = pd.DataFrame({
        "d": g("ДатаДокум").map(_date), "mat": g("Материал").map(lambda v: clean_text(v) or ""),
        "name": g("Название").map(clean_text), "qty": g("КумулКлчЗк").map(_num), "price": g("Цена нетто").map(_num),
        "val": g("СтоимНетто").map(_num), "doc": g("Документ").map(_num), "pos": g("Поз.").map(_num),
        "po": g("Номер заказа на поставку").map(clean_text), "st": g("Статус").map(lambda v: (clean_text(v) or "").lower()),
        "typ": g("ВТДк").map(clean_text), "cur": g("Вал.").map(lambda v: (clean_text(v) or "USD").upper()),
        "th": g("Торговый дом").map(clean_text)})
    total = int(df["mat"].ne("").sum())
    df = df[df["mat"].ne("") & df["d"].notna() & df["doc"].notna()].copy()
    return df, total


async def load_ship(pool, data: bytes, file_name: str | None, tg_user_id: int | None) -> ShipReport:
    async with pool.acquire() as con:
        upload_id = await con.fetchval("INSERT INTO uploads(kind, file_name, tg_user_id) VALUES ('ship',$1,$2) RETURNING id",
                                       file_name, tg_user_id)
        try:
            df, total = await asyncio.to_thread(_parse, data)
            if df.empty:
                raise LoaderError("Faylda birorta ham jo'natma qatori yo'q")
            rep = ShipReport(upload_id, rows_total=total)
            ret = (df["typ"].fillna("").str.upper().eq("YBRE")) | (df["qty"].fillna(0) <= 0)
            rep.returns_qty = abs(float(df.loc[ret, "qty"].fillna(0).sum()))
            df = df[~ret]
            kit = df["val"].fillna(0).eq(0) & df["price"].fillna(0).eq(0)
            rep.kits = int(kit.sum())
            df = df[~kit].copy()
            async with con.transaction():
                # valyuta: USD bo'lmasa — o'sha kunning bot kursi bilan
                if (df["cur"] != "USD").any():
                    for d in df.loc[df["cur"] != "USD", "d"].unique():
                        rate = float(await con.fetchval(
                            "SELECT rate FROM fx_periods WHERE $1 >= valid_from AND $1 < valid_to", d) or 0) or None
                        m = (df["cur"] != "USD") & (df["d"] == d)
                        df.loc[m, "val"] = df.loc[m, "val"] / rate if rate else None
                df["status"] = df["st"].map(lambda s: STATUS.get(s, "done" if "исполн" in s else "work"))
                pdest = df["po"].map(parse_dest)
                df["city"] = pdest.map(lambda x: x[0])
                df["base"] = pdest.map(lambda x: x[1])
                df["dkey"] = df["city"].map(lambda v: name_key(v) or "")
                whs = [(r["id"], name_key(r["name"]) or "") for r in await con.fetch("SELECT id, name FROM warehouses")]

                def wh_of(k):
                    if k in DIRECT:
                        return None, True
                    pref = DEST_WH.get(k)
                    if pref:
                        for wid, wn in whs:
                            if wn.startswith(pref):
                                return wid, False
                    for wid, wn in whs:
                        if k and wn.startswith(k):
                            return wid, False
                    return None, False
                dmap = {k: wh_of(k) for k in df["dkey"].unique()}
                df["wid"] = df["dkey"].map(lambda k: dmap[k][0])
                df["direct"] = df["dkey"].map(lambda k: dmap[k][1])
                alias = {r["alias_key"][3:]: r["product_id"] for r in await con.fetch(
                    "SELECT alias_key, product_id FROM product_aliases WHERE alias_key LIKE 'am:%'")}
                df["pid"] = df["mat"].map(alias.get)
                dates = sorted(set(df["d"]))
                rep.replaced = int((await con.execute(
                    "DELETE FROM sup_ship WHERE supplier = $1 AND doc_date = ANY($2::date[])", SUPPLIER, dates)).split()[-1])
                nn = lambda v: None if v is None or (isinstance(v, float) and v != v) else v  # noqa: E731
                recs: dict = {}
                for r in df.itertuples():
                    k = (int(r.doc), int(r.pos or 0))
                    if k in recs:                  # takroriy pozitsiya — qo'shiladi
                        recs[k][6] += float(r.qty)
                        recs[k][7] = (recs[k][7] or 0) + (nn(r.val) or 0)
                        continue
                    recs[k] = [k[0], k[1], r.d, SUPPLIER, r.mat, nn(r.name), float(r.qty), nn(r.val),
                               int(r.pid) if nn(r.pid) is not None else None, r.status, nn(r.po), nn(r.dkey) or None,
                               int(r.wid) if nn(r.wid) is not None else None, bool(r.direct), nn(r.base), nn(r.th), upload_id]
                await con.execute("DELETE FROM sup_ship WHERE (doc_no, pos) IN (SELECT * FROM unnest($1::bigint[], $2::int[]))",
                                  [k[0] for k in recs], [k[1] for k in recs])
                await con.copy_records_to_table(
                    "sup_ship", columns=["doc_no", "pos", "doc_date", "supplier", "material", "mat_name", "qty",
                                         "value_usd", "product_id", "status", "dest", "dest_key", "warehouse_id",
                                         "direct", "base", "trade_house", "upload_id"],
                    records=[tuple(v) for v in recs.values()])
                rep.signals = await make_ship_signals(con, dates, upload_id)
            rep.dates = [d.isoformat() for d in dates]
            rep.rows_loaded = len(df)
            rep.docs = int(df["doc"].nunique())
            sh = df["status"] != "work"
            rep.done_usd, rep.done_qty = float(df.loc[sh, "val"].sum()), float(df.loc[sh, "qty"].sum())
            rep.work_usd, rep.work_qty = float(df.loc[~sh, "val"].sum()), float(df.loc[~sh, "qty"].sum())
            rep.direct_usd = float(df.loc[df["direct"], "val"].sum())
            u = df.groupby("mat").agg(name=("name", "first"), pid=("pid", "first"), usd=("val", "sum"))
            rep.mats, rep.mats_bound = len(u), int(u["pid"].notna().sum())
            miss = u[u["pid"].isna()].sort_values("usd", ascending=False)
            rep.miss_usd = float(miss["usd"].sum())
            rep.miss = [(m, r["name"], float(r["usd"])) for m, r in miss.head(10).iterrows()]
            unk = df[df["wid"].isna() & ~df["direct"]]
            rep.unknown_dest = {str(k or "—"): float(v) for k, v in unk.groupby(unk["city"].fillna("—"))["val"].sum().items()}
        except Exception as e:
            await con.execute("UPDATE uploads SET status='failed', finished_at=now(), report=$2 WHERE id=$1",
                              upload_id, json.dumps({"error": str(e)}, ensure_ascii=False))
            raise
        await con.execute(
            "UPDATE uploads SET status='done', finished_at=now(), rows_total=$2, rows_loaded=$3, report=$4 WHERE id=$1",
            upload_id, rep.rows_total, rep.rows_loaded, json.dumps(rep.__dict__, ensure_ascii=False, default=str))
        return rep


async def make_ship_signals(con, dates: list, upload_id: int | None = None) -> int:
    """Matritsada yo'q tovar zakaz qilindi / jo'natildi: ombor × tovar × oy (kirim signalidan oldin keladi).
    Kategoriyasi matritsada bor, lekin shu ombor filiallarining birortasi matritsasida yo'q."""
    if not dates:
        return 0
    months = sorted({d.replace(day=1) for d in dates})
    rows = await con.fetch(
        """WITH mc AS (SELECT DISTINCT category_id FROM matrix_rows WHERE category_id IS NOT NULL),
                wmx AS (SELECT DISTINCT b.warehouse_id, m.product_id FROM matrix_rows m JOIN branches b ON b.id = m.branch_id
                        WHERE m.product_id IS NOT NULL AND b.warehouse_id IS NOT NULL),
                r AS (SELECT date_trunc('month', s.doc_date)::date mon, s.warehouse_id, s.product_id,
                             max(s.doc_date) last_date, string_agg(DISTINCT s.doc_no::text, ', ') orders,
                             max(s.supplier) supplier, sum(s.qty)::float8 qty, sum(s.value_usd)::float8 usd,
                             bool_and(s.status = 'work') zakaz
                      FROM sup_ship s JOIN products p USING (product_id)
                      WHERE date_trunc('month', s.doc_date)::date = ANY($1::date[]) AND s.warehouse_id IS NOT NULL
                        AND p.category_id IN (SELECT category_id FROM mc)
                        AND NOT EXISTS (SELECT 1 FROM wmx WHERE wmx.warehouse_id = s.warehouse_id AND wmx.product_id = s.product_id)
                      GROUP BY 1, 2, 3)
           INSERT INTO supply_signals AS g (kind, skey, warehouse_id, product_id, data, upload_id)
           SELECT 'ship_nomx', 'sn:' || to_char(mon, 'YYYY-MM') || ':' || warehouse_id || ':' || product_id,
                  warehouse_id, product_id,
                  jsonb_build_object('month', mon, 'date', last_date, 'order', orders, 'supplier', supplier,
                                     'qty', qty, 'usd', usd, 'stage', CASE WHEN zakaz THEN 'zakaz' ELSE 'jo''natildi' END), $2
           FROM r
           ON CONFLICT (skey) DO UPDATE SET data = EXCLUDED.data
           RETURNING (xmax = 0) AS ins""", months, upload_id)
    from supply import recheck_signals
    await recheck_signals(con)
    return sum(1 for r in rows if r["ins"])


# ---------------------------------------------------------------------------
# Bog'lash jadvali: Материал → Товар ид
def is_map_file(data: bytes) -> bool:
    try:
        sheets = pd.read_excel(io.BytesIO(data), header=None, dtype=object, nrows=6, sheet_name=None, engine=EXCEL_ENGINE)
    except Exception:  # noqa: BLE001
        return False
    return any(_map_cols(df) for df in sheets.values())


def _map_cols(df: pd.DataFrame):
    for i in range(min(6, len(df))):
        h = [(name_key(c) or "") for c in df.iloc[i].tolist()]
        if "материал" in h and any(x in h for x in ("товар ид", "товар id", "tovar id")):
            return i, h.index("материал"), next(h.index(x) for x in ("товар ид", "товар id", "tovar id") if x in h)
    return None


def _parse_map(data: bytes) -> list[tuple[str, int]]:
    out = []
    for df in pd.read_excel(io.BytesIO(data), header=None, dtype=object, sheet_name=None, engine=EXCEL_ENGINE).values():
        mc = _map_cols(df.head(6))
        if not mc:
            continue
        hi, cm, cp = mc
        for r in df.iloc[hi + 1:].itertuples(index=False):
            m, p = clean_text(r[cm]), _num(r[cp])
            if m and p and p > 0:
                out.append((m, int(p)))
    return out


async def load_map(pool, data: bytes, file_name: str | None, tg_user_id: int | None) -> dict:
    pairs = await asyncio.to_thread(_parse_map, data)
    if not pairs:
        raise LoaderError("Jadvalda «Материал» va «Товар ид» ustunli qator topilmadi")
    async with pool.acquire() as con:
        upload_id = await con.fetchval("INSERT INTO uploads(kind, file_name, tg_user_id) VALUES ('ship_map',$1,$2) RETURNING id",
                                       file_name, tg_user_id)
        async with con.transaction():
            have = {r[0] for r in await con.fetch("SELECT product_id FROM products WHERE product_id = ANY($1::bigint[])",
                                                   list({p for _, p in pairs}))}
            ok = [(m, p) for m, p in pairs if p in have]
            bad = sorted({(m, p) for m, p in pairs if p not in have})
            await con.executemany(
                """INSERT INTO product_aliases (alias_key, product_id, created_by) VALUES ($1, $2, $3)
                   ON CONFLICT (alias_key) DO UPDATE SET product_id = EXCLUDED.product_id, created_by = EXCLUDED.created_by,
                   created_at = now()""", [("am:" + m, p, tg_user_id) for m, p in ok])
            n = await rebind_ship(con)
        rep = {"upload_id": upload_id, "pairs": len(pairs), "ok": len(ok), "bad": bad[:15], "bad_n": len(bad), "rebound": n}
        await con.execute("UPDATE uploads SET status='done', finished_at=now(), rows_total=$2, rows_loaded=$3, report=$4 "
                          "WHERE id=$1", upload_id, len(pairs), len(ok), json.dumps(rep, ensure_ascii=False))
    return rep


async def rebind_ship(con) -> int:
    """sup_ship.product_id — Artel kodi bog'lanishlari bo'yicha (yangi / o'zgargan). Yangilangan qatorlar soni."""
    r = await con.execute(
        """UPDATE sup_ship s SET product_id = a.product_id FROM product_aliases a
           WHERE a.alias_key = 'am:' || s.material AND s.product_id IS DISTINCT FROM a.product_id""")
    n = int(r.split()[-1])
    if n:
        d = [x[0] for x in await con.fetch("SELECT DISTINCT doc_date FROM sup_ship WHERE product_id IS NOT NULL")]
        await make_ship_signals(con, d)
    return n


def format_map_report(r: dict) -> str:
    lines = ["🔗 <b>Artel kodi ↔ Товар ид jadvali yuklandi</b>",
             f"• Juftliklar: {r['pairs']} · saqlandi: <b>{r['ok']}</b>",
             f"• Jo'natmalarda qayta bog'landi: {r['rebound']} qator"]
    if r["bad_n"]:
        lines += ["", f"⚠️ <b>Товар ид spravochnikda yo'q: {r['bad_n']}</b>"]
        lines += [f"• {escape(m)} → {p}" for m, p in r["bad"][:10]]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
def _usd(v: float) -> str:
    return f"{v / 1e6:,.2f} mln $".replace(",", " ").replace(".", ",") if abs(v) >= 1e6 else \
        f"{v / 1e3:,.0f} ming $".replace(",", " ")


def _f(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")


def format_report(r: ShipReport) -> str:
    d1, d2 = r.dates[0], r.dates[-1]
    lines = [f"✅ <b>Artel jo'natmalari yuklandi</b> (#{r.upload_id}) · {d1[8:10]}.{d1[5:7]}–{d2[8:10]}.{d2[5:7]}",
             f"📦 {_f(r.rows_loaded)} qator · {r.docs} hujjat · {_usd(r.done_usd + r.work_usd)}",
             f"• Jo'natilgan (Исполнено): <b>{_usd(r.done_usd)}</b> · {_f(r.done_qty)} dona",
             f"• Zakazda (в работе): <b>{_usd(r.work_usd)}</b> · {_f(r.work_qty)} dona"]
    if r.direct_usd:
        lines.append(f"• Filialning o'ziga (Наманган, Фаргона): {_usd(r.direct_usd)}")
    if r.kits:
        lines.append(f"• Konditsioner bloklari (IN/OUT, 0 $): {r.kits} qator — komplekt bilan, alohida sanalmadi")
    if r.returns_qty:
        lines.append(f"• Qaytarish / tuzatish (YBRE): {_f(r.returns_qty)} dona — hisobga olinmadi")
    if r.replaced:
        lines.append(f"♻️ Shu sanalarning oldingi {_f(r.replaced)} qatori almashtirildi")
    lines.append(f"🔗 Tovar: <b>{r.mats_bound} / {r.mats}</b> Artel kodi bog'langan")
    if r.miss:
        lines.append(f"⏳ <b>{r.mats - r.mats_bound} ta kod bog'lanmagan</b> — {_usd(r.miss_usd)} → 📥 Kutayotgan")
        lines += [f"   • {escape(m)} {escape(n or '')} · {_usd(u)}" for m, n, u in r.miss[:5]]
    if r.unknown_dest:
        lines.append("❓ Manzil omborga bog'lanmadi: " + ", ".join(f"{escape(k)} ({_usd(v)})" for k, v in r.unknown_dest.items()))
    if r.signals:
        lines.append(f"🚫 Matritsada yo'q tovar zakazi / jo'natmasi: <b>{r.signals}</b> yangi signal")
    lines.append("<i>Mini App → Ta'minot → Zakaz / Yo'lda</i>")
    return "\n".join(lines)
