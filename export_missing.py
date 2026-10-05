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


def has_missing(report: dict | None) -> bool:
    return bool(report and (report.get("unknown_skus") or report.get("unknown_branches")))


async def report_of(con, upload_id: int) -> dict | None:
    raw = await con.fetchval("SELECT report FROM uploads WHERE id = $1 AND kind = 'sales'", upload_id)
    if raw is None:
        return None
    return json.loads(raw) if isinstance(raw, str) else raw


def file_name(report: dict) -> str:
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
    wi.append(["Savdo fayli", ", ".join(filter(None, [d[0] if d else None, d[-1] if len(d) > 1 else None]))])
    wi.append(["Spravochnikda yo'q SKU", len(skus), "summa", round(sum(s.get("amount") or 0 for s in skus))])
    wi.append(["Spravochnikda yo'q filial", len(brs), "summa", round(sum(b.get("amount") or 0 for b in brs))])
    wi.append([])
    wi.append(["Qanday ishlatiladi:"])
    wi.append(["1. «Tovarlar» varag'ida faqat SARIQ kataklarni to'ldiring (Статус va o'z kategoriyasining xususiyatlari); KULRANG — boshqa kategoriyaniki, bo'sh qoladi."])
    wi.append(["2. Faylni botga yuboring — tovar spravochnigi sifatida yuklanadi (faqat shu SKU'lar qo'shiladi)."])
    wi.append(["3. «Filiallar» varag'ini filial spravochnigingizga ko'chiring (Территория, Кластер) va yuklang."])
    wi.append(["4. Keyin shu savdo faylini qayta yuklang — sanalar almashtiriladi, yo'qolgan savdo qo'shiladi."])
    wi.append([])
    wi.append(["Tur", "Товар Ид / Филиал", "Nomi", "Категория", "Qator", "Dona", "Summa"])
    for c in wi[wi.max_row]:
        c.font, c.fill = BOLD, HEAD_FILL
    for s in sorted(skus, key=lambda x: -(x.get("amount") or 0)):
        wi.append(["SKU", s.get("product_id"), s.get("name"), s.get("category"), s.get("rows"),
                   s.get("qty"), round(s.get("amount") or 0)])
    for b in sorted(brs, key=lambda x: -(x.get("amount") or 0)):
        wi.append(["Filial", b.get("name"), None, None, b.get("rows"), None, round(b.get("amount") or 0)])
    for r in wi.iter_rows(min_row=1, max_row=1):
        r[0].font = BOLD
    for row in wi.iter_rows(min_row=12):
        if row[6].value is not None:
            row[6].number_format = "#,##0"
    wi.column_dimensions["A"].width = 26
    for col in "BCDEFG":
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
                                caption=missing_caption(report))
    finally:
        await bot.session.close()


def missing_caption(report: dict) -> str:
    n_s, n_b = len(report.get("unknown_skus") or []), len(report.get("unknown_branches") or [])
    parts = []
    if n_s:
        parts.append(f"{n_s} ta SKU")
    if n_b:
        parts.append(f"{n_b} ta filial")
    return (f"📎 Spravochnikda yo'q: {', '.join(parts)}.\n"
            "«Tovarlar» varag'ini to'ldirib botga yuboring, so'ng savdo faylini qayta yuklang.")
