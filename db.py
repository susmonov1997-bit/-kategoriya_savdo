"""PostgreSQL ulanish puli va migratsiyalar."""
from __future__ import annotations

import logging
from pathlib import Path

import asyncpg

import config

log = logging.getLogger(__name__)
SQL_DIR = Path(__file__).resolve().parent


async def create_pool(dsn: str | None = None) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn or config.DATABASE_URL, min_size=1, max_size=5)


async def migrate(pool: asyncpg.Pool) -> list[str]:
    """sql/*.sql fayllarini tartib bilan, faqat bir marta qo'llaydi."""
    applied_now: list[str] = []
    async with pool.acquire() as con:
        await con.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now())"
        )
        # bir vaqtda ishga tushgan API va bot migratsiyani ikki marta qo'llamasligi uchun
        await con.execute("SELECT pg_advisory_lock(727001)")
        done = {r["name"] for r in await con.fetch("SELECT name FROM schema_migrations")}
        for f in sorted(SQL_DIR.glob("0*.sql")):
            if f.name in done:
                continue
            async with con.transaction():
                await con.execute(f.read_text(encoding="utf-8"))
                await con.execute("INSERT INTO schema_migrations(name) VALUES ($1)", f.name)
            applied_now.append(f.name)
            log.info("Migratsiya qo'llandi: %s", f.name)
        await con.execute("SELECT pg_advisory_unlock(727001)")
    return applied_now
