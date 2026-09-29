"""Excel fayllarni o'qish uchun umumiy yordamchilar (tovar, filial, savdo loaderlari uchun)."""
from __future__ import annotations

import io
import math
import re
from typing import Any, BinaryIO, Iterable

import pandas as pd

_WS = re.compile(r"\s+")
_APOS = re.compile(r"[ʻʼ’‘`´']")

try:  # tez o'quvchi (39 MB oylik fayl ~10 s); bo'lmasa openpyxl
    import python_calamine  # noqa: F401
    EXCEL_ENGINE = "calamine"
except ImportError:  # pragma: no cover
    EXCEL_ENGINE = "openpyxl"


class LoaderError(Exception):
    """Fayl umuman yuklanmaydigan holat (ustun yo'q, sarlavha topilmadi va h.k.)."""


def clean_text(v: Any) -> str | None:
    """Tab, ikki probel, boshi/oxiridagi bo'shliqlarni tozalaydi. Bo'sh → None."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = _WS.sub(" ", str(v)).strip()
    return s or None


def name_key(v: Any) -> str | None:
    """Nomlarni solishtirish kaliti: bo'shliqlar, apostrof turlari (ʻ ’ ' `) va registr birxillashtiriladi.
    'Qoʻqon  filiali' == "Qo'qon filiali"."""
    s = clean_text(v)
    return _APOS.sub("'", s).lower() if s else None


def clean_header(v: Any) -> str:
    return clean_text(v) or ""


def to_number(v: Any) -> int | float | None:
    """185 / 185.0 / '185' / '173,5' → son. Son bo'lmasa None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if isinstance(v, float) and math.isnan(v):
            return None
        f = float(v)
    else:
        s = clean_text(v)
        if s is None:
            return None
        try:
            f = float(s.replace(" ", "").replace(",", "."))
        except ValueError:
            return None
    return int(f) if f.is_integer() else f


def read_headers(data: bytes, scan_rows: int = 15) -> list[set[str]]:
    """Faqat birinchi qatorlarni o'qiydi (fayl turini aniqlash uchun)."""
    raw = pd.read_excel(io.BytesIO(data), header=None, dtype=object, nrows=scan_rows, engine=EXCEL_ENGINE)
    return [{clean_header(c) for c in raw.iloc[i].tolist()} - {""} for i in range(len(raw))]


def read_sheet_with_header(
    data: bytes | BinaryIO, must_have: Iterable[str], scan_rows: int = 15
) -> tuple[pd.DataFrame, int]:
    """Birinchi varaqni o'qiydi va `must_have` ustunlari bor sarlavha qatorini topadi.

    Qaytaradi: (DataFrame, sarlavha qatorining Excel'dagi raqami 1-dan boshlab).
    DataFrame'ga `_excel_row` ustuni qo'shiladi — xatolarni Excel qator raqami bilan ko'rsatish uchun.
    """
    buf = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
    try:
        raw = pd.read_excel(buf, header=None, dtype=object, engine=EXCEL_ENGINE)
    except Exception as e:  # noqa: BLE001
        raise LoaderError(f"Excel faylni o'qib bo'lmadi: {e}") from e

    must = {clean_header(m) for m in must_have}
    header_idx = None
    for i in range(min(scan_rows, len(raw))):
        cells = {clean_header(c) for c in raw.iloc[i].tolist()}
        if must <= cells:
            header_idx = i
            break
    if header_idx is None:
        raise LoaderError(
            "Sarlavha qatori topilmadi. Birinchi {} qatorda quyidagi ustunlar bo'lishi kerak: {}".format(
                scan_rows, ", ".join(sorted(must))
            )
        )

    headers = [clean_header(c) for c in raw.iloc[header_idx].tolist()]
    df = raw.iloc[header_idx + 1 :].copy()
    df.columns = headers
    df = df.loc[:, [h for h in headers if h]]          # nomsiz ustunlarni tashlaymiz
    df = df.loc[:, ~df.columns.duplicated()]
    df["_excel_row"] = [header_idx + 2 + k for k in range(len(df))]
    data_cols = [c for c in df.columns if c != "_excel_row"]
    df = df.dropna(how="all", subset=data_cols)        # butunlay bo'sh qatorlar
    return df.reset_index(drop=True), header_idx + 1
