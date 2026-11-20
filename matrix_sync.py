"""Matritsa: Google Sheet «Матрица КБТ» → bazaga (013_matrix.sql) + bog'lanish hisoboti.

Manba: Railway Variables
    GOOGLE_SA_JSON   — servis-akkaunt kaliti (JSON fayl matni to'liq; yoki base64)
    MATRIX_SHEET_ID  — jadval ID yoki to'liq havolasi
    MATRIX_SHEET_TAB — varaq nomi (standart: Matritsa)
Varaq ustunlari: Xudud, Filial, Filial ID, Format, Klaster, Категория, Vitrina sig'imi, Подкатегория,
                 1/2/3-Субкатегория, Tovar nomi, Brend, Turishi.

Bog'lash:
  * filial — «Filial» nomi bo'yicha (branches.name_key; apostrof/registr/bo'shliq farqi hisobga olinmaydi);
  * tovar — «Tovar nomi» = spravochnikdagi tovar (variant) nomi.
      1-daraja: aynan mos (name_key);
      2-daraja: taxminiy — bo'shliq, tinish belgilari va kirill/lotin bir xil ko'rinadigan harflar farqi
                e'tiborga olinmaydi; faqat BITTA SKU chiqsa bog'lanadi.
Har kuni MATRIX_SYNC_HOUR (06:00, Toshkent) da avtomatik; /matritsa — qo'lda.
"""
from __future__ import annotations

import asyncio
import base64
import html
import io
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import asyncpg

import config
from loaders_common import clean_text, name_key, release_memory, to_number

log = logging.getLogger(__name__)
TZ = timezone(timedelta(hours=5))          # Asia/Tashkent (yozgi vaqt yo'q)
_LOCK = asyncio.Lock()

COLS = {  # ichki nom → varaqdagi sarlavha
    "region": "Xudud", "branch_name": "Filial", "branch_ext_id": "Filial ID", "format": "Format",
    "cluster": "Klaster", "category": "Категория", "vitrina_cap": "Vitrina sig'imi", "sub0": "Подкатегория",
    "sub1": "1-Субкатегория", "sub2": "2-Субкатегория", "sub3": "3-Субкатегория",
    "product_name": "Tovar nomi", "brand": "Brend", "need_qty": "Turishi",
}
REQUIRED = ("branch_name", "category", "product_name", "need_qty", "vitrina_cap")


class MatrixError(Exception):
    """Sheet'ni o'qib/tushunib bo'lmadi — eski matritsa saqlanadi."""


# ---------------------------------------------------------------------------
# Google Sheets
def sheet_id() -> str:
    v = config.MATRIX_SHEET_ID
    m = re.search(r"/d/([A-Za-z0-9_-]{20,})", v)
    return m.group(1) if m else v


def _sa_info() -> dict:
    raw = config.GOOGLE_SA_JSON
    if not raw:
        raise MatrixError("GOOGLE_SA_JSON o'rnatilmagan (Railway Variables)")
    if not raw.lstrip().startswith("{"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except Exception as e:  # noqa: BLE001
            raise MatrixError("GOOGLE_SA_JSON o'qilmadi — JSON fayl matnini to'liq qo'ying") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise MatrixError(f"GOOGLE_SA_JSON — JSON xato: {e}") from e


def sa_email() -> str | None:
    try:
        return _sa_info().get("client_email")
    except MatrixError:
        return None


def _fetch_values_sync() -> list[list]:
    import requests
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    if not config.MATRIX_SHEET_ID:
        raise MatrixError("MATRIX_SHEET_ID o'rnatilmagan (Railway Variables)")
    info = _sa_info()
    try:
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        creds.refresh(Request())
    except Exception as e:  # noqa: BLE001
        raise MatrixError(f"Google'ga kirib bo'lmadi (kalit noto'g'ri?): {e}") from e
    sid, tab = sheet_id(), config.MATRIX_SHEET_TAB
    h = {"Authorization": f"Bearer {creds.token}"}
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sid}"
    r = requests.get(f"{base}/values/{quote(tab)}", headers=h, timeout=120,
                     params={"valueRenderOption": "UNFORMATTED_VALUE", "majorDimension": "ROWS"})
    if r.status_code == 403:
        raise MatrixError(f"Jadvalga ruxsat yo'q. Jadval egasi uni {info.get('client_email')} "
                          f"pochtasiga «Ko'ruvchi» (Viewer) qilib ulashishi kerak")
    if r.status_code == 404:
        raise MatrixError("Jadval topilmadi — MATRIX_SHEET_ID ni tekshiring")
    if r.status_code == 400:
        tabs = []
        try:
            m = requests.get(base, headers=h, timeout=60, params={"fields": "sheets.properties.title"})
            tabs = [s["properties"]["title"] for s in m.json().get("sheets", [])]
        except Exception:  # noqa: BLE001
            pass
        raise MatrixError(f"«{tab}» varag'i topilmadi. Jadvaldagi varaqlar: {', '.join(tabs) or '?'} "
                          f"(kerak bo'lsa MATRIX_SHEET_TAB ni o'zgartiring)")
    if r.status_code == 429:
        raise MatrixError("Google so'rovlar chegarasi (429) — birozdan keyin qayta urinib ko'ring")
    if not r.ok:
        raise MatrixError(f"Google xatosi {r.status_code}: {r.text[:200]}")
    return r.json().get("values", [])


