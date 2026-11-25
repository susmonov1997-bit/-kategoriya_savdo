"""Ta'minotchi qoidalari Excel'i (Mini App → Ta'minot → Ta'minotchi → «⬇ Excel») — tuzatilib botga qayta yuklanadi.

Varaqlar (hammasi ixtiyoriy, birinchisi — «Qoidalar»):
  Qoidalar: Kategoriya ID, Kategoriya, Brend, Ta'minotchi  — bo'sh: qo'lda qoida o'chadi (avtomatikka qaytadi)
  Guruhlar: Ta'minotchi, Guruh                              — yangi nom: yangi guruh
  Tovarlar: Товар Ид, Ta'minotchi                            — bo'sh: istisno o'chadi
Avtomatik qiymatga teng bo'lgan qatorlar qo'lda qoida sifatida saqlanmaydi (kirim o'zgarsa — o'zi yangilanadi).
"""
from __future__ import annotations

import asyncio
import io
import json
from html import escape

import pandas as pd

import suppliers as S
from loaders_common import EXCEL_ENGINE, LoaderError, clean_text, name_key


def _sheets(data: bytes) -> dict[str, pd.DataFrame]:
    try:
        x = pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=object, engine=EXCEL_ENGINE)
    except Exception as e:  # noqa: BLE001
        raise LoaderError(f"Excel faylni o'qib bo'lmadi: {e}") from e
    out = {}
    for i, (name, df) in enumerate(x.items()):
        df.columns = [clean_text(c) or "" for c in df.columns]
        key = "Qoidalar" if i == 0 else (clean_text(name) or "")
        out[key] = df
    return out


def _col(df: pd.DataFrame, *names: str) -> str | None:
    norm = {name_key(c).replace("'", "") if c else "": c for c in df.columns}
    for n in names:
        k = name_key(n).replace("'", "")
        if k in norm:
            return norm[k]
    return None


