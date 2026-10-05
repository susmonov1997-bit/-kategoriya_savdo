"""Mini App orqali fayl yuklash (Telegram 20 MB cheklovisiz): tekshirish → tasdiqlash → bazaga yozish.

  POST   /api/upload                 fayl (multipart) → job; fonda TEKSHIRUV boshlanadi
  GET    /api/upload/{job_id}        holat: checking | checked | loading | done | failed | cancelled
  POST   /api/upload/{job_id}/confirm   tekshiruvdan o'tgan faylni bazaga yozish (fonda)
  DELETE /api/upload/{job_id}        bekor qilish
  GET    /api/uploads                yuklashlar tarixi

Tekshiruv haqiqiy yuklash bilan AYNAN bir xil kodni ishlatadi, lekin hammasi bitta tranzaksiya ichida
bajarilib, oxirida ROLLBACK qilinadi — shuning uchun tekshiruv hisoboti yakuniy natija bilan bir xil,
bazada esa hech narsa o'zgarmaydi. Jarayon bitta nusxada ishlaydi (numReplicas = 1), holat xotirada.
"""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

import config
from api_auth import User, current_user
from loaders_common import LoaderError
from loaders_dispatch import KIND_NAMES, detect_kind, load_any_ex
from export_missing import build_missing_xlsx, has_missing, missing_caption, report_of, send_missing

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

UPLOAD_DIR = Path(tempfile.gettempdir()) / "savdo_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
JOB_TTL = 3600                       # tasdiqlanmagan fayl 1 soatdan keyin o'chiriladi
_JOBS: dict[str, dict] = {}
_DB_LOCK = asyncio.Lock()            # bir vaqtda faqat bitta tekshiruv/yuklash


def can_upload(user: User) -> bool:
    return user.can_upload


async def uploader(user: User = Depends(current_user)) -> User:
    if not can_upload(user):
        raise HTTPException(403, "Fayl yuklash huquqi yo'q")
    return user


class _OneConnection:
    """Loaderlar pool.acquire() kutadi — tekshiruvda ularga bitta (tranzaksiyadagi) ulanishni beramiz."""
    def __init__(self, con):
        self._con = con

    @asynccontextmanager
    async def acquire(self):
        yield self._con


def _public(job: dict) -> dict:
    out = {k: job[k] for k in ("id", "status", "file_name", "size_mb", "kind", "kind_name", "report",
                               "error", "upload_id", "created", "elapsed") if k in job}
    out["missing"] = bool(job.get("missing_xlsx"))
    return out


def _cleanup() -> None:
    now = time.time()
    for jid, job in list(_JOBS.items()):
        if now - job["created"] > JOB_TTL:
            Path(job["path"]).unlink(missing_ok=True)
            _JOBS.pop(jid, None)


def _preview_text(text: str, kind: str) -> str:
    """Birinchi qator ("✅ … yuklandi (#N)") tekshiruv sarlavhasi bilan almashtiriladi."""
    lines = text.split("\n")
    lines[0] = f"🔎 <b>Tekshiruv: {KIND_NAMES.get(kind, kind)}</b> — bazaga hali yozilmadi"
    return "\n".join(lines)