async def fetch_values() -> list[list]:
    return await asyncio.to_thread(_fetch_values_sync)


# ---------------------------------------------------------------------------
# Varaqni tushunish
def parse_rows(values: list[list]) -> list[dict]:
    """Sarlavha qatorini topadi (birinchi 10 qatordan), ustunlarni nom bo'yicha bog'laydi."""
    want = {name_key(v): k for k, v in COLS.items()}
    hdr_i, idx = None, {}
    for i, row in enumerate(values[:10]):
        found = {want[name_key(c)]: j for j, c in enumerate(row) if name_key(c) in want}
        if all(k in found for k in REQUIRED):
            hdr_i, idx = i, found
            break
    if hdr_i is None:
        miss = ", ".join(f"«{COLS[k]}»" for k in REQUIRED)
        raise MatrixError(f"Sarlavha topilmadi. Majburiy ustunlar: {miss}")
    out = []
    for row in values[hdr_i + 1:]:
        g = lambda k: row[idx[k]] if k in idx and idx[k] < len(row) else None  # noqa: E731
        name, br = clean_text(g("product_name")), clean_text(g("branch_name"))
        if not name or not br:
            continue
        need = to_number(g("need_qty"))
        if need is None or need <= 0:
            continue
        ext = g("branch_ext_id")
        if isinstance(ext, float) and ext.is_integer():
            ext = int(ext)
        out.append({
            "region": clean_text(g("region")), "branch_name": br, "branch_ext_id": clean_text(ext),
            "format": clean_text(g("format")), "cluster": clean_text(g("cluster")),
            "category": clean_text(g("category")) or "—", "vitrina_cap": to_number(g("vitrina_cap")),
            "sub0": clean_text(g("sub0")), "sub1": clean_text(g("sub1")), "sub2": clean_text(g("sub2")),
            "sub3": clean_text(g("sub3")), "product_name": name, "brand": clean_text(g("brand")),
            "need_qty": need,
        })
    if not out:
        raise MatrixError("Varaqda birorta to'liq qator yo'q (Filial, Tovar nomi, Turishi)")
    return out


# kirill/lotin bir xil ko'rinadigan harflar + kg/кг
_LOOK = str.maketrans({"а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p",
                       "с": "c", "т": "t", "у": "y", "х": "x", "г": "g"})
_NONALNUM = re.compile(r"[\W_]+", re.UNICODE)


def loose_key(v) -> str | None:
    k = name_key(v)
    if not k:
        return None
    return _NONALNUM.sub("", k.replace(",", ".").translate(_LOOK)) or None


# ---------------------------------------------------------------------------
# Bog'lash va yozish
_CUR_MX = """SELECT branch_id, product_id, max(category_id) category_id, max(need_qty) need FROM matrix_rows
             WHERE branch_id IS NOT NULL AND product_id IS NOT NULL AND category_id IS NOT NULL GROUP BY 1, 2"""


