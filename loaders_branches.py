"""Filial spravochnigi loaderi (Filial spravochnigi.xlsx).

Ustunlar: Территория (hudud), Филиал, Кластер — majburiy; Омбор — ixtiyoriy (filial qaysi hududiy ombordan oladi).
Qoidalar:
  * Kalit — filial nomi (name_key: apostrof, bo'shliq, registr birxillashtiriladi).
  * Filial boshqa hudud/klasterga o'tgan bo'lsa — yangilanadi va hisobotda ko'rsatiladi.
  * Faylda yo'q, bazada bor filial o'chirilmaydi (savdo tarixi uchun).
  * «Омбор» qiymati (Кукон, Гузор …) omborga bog'lanadi; bazada yo'q ombor bo'lsa — yangi ombor yaratiladi
    («<qiymat> омбори»). Ustun yoki katak bo'sh bo'lsa, filialning oldingi ombori saqlanadi.
"""
from __future__ import annotations

import asyncio
import html
import json
from dataclasses import dataclass, field

import asyncpg

from loaders_common import LoaderError, clean_text, name_key, read_sheet_with_header

COL_REGION = "Территория"
COL_BRANCH = "Филиал"
COL_CLUSTER = "Кластер"
COL_WH = "Омбор"
REQUIRED = [COL_REGION, COL_BRANCH, COL_CLUSTER]


def warehouse_name(v: str) -> str:
    """«Гузор» → «Гузор омбори»; «Гузор омбори» o'zgarmaydi."""
    return v if "омбор" in v.lower() or "ombor" in v.lower() else f"{v} омбори"


async def ensure_warehouse(con: asyncpg.Connection, value: str, cache: dict[str, int], new: list[str]) -> int:
    """Ombor nomi/taxallusi bo'yicha id; yo'q bo'lsa yaratadi (va taxalluslarini yozadi)."""
    k = name_key(value)
    if k in cache:
        return cache[k]
    wid = await con.fetchval("SELECT warehouse_id FROM warehouse_aliases WHERE alias_key = ANY($1::text[])",
                             [k, name_key(warehouse_name(value))])
    if wid is None:
        nm = warehouse_name(value)
        wid = await con.fetchval("SELECT id FROM warehouses WHERE name = $1", nm)
        if wid is None:
            wid = await con.fetchval("INSERT INTO warehouses(name) VALUES ($1) RETURNING id", nm)
            new.append(nm)
        await con.executemany("INSERT INTO warehouse_aliases(alias_key, warehouse_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
                              [(k, wid), (name_key(nm), wid)])
    cache[k] = wid
    return wid


def is_branches_file(headers: set[str]) -> bool:
    return set(REQUIRED) <= headers


@dataclass
class BranchesLoadReport:
    upload_id: int
    rows_total: int
    loaded: int
    inserted: int
    moved: list[str] = field(default_factory=list)      # hudud/klaster o'zgargan
    missing_in_file: int = 0
    new_regions: list[str] = field(default_factory=list)
    new_clusters: list[str] = field(default_factory=list)
    new_warehouses: list[str] = field(default_factory=list)
    has_wh: bool = False
    by_wh: dict[str, int] = field(default_factory=dict)
    duplicates: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    by_region: dict[str, int] = field(default_factory=dict)
    by_cluster: dict[str, int] = field(default_factory=dict)


def parse_branches(data: bytes) -> tuple[dict[str, dict], int, list[str], list[str], bool]:
    df, _ = read_sheet_with_header(data, must_have=REQUIRED)
    has_wh = COL_WH in df.columns
    items: dict[str, dict] = {}
    errors, dups = [], []
    for rec in df.to_dict("records"):
        r = rec["_excel_row"]
        reg, br, cl = (clean_text(rec.get(c)) for c in REQUIRED)
        empty = [c for c, v in zip(REQUIRED, (reg, br, cl)) if not v]
        if empty:
            errors.append(f"{r}-qator: bo'sh — {', '.join(empty)}")
            continue
        key = name_key(br)
        if key in items:
            dups.append(f"{br}: {items[key]['row']} va {r}-qatorlar → {r} olindi")
        items[key] = {"name": br, "region": reg, "cluster": cl, "row": r,
                      "wh": clean_text(rec.get(COL_WH)) if has_wh else None}
    return items, len(df), errors, dups, has_wh