async def _check(pool, job: dict) -> None:
    t0 = time.time()
    try:
        data = Path(job["path"]).read_bytes()
        kind = await asyncio.to_thread(detect_kind, data)
        if kind is None:
            raise LoaderError("Fayl turi aniqlanmadi — savdo, tovar spravochnigi, filial spravochnigi "
                              "yoki qo'shimcha daromad % fayli bo'lishi kerak")
        job.update(kind=kind, kind_name=KIND_NAMES[kind])
        async with _DB_LOCK, pool.acquire() as con:
            tr = con.transaction()
            await tr.start()
            try:
                _, text, uid = await load_any_ex(_OneConnection(con), data, job["file_name"], job["user_id"])
                if kind == "sales":            # spravochnikda yo'qlar — rollback'dan oldin Excel'ga olinadi
                    rep = await report_of(con, uid)
                    if has_missing(rep):
                        job["missing_xlsx"] = await build_missing_xlsx(con, rep)
                        job["missing_report"] = {k: rep.get(k) for k in ("dates", "unknown_skus", "unknown_branches")}
            finally:
                await tr.rollback()            # tekshiruv — hech narsa saqlanmaydi
        job.update(status="checked", report=_preview_text(text, kind))
    except LoaderError as e:
        job.update(status="failed", error=str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("Tekshiruvda xato")
        job.update(status="failed", error=f"Kutilmagan xato: {e}")
    job["elapsed"] = round(time.time() - t0, 1)


async def _load(pool, job: dict) -> None:
    t0 = time.time()
    try:
        data = Path(job["path"]).read_bytes()
        async with _DB_LOCK:
            kind, text, upload_id = await load_any_ex(pool, data, job["file_name"], job["user_id"])
        job.update(status="done", report=text, upload_id=upload_id, kind=kind)
        # hisobot matni tarixda qayta ko'rish uchun saqlanadi
        await pool.execute(
            "UPDATE uploads SET report = COALESCE(report, '{}'::jsonb) || jsonb_build_object('text', $2::text, "
            "'via', 'miniapp') WHERE id = $1", upload_id, text)
        await _notify(job["user_id"], text)
        if kind == "sales":
            async with pool.acquire() as con:
                rep = await report_of(con, upload_id)
                if has_missing(rep):
                    job["missing_xlsx"] = await build_missing_xlsx(con, rep)
                    job["missing_report"] = {k: rep.get(k) for k in ("dates", "unknown_skus", "unknown_branches")}
                    await _send_missing(job["user_id"], job["missing_xlsx"], job["missing_report"])
                else:
                    job.pop("missing_xlsx", None)
    except LoaderError as e:
        job.update(status="failed", error=str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("Yuklashda xato")
        job.update(status="failed", error=f"Kutilmagan xato — ma'lumot o'zgartirilmadi: {e}")
    finally:
        Path(job["path"]).unlink(missing_ok=True)
    job["elapsed"] = round(time.time() - t0, 1)


async def _notify(chat_id: int, text: str) -> None:
    """Yakuniy hisobotni botga ham yuboradi (xato bo'lsa jim o'tadi)."""
    if not config.BOT_TOKEN:
        return
    try:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        try:
            await bot.send_message(chat_id, "📥 Mini App orqali yuklandi\n\n" + text[:3900])
        finally:
            await bot.session.close()
    except Exception as e:  # noqa: BLE001
        log.warning("Botga hisobot yuborilmadi: %s", e)


async def _send_missing(chat_id: int, data: bytes, rep: dict) -> bool:
    if not config.BOT_TOKEN:
        return False
    try:
        await send_missing(config.BOT_TOKEN, chat_id, data, rep)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Excel botga yuborilmadi: %s", e)
        return False


# ---------------------------------------------------------------------------
@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...), user: User = Depends(uploader)):
    _cleanup()
    name = file.filename or "fayl.xlsx"
    if not name.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(422, "Faqat .xlsx fayl qabul qilinadi")
    if any(j["status"] in ("checking", "loading") for j in _JOBS.values()):
        raise HTTPException(409, "Boshqa fayl hozir qayta ishlanmoqda — biroz kuting")
    jid = uuid.uuid4().hex[:12]
    path = UPLOAD_DIR / f"{jid}.xlsx"
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    with path.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                path.unlink(missing_ok=True)
                raise HTTPException(413, f"Fayl {config.MAX_UPLOAD_MB} MB dan katta")
            out.write(chunk)
    job = {"id": jid, "status": "checking", "file_name": name, "size_mb": round(size / 1048576, 1),
           "path": str(path), "user_id": user.id, "created": time.time()}
    _JOBS[jid] = job
    asyncio.create_task(_check(request.app.state.pool, job))
    return _public(job)