async def snapshot_hist(con: asyncpg.Connection) -> bool:
    """Joriy matritsani matrix_hist'ga versiya sifatida yozadi (o'zgargan bo'lsa). Kunlik chartlar uchun."""
    sig = ("SELECT md5(coalesce(string_agg(branch_id || ':' || product_id || ':' || need, ',' "
           "ORDER BY branch_id, product_id), '')) FROM ({}) x")
    last = await con.fetchval("SELECT max(ver) FROM matrix_hist")
    cur = await con.fetchval(sig.format(_CUR_MX))
    if last is not None and cur == await con.fetchval(
            sig.format("SELECT branch_id, product_id, need FROM matrix_hist WHERE ver = $1"), last):
        return False
    today = datetime.now(TZ).date()
    ver = today if last is not None else datetime(2000, 1, 1).date()
    async with con.transaction():
        await con.execute("DELETE FROM matrix_hist WHERE ver = $1", ver)
        await con.execute(f"INSERT INTO matrix_hist (ver, branch_id, product_id, category_id, need) "
                          f"SELECT $1, branch_id, product_id, category_id, need FROM ({_CUR_MX}) x", ver)
        # kunlik yig'indilar: shu versiya amal qiladigan sanalar qayta hisoblanadi
        await con.execute("DELETE FROM mx_dsum_meta WHERE snap_date >= $1", ver)
    return True


async def branch_map(con: asyncpg.Connection) -> dict[str, tuple[int, int | None]]:
    """name_key → (branch_id, warehouse_id): botdagi filial nomlari + qo'lda bog'langan matritsa nomlari."""
    br = {b["name_key"]: (b["id"], b["warehouse_id"])
          for b in await con.fetch("SELECT id, name_key, warehouse_id FROM branches")}
    for a in await con.fetch("SELECT a.alias_key, b.id, b.warehouse_id FROM branch_aliases a "
                             "JOIN branches b ON b.id = a.branch_id"):
        br.setdefault(a["alias_key"], (a["id"], a["warehouse_id"]))
    return br


async def unbound_branches(con: asyncpg.Connection) -> dict[str, dict]:
    """Matritsadagi bog'lanmagan filiallar: name_key → {name, region, cluster, format, skus}."""
    out: dict[str, dict] = {}
    for r in await con.fetch(
            """SELECT branch_name, max(region) region, max(cluster) cluster, max(format) fmt,
                      count(DISTINCT product_name) skus
               FROM matrix_rows WHERE branch_id IS NULL GROUP BY branch_name"""):
        k = name_key(r["branch_name"])
        if not k:
            continue
        x = out.setdefault(k, {"name": r["branch_name"], "region": None, "cluster": None, "format": None, "skus": 0})
        x["skus"] += r["skus"]
        x["region"] = x["region"] or r["region"]
        x["cluster"] = x["cluster"] or r["cluster"]
        x["format"] = x["format"] or r["fmt"]
    return out


async def rebind_branches(con: asyncpg.Connection) -> int:
    """Bog'lanmagan matritsa qatorlarini hozirgi filiallar/bog'lashlar bo'yicha qayta ulaydi
    (06:00 ni kutmasdan). Ulangan qatorlar sonini qaytaradi."""
    names = [r["branch_name"] for r in
             await con.fetch("SELECT DISTINCT branch_name FROM matrix_rows WHERE branch_id IS NULL")]
    if not names:
        return 0
    br = await branch_map(con)
    pairs = [(n, br[k][0]) for n in names if (k := name_key(n)) in br]
    if not pairs:
        return 0
    n = 0
    async with con.transaction():
        for nm, bid in pairs:
            n += int((await con.execute("UPDATE matrix_rows SET branch_id = $2 WHERE branch_name = $1 "
                                        "AND branch_id IS NULL", nm, bid)).split()[-1])
        # oxirgi sinxron statistikasini yangilash (Mini App'dagi «Bog'lanmagan» yozuvi)
        allb = [r["branch_name"] for r in await con.fetch("SELECT DISTINCT branch_name FROM matrix_rows")]
        ok = {x for x in allb if name_key(x) in br}
        no_wh = sum(1 for x in ok if br[name_key(x)][1] is None)
        rows_ok = await con.fetchval("SELECT count(*) FROM matrix_rows WHERE branch_id IS NOT NULL "
                                     "AND product_id IS NOT NULL")
        await con.execute(
            """UPDATE matrix_syncs SET stats = coalesce(stats, '{}'::jsonb) || jsonb_build_object('branches', $1::int, 'branches_ok', $2::int,
                      'branches_no_wh', $3::int, 'rows_ok', $4::int)
               WHERE id = (SELECT max(id) FROM matrix_syncs WHERE status = 'ok')""",
            len(set(allb)), len(ok), no_wh, rows_ok)
        # ▲▼ tarixi joriy qoldiq sanasi uchun qayta hisoblansin
        await con.execute("DELETE FROM matrix_daily WHERE snap_date = (SELECT max(snap_date) FROM stock_branch)")
    try:
        await snapshot_hist(con)
    except Exception:  # noqa: BLE001
        log.exception("matrix_hist yozilmadi")
    return n


