"""sales_fact — tayyor hisob-kitob jadvali (008_fact.sql). Qayta qurish va sanalar bo'yicha yangilash."""
from __future__ import annotations

import logging
import time

import asyncpg

log = logging.getLogger(__name__)

FACT_SELECT = """
SELECT e.sale_date, e.branch_id, b.region_id, b.cluster_id, e.product_id, e.is_bonus,
       e.qty::float8, e.amount::float8, e.gross_margin::float8, e.supplier_income::float8, e.margin::float8
FROM sales_enriched e JOIN branches b ON b.id = e.branch_id"""


async def refresh_dates(con: asyncpg.Connection, dates: list) -> None:
    """Savdo yuklanganda: shu sanalar qayta yoziladi (chaqiruvchining tranzaksiyasida)."""
    if not dates:
        return
    await con.execute("DELETE FROM sales_fact WHERE sale_date = ANY($1::date[])", list(dates))
    await con.execute(f"INSERT INTO sales_fact {FACT_SELECT} WHERE e.sale_date = ANY($1::date[])", list(dates))


async def rebuild(con: asyncpg.Connection) -> None:
    t0 = time.time()
    v0 = await con.fetchval("SELECT version FROM fact_meta WHERE id = 1")
    async with con.transaction():
        await con.execute("TRUNCATE sales_fact")
        await con.execute(f"INSERT INTO sales_fact {FACT_SELECT}")
        # qayta qurish paytida spravochnik/kurs o'zgargan bo'lsa (versiya oshgan) — dirty saqlanadi, keyingisi yana quradi
        await con.execute("UPDATE fact_meta SET dirty = (version <> $1), rebuilt_at = now() WHERE id = 1", v0)
    await con.execute("ANALYZE sales_fact")
    log.info("sales_fact qayta qurildi: %.1f s", time.time() - t0)


async def ensure_fact(pool) -> int:
    """dirty bo'lsa — qayta quradi (bir vaqtda faqat bitta jarayon). Joriy ma'lumot versiyasini qaytaradi."""
    async with pool.acquire() as con:
        r = await con.fetchrow("SELECT version, dirty FROM fact_meta WHERE id = 1")
        if not r["dirty"]:
            return r["version"]
        await con.execute("SELECT pg_advisory_lock(727002)")
        try:
            if await con.fetchval("SELECT dirty FROM fact_meta WHERE id = 1"):
                await rebuild(con)
            return await con.fetchval("SELECT version FROM fact_meta WHERE id = 1")
        finally:
            await con.execute("SELECT pg_advisory_unlock(727002)")
