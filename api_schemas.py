"""API so'rov/javob modellari."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Compare = Literal["prev", "yoy", "none"]
Metric = Literal["amount", "qty", "gross", "gross_pct", "income", "margin", "margin_pct", "avg_price", "share", "name"]


class Filters(BaseModel):
    """Barcha endpointlar uchun umumiy filtr. Bo'sh ro'yxat = filtr yo'q."""
    category_id: int
    date_from: date
    date_to: date
    compare: Compare = "prev"
    region_ids: list[int] = []
    cluster_ids: list[int] = []
    branch_ids: list[int] = []
    brands: list[str] = []
    statuses: list[str] = []
    product_ids: list[int] = []
    # xususiyatlar: {"1": ["176–190 sm"], "0": ["Но фрост"]} — kalit slot raqami, qiymat — filtr qiymatlari
    attrs: dict[int, list[str]] = {}

    @model_validator(mode="after")
    def _check(self):
        if self.date_to < self.date_from:
            raise ValueError("date_to < date_from")
        if (self.date_to - self.date_from).days > 800:
            raise ValueError("Davr 800 kundan oshmasin")
        return self


class BreakdownRequest(Filters):
    # o'lchovlar: region | cluster | branch | brand | sku | status | attr:<slot>
    rows: str = "region"
    cols: str | None = None
    sort: Metric = "amount"
    desc: bool = True
    limit: int = Field(200, ge=1, le=2000)


class Kpi(BaseModel):
    amount: float = 0          # savdo summasi
    qty: float = 0             # dona (bonus bilan)
    bonus_qty: float = 0
    gross: float = 0           # valovka
    income: float = 0          # qo'shimcha daromad
    margin: float = 0          # marja = valovka + qo'shimcha daromad
    gross_pct: float | None = None
    margin_pct: float | None = None
    avg_price: float | None = None   # savdo / sotilgan dona (bonussiz)
    skus: int = 0
    branches: int = 0