async def load_rules(pool, data: bytes, file_name: str | None, tg_user_id: int | None) -> dict:
    sh = await asyncio.to_thread(_sheets, data)
    rep = {"rules_set": 0, "rules_del": 0, "groups": 0, "prods_set": 0, "prods_del": 0, "unknown": [], "errors": []}
    async with pool.acquire() as con:
        upload_id = await con.fetchval("INSERT INTO uploads(kind, file_name, tg_user_id) VALUES ('sup_rules',$1,$2) RETURNING id",
                                       file_name, tg_user_id)
        rep["upload_id"] = upload_id
        try:
            async with con.transaction():
                await S.sync(con)
                sups = {name_key(r["name"]): r["id"] for r in await con.fetch("SELECT id, name FROM suppliers")}
                sid_of = lambda v: sups.get(name_key(v)) if clean_text(v) else None  # noqa: E731

                # Guruhlar
                g = sh.get("Guruhlar")
                if g is not None and (cs := _col(g, "Ta'minotchi")) and (cg := _col(g, "Guruh")):
                    gids = {r["name"].lower(): r["id"] for r in await con.fetch("SELECT id, name FROM sup_groups")}
                    for row in g.itertuples(index=False):
                        rd = dict(zip(g.columns, row))
                        sid, gn = sid_of(rd[cs]), clean_text(rd[cg])
                        if not clean_text(rd[cs]) or not gn:
                            continue
                        if sid is None:
                            rep["unknown"].append(clean_text(rd[cs]))
                            continue
                        gid = gids.get(gn.lower())
                        if gid is None:
                            gid = await con.fetchval("INSERT INTO sup_groups(name) VALUES ($1) RETURNING id", gn)
                            gids[gn.lower()] = gid
                        r = await con.execute("UPDATE suppliers SET group_id = $2, confirmed = true WHERE id = $1 "
                                              "AND (group_id IS DISTINCT FROM $2 OR NOT confirmed)", sid, gid)
                        rep["groups"] += int(r.split()[-1])
                    await con.execute("DELETE FROM sup_groups x WHERE NOT EXISTS (SELECT 1 FROM suppliers s WHERE s.group_id = x.id)")

                # Qoidalar
                q = sh.get("Qoidalar")
                if q is not None and (cc := _col(q, "Kategoriya ID")) and (cb := _col(q, "Brend")) \
                        and (cs := _col(q, "Ta'minotchi")):
                    auto = {(r["category_id"], r["brand"]): r["sid"] for r in await con.fetch(
                        f"WITH {S._K}, {S._AUTO} SELECT category_id, brand, sid FROM auto")}
                    manual = {(r["category_id"], r["brand"]): r["supplier_id"] for r in await con.fetch(
                        "SELECT category_id, brand, supplier_id FROM sup_rules")}
                    cats = {r[0] for r in await con.fetch("SELECT id FROM categories")}
                    for i, row in enumerate(q.itertuples(index=False), start=2):
                        rd = dict(zip(q.columns, row))
                        try:
                            cid = int(float(rd[cc]))
                        except (TypeError, ValueError):
                            continue
                        brand = clean_text(rd[cb]) or "—"
                        if cid not in cats:
                            rep["errors"].append(f"{i}-qator: kategoriya ID {cid} topilmadi")
                            continue
                        key, raw = (cid, brand), clean_text(rd[cs])
                        sid = sid_of(raw)
                        if raw and sid is None:
                            rep["unknown"].append(raw)
                            continue
                        if sid is None or (sid == auto.get(key) and key not in manual):
                            if key in manual and sid is None:
                                await con.execute("DELETE FROM sup_rules WHERE category_id = $1 AND brand = $2", cid, brand)
                                rep["rules_del"] += 1
                            continue
                        if manual.get(key) != sid:
                            await con.execute(
                                """INSERT INTO sup_rules(category_id, brand, supplier_id, updated_by) VALUES ($1,$2,$3,$4)
                                   ON CONFLICT (category_id, brand) DO UPDATE SET supplier_id = EXCLUDED.supplier_id,
                                   updated_by = EXCLUDED.updated_by, updated_at = now()""", cid, brand, sid, tg_user_id)
                            rep["rules_set"] += 1

                # Tovarlar (istisnolar)
                t = sh.get("Tovarlar")
                if t is not None and (cp := _col(t, "Товар Ид", "Товар ИД", "Tovar ID")) and (cs := _col(t, "Ta'minotchi")):
                    await S.refresh(con)            # qoidalar o'zgargan bo'lsa — joriy natija bilan solishtiramiz
                    cur = {r["product_id"]: (r["supplier_id"], r["src"]) for r in await con.fetch(
                        "SELECT product_id, supplier_id, src FROM product_supplier")}
                    for i, row in enumerate(t.itertuples(index=False), start=2):
                        rd = dict(zip(t.columns, row))
                        try:
                            pid = int(float(rd[cp]))
                        except (TypeError, ValueError):
                            continue
                        raw = clean_text(rd[cs])
                        sid = sid_of(raw)
                        if raw and sid is None:
                            rep["unknown"].append(raw)
                            continue
                        c = cur.get(pid)
                        if sid is None:
                            if c and c[1] == "x":
                                await con.execute("DELETE FROM sup_product WHERE product_id = $1", pid)
                                rep["prods_del"] += 1
                            continue
                        if c and c[0] == sid:
                            continue
                        if not await con.fetchval("SELECT 1 FROM products WHERE product_id = $1", pid):
                            rep["errors"].append(f"Tovarlar, {i}-qator: Товар Ид {pid} spravochnikda yo'q")
                            continue
                        await con.execute(
                            """INSERT INTO sup_product(product_id, supplier_id, updated_by) VALUES ($1,$2,$3)
                               ON CONFLICT (product_id) DO UPDATE SET supplier_id = EXCLUDED.supplier_id,
                               updated_by = EXCLUDED.updated_by, updated_at = now()""", pid, sid, tg_user_id)
                        rep["prods_set"] += 1
                await S.refresh(con)
        except Exception as e:
            await con.execute("UPDATE uploads SET status='failed', finished_at=now(), report=$2 WHERE id=$1",
                              upload_id, json.dumps({"error": str(e)}, ensure_ascii=False))
            raise
        rep["unknown"] = sorted(set(rep["unknown"]))
        await con.execute("UPDATE uploads SET status='done', finished_at=now(), report=$2 WHERE id=$1",
                          upload_id, json.dumps(rep, ensure_ascii=False, default=str))
    return rep


def format_report(r: dict) -> str:
    lines = ["🏭 <b>Ta'minotchi qoidalari yuklandi</b>",
             f"• Qoidalar: {r['rules_set']} o'rnatildi" + (f", {r['rules_del']} o'chirildi (avtomatikka qaytdi)" if r["rules_del"] else ""),
             f"• Guruhlar: {r['groups']} ta'minotchi guruhi o'zgardi",
             f"• Tovar istisnolari: {r['prods_set']} o'rnatildi" + (f", {r['prods_del']} o'chirildi" if r["prods_del"] else "")]
    if not (r["rules_set"] or r["rules_del"] or r["groups"] or r["prods_set"] or r["prods_del"]):
        lines.append("<i>O'zgarish yo'q — fayldagi qiymatlar joriy holat bilan bir xil.</i>")
    if r["unknown"]:
        lines += ["", f"⚠️ <b>Ro'yxatda yo'q ta'minotchi nomlari ({len(r['unknown'])})</b> — o'tkazib yuborildi:"]
        lines += [f"• {escape(n)}" for n in r["unknown"][:10]]
        lines.append("<i>Nomni «Guruhlar» varag'idagidek aynan yozing.</i>")
    if r["errors"]:
        lines += ["", "⚠️ <b>Xatolar:</b>"] + [f"• {escape(e)}" for e in r["errors"][:10]]
    return "\n".join(lines)
