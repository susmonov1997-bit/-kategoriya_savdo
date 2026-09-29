"""Telegram Mini App autentifikatsiyasi: initData imzosini tekshirish.

Mini App so'rovlarga sarlavha qo'shadi:  Authorization: tma <Telegram.WebApp.initData>
Hujjat: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException, Request

import config
from access import get_access

log = logging.getLogger(__name__)


@dataclass
class User:
    id: int
    first_name: str = ""
    username: str | None = None
    level: str | None = None           # admin | uploader | viewer
    can_upload: bool = False
    is_admin: bool = False


def validate_init_data(init_data: str, bot_token: str, max_age: int) -> User:
    pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received = pairs.pop("hash", None)
    if not received:
        raise ValueError("hash yo'q")
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        raise ValueError("imzo noto'g'ri")
    auth_date = int(pairs.get("auth_date", "0"))
    if max_age and time.time() - auth_date > max_age:
        raise ValueError("initData eskirgan")
    u = json.loads(pairs.get("user", "{}"))
    if "id" not in u:
        raise ValueError("user yo'q")
    return User(id=int(u["id"]), first_name=u.get("first_name", ""), username=u.get("username"))


async def current_user(request: Request, authorization: str | None = Header(default=None)) -> User:
    if authorization and authorization.startswith("tma "):
        try:
            user = validate_init_data(authorization[4:], config.BOT_TOKEN, config.INITDATA_MAX_AGE)
        except ValueError as e:
            raise HTTPException(401, f"Telegram autentifikatsiyasi o'tmadi: {e}")
    elif config.API_DEV_USER_ID and not config.ON_RAILWAY:
        # Faqat lokal sinov uchun! Serverda API_DEV_USER_ID o'rnatilmaydi.
        user = User(id=config.API_DEV_USER_ID, first_name="dev")
    else:
        raise HTTPException(401, "Authorization: tma <initData> kerak")
    acc = await get_access(request.app.state.pool, user.id)
    if not acc.can_view:
        msg = {"pending": "So'rovingiz admin tomonidan ko'rib chiqilmoqda",
               "rejected": "Kirish so'rovingiz rad etilgan",
               "revoked": "Kirish huquqingiz bekor qilingan"}.get(acc.status or "",
                                                              "Ruxsat yo'q — botda /start bosib, kirish so'rang")
        raise HTTPException(403, msg)
    user.level, user.can_upload, user.is_admin = acc.level, acc.can_upload, acc.is_admin
    return user


async def allowed_categories(user: User) -> set[int] | None:
    """4-bosqichda: foydalanuvchiga biriktirilgan kategoriyalar. Hozircha allowlist'dagilar hammasini ko'radi."""
    return None