async def bind_and_store(con: asyncpg.Connection, rows: list[dict]) -> dict:
    exact: dict[str, list[int]] = defaultdict(list)
    loose: dict[str, list[int]] = defaultdict(list)
    pcat: dict[int, int] = {}
    pname: dict[int, str] = {}
    for p in await con.fetch("SELECT product_id, name, category_id FROM products"):
        exact[name_key(p["name"])].append(p["product_id"])
        lk = loose_key(p["name"])
        if lk:
            loose[lk].append(p["product_id"])
        pcat[p["product_id"]] = p["category_id"]
        pname[p["product_id"]] = p["name"]
    br = await branch_map(con)
    cats = {name_key(c["name"]): c["id"] for c in await con.fetch("SELECT id, name FROM categories")}

    pmatch: dict[str, tuple[int | None, int | None, str | None]] = {}  # nom → (pid, tier, izoh)
    for name in {r["product_name"] for r in rows}:
        ids = exact.get(name_key(name), [])
        if len(ids) == 1:
            pmatch[name] = (ids[0], 1, None)
            continue
        if len(ids) > 1:
            pmatch[name] = (min(ids), 1, f"spravochnikda {len(ids)} ta bir xil nomli SKU")
            continue
        ids = loose.get(loose_key(name) or "", [])
        if len(ids) == 1:
            pmatch[name] = (ids[0], 2, None)
        elif len(ids) > 1:
            pmatch[name] = (None, None, f"taxminan {len(ids)} ta SKU'ga o'xshaydi")
        else:
            pmatch[name] = (None, None, None)

    recs = []
    for r in rows:
        pid, tier, _ = pmatch[r["product_name"]]
        b = br.get(name_key(r["branch_name"]))
        cid = cats.get(name_key(r["category"])) or (pcat.get(pid) if pid else None)
        recs.append((r["region"], r["branch_name"], r["branch_ext_id"], r["format"], r["cluster"], r["category"],
                     r["vitrina_cap"], r["sub0"], r["sub1"], r["sub2"], r["sub3"], r["product_name"], r["brand"],
                     r["need_qty"], b[0] if b else None, pid, tier, cid))
    async with con.transaction():
        await con.execute("TRUNCATE matrix_rows RESTART IDENTITY")
        await con.copy_records_to_table(
            "matrix_rows", records=recs,
            columns=["region", "branch_name", "branch_ext_id", "format", "cluster", "category", "vitrina_cap",
                     "sub0", "sub1", "sub2", "sub3", "product_name", "brand", "need_qty", "branch_id",
                     "product_id", "match_tier", "category_id"])

    # statistika
    bnames = {r["branch_name"] for r in rows}
    b_ok = {n for n in bnames if name_key(n) in br}
    no_wh = sorted(n for n in b_ok if br[name_key(n)][1] is None)
    names = set(pmatch)
    t1 = sum(1 for n in names if pmatch[n][1] == 1)
    t2 = sum(1 for n in names if pmatch[n][1] == 2)
    rows_ok = sum(1 for x in recs if x[14] and x[15])
    cats_cnt: dict[str, int] = defaultdict(int)
    for r in rows:
        cats_cnt[r["category"]] += 1
    return {
        "rows": len(rows), "rows_ok": rows_ok,
        "branches": len(bnames), "branches_ok": len(b_ok), "branches_no_wh": len(no_wh),
        "skus": len(names), "skus_exact": t1, "skus_loose": t2, "skus_miss": len(names) - t1 - t2,
        "cat_unknown": sorted(c for c in cats_cnt if name_key(c) not in cats),
        "categories": dict(sorted(cats_cnt.items(), key=lambda kv: -kv[1])),
    }


