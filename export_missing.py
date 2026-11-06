"""Savdo yuklanganda spravochnikda topilmagan SKU va filiallarni Excel shablonga chiqarish.

Fayl tuzilishi:
  1-varaq «Tovarlar»   — tovar spravochnigi formatida: Группа, Категория, Товар Ид, Товар номи, Бренд, Статус
                         + shu kategoriyalarning xususiyat ustunlari (bo'sh, sariq). To'ldirib, aynan shu faylni
                         botga yuborsa bo'ladi — tovar spravochnigi sifatida yuklanadi.
  2-varaq «Filiallar»  — (bo'lsa) Территория, Филиал, Кластер — filial spravochnigiga qo'shish uchun.
  3-varaq «Ma'lumot»   — har bir SKU/filial bo'yicha qator soni, dona, summa va qisqa yo'riqnoma.
"""
from __future__ import annotations

import io
import json

import asyncpg
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from loaders_common import name_key

HEAD_FILL = PatternFill("solid", fgColor="DDE7F5")
TODO_FILL = PatternFill("solid", fgColor="FFF4CC")
NA_FILL = PatternFill("solid", fgColor="E7E7E7")       # boshqa kategoriyaning ustuni — to'ldirilmaydi
NA_FONT = Font(color="9A9A9A")
BOLD = Font(bold=True)


def _d(v):
    from datetime import date as _date
    return _date.fromisoformat(v) if isinstance(v, str) else v


def has_missing(report: dict | None) -> bool:
    return bool(report and (report.get("unknown_skus") or report.get("unknown_branches")))


async def report_of(con, upload_id: int) -> dict | None:
    raw = await con.fetchval("SELECT report FROM uploads WHERE id = $1 AND kind = 'sales'", upload_id)
    if raw is None:
        return None
    return json.loads(raw) if isinstance(raw, str) else raw


def file_name(report: dict) -> str:
    if report.get("cumulative"):
        return f"kutayotganlar_{report.get('asof', '')}.xlsx"
    d = report.get("dates") or []
    period = (d[0] if len(d) == 1 else f"{d[0]}_{d[-1]}") if d else "savdo"
    return f"spravochnikda_yoq_{period}.xlsx"


def _autowidth(ws, min_w: int = 8, max_w: int = 60) -> None:
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None), default=0)
        ws.column_dimensions[get_column_letter(col[0].column)].width = max(min_w, min(max_w, width + 2))