def _job(jid: str, user: User) -> dict:
    job = _JOBS.get(jid)
    if not job or job["user_id"] != user.id:
        raise HTTPException(404, "Yuklash topilmadi yoki muddati o'tgan — faylni qayta tanlang")
    return job


@router.get("/upload/{jid}")
async def upload_status(jid: str, user: User = Depends(uploader)):
    return _public(_job(jid, user))


@router.post("/upload/{jid}/confirm")
async def upload_confirm(jid: str, request: Request, user: User = Depends(uploader)):
    job = _job(jid, user)
    if job["status"] != "checked":
        raise HTTPException(409, "Faqat tekshiruvdan o'tgan faylni tasdiqlash mumkin")
    job.update(status="loading", report=None)
    asyncio.create_task(_load(request.app.state.pool, job))
    return _public(job)


@router.post("/upload/{jid}/missing")
async def upload_missing_send(jid: str, user: User = Depends(uploader)):
    """Tekshiruv/yuklash natijasidagi spravochnikda yo'q SKU/filiallar Excel'ini botga yuboradi."""
    job = _job(jid, user)
    if not job.get("missing_xlsx"):
        raise HTTPException(404, "Spravochnikda yo'q tovar yoki filial topilmadi")
    if not await _send_missing(user.id, job["missing_xlsx"], job["missing_report"]):
        raise HTTPException(503, "Botga yuborib bo'lmadi — botga /start bosilganini tekshiring")
    return {"ok": True}


@router.post("/uploads/{upload_id}/missing")
async def history_missing_send(upload_id: int, request: Request, user: User = Depends(uploader)):
    """Tarixdagi savdo yuklashi bo'yicha spravochnikda yo'qlar Excel'ini botga yuboradi."""
    async with request.app.state.pool.acquire() as con:
        rep = await report_of(con, upload_id)
        if not has_missing(rep):
            raise HTTPException(404, "Bu yuklashda spravochnikda yo'q tovar yoki filial bo'lmagan")
        data = await build_missing_xlsx(con, rep)
    if not await _send_missing(user.id, data, rep):
        raise HTTPException(503, "Botga yuborib bo'lmadi — botga /start bosilganini tekshiring")
    return {"ok": True}


@router.delete("/upload/{jid}")
async def upload_cancel(jid: str, user: User = Depends(uploader)):
    job = _job(jid, user)
    if job["status"] in ("checking", "loading"):
        raise HTTPException(409, "Jarayon ketmoqda — tugashini kuting")
    Path(job["path"]).unlink(missing_ok=True)
    job["status"] = "cancelled"
    _JOBS.pop(jid, None)
    return {"ok": True}


@router.get("/uploads")
async def uploads_history(request: Request, limit: int = 30, user: User = Depends(uploader)):
    rows = await request.app.state.pool.fetch(
        """SELECT id, kind, file_name, tg_user_id, status, rows_total, rows_loaded, started_at, finished_at,
                  report->'dates' AS dates, report->>'text' AS text, report->>'error' AS error,
                  COALESCE(jsonb_array_length(report->'unknown_skus'), 0)
                  + COALESCE(jsonb_array_length(report->'unknown_branches'), 0) AS missing
           FROM uploads ORDER BY id DESC LIMIT $1""", min(max(limit, 1), 100))
    out = []
    for r in rows:
        dates = json.loads(r["dates"]) if r["dates"] else None
        out.append({
            "id": r["id"], "kind": r["kind"], "kind_name": KIND_NAMES.get(r["kind"], r["kind"]),
            "file_name": r["file_name"], "user_id": r["tg_user_id"], "status": r["status"],
            "rows_total": r["rows_total"], "rows_loaded": r["rows_loaded"],
            "started_at": r["started_at"], "finished_at": r["finished_at"],
            "period": f"{dates[0]} — {dates[-1]}" if dates and len(dates) > 1 else (dates[0] if dates else None),
            "text": r["text"], "error": r["error"], "missing": r["missing"],
        })
    return {"uploads": out}
