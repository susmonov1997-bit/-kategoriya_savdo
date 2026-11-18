"""Bot: /matritsa — Google Sheet'dagi matritsani hozir o'qish va bog'lanish hisoboti (faqat adminlar)."""
from __future__ import annotations

import html
from datetime import date

import asyncpg
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

import config
from access import get_access
from matrix_sync import MatrixError, binding_xlsx, last_ok, result_kb, sa_email, summary_text, sync

router = Router()

SETUP = ("ℹ️ Matritsa hali ulanmagan. Railway → Variables'ga qo'shing:\n"
         "• <code>GOOGLE_SA_JSON</code> — servis-akkaunt kaliti (JSON fayl matni)\n"
         "• <code>MATRIX_SHEET_ID</code> — «Матрица КБТ» havolasi\n"
         "Keyin jadvalni servis-akkaunt pochtasiga «Ko'ruvchi» qilib ulashing.")


async def _admin(pool, uid: int) -> bool:
    return uid in config.ADMIN_IDS or (await get_access(pool, uid)).is_admin


async def _do_sync(pool: asyncpg.Pool, msg: Message, uid: int) -> None:
    if not config.MATRIX_SHEET_ID or not config.GOOGLE_SA_JSON:
        await msg.answer(SETUP)
        return
    wait = await msg.answer("⏳ Google Sheet'dan matritsa o'qilmoqda…")
    try:
        s = await sync(pool, "manual", uid)
        text, kb = summary_text(s), result_kb()
    except MatrixError as e:
        text = f"❌ <b>Matritsani o'qib bo'lmadi</b>\n{html.escape(str(e), quote=False)}"
        prev = await last_ok(pool)
        if prev:
            text += f"\n\nOxirgi muvaffaqiyatli o'qish ({prev['at']:%d.%m %H:%M}) saqlanib turibdi."
        if (em := sa_email()):
            text += f"\n\nServis-akkaunt: <code>{html.escape(em)}</code>"
        kb = None
    try:
        await wait.delete()
    except Exception:  # noqa: BLE001
        pass
    await msg.answer(text, reply_markup=kb)


@router.message(Command("matritsa"))
async def matrix_cmd(m: Message, pool: asyncpg.Pool) -> None:
    if not await _admin(pool, m.from_user.id):
        await m.answer("⛔ Bu buyruq faqat adminlar uchun.")
        return
    await _do_sync(pool, m, m.from_user.id)


@router.callback_query(F.data == "mx:sync")
async def matrix_sync_cb(c: CallbackQuery, pool: asyncpg.Pool) -> None:
    if not await _admin(pool, c.from_user.id):
        await c.answer("Faqat adminlar uchun", show_alert=True)
        return
    await c.answer()
    await _do_sync(pool, c.message, c.from_user.id)


@router.callback_query(F.data == "mx:xl")
async def matrix_xl_cb(c: CallbackQuery, pool: asyncpg.Pool) -> None:
    if not await _admin(pool, c.from_user.id):
        await c.answer("Faqat adminlar uchun", show_alert=True)
        return
    await c.answer("Tayyorlanmoqda…")
    async with pool.acquire() as con:
        data = await binding_xlsx(con)
    await c.message.answer_document(
        BufferedInputFile(data, filename=f"matritsa_boglanish_{date.today():%d.%m.%Y}.xlsx"),
        caption="Varaqlar: Tovar topilmadi · Taxminiy bog'langan · Filial topilmadi · Ombori yo'q filial")