async def build_missing_xlsx(con: asyncpg.Connection, report: dict) -> bytes | None:
    skus = report.get("unknown_skus") or []
    brs = report.get("unknown_branches") or []
    if not skus and not brs:
        return None

    # kategoriya → guruh va xususiyat ustunlari (bazadagi nomlar bilan, birlik qavsda)
    cats = {name_key(r["name"]): r for r in await con.fetch("SELECT id, name, grp FROM categories")}
    owners = {r["name_key"]: r["grp"] for r in await con.fetch("SELECT name_key, grp FROM category_owner_list")}
    attr_rows = await con.fetch(
        "SELECT category_id, name, unit FROM category_attributes WHERE is_filter ORDER BY category_id, sort_order, slot")
    attrs_by_cat: dict[int, list[str]] = {}
    for a in attr_rows:
        attrs_by_cat.setdefault(a["category_id"], []).append(f"{a['name']} ({a['unit']})" if a["unit"] else a["name"])

    attr_cols: list[str] = []
    for s in skus:
        c = cats.get(name_key(s.get("category")))
        for col in attrs_by_cat.get(c["id"], []) if c else []:
            if col not in attr_cols:
                attr_cols.append(col)

    wb = Workbook()
    # --- 1. Tovarlar (spravochnik formatida) ---
    ws = wb.active
    ws.title = "Tovarlar"
    head = ["Группа", "Категория", "Товар Ид", "Товар номи", "Бренд", "Статус"] + attr_cols
    ws.append(head)
    own_cols: list[set[str]] = []                         # har qatorda o'z kategoriyasining ustunlari
    for s in sorted(skus, key=lambda x: (x.get("category") or "", -(x.get("amount") or 0))):
        k = name_key(s.get("category"))
        grp = (cats[k]["grp"] if k in cats else None) or owners.get(k)
        ws.append([grp, s.get("category"), s.get("product_id"), s.get("name"), s.get("brand"), None]
                  + [None] * len(attr_cols))
        own_cols.append(set(attrs_by_cat.get(cats[k]["id"], [])) if k in cats else set(attr_cols))
    for i, h in enumerate(head, 1):
        cell = ws.cell(row=1, column=i)
        cell.font, cell.fill = BOLD, HEAD_FILL
    # sariq — shu qatorda to'ldiriladi (Статус + o'z kategoriyasining xususiyatlari); kulrang — kerak emas
    for r, own in enumerate(own_cols, start=2):
        ws.cell(row=r, column=6).fill = TODO_FILL
        for i, col in enumerate(attr_cols, start=7):
            cell = ws.cell(row=r, column=i)
            if col in own:
                cell.fill = TODO_FILL
            else:
                cell.fill, cell.font = NA_FILL, NA_FONT
                cell.value = None
    ws.freeze_panes = "C2"
    _autowidth(ws)

    # --- 2. Filiallar ---
    if brs:
        wb2 = wb.create_sheet("Filiallar")
        wb2.append(["Территория", "Филиал", "Кластер"])
        for b in brs:
            wb2.append([None, b.get("name"), None])
        for i in range(1, 4):
            c = wb2.cell(row=1, column=i)
            c.font, c.fill = BOLD, HEAD_FILL
        for r in range(2, wb2.max_row + 1):
            wb2.cell(row=r, column=1).fill = TODO_FILL
            wb2.cell(row=r, column=3).fill = TODO_FILL
        _autowidth(wb2)

    # --- 3. Ma'lumot ---
    wi = wb.create_sheet("Ma'lumot")
    d = report.get("dates") or []
    cum = bool(report.get("cumulative"))
    if cum:
        wi.append(["Barcha kutayotganlar", f"{report.get('asof', '')} holati (hamma yuklangan fayllardan)"])
    else:
        wi.append(["Qoldiq fayli" if report.get("stock") else "Savdo fayli",
                   ", ".join(filter(None, [d[0] if d else None, d[-1] if len(d) > 1 else None]))])
    wi.append(["Spravochnikda yo'q SKU", len(skus), "summa", round(sum(s.get("amount") or 0 for s in skus))])
    wi.append(["Spravochnikda yo'q filial", len(brs), "summa", round(sum(b.get("amount") or 0 for b in brs))])
    wi.append([])
    wi.append(["Qanday ishlatiladi:"])
    wi.append(["1. «Tovarlar» varag'ida faqat SARIQ kataklarni to'ldiring (Статус va o'z kategoriyasining xususiyatlari); KULRANG — boshqa kategoriyaniki, bo'sh qoladi."])
    wi.append(["2. Faylni botga yuboring — tovar spravochnigi sifatida yuklanadi (faqat shu SKU'lar qo'shiladi)."])
    wi.append(["3. «Filiallar» varag'ini filial spravochnigingizga ko'chiring (Территория, Кластер) va yuklang."])
    wi.append(["4. Savdo faylini qayta yuklash shart emas — bu SKU/filiallar savdosi saqlab qo'yilgan va spravochnik yuklanishi bilan avtomatik qo'shiladi."])
    wi.append([])
    wi.append(["Tur", "Товар Ид / Филиал", "Nomi", "Категория", "Qator", "Dona", "Summa"]
              + (["Birinchi sana", "Oxirgi sana", "Necha kun sotilgan", "Manba", "Qoldiq, dona"] if cum else []))
    for c in wi[wi.max_row]:
        c.font, c.fill = BOLD, HEAD_FILL
    for s in sorted(skus, key=lambda x: (-(x.get("amount") or 0), -(x.get("stock_qty") or 0))):
        wi.append(["SKU", s.get("product_id"), s.get("name"), s.get("category"), s.get("rows"),
                   s.get("qty"), round(s.get("amount") or 0)]
                  + ([_d(s.get("first")), _d(s.get("last")), s.get("days"), s.get("src"), s.get("stock_qty") or None]
                     if cum else []))
    for b in sorted(brs, key=lambda x: -(x.get("amount") or 0)):
        wi.append(["Filial", b.get("name"), None, None, b.get("rows"), None, round(b.get("amount") or 0)]
                  + ([_d(b.get("first")), _d(b.get("last")), b.get("days")] if cum else []))
    for r in wi.iter_rows(min_row=1, max_row=1):
        r[0].font = BOLD
    for row in wi.iter_rows(min_row=12):
        if row[6].value is not None:
            row[6].number_format = "#,##0"
        if cum:
            for c in row[7:9]:
                c.number_format = "DD.MM.YYYY"
    wi.column_dimensions["A"].width = 26
    for col in "BCDEFGHIJKL":
        wi.column_dimensions[col].width = 18
    wi.column_dimensions["C"].width = 45
    for row in wi.iter_rows(min_row=5, max_row=9):
        row[0].alignment = Alignment(wrap_text=False)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def send_missing(bot_token: str, chat_id: int, data: bytes, report: dict) -> None:
    """Excel'ni botdan foydalanuvchiga yuboradi (Mini App'dan chaqirilganda)."""
    from aiogram import Bot
    from aiogram.types import BufferedInputFile
    bot = Bot(bot_token)
    try:
        await bot.send_document(chat_id, BufferedInputFile(data, filename=file_name(report)),
                                caption=missing_caption(report), reply_markup=all_pending_kb())
    finally:
        await bot.session.close()


