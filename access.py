"""Kirish huquqlari: bot va API uchun umumiy.

  admin    — ADMIN_IDS dagilar: hammasi + so'rovlarni tasdiqlash + foydalanuvchilarni boshqarish
  uploader — tasdiqlangan: ko'radi va fayl yuklaydi
  viewer   — tasdiqlangan: faqat ko'radi
  None     — kirish yo'q (so'rov yubormagan, kutilmoqda, rad etilgan yoki o'chirilgan)
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import config

_CACHE: dict[int, tuple[float, "Access"]] = {}
_TTL = 30.0


@dataclass
class Access:
    level: str | None          # admin | uploader | viewer | None
    status: str | None         # pending | approved | rejected | revoked | None (so'rov yo'q)

    @property
    def can_view(self) -> bool:
        return self.level is not None

    @property
    def can_upload(self) -> bool:
        return self.level in ("admin", "uploader")

    @property
    def is_admin(self) -> bool:
        return self.level == "admin"


async def get_access(pool, tg_id: int) -> Access:
    if tg_id in config.ADMIN_IDS:
        return Access("admin", "approved")
    hit = _CACHE.get(tg_id)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    row = await pool.fetchrow("SELECT role, status FROM users WHERE tg_id = $1", tg_id)
    if row is None:
        acc = Access(None, None)
    elif row["status"] == "approved":
        acc = Access(row["role"], "approved")
    else:
        acc = Access(None, row["status"])
    _CACHE[tg_id] = (time.time(), acc)
    return acc


def invalidate(tg_id: int | None = None) -> None:
    """Tasdiqlash / rad etish / o'chirishdan keyin darhol kuchga kirishi uchun."""
    if tg_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(tg_id, None)


ROLE_NAMES = {"viewer": "👁 Ko'ruvchi", "uploader": "📥 Yuklovchi", "admin": "⭐ Admin"}
