"""Статистика Keitaro: цифры по офферам и кампаниям."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.keitaro.client import KeitaroClient
from app.keitaro.errors import KeitaroAPIError, KeitaroError
from app.keitaro.report import Metrics, ReportResult, to_float

CORE_METRICS = ["clicks", "leads", "conversions", "revenue", "cost", "profit"]
MONEY_METRICS = ["clicks", "leads", "revenue", "cost", "profit"]

PERIODS: dict[str, tuple[str, str]] = {
    "today": ("Сегодня", "today"),
    "7d": ("7 дней", "7_days_ago"),
    "30d": ("30 дней", "1_month_ago"),
    "all": ("Всё время", "all_time"),
}
DEFAULT_PERIOD = "today"


@dataclass(slots=True)
class MetricsBook:
    by_id: dict[int, Metrics] = field(default_factory=dict)
    by_name: dict[str, Metrics] = field(default_factory=dict)
    totals: Metrics = field(default_factory=Metrics)

    def for_offer(self, offer_id: int, name: str = "") -> Metrics:
        if offer_id in self.by_id:
            return self.by_id[offer_id]
        if name and name in self.by_name:
            return self.by_name[name]
        return Metrics()

    @property
    def is_empty(self) -> bool:
        return not self.by_id and not self.by_name


def period_label(period: str) -> str:
    return PERIODS.get(period, PERIODS[DEFAULT_PERIOD])[0]


def period_interval(period: str) -> str:
    return PERIODS.get(period, PERIODS[DEFAULT_PERIOD])[1]


class StatsService:
    def __init__(self, client: KeitaroClient) -> None:
        self._client = client

    def offer_metrics(self, campaign_id: int, period: str = DEFAULT_PERIOD) -> MetricsBook:
        filters = [
            {"name": "campaign_id", "operator": "EQUALS", "expression": campaign_id},
        ]
        attempts = [
            (["offer_id", "offer"], CORE_METRICS),
            (["offer_id"], CORE_METRICS),
            (["offer"], CORE_METRICS),
            (["offer"], MONEY_METRICS),
        ]

        last_error: KeitaroError | None = None
        for dimensions, metrics in attempts:
            try:
                report = self._client.build_report(
                    dimensions=dimensions,
                    metrics=metrics,
                    filters=filters,
                    range_={"interval": period_interval(period)},
                )
            except KeitaroAPIError as exc:
                if exc.status in (400, 406, 422):
                    last_error = exc
                    continue
                raise
            return _book_from_report(report)

        if last_error is not None:
            raise last_error
        return MetricsBook()

    def campaign_totals(
        self, campaign_ids: list[int], period: str = DEFAULT_PERIOD
    ) -> dict[int, Metrics]:
        if not campaign_ids:
            return {}
        filters = [
            {
                "name": "campaign_id",
                "operator": "IN_LIST",
                "expression": campaign_ids,
            }
        ]
        report = self._client.build_report(
            dimensions=["campaign_id"],
            metrics=CORE_METRICS,
            filters=filters,
            range_={"interval": period_interval(period)},
        )
        totals: dict[int, Metrics] = {}
        for row in report.rows:
            campaign_id = _as_int(
                report.value(row, "campaign_id", "campaign", "id", "campaignId")
            )
            if campaign_id is None:
                continue
            totals[campaign_id] = totals.get(campaign_id, Metrics()) + _metrics_from_row(row)
        return totals


def _book_from_report(report: ReportResult) -> MetricsBook:
    book = MetricsBook()
    for row in report.rows:
        metrics = _metrics_from_row(row)
        offer_id = _as_int(report.value(row, "offer_id", "id", "offerId"))
        offer_name = _as_name(report.value(row, "offer", "offer_name", "name"))
        if offer_id is not None:
            book.by_id[offer_id] = book.by_id.get(offer_id, Metrics()) + metrics
        if offer_name:
            book.by_name[offer_name] = book.by_name.get(offer_name, Metrics()) + metrics
        book.totals = book.totals + metrics
    return book


def _metrics_from_row(row: dict[str, Any]) -> Metrics:
    clicks = round(to_float(row.get("clicks")))
    leads = round(to_float(row.get("leads")))
    if leads == 0:
        leads = round(to_float(row.get("conversions")))
    revenue = to_float(row.get("revenue"))
    cost = to_float(row.get("cost"))
    profit = to_float(row.get("profit"))
    if profit == 0 and (revenue or cost):
        profit = revenue - cost
    conversions = round(to_float(row.get("conversions"))) or leads
    return Metrics(
        clicks=clicks,
        leads=leads,
        conversions=conversions,
        revenue=revenue,
        cost=cost,
        profit=profit,
    )


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        for key in ("id", "value", "offer_id"):
            if key in value:
                return _as_int(value[key])
        return None
    try:
        return round(to_float(value))
    except (TypeError, ValueError):
        return None


def _as_name(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or "")
    return str(value).strip()
