"""Mas'ul xodimlar ro'yxati ("Masullar royxati.xlsx").

Ustunlar:
  Группа        — ixtiyoriy: КБТ / МБТ (katta harfga keltiriladi)
  Категория     — majburiy, tovar spravochnigidagi nomi bilan
  Жавобгар КМ   — majburiy, mas'ul xodim

Qoidalar:
  * Har yuklashda ro'yxat TO'LIQ almashtiriladi (fayl — amaldagi to'liq ro'yxat).
  * Kategoriya nomi bazadagi kategoriya bilan name_key orqali solishtiriladi (registr, bo'shliq, apostrof).
  * Bazada hali yo'q kategoriyalar ham saqlanadi — keyin tovar spravochnigi yuklanganda avtomatik bog'lanadi.
  * Faqat filtr uchun: kirish huquqlariga ta'sir qilmaydi.
"""
from __future__ import annotations

import asyncio
import html
import json
from dataclasses import dataclass, field

import asyncpg

from loaders_common import LoaderError, clean_text, name_key, read_sheet_with_header

COL_GROUP = "Группа"
COL_CAT = "Категория"
COL_OWNER = "Жавобгар КМ"


def is_owners_file(headers: set[str]) -> bool:
    return {COL_CAT, COL_OWNER} <= headers and "Товар Ид" not in headers and "Товар ИД" not in headers


def norm_group(v) -> str | None:
    t = clean_text(v)
    return t.upper() if t else None


@dataclass
class OwnersLoadReport:
    upload_id: int
    rows_total: int
    loaded: int
    owners: dict[str, int] = field(default_factory=dict)          # xodim → kategoriyalar soni
    matched: int = 0                                              # bazadagi kategoriyalarga bog'landi
    not_in_db: list[str] = field(default_factory=list)            # ro'yxatda bor, bazada hali yo'q
    without_owner: list[str] = field(default_factory=list)        # bazada bor, ro'yxatda yo'q
    group_changes: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _parse(data: bytes) -> tuple[dict[str, dict], int, list[str], list[str]]:
    df, _ = read_sheet_with_header(data, must_have=[COL_CAT, COL_OWNER])
    rows: dict[str, dict] = {}
    errors, dups = [], []
    for rec in df.to_dict("records"):
        r = rec["_excel_row"]
        cat, owner = clean_text(rec.get(COL_CAT)), clean_text(rec.get(COL_OWNER))
        if not cat and not owner:
            continue
        if not cat or not owner:
            errors.append(f"{r}-qator: bo'sh — {COL_CAT if not cat else COL_OWNER}")
            continue
        k = name_key(cat)
        if k in rows:
            dups.append(f"{cat}: {rows[k]['owner']} → {owner} (oxirgisi olindi)")
        rows[k] = {"category": cat, "owner": owner, "grp": norm_group(rec.get(COL_GROUP))}
    return rows, len(df), errors, dups


async def apply_owner_list(con: asyncpg.Connection) -> tuple[int, list[str], list[str]]:
    """Ro'yxatni bazadagi kategoriyalarga qo'llaydi: owner va (bo'lsa) grp.
    Qaytaradi: (bog'langanlar soni, guruhi o'zgarganlar, mas'ulsiz kategoriyalar)."""
    lst = {r["name_key"]: r for r in await con.fetch("SELECT name_key, owner, grp FROM category_owner_list")}
    matched, grp_changes, without = 0, [], []
    for c in await con.fetch("SELECT id, name, grp, owner FROM categories ORDER BY name"):
        o = lst.get(name_key(c["name"]))
        owner = o["owner"] if o else None
        grp = (o["grp"] if o and o["grp"] else c["grp"])
        if o:
            matched += 1
        else:
            without.append(c["name"])
        if grp != c["grp"]:
            grp_changes.append(f"{c['name']}: {c['grp'] or '—'} → {grp}")
        if owner != c["owner"] or grp != c["grp"]:
            await con.execute("UPDATE categories SET owner = $2, grp = $3 WHERE id = $1", c["id"], owner, grp)
    return matched, grp_changes, without


async def load_owners(pool: asyncpg.Pool, data: bytes, file_name: str | None = None,
                      tg_user_id: int | None = None) -> OwnersLoadReport:
    rows, total, errors, dups = await asyncio.to_thread(_parse, data)
    if not rows:
        raise LoaderError("Faylda birorta ham to'g'ri qator yo'q (Категория va Жавобгар КМ to'ldirilishi kerak)")
    async with pool.acquire() as con:
        upload_id = await con.fetchval(
            "INSERT INTO uploads(kind, file_name, tg_user_id) VALUES ('owners',$1,$2) RETURNING id",
            file_name, tg_user_id)
        async with con.transaction():
            await con.execute("DELETE FROM category_owner_list")
            await con.executemany(
                "INSERT INTO category_owner_list(name_key, category, grp, owner, upload_id) VALUES ($1,$2,$3,$4,$5)",
                [(k, v["category"], v["grp"], v["owner"], upload_id) for k, v in rows.items()])
            matched, grp_changes, without = await apply_owner_list(con)
            db_keys = {name_key(r["name"]) for r in await con.fetch("SELECT name FROM categories")}
        owners: dict[str, int] = {}
        for v in rows.values():
            owners[v["owner"]] = owners.get(v["owner"], 0) + 1
        rep = OwnersLoadReport(
            upload_id=upload_id, rows_total=total, loaded=len(rows),
            owners=dict(sorted(owners.items(), key=lambda kv: (-kv[1], kv[0]))),
            matched=matched, not_in_db=sorted(v["category"] for k, v in rows.items() if k not in db_keys),
            without_owner=without, group_changes=grp_changes, duplicates=dups, errors=errors)
        await con.execute(
            "UPDATE uploads SET status='done', finished_at=now(), rows_total=$2, rows_loaded=$3, report=$4 WHERE id=$1",
            upload_id, total, len(rows), json.dumps(rep.__dict__, ensure_ascii=False, default=str))
    return rep


def format_report(r: OwnersLoadReport, max_items: int = 20) -> str:
    esc = lambda s: html.escape(str(s), quote=False)  # noqa: E731

    def lst(items: list[str]) -> str:
        head = "\n".join(f"  • {esc(x)}" for x in items[:max_items])
        return head + (f"\n  … yana {len(items) - max_items} ta" if len(items) > max_items else "")

    out = [
        f"✅ <b>Mas'ullar ro'yxati yuklandi</b> (#{r.upload_id})",
        f"Kategoriyalar: <b>{r.loaded}</b> · bazadagilarga bog'landi: <b>{r.matched}</b>",
        "",
        "<b>Mas'ullar:</b>",
        *[f"  • {esc(o)}: {n} ta kategoriya" for o, n in r.owners.items()],
    ]
    if r.without_owner:
        out += ["\n⚠️ <b>Bazada bor, ro'yxatda yo'q (mas'ulsiz):</b>", lst(r.without_owner)]
    if r.not_in_db:
        out += [f"\nℹ️ Ro'yxatda bor, tovar spravochnigida hali yo'q: {len(r.not_in_db)} ta "
                "(spravochnikka qo'shilganda avtomatik bog'lanadi)"]
    if r.group_changes:
        out += ["\n🔁 <b>Guruh o'zgardi:</b>", lst(r.group_changes)]
    if r.duplicates:
        out += ["\n⚠️ <b>Takrorlangan kategoriya:</b>", lst(r.duplicates)]
    if r.errors:
        out += [f"\n❌ <b>Yuklanmagan qatorlar: {len(r.errors)}</b>", lst(r.errors)]
    return "\n".join(out)