def missing_caption(report: dict) -> str:
    n_s, n_b = len(report.get("unknown_skus") or []), len(report.get("unknown_branches") or [])
    parts = []
    if n_s:
        parts.append(f"{n_s} ta SKU")
    if n_b:
        parts.append(f"{n_b} ta filial")
    what = "qoldig'i" if report.get("stock") else "savdosi"
    return (f"📎 Spravochnikda yo'q: {', '.join(parts)}.\n"
            f"«Tovarlar» varag'ini to'ldirib botga yuboring — ularning {what} avtomatik qo'shiladi, "
            "faylni qayta yuklash shart emas.")


# ---------------------------------------------------------------------------
# Barcha kutayotganlar (/kutayotgan): hamma yuklangan fayllardan hali spravochnikka qo'shilmagan SKU va filiallar
def all_pending_kb():
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📎 Barcha kutayotganlar",
                                                                       callback_data="pend:all")]])


def _dm(d) -> str:
    return d.strftime("%d.%m") if d else ""


async def pending_report(con: asyncpg.Connection) -> dict:
    """sales_pending dan: spravochnikda yo'q SKU'lar va filiallar (to'planib borgan), summa bo'yicha."""
    from datetime import date as _date
    skus = [{"product_id": int(r["pid"]), "name": r["name"], "category": r["cat"], "brand": r["brand"],
             "rows": int(r["n"]), "qty": float(r["qty"]), "amount": float(r["amount"]),
             "first": r["d1"].isoformat(), "last": r["d2"].isoformat(), "days": int(r["days"])}
            for r in await con.fetch(
                """SELECT sp.product_id pid, (array_agg(sp.product_name ORDER BY sp.sale_date DESC))[1] name, max(sp.category) cat, max(sp.brand) brand,
                          count(*) n, sum(sp.qty) qty, sum(sp.amount) amount, min(sp.sale_date) d1, max(sp.sale_date) d2,
                          count(DISTINCT sp.sale_date) days
                   FROM sales_pending sp
                   WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.product_id = sp.product_id)
                   GROUP BY sp.product_id ORDER BY sum(sp.amount) DESC""")]
    # qoldiqda yo'q SKU'lar — har tur (filial/ombor) bo'yicha oxirgi qoldiq sanasidan
    by_id = {x["product_id"]: x for x in skus}
    for x in skus:
        x["src"] = "savdo"
    for r in await con.fetch(
            """WITH last AS (SELECT kind, max(snap_date) d FROM stock_pending GROUP BY kind)
               SELECT sp.product_id pid, max(sp.product_name) name, max(sp.category) cat, max(sp.brand) brand,
                      sum(sp.qty) FILTER (WHERE sp.kind = 'b') qb, sum(sp.qty) FILTER (WHERE sp.kind = 'w') qw
               FROM stock_pending sp JOIN last l ON l.kind = sp.kind AND l.d = sp.snap_date
               WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.product_id = sp.product_id)
               GROUP BY sp.product_id"""):
        pid = int(r["pid"])
        src = ", ".join(t for t, q in (("filial qoldig'i", r["qb"]), ("ombor qoldig'i", r["qw"])) if q)
        stock = float((r["qb"] or 0) + (r["qw"] or 0))
        x = by_id.get(pid)
        if x is None:
            x = {"product_id": pid, "name": r["name"], "category": r["cat"], "brand": r["brand"],
                 "rows": 0, "qty": 0.0, "amount": 0.0, "first": None, "last": None, "days": 0, "src": src}
            skus.append(x)
        elif src:
            x["src"] += ", " + src
        x["stock_qty"] = stock
    brs = [{"name": r["name"], "rows": int(r["n"]), "amount": float(r["amount"]),
            "first": r["d1"].isoformat(), "last": r["d2"].isoformat(), "days": int(r["days"])}
           for r in await con.fetch(
               """SELECT max(sp.branch_name) name, count(*) n, sum(sp.amount) amount, min(sp.sale_date) d1,
                         max(sp.sale_date) d2, count(DISTINCT sp.sale_date) days
                  FROM sales_pending sp
                  WHERE NOT EXISTS (SELECT 1 FROM branches b WHERE b.name_key = sp.branch_key)
                  GROUP BY sp.branch_key ORDER BY sum(sp.amount) DESC""")]
    return {"cumulative": True, "asof": _date.today().strftime("%d.%m.%Y"),
            "unknown_skus": skus, "unknown_branches": brs}