async def sync(pool: asyncpg.Pool, trigger: str = "auto", user_id: int | None = None) -> dict:
    """Sheet → baza. Muvaffaqiyatli bo'lsa stats; xato bo'lsa MatrixError (eski matritsa qoladi)."""
    if _LOCK.locked():
        raise MatrixError("Matritsa hozir yangilanmoqda — bir daqiqadan keyin qayta urinib ko'ring")
    async with _LOCK:
        sid = await pool.fetchval("INSERT INTO matrix_syncs(trigger, started_by) VALUES ($1,$2) RETURNING id",
                                  trigger, user_id)
        try:
            values = await fetch_values()
            rows = parse_rows(values)
            del values
            async with pool.acquire() as con:
                stats = await bind_and_store(con, rows)
                try:                                   # kunlik chartlar uchun matritsa versiyasi
                    await snapshot_hist(con)
                except Exception:  # noqa: BLE001
                    log.exception("matrix_hist yozilmadi")
                try:                                   # ▲▼ uchun bugungi bajarilish tarixi
                    from api_matrix import refresh_daily
                    await refresh_daily(con, await con.fetchval("SELECT max(snap_date) FROM stock_branch"))
                except Exception:  # noqa: BLE001
                    log.exception("matrix_daily yozilmadi")
            await pool.execute("""UPDATE matrix_syncs SET status='ok', finished_at=now(), rows=$2, stats=$3::jsonb
                                  WHERE id=$1""", sid, stats["rows"], json.dumps(stats, ensure_ascii=False))
            stats["at"] = datetime.now(TZ)
            return stats
        except Exception as e:
            msg = str(e) if isinstance(e, MatrixError) else f"{type(e).__name__}: {e}"
            await pool.execute("UPDATE matrix_syncs SET status='failed', finished_at=now(), error=$2 WHERE id=$1",
                               sid, msg[:1000])
            if isinstance(e, MatrixError):
                raise
            log.exception("Matritsa sinxronlash xatosi")
            raise MatrixError(msg) from e
        finally:
            release_memory()


async def last_ok(pool: asyncpg.Pool) -> dict | None:
    r = await pool.fetchrow("""SELECT finished_at, stats FROM matrix_syncs WHERE status='ok'
                               ORDER BY id DESC LIMIT 1""")
    if not r:
        return None
    s = r["stats"] if isinstance(r["stats"], dict) else json.loads(r["stats"])
    s["at"] = r["finished_at"].astimezone(TZ)
    return s


# ---------------------------------------------------------------------------
# Hisobot
def _n(v) -> str:
    return f"{v:,}".replace(",", " ")


def _pct(a, b) -> str:
    return f"{a / b * 100:.1f}%".replace(".", ",") if b else "—"


def summary_text(s: dict, title: str = "🧩 <b>Matritsa yangilandi</b>") -> str:
    e = lambda v: html.escape(str(v), quote=False)  # noqa: E731
    lines = [f"{title} · {s['at']:%d.%m.%Y %H:%M}",
             f"{_n(s['rows'])} qator · {_n(s['branches'])} filial · {len(s['categories'])} kategoriya · "
             f"{_n(s['skus'])} SKU", "",
             f"<b>Bog'langan qatorlar: {_n(s['rows_ok'])} / {_n(s['rows'])} ({_pct(s['rows_ok'], s['rows'])})</b>",
             f"🏬 Filial: {_n(s['branches_ok'])} bog'landi" + (
                 f" · ⚠️ <b>{_n(s['branches'] - s['branches_ok'])} topilmadi</b>"
                 if s['branches'] > s['branches_ok'] else " ✓"),
             f"📦 SKU: {_n(s['skus_exact'])} aynan" + (f" + {_n(s['skus_loose'])} taxminiy" if s['skus_loose'] else "")
             + (f" · ⚠️ <b>{_n(s['skus_miss'])} topilmadi</b>" if s['skus_miss'] else " ✓")]
    if s.get("branches_no_wh"):
        lines.append(f"🚚 Ombori ko'rsatilmagan filial: {s['branches_no_wh']} (zakaz hisobiga kirmaydi)")
    if s.get("cat_unknown"):
        lines.append("🗂 Botda yo'q kategoriya nomi: " + ", ".join(e(c) for c in s["cat_unknown"][:6]))
    if s['skus_miss'] or s['skus_loose'] or s['branches'] > s['branches_ok'] or s.get("branches_no_wh"):
        lines += ["", "Ro'yxat — «⬇ Bog'lanish Excel» tugmasida."]
    return "\n".join(lines)


