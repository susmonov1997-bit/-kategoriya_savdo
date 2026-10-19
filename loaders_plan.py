"""Yillik savdo rejasi ("Reja 2026.xlsx").

Format (har bir varaq):
    (sarlavha ustida)        2026                      ← yil (sarlavha qatori ustidagi istalgan katakda)
    Гурух | Категория | январь | февраль | … | декабрь | Жами
    КБТ   | Телевизорлар | 1 200 000 000 | …
    МБТ   | Блендерлар   | …

Varaqlar:
  * nomida "маржа" / "marja" / "gross" / "валов" bo'lsa (yoki sarlavha ustida shu so'z yozilsa) — GROSS MARJA rejasi;
  * qolgani — SAVDO SUMMASI rejasi. Bitta varaqli fayl — faqat savdo rejasi.
Qiymatlar so'mda. "Жами" ustuni faqat tekshiruv uchun (oylar yig'indisi bilan solishtiriladi).

Qoidalar:
  * Fayldagi yil va ko'rsatkich (savdo / gross) bo'yicha reja TO'LIQ almashtiriladi; faylda yo'q ko'rsatkich
    (masalan, faqat savdo varag'i yuklansa — gross rejasi) o'zgarmaydi.
  * Kategoriya bazadagi kategoriya bilan name_key orqali bog'lanadi; spravochnikda hali yo'q kategoriya ham saqlanadi.
"""
from __future__ import annotations

import re

import asyncio
import html
import io
import json
from dataclasses import dataclass, field

import asyncpg
import pandas as pd

from loaders_common import EXCEL_ENGINE, LoaderError, clean_header, clean_text, name_key, to_number

GROUP_COLS = {"гурух", "группа", "guruh", "гуруҳ"}
CAT_COLS = {"категория", "kategoriya", "категориялар"}
TOTAL_COLS = {"жами", "итого", "jami", "всего"}
_MONTH_PREFIX = [("янв", "yan"), ("фев", "fev"), ("мар", "mar"), ("апр", "apr"), ("май", "may"),
                 ("июн", "iyun"), ("июл", "iyul"), ("авг", "avg"), ("сен", "sen"), ("окт", "okt"),
                 ("ноя", "noy"), ("дек", "dek")]
_GROSS_WORDS = ("марж", "marj", "gross", "валов", "гросс")
_SALES_WORDS = ("savdo", "продаж", "савдо", "сотув", "товарооборот")
_SKIP_CATS = {"жами", "итого", "jami", "всего", "total"}


def month_of(h: str) -> int | None:
    t = (h or "").strip().lower()
    if not t:
        return None
    for i, pre in enumerate(_MONTH_PREFIX, 1):
        if any(t.startswith(p) for p in pre):
            return i
    return None


def _is_header(cells: list[str]) -> bool:
    low = [c.lower() for c in cells]
    return any(c in CAT_COLS for c in low) and len({m for m in map(month_of, cells) if m}) >= 10


def is_plan_file(headers: set[str]) -> bool:
    return _is_header(list(headers))


@dataclass
class Sheet:
    name: str
    metric: str                       # amount | gross
    year: int
    rows: dict[str, dict]             # name_key -> {category, grp, months{m: value}}
    warnings: list[str] = field(default_factory=list)


