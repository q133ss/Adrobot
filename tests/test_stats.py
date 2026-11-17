from __future__ import annotations

import json

import httpx
import pytest

from app.keitaro.client import KeitaroClient
from app.keitaro.errors import KeitaroAPIError
from app.keitaro.report import Metrics, ReportResult, to_float
from app.services.stats import StatsService
from tools.fake_keitaro.core import FakeTracker


def test_to_float_handles_formats() -> None:
    assert to_float("1 234.5") == 1234.5
    assert to_float("1\xa0234") == 1234
    assert to_float("12,5") == 12.5
    assert to_float("1,234.56") == 1234.56
    assert to_float("45%") == 0.45
    assert to_float("—") == 0
    assert to_float(None) == 0
    assert to_float(3) == 3.0


def test_report_parses_rows_with_meta_columns() -> None:
    payload = {
        "rows": [[1, "Offer A", 10, 2], [2, "Offer B", 5, 0]],
        "meta": [{"name": "offer_id"}, {"name": "offer"}, {"name": "clicks"}, {"name": "leads"}],
        "total": 2,
    }
    report = ReportResult.from_payload(payload)
    assert report.columns == ["offer_id", "offer", "clicks", "leads"]
    assert report.rows[0]["offer"] == "Offer A"
    assert report.total == 2


def test_report_parses_dict_rows() -> None:
    payload = {"rows": [{"offer_id": 3, "clicks": "12"}], "meta": [], "total": 1}
    report = ReportResult.from_payload(payload)
    assert report.rows[0]["offer_id"] == 3
    assert report.columns == ["offer_id", "clicks"]


def test_report_parses_flat_list() -> None:
    report = ReportResult.from_payload([[1, 2], [3, 4]])
    assert report.rows == [{"col0": 1, "col1": 2}, {"col0": 3, "col1": 4}]


def test_report_tolerates_garbage() -> None:
    report = ReportResult.from_payload("unexpected")
    assert report.rows == []


def test_metrics_derived_values() -> None:
    metrics = Metrics(clicks=100, leads=5, revenue=25.0, cost=10.0, profit=15.0)
    assert metrics.cr == 5.0
    assert metrics.roi == 150.0
    assert metrics.epc == 0.25
    assert Metrics().cr == 0
    assert Metrics().roi == 0


def test_stats_service_maps_offers(keitaro) -> None:
    stats = StatsService(keitaro).offer_metrics(1, "today")
    metrics = stats.for_offer(1, "Crypto Wallet — MX")

    assert metrics.clicks == 102
    assert metrics.leads == 9
    assert metrics.revenue == pytest.approx(22.63)
    assert metrics.cost == pytest.approx(16.32)
    assert metrics.profit == pytest.approx(6.31)
    assert stats.totals.clicks == 102


def test_stats_period_scales(keitaro) -> None:
    today = StatsService(keitaro).offer_metrics(2, "today")
    week = StatsService(keitaro).offer_metrics(2, "7d")
    assert week.totals.clicks == today.totals.clicks * 7


def test_stats_falls_back_when_combined_dimension_rejected(tracker: FakeTracker) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/report/build"):
            payload = json.loads(request.content)
            if len(payload.get("dimensions", [])) > 1:
                return httpx.Response(400, json={"error": "Unknown dimension combination"})
        status, body = tracker.handle(
            request.method,
            request.url.path.replace("/admin_api/v1", ""),
            json.loads(request.content) if request.content else None,
            dict(request.headers),
        )
        return httpx.Response(status, json=body)

    client = KeitaroClient("http://fake", "demo-api-key", transport=httpx.MockTransport(handler))
    try:
        stats = StatsService(client).offer_metrics(1, "today")
    finally:
        client.close()

    assert stats.by_id
    assert stats.totals.clicks > 0


def test_campaign_totals_groups_by_campaign(keitaro) -> None:
    totals = StatsService(keitaro).campaign_totals([1, 2])
    assert set(totals) == {1, 2}
    assert totals[1].clicks == 102
    assert totals[2].clicks == sum(
        metrics.clicks for metrics in StatsService(keitaro).offer_metrics(2, "today").by_id.values()
    )


def test_campaign_totals_empty_input(keitaro) -> None:
    assert StatsService(keitaro).campaign_totals([]) == {}


def test_stats_service_raises_on_server_error(tracker: FakeTracker) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "Database is down"})

    client = KeitaroClient("http://fake", "demo-api-key", transport=httpx.MockTransport(handler))
    with pytest.raises(KeitaroAPIError):
        StatsService(client).offer_metrics(1, "today")
    client.close()