async def binding_xlsx(con: asyncpg.Connection) -> bytes:
    from openpyxl import Workbook

    from export_missing import BOLD, HEAD_FILL, _autowidth

    wb = Workbook()

    def sheet(title, head, rows, first=False):
        ws = wb.active if first else wb.create_sheet(title)
        ws.title = title
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

    sheet("Tovar topilmadi", ["Категория", "Brend", "Tovar nomi (matritsada)", "Filiallar soni", "Jami turishi"],
          await con.fetch("""SELECT category, brand, product_name, count(DISTINCT branch_name), sum(need_qty)::float
                             FROM matrix_rows WHERE product_id IS NULL
                             GROUP BY 1,2,3 ORDER BY 1,2,3"""), first=True)
    sheet("Taxminiy bog'langan", ["Категория", "Tovar nomi (matritsada)", "Botdagi nom", "Tovar ID"],
          await con.fetch("""SELECT DISTINCT m.category, m.product_name, p.name, p.product_id
                             FROM matrix_rows m JOIN products p ON p.product_id = m.product_id
                             WHERE m.match_tier = 2 ORDER BY 1,2"""))
    sheet("Filial topilmadi", ["Xudud", "Filial (matritsada)", "Filial ID", "Format", "Klaster", "Qatorlar"],
          await con.fetch("""SELECT region, branch_name, branch_ext_id, format, cluster, count(*)
                             FROM matrix_rows WHERE branch_id IS NULL
                             GROUP BY 1,2,3,4,5 ORDER BY 1,2"""))
    sheet("Ombori yo'q filial", ["Xudud", "Filial", "Qatorlar"],
          await con.fetch("""SELECT m.region, m.branch_name, count(*) FROM matrix_rows m
                             JOIN branches b ON b.id = m.branch_id WHERE b.warehouse_id IS NULL
                             GROUP BY 1,2 ORDER BY 1,2"""))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Har kuni 06:00
async def _notify_admins(text: str, kb=None) -> None:
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    if not config.BOT_TOKEN:
        return
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        for uid in config.ADMIN_IDS:
            try:
                await bot.send_message(uid, text, reply_markup=kb)
            except Exception as e:  # noqa: BLE001
                log.warning("Matritsa xabari %s ga yuborilmadi: %s", uid, e)
    finally:
        await bot.session.close()