def _parse_sheet(name: str, raw: pd.DataFrame, scan_rows: int = 20) -> Sheet | None:
    hidx = None
    for i in range(min(scan_rows, len(raw))):
        if _is_header([clean_header(c) for c in raw.iloc[i].tolist()]):
            hidx = i
            break
    if hidx is None:
        return None
    headers = [clean_header(c) for c in raw.iloc[hidx].tolist()]
    low = [h.lower() for h in headers]
    ci = next(i for i, h in enumerate(low) if h in CAT_COLS)
    gi = next((i for i, h in enumerate(low) if h in GROUP_COLS), None)
    ti = next((i for i, h in enumerate(low) if h in TOTAL_COLS), None)
    mcols = {}
    for i, h in enumerate(headers):
        m = month_of(h)
        if m and m not in mcols and i != ci:
            mcols[m] = i

    # yil va ko'rsatkich — sarlavha ustidagi kataklar va varaq nomi
    above = [clean_text(v) or "" for r in range(hidx) for v in raw.iloc[r].tolist()]
    year = None
    for v in above + [name]:
        n = to_number(v)
        if isinstance(n, int) and 2000 <= n <= 2100:
            year = n
            break
        m_ = re.search(r"(?<!\d)(20\d\d)(?!\d)", str(v))      # «2026-yil», «Reja_2026», «2026 йил»
        if m_:
            year = int(m_.group(1))
            break
    # ko'rsatkich: avval varaq nomi; nomda yo'q bo'lsa — sarlavha ustidagi matn, lekin «savdo» so'zi ham bo'lsa — savdo
    nm, text = name.lower(), " ".join(above).lower()
    if any(w in nm for w in _GROSS_WORDS):
        metric = "gross"
    elif any(w in nm for w in _SALES_WORDS):
        metric = "amount"
    elif any(w in text for w in _GROSS_WORDS) and not any(w in text for w in _SALES_WORDS):
        metric = "gross"
    else:
        metric = "amount"
    if year is None:
        raise LoaderError(f"«{name}» varag'ida yil topilmadi — sarlavha qatori ustiga yilni yozing (masalan, 2026)")

    sh = Sheet(name=name, metric=metric, year=year, rows={})
    last_grp = None
    for r in range(hidx + 1, len(raw)):
        vals = raw.iloc[r].tolist()
        cat = clean_text(vals[ci]) if ci < len(vals) else None
        grp = clean_text(vals[gi]) if gi is not None and gi < len(vals) else None
        if grp:
            last_grp = grp.upper()
        if not cat or cat.lower() in _SKIP_CATS or cat.lower().startswith(("жами", "итого")):
            continue
        months = {}
        for m, i in mcols.items():
            v = to_number(vals[i]) if i < len(vals) else None
            if v is not None:
                months[m] = float(v)
        if not months:
            continue
        k = name_key(cat)
        if k in sh.rows:
            sh.warnings.append(f"{cat}: takrorlangan qator ({r + 1}-qator) — oxirgisi olindi")
        if ti is not None and ti < len(vals):
            tot = to_number(vals[ti])
            if tot is not None and abs(float(tot) - sum(months.values())) > max(1.0, abs(float(tot)) * 0.001):
                sh.warnings.append(f"{cat}: «Жами» {float(tot):,.0f} ≠ oylar yig'indisi {sum(months.values()):,.0f}"
                                   .replace(",", " "))
        sh.rows[k] = {"category": cat, "grp": grp.upper() if grp else last_grp, "months": months}
    return sh


def parse_plan(data: bytes) -> list[Sheet]:
    try:
        book = pd.read_excel(io.BytesIO(data), header=None, dtype=object, sheet_name=None, engine=EXCEL_ENGINE)
    except Exception as e:  # noqa: BLE001
        raise LoaderError(f"Excel faylni o'qib bo'lmadi: {e}") from e
    sheets = [s for s in (_parse_sheet(n, df) for n, df in book.items()) if s and s.rows]
    if not sheets:
        raise LoaderError("Reja jadvali topilmadi: sarlavhada «Категория» va oylar (январь … декабрь) bo'lishi kerak")
    seen = set()
    for s in sheets:
        if (s.year, s.metric) in seen:
            raise LoaderError(f"{s.year} yil uchun {'gross marja' if s.metric == 'gross' else 'savdo'} rejasi "
                              "ikki varaqda bor — bittasini qoldiring (gross varag'i nomida «Маржа» bo'lsin)")
        seen.add((s.year, s.metric))
    return sheets


@dataclass
class PlanLoadReport:
    upload_id: int
    years: list[int]
    sheets: list[dict] = field(default_factory=list)    # {name, metric, year, cats, total}
    groups: dict = field(default_factory=dict)          # "2026|КБТ" -> {amount, gross}
    not_in_db: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