async def load_branches(
    pool: asyncpg.Pool, data: bytes, file_name: str | None = None, tg_user_id: int | None = None
) -> BranchesLoadReport:
    async with pool.acquire() as con:
        upload_id = await con.fetchval(
            "INSERT INTO uploads(kind, file_name, tg_user_id) VALUES ('branches',$1,$2) RETURNING id",
            file_name, tg_user_id,
        )
        try:
            items, total, errors, dups, has_wh = await asyncio.to_thread(parse_branches, data)
            if not items:
                raise LoaderError("Faylda birorta ham to'g'ri filial qatori yo'q")
            rep = BranchesLoadReport(upload_id=upload_id, rows_total=total, loaded=len(items), inserted=0,
                                     errors=errors, duplicates=dups, has_wh=has_wh)
            async with con.transaction():
                regions = {r["name"]: r["id"] for r in await con.fetch("SELECT id, name FROM regions")}
                clusters = {r["name"]: r["id"] for r in await con.fetch("SELECT id, name FROM clusters")}
                for it in items.values():
                    if it["region"] not in regions:
                        regions[it["region"]] = await con.fetchval(
                            "INSERT INTO regions(name) VALUES ($1) RETURNING id", it["region"])
                        rep.new_regions.append(it["region"])
                    if it["cluster"] not in clusters:
                        clusters[it["cluster"]] = await con.fetchval(
                            "INSERT INTO clusters(name) VALUES ($1) RETURNING id", it["cluster"])
                        rep.new_clusters.append(it["cluster"])
                wcache: dict[str, int] = {}
                for it in items.values():
                    it["wh_id"] = await ensure_warehouse(con, it["wh"], wcache, rep.new_warehouses) if it["wh"] else None
                wnames = {r["id"]: r["name"] for r in await con.fetch("SELECT id, name FROM warehouses")}

                old = {
                    r["name_key"]: r for r in await con.fetch(
                        """SELECT b.name_key, rg.name region, cl.name cluster, b.warehouse_id
                           FROM branches b JOIN regions rg ON rg.id=b.region_id JOIN clusters cl ON cl.id=b.cluster_id""")
                }
                for key, it in items.items():
                    o = old.get(key)
                    if o is None:
                        rep.inserted += 1
                    elif (o["region"], o["cluster"]) != (it["region"], it["cluster"]):
                        rep.moved.append(f"{it['name']}: {o['region']}/{o['cluster']} → {it['region']}/{it['cluster']}")
                    if o is not None and it["wh_id"] and o["warehouse_id"] and o["warehouse_id"] != it["wh_id"]:
                        rep.moved.append(f"{it['name']}: {wnames[o['warehouse_id']]} → {wnames[it['wh_id']]}")
                    rep.by_region[it["region"]] = rep.by_region.get(it["region"], 0) + 1
                    rep.by_cluster[it["cluster"]] = rep.by_cluster.get(it["cluster"], 0) + 1
                    if it["wh_id"]:
                        w = wnames[it["wh_id"]]
                        rep.by_wh[w] = rep.by_wh.get(w, 0) + 1

                await con.executemany(
                    """INSERT INTO branches(name, name_key, region_id, cluster_id, warehouse_id, upload_id)
                       VALUES ($1,$2,$3,$4,$5,$6)
                       ON CONFLICT (name_key) DO UPDATE SET
                         name=EXCLUDED.name, region_id=EXCLUDED.region_id, cluster_id=EXCLUDED.cluster_id,
                         warehouse_id=COALESCE(EXCLUDED.warehouse_id, branches.warehouse_id),
                         upload_id=EXCLUDED.upload_id, updated_at=now()
                       WHERE (branches.name, branches.region_id, branches.cluster_id, branches.warehouse_id)
                             IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.region_id, EXCLUDED.cluster_id,
                                               COALESCE(EXCLUDED.warehouse_id, branches.warehouse_id))""",
                    [(it["name"], k, regions[it["region"]], clusters[it["cluster"]], it["wh_id"], upload_id)
                     for k, it in items.items()],
                )
                rep.missing_in_file = len(set(old) - set(items))
        except Exception as e:
            await con.execute(
                "UPDATE uploads SET status='failed', finished_at=now(), report=$2 WHERE id=$1",
                upload_id, json.dumps({"error": str(e)}, ensure_ascii=False))
            raise
        await con.execute(
            "UPDATE uploads SET status='done', finished_at=now(), rows_total=$2, rows_loaded=$3, report=$4 WHERE id=$1",
            upload_id, rep.rows_total, rep.loaded, json.dumps(rep.__dict__, ensure_ascii=False, default=str))
        return rep


def format_report(r: BranchesLoadReport, max_items: int = 15) -> str:
    def lst(items: list[str]) -> str:
        head = "\n".join(f"  • {html.escape(x, quote=False)}" for x in items[:max_items])
        return head + (f"\n  … yana {len(items) - max_items} ta" if len(items) > max_items else "")

    out = [
        f"✅ <b>Filial spravochnigi yuklandi</b> (#{r.upload_id})",
        f"Qatorlar: {r.rows_total} → filiallar: <b>{r.loaded}</b> (yangi: {r.inserted})",
        f"Hududlar: {len(r.by_region)} · Klasterlar: {len(r.by_cluster)}",
        "",
        "<b>Klasterlar:</b>",
        *[f"  • {html.escape(c)}: {n}" for c, n in sorted(r.by_cluster.items())],
    ]
    if r.by_wh:
        out += ["", "<b>Omborlar:</b>", *[f"  • {html.escape(w)}: {n}" for w, n in sorted(r.by_wh.items())]]
        nowh = r.loaded - sum(r.by_wh.values())
        if nowh:
            out.append(f"  • ombori ko'rsatilmagan: {nowh}")
    elif not r.has_wh:
        out.append("\nℹ️ Faylda «Омбор» ustuni yo'q — filiallarning ombori o'zgarmadi")
    if r.moved:
        out += [f"\n🔀 <b>Hudud/klasteri/ombori o'zgargan: {len(r.moved)}</b>", lst(r.moved)]
    if r.missing_in_file:
        out.append(f"\nℹ️ Bazada bor, faylda yo'q filial: {r.missing_in_file} (o'chirilmadi)")
    if r.new_regions:
        out += ["\n🆕 <b>Yangi hududlar:</b>", lst(r.new_regions)]
    if r.new_clusters:
        out += ["\n🆕 <b>Yangi klasterlar:</b>", lst(r.new_clusters)]
    if r.new_warehouses:
        out += ["\n🆕 <b>Yangi omborlar:</b>", lst(r.new_warehouses)]
    if r.duplicates:
        out += [f"\n⚠️ <b>Takrorlangan filial: {len(r.duplicates)}</b>", lst(r.duplicates)]
    if r.errors:
        out += [f"\n❌ <b>Yuklanmagan qatorlar: {len(r.errors)}</b>", lst(r.errors)]
    return "\n".join(out)