def result_kb(s: dict | None = None):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    rows = []
    top = []
    if config.WEBAPP_URL:
        top.append(InlineKeyboardButton(text="📱 Mini App'da ochish", web_app=WebAppInfo(url=config.WEBAPP_URL)))
    top.append(InlineKeyboardButton(text="⬇ Zakaz Excel", callback_data="mx:zx"))
    rows.append(top)
    unb = s and (s.get("skus_miss") or s.get("branches", 0) > s.get("branches_ok", 0) or s.get("branches_no_wh"))
    rows.append(([InlineKeyboardButton(text="⚠️ Bog'lanish Excel", callback_data="mx:xl")] if unb else [])
                + [InlineKeyboardButton(text="🔄 Qayta o'qish", callback_data="mx:sync")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _usd(v: float) -> str:
    if v >= 1e6:
        return f"{v / 1e6:.2f} mln $".replace(".", ",")
    if v >= 1e3:
        return f"{v / 1e3:.0f} ming $"
    return f"{v:.0f} $"


def _pp(d) -> str:
    if d is None:
        return ""
    a = "▲" if d > 0.05 else "▼" if d < -0.05 else "•"
    return f" ({a}{abs(d):.1f} oldingi kunga)".replace(".", ",")


def _p1(v) -> str:
    return "—" if v is None else f"{v:.1f}%".replace(".", ",")


def digest_text(s: dict, dg: dict | None) -> str:
    """06:00 / /matritsa xabari: o'qish natijasi + holat + zakaz."""
    e = lambda v: html.escape(str(v), quote=False)  # noqa: E731
    unb = []
    if s["branches"] > s["branches_ok"]:
        unb.append(f"{s['branches'] - s['branches_ok']} filial")
    if s["skus_miss"]:
        unb.append(f"{s['skus_miss']} SKU")
    lines = [f"🧩 <b>Matritsa yangilandi</b> · {s['at']:%d.%m.%Y %H:%M}",
             f"{_n(s['rows'])} qator · {_n(s['branches'])} filial · bog'landi {_pct(s['rows_ok'], s['rows'])}"
             + (f" · ⚠️ bog'lanmagan: {', '.join(unb)}" if unb else " ✓")]
    if s.get("branches_no_wh"):
        lines.append(f"🚚 Ombori ko'rsatilmagan filial: {s['branches_no_wh']} (zakazga kirmaydi)")
    ov = (dg or {}).get("ov") or {}
    if not ov.get("snap", {}).get("b"):
        lines += ["", "Filial qoldig'i hali yuklanmagan — bajarilish hisoblanmadi."]
        return "\n".join(lines)
    KB = ov.get("kpi_by") or {}
    lines.append("")
    lines.append(f"<i>qoldiq {ov['snap']['b'][8:10]}.{ov['snap']['b'][5:7]} holati</i>")
    for g, icon, name in (("f", "🏬", "Filiallar"), ("m", "🏪", "Mini do'konlar")):
        K = KB.get(g)
        if not K or not K.get("pos"):
            continue
        lines.append(f"{icon} <b>{name}: {_p1(K['pct'])}</b>{_pp(K.get('d'))}")
        lines.append(f"   yo'q {_n(K['miss'])} poz" + (f" · kam {_n(K['low'])}" if K.get("low") else "")
                     + f" · omborda bor {_n(K['inwh'])}")
    worst = [b for b in ov.get("branches", []) if b.get("g", "f") == "f" and not b.get("nodata") and b.get("pct") is not None][:3]
    if worst:
        lines += ["", "<b>Eng past 3 filial:</b>"] + [f"• {e(b['name'])} — {_p1(b['pct'])}" for b in worst]
    od = (dg or {}).get("order") or {}
    T = od.get("total")
    if T and T.get("z"):
        lines += ["", f"🛒 <b>Zakaz: {_n(int(T['pos']))} poz · {_n(round(T['z']))} dona</b>"
                  + (f" · ≈ {_usd(T.get('usd') or 0)}" if T.get("usd") else ""),
                  " · ".join(f"{e(w['short'])} {_n(round(w['z']))}" for w in od["whs"] if w["z"] > 0)]
        if T.get("zm"):
            lines.append(f"   shundan faqat matritsa uchun: {_n(round(T['zm']))} dona"
                         + (f" · ≈ {_usd(T.get('usdm') or 0)}" if T.get("usdm") else ""))
    elif T is not None:
        lines += ["", "🛒 Zakaz kerak emas — omborlardagi qoldiq yetarli."]
    return "\n".join(lines)


async def digest(pool: asyncpg.Pool) -> dict | None:
    try:
        from api_matrix import bot_digest
        async with pool.acquire() as con:
            return await bot_digest(con)
    except Exception:  # noqa: BLE001
        log.exception("Matritsa holatini hisoblab bo'lmadi")
        return None


async def _run_auto(pool: asyncpg.Pool) -> None:
    try:
        s = await sync(pool, "auto")
        await _notify_admins(digest_text(s, await digest(pool)), result_kb(s))
        if s.get("branches", 0) > s.get("branches_ok", 0):     # bog'lanmagan filiallar → «🆕» so'rovlari
            from bot_stock import prompt_new
            await prompt_new(pool, only_new=True)
    except MatrixError as e:
        await _notify_admins(f"❌ <b>Matritsani o'qib bo'lmadi</b> (06:00)\n{html.escape(str(e), quote=False)}\n"
                             "Kechagi matritsa saqlanib qoldi. /matritsa — qayta urinish.")


async def daily_loop(pool: asyncpg.Pool) -> None:
    """Har kuni MATRIX_SYNC_HOUR:00 da (Toshkent). Server qayta ishga tushganda bugungi o'qish
    o'tkazib yuborilgan bo'lsa — 1 daqiqadan keyin bir marta o'qiydi."""
    if not config.MATRIX_SHEET_ID or not config.GOOGLE_SA_JSON:
        log.info("Matritsa: MATRIX_SHEET_ID / GOOGLE_SA_JSON yo'q — avtomatik o'qish o'chiq")
        return
    H = config.MATRIX_SYNC_HOUR
    try:
        now = datetime.now(TZ)
        today_run = now.replace(hour=H, minute=0, second=0, microsecond=0)
        if now > today_run:
            done = await pool.fetchval("SELECT count(*) FROM matrix_syncs WHERE started_at >= $1",
                                       today_run.astimezone(timezone.utc))
            if not done:
                await asyncio.sleep(60)
                await _run_auto(pool)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.exception("Matritsa: ishga tushishdagi o'qish xatosi")
    while True:
        now = datetime.now(TZ)
        nxt = now.replace(hour=H, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            await _run_auto(pool)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Matritsa: kunlik o'qish xatosi")
