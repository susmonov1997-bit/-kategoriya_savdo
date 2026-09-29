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


async def supervise_bot(pool) -> None:
    """Botni ishga tushiradi; xato bo'lsa API'ni to'xtatmaydi — logga yozib, qayta urinadi."""
    from aiogram.exceptions import TelegramUnauthorizedError
    from aiogram.utils.token import TokenValidationError

    delay = 5
    while True:
        try:
            await run_bot(pool)
            log.warning("Bot polling to'xtadi — %s s dan keyin qayta ishga tushadi", delay)
        except asyncio.CancelledError:
            raise
        except (TelegramUnauthorizedError, TokenValidationError) as e:
            log.error("BOT_TOKEN noto'g'ri (%s). Railway Variables'da tokenni tekshiring — "
                      "bo'sh joy/qo'shtirnoqsiz, BotFather bergandek. API ishlashda davom etadi.", e)
            return
        except Exception:  # noqa: BLE001
            log.exception("Bot xatosi — %s s dan keyin qayta urinish", delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 300)


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
    bot_task = None
    if config.BOT_TOKEN:
        bot_task = asyncio.create_task(supervise_bot(pool), name="bot")
    else:
        log.warning("BOT_TOKEN yo'q — faqat API ishga tushdi")
    try:
        await server.serve()          # jarayon API ishlaguncha yashaydi
    finally:
        if bot_task:
            bot_task.cancel()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