async def load_plan(pool: asyncpg.Pool, data: bytes, file_name: str | None = None,
                    tg_user_id: int | None = None) -> PlanLoadReport:
    sheets = await asyncio.to_thread(parse_plan, data)
    col = {"amount": "plan_amount", "gross": "plan_gross"}
    async with pool.acquire() as con:
        upload_id = await con.fetchval(
            "INSERT INTO uploads(kind, file_name, tg_user_id) VALUES ('plan',$1,$2) RETURNING id", file_name, tg_user_id)
        try:
            async with con.transaction():
                for s in sheets:
                    c = col[s.metric]
                    await con.execute(f"UPDATE sales_plan SET {c} = NULL WHERE year = $1", s.year)
                    recs = [(s.year, m, k, v["category"], v["grp"], val, upload_id)
                            for k, v in s.rows.items() for m, val in v["months"].items()]
                    await con.executemany(
                        f"""INSERT INTO sales_plan(year, month, name_key, category, grp, {c}, upload_id)
                            VALUES ($1,$2,$3,$4,$5,$6,$7)
                            ON CONFLICT (year, month, name_key) DO UPDATE
                            SET {c} = EXCLUDED.{c}, category = EXCLUDED.category,
                                grp = COALESCE(EXCLUDED.grp, sales_plan.grp), upload_id = EXCLUDED.upload_id""", recs)
                await con.execute("DELETE FROM sales_plan WHERE plan_amount IS NULL AND plan_gross IS NULL")
                db_cats = {name_key(r["name"]): r for r in await con.fetch("SELECT name, grp FROM categories")}
                db_keys = set(db_cats)
            rep = PlanLoadReport(upload_id=upload_id, years=sorted({s.year for s in sheets}))
            nid = set()
            for s in sheets:
                tot = sum(sum(v["months"].values()) for v in s.rows.values())
                rep.sheets.append({"name": s.name, "metric": s.metric, "year": s.year, "cats": len(s.rows), "total": tot,
                                   "months": len({m for v in s.rows.values() for m in v["months"]})})
                for v in s.rows.values():
                    g = rep.groups.setdefault(f"{s.year}|{v['grp'] or '—'}", {"amount": 0.0, "gross": 0.0, "cats": 0})
                    g[s.metric] += sum(v["months"].values())
                    if s.metric == "amount":
                        g["cats"] += 1
                nid |= {v["category"] for k, v in s.rows.items() if k not in db_keys}
                if s.metric == "amount":
                    for k, v in s.rows.items():
                        dg = db_cats[k]["grp"] if k in db_cats else None
                        if dg and v["grp"] and dg.upper() != v["grp"]:
                            rep.warnings.append(f"{v['category']}: rejada {v['grp']}, mas'ullar ro'yxatida {dg} — "
                                                "reja bo'limida rejadagi guruh olinadi")
                rep.warnings += [f"«{s.name}»: {w}" for w in s.warnings]
            rep.not_in_db = sorted(nid)
            await con.execute(
                "UPDATE uploads SET status='done', finished_at=now(), rows_total=$2, rows_loaded=$2, report=$3 WHERE id=$1",
                upload_id, sum(len(s.rows) for s in sheets), json.dumps(rep.__dict__, ensure_ascii=False, default=str))
        except Exception as e:
            try:
                await con.execute("UPDATE uploads SET status='failed', finished_at=now(), report=$2 WHERE id=$1",
                                  upload_id, json.dumps({"error": str(e)}, ensure_ascii=False))
            except Exception:  # noqa: BLE001
                pass
            raise

    return rep


def format_report(r: PlanLoadReport, max_items: int = 15) -> str:
    esc = lambda s: html.escape(str(s), quote=False)  # noqa: E731
    mln = lambda v: f"{v / 1e6:,.1f}".replace(",", " ") + " mln"  # noqa: E731
    out = [f"✅ <b>Savdo rejasi yuklandi</b> (#{r.upload_id})",
           f"Yil: <b>{', '.join(map(str, r.years))}</b>", ""]
    for s in r.sheets:
        out.append(f"• «{esc(s['name'])}» → {'gross marja' if s['metric'] == 'gross' else 'savdo'} rejasi: "
                   f"{s['cats']} kategoriya · {s['months']} oy · jami <b>{mln(s['total'])}</b>")
    out += ["", "<b>Guruhlar bo'yicha (yillik):</b>"]
    for k, g in sorted(r.groups.items()):
        y, grp = k.split("|")
        gp = f" · gross {mln(g['gross'])} ({g['gross'] / g['amount'] * 100:.1f}%)" if g["gross"] and g["amount"] else ""
        out.append(f"  • {esc(grp)} {y}: savdo {mln(g['amount'])}{gp}")
    if not any(s["metric"] == "gross" for s in r.sheets):
        out.append("\nℹ️ Gross marja rejasi yo'q — uni alohida varaqda bering (varaq nomi «Маржа»), format xuddi shu.")
    if r.not_in_db:
        items = "\n".join(f"  • {esc(x)}" for x in r.not_in_db[:max_items])
        more = f"\n  … yana {len(r.not_in_db) - max_items} ta" if len(r.not_in_db) > max_items else ""
        out += [f"\nℹ️ Tovar spravochnigida hali yo'q kategoriyalar: {len(r.not_in_db)} ta "
                "(reja saqlandi, fakt spravochnik yuklangach chiqadi):", items + more]
    if r.warnings:
        items = "\n".join(f"  • {esc(x)}" for x in r.warnings[:max_items])
        out += ["\n⚠️ <b>Ogohlantirishlar:</b>", items]
    return "\n".join(out)
