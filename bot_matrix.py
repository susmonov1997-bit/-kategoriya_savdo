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
from matrix_sync import MatrixError, binding_xlsx, digest, digest_text, last_ok, result_kb, sa_email, sync

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
        text, kb = digest_text(s, await digest(pool)), result_kb(s)
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
    if kb is not None:                     # bog'lanmagan filiallar → «🆕» (bog'lash / yangi filial)
        from bot_stock import prompt_new
        await prompt_new(pool, msg.bot, only_new=False, chat_ids=[uid], mx_only=True)


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


@router.callback_query(F.data == "mx:zx")
async def matrix_order_cb(c: CallbackQuery, pool: asyncpg.Pool) -> None:
    if not await _admin(pool, c.from_user.id):
        await c.answer("Faqat adminlar uchun", show_alert=True)
        return
    await c.answer("Tayyorlanmoqda…")
    from datetime import date as _date
    from types import SimpleNamespace
    from api_auth import User
    from api_main import app as api_app
    from api_matrix import MxReq, _order_full, _ship, order_fname, order_xlsx
    # API bilan bir xil kesh: 06:00 dan keyin oldindan hisoblangan bo'lsa — darhol
    rq, u = SimpleNamespace(app=api_app), User(id=0, is_admin=True, level="admin")
    f = MxReq(scope="a:", date_from=_date.today(), date_to=_date.today())
    d = await _order_full(f, rq, u)
    ship = await _ship(f, rq, u)
    if not d["whs"]:
        await c.message.answer("Zakaz uchun ma'lumot yo'q (matritsa yoki qoldiq yuklanmagan).")
        return
    await c.message.answer_document(BufferedInputFile(order_xlsx(d, ship), filename=order_fname()),
                                    caption="🛒 Zakaz: har ombor alohida varaq + «Ombordan yuborish» + «Izoh»")