def pending_text(rep: dict, top: int = 5) -> str:
    import html
    esc = lambda v: html.escape(str(v or ""), quote=False)  # noqa: E731
    mln = lambda v: (f"{v / 1e9:,.2f} mlrd" if abs(v) >= 1e9 else f"{v / 1e6:,.1f} mln").replace(",", " ").replace(".", ",")  # noqa: E731
    skus, brs = rep.get("unknown_skus") or [], rep.get("unknown_branches") or []
    if not skus and not brs:
        return "✅ Kutayotgan savdo va qoldiq yo'q — hamma SKU va filiallar spravochnikda bor."
    out = ["⏳ <b>Spravochnikda yo'qligi uchun kutayotgan savdo va qoldiq</b>",
           f"<i>hamma yuklangan fayllardan, {esc(rep.get('asof'))} holati</i>", ""]
    sold = [s for s in skus if s.get("rows")]
    only_stock = [s for s in skus if not s.get("rows")]
    if sold:
        d1, d2 = min(s["first"] for s in sold), max(s["last"] for s in sold)
        out.append(f"Savdoda: <b>{len(sold)} ta SKU</b> · {mln(sum(s['amount'] for s in sold))} so'm · "
                   f"{d1[8:10]}.{d1[5:7]} — {d2[8:10]}.{d2[5:7]}")
    if only_stock:
        q = sum(s.get("stock_qty") or 0 for s in only_stock)
        out.append(f"Faqat qoldiqda (sotilmagan): <b>{len(only_stock)} ta SKU</b> · {q:,.0f} dona".replace(",", " "))
    if brs:
        out.append(f"Filial: <b>{len(brs)} ta</b> · {mln(sum(b['amount'] for b in brs))} so'm")
    if sold:
        out += ["", f"Savdosi eng katta {min(top, len(sold))} ta:"]
        for i, s in enumerate(sold[:top], 1):
            out.append(f"{i}. {esc(s['name'] or s['product_id'])} · {esc(s['category'])} — {mln(s['amount'])} ({s['days']} kun)")
    if only_stock:
        big = sorted(only_stock, key=lambda x: -(x.get("stock_qty") or 0))[:top]
        out += ["", f"Qoldig'i eng ko'p {len(big)} ta:"]
        for i, s in enumerate(big, 1):
            out.append(f"{i}. {esc(s['name'] or s['product_id'])} · {esc(s['category'])} — {s.get('stock_qty') or 0:,.0f} dona".replace(",", " "))
    out += ["", "📎 Excel'ni to'ldirib botga yuboring — savdo va qoldiq avtomatik qo'shiladi."]
    return "\n".join(out)


async def stock_missing_report(con: asyncpg.Connection, upload_id: int) -> dict | None:
    """Qoldiq yuklashi bo'yicha spravochnikda yo'q SKU'lar (kunlik «spravochnikda yo'q» Excel'i uchun)."""
    rows = await con.fetch(
        """SELECT sp.product_id pid, max(sp.product_name) name, max(sp.category) cat, max(sp.brand) brand,
                  count(*) n, sum(sp.qty) qty, max(sp.snap_date) d
           FROM stock_pending sp
           WHERE sp.upload_id = $1 AND NOT EXISTS (SELECT 1 FROM products p WHERE p.product_id = sp.product_id)
           GROUP BY sp.product_id ORDER BY sum(sp.qty) DESC""", upload_id)
    if not rows:
        return None
    return {"stock": True, "dates": [rows[0]["d"].isoformat()],
            "unknown_skus": [{"product_id": int(r["pid"]), "name": r["name"], "category": r["cat"], "brand": r["brand"],
                              "rows": int(r["n"]), "qty": float(r["qty"]), "amount": 0.0} for r in rows],
            "unknown_branches": []}


async def missing_for(con: asyncpg.Connection, kind: str, upload_id: int) -> dict | None:
    """Yuklash turi bo'yicha «spravochnikda yo'q» hisoboti: savdo — upload hisobotidan, qoldiq — stock_pending dan."""
    if kind == "sales":
        rep = await report_of(con, upload_id)
        return rep if has_missing(rep) else None
    if kind in ("stock_branch", "stock_wh"):
        return await stock_missing_report(con, upload_id)
    return None
