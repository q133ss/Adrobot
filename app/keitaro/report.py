from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_NUMBER_CLEANUP = re.compile(r"[^\d,.\-+eE]")


def to_float(value: Any) -> float:
    """Аккуратно превратить значение из отчёта в float.

    Keitaro может отдавать числа строками, с пробелами-разделителями тысяч,
    процентами и запятой как десятичным разделителем.
    """
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace("\xa0", "").replace(" ", "")
    if not text or text in {"-", "—", "N/A", "n/a"}:
        return 0.0
    if "%" in text:
        text = text.replace("%", "")
        return to_float(text) / 100
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    text = _NUMBER_CLEANUP.sub("", text)
    try:
        return float(text)
    except ValueError:
        return 0.0


@dataclass(slots=True)
class Metrics:
    clicks: int = 0
    leads: int = 0
    conversions: int = 0
    revenue: float = 0.0
    cost: float = 0.0
    profit: float = 0.0

    @property
    def cr(self) -> float:
        """Конверсия в процентах."""
        if self.clicks <= 0:
            return 0.0
        return self.leads / self.clicks * 100

    @property
    def roi(self) -> float:
        """ROI в процентах (profit / cost)."""
        if self.cost <= 0:
            return 0.0
        return self.profit / self.cost * 100

    @property
    def epc(self) -> float:
        if self.clicks <= 0:
            return 0.0
        return self.revenue / self.clicks

    def __add__(self, other: Metrics) -> Metrics:
        return Metrics(
            clicks=self.clicks + other.clicks,
            leads=self.leads + other.leads,
            conversions=self.conversions + other.conversions,
            revenue=self.revenue + other.revenue,
            cost=self.cost + other.cost,
            profit=self.profit + other.profit,
        )


@dataclass(slots=True)
class ReportResult:
    """Нормализованный ответ POST /report/build.

    Keitaro отдаёт строки либо объектами, либо массивами значений, а названия
    колонок — в `meta`. Здесь приводим всё к списку словарей.
    """

    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    total: int | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> ReportResult:
        if isinstance(payload, list):
            raw_rows = payload
            meta: list[Any] = []
            total = None
        elif isinstance(payload, dict):
            raw_rows = payload.get("rows") or payload.get("data") or []
            meta = payload.get("meta") or payload.get("columns") or []
            total = payload.get("total")
        else:
            raw_rows, meta, total = [], [], None

        columns = _column_names(meta)
        rows: list[dict[str, Any]] = []
        for row in raw_rows:
            if isinstance(row, dict):
                rows.append(row)
                continue
            if isinstance(row, (list, tuple)):
                if not columns:
                    columns = [f"col{position}" for position in range(len(row))]
                rows.append(dict(zip(columns, row, strict=False)))
                continue
            rows.append({"value": row})

        if not columns and rows:
            columns = list(rows[0].keys())

        parsed_total: int | None = None
        if total is not None:
            try:
                parsed_total = int(to_float(total))
            except (TypeError, ValueError):
                parsed_total = None

        return cls(columns=columns, rows=rows, total=parsed_total)

    def value(self, row: dict[str, Any], *names: str) -> Any:
        for name in names:
            if name in row:
                return row[name]
        return None


def _column_names(meta: Any) -> list[str]:
    if not isinstance(meta, list):
        return []
    names: list[str] = []
    for item in meta:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("field") or item.get("alias")
            names.append(str(name) if name else "")
    return names
