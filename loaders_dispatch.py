"""Fayl turini sarlavhalar bo'yicha aniqlash va mos loaderni chaqirish (bot va skript uchun umumiy)."""
from __future__ import annotations

import asyncio

import asyncpg

import loaders_bonus as bonus
import loaders_branches as branches
import loaders_owners as owners
import loaders_products as products
import loaders_sales as sales
from loaders_common import LoaderError, read_headers

KIND_NAMES = {"products": "Tovar spravochnigi", "branches": "Filial spravochnigi", "sales": "Savdo fayli",
              "bonus": "Qo'shimcha daromad %", "owners": "Mas'ullar ro'yxati"}


def detect_kind(data: bytes) -> str | None:
    try:
        rows = read_headers(data)
    except Exception:  # noqa: BLE001
        return None
    for h in rows:
        if sales.is_sales_file(h):
            return "sales"
        if products.is_products_file(list(h)):
            return "products"
        if branches.is_branches_file(h):
            return "branches"
        if bonus.is_bonus_file(h):
            return "bonus"
        if owners.is_owners_file(h):
            return "owners"
    return None


async def load_any_ex(pool, data: bytes, file_name: str | None, tg_user_id: int | None) -> tuple[str, str, int]:
    """Faylni yuklaydi. Qaytaradi: (tur, HTML hisobot, upload_id). LoaderError tashqariga chiqadi."""
    kind = await asyncio.to_thread(detect_kind, data)
    if kind == "products":
        r = await products.load_products(pool, data, file_name, tg_user_id)
        return kind, products.format_report(r), r.upload_id
    if kind == "branches":
        r = await branches.load_branches(pool, data, file_name, tg_user_id)
        return kind, branches.format_report(r), r.upload_id
    if kind == "sales":
        r = await sales.load_sales(pool, data, file_name, tg_user_id)
        return kind, sales.format_report(r), r.upload_id
    if kind == "owners":
        r = await owners.load_owners(pool, data, file_name, tg_user_id)
        return kind, owners.format_report(r), r.upload_id
    if kind == "bonus":
        r = await bonus.load_bonus(pool, data, file_name, tg_user_id)
        return kind, bonus.format_report(r), r.upload_id
    raise LoaderError(
        "Fayl turi aniqlanmadi. Kutilgan sarlavhalar:\n"
        "• Savdo: Филиал, Категория, Товар ИД, Сана, Сони, Жами(Кирим нархи), Жами(Чиқим нархи)\n"
        "• Tovar spravochnigi: Категория, Товар Ид, Товар номи, Бренд + xususiyat ustunlari (Turi, Balandlik (sm), Rang…)\n"
        "• Filial spravochnigi: Территория, Филиал, Кластер\n"
        "• Qo'shimcha daromad: Категория, Бренд, Қўшимча даромад %\n"
        "• Mas'ullar ro'yxati: Группа, Категория, Жавобгар КМ")


async def load_any(pool, data: bytes, file_name: str | None, tg_user_id: int | None) -> str:
    """Faylni yuklaydi va Telegram uchun HTML hisobot qaytaradi. LoaderError tashqariga chiqadi."""
    return (await load_any_ex(pool, data, file_name, tg_user_id))[1]
