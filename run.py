"""Railway uchun yagona jarayon: FastAPI (API + Mini App) va Telegram bot bitta konteynerda.

    python run.py

API `PORT` (Railway beradi) portida tinglaydi. BOT_TOKEN bo'lmasa faqat API ishlaydi.
Bot long-polling bilan ishlaydi — servis faqat BITTA nusxada (replica = 1) bo'lishi kerak.
"""
from __future__ import annotations

import asyncio
import logging

import uvicorn

import config
from api_main import app as api_app
from bot import run_bot
from db import create_pool, migrate

log = logging.getLogger("app.run")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.DATABASE_URL:
        raise SystemExit("DATABASE_URL o'rnatilmagan")
    if config.API_DEV_USER_ID and config.ON_RAILWAY:
        log.warning("API_DEV_USER_ID Railway'da e'tiborga olinmaydi — o'chirib qo'ying")

    pool = await create_pool()
    applied = await migrate(pool)
    log.info("Migratsiyalar: %s", applied or "yangi yo'q")

    server = uvicorn.Server(uvicorn.Config(api_app, host="0.0.0.0", port=config.PORT, log_level="info",
                                           proxy_headers=True, forwarded_allow_ips="*"))
    tasks = [asyncio.create_task(server.serve(), name="api")]
    if config.BOT_TOKEN:
        tasks.append(asyncio.create_task(run_bot(pool), name="bot"))
    else:
        log.warning("BOT_TOKEN yo'q — faqat API ishga tushdi")

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for t in done:
            if t.exception():
                log.error("%s to'xtadi: %r", t.get_name(), t.exception())
                raise t.exception()
    finally:
        server.should_exit = True
        for t in tasks:
            t.cancel()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
