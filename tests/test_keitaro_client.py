from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from app.keitaro.client import KeitaroClient
from app.keitaro.entities import KeitaroOffer
from app.keitaro.errors import (
    KeitaroAPIError,
    KeitaroNotFound,
    KeitaroUnavailable,
    KeitaroValidationError,
)
from app.models import ApiCall
from app.services.audit import ApiCallRecorder
from tools.fake_keitaro.core import FakeTracker, mock_transport


def test_offer_country_accepts_empty_string() -> None:
    """Реальный Keitaro отдаёт country: "" вместо []."""
    offer = KeitaroOffer.model_validate({"id": 1, "name": "x", "country": ""})
    assert offer.country == []


def test_offer_country_accepts_csv_string() -> None:
    offer = KeitaroOffer.model_validate({"id": 1, "name": "x", "country": "MX, AU"})
    assert offer.country == ["MX", "AU"]


def test_list_campaigns_uses_api_key(tracker: FakeTracker) -> None:
    client = KeitaroClient("http://fake", "demo-api-key", transport=mock_transport(tracker))
    campaigns = client.list_campaigns()
    client.close()

    assert [campaign.id for campaign in campaigns] == [1, 2]
    assert tracker.calls[0] == ("GET", "/campaigns")


def test_invalid_api_key_raises(tracker: FakeTracker) -> None:
    client = KeitaroClient("http://fake", "wrong-key", transport=mock_transport(tracker))
    with pytest.raises(KeitaroAPIError) as exc_info:
        client.list_campaigns()
    client.close()

    assert "401" in str(exc_info.value)
    assert "Invalid API key" in str(exc_info.value)


def test_not_found_maps_to_exception(tracker: FakeTracker) -> None:
    client = KeitaroClient("http://fake", "demo-api-key", transport=mock_transport(tracker))
    with pytest.raises(KeitaroNotFound):
        client.get_campaign(999)
    client.close()


def test_validation_error_maps(tracker: FakeTracker) -> None:
    client = KeitaroClient("http://fake", "demo-api-key", transport=mock_transport(tracker))
    with pytest.raises(KeitaroValidationError):
        client.create_campaign({"name": "No alias"})
    client.close()


def test_unavailable_tracker(tracker: FakeTracker) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = KeitaroClient("http://fake", "demo-api-key", transport=httpx.MockTransport(handler))
    with pytest.raises(KeitaroUnavailable):
        client.list_offers()
    client.close()


def test_audit_log_written(session, tracker: FakeTracker) -> None:
    client = KeitaroClient(
        "http://fake",
        "demo-api-key",
        transport=mock_transport(tracker),
        audit=ApiCallRecorder(session),
    )
    client.list_offers()
    client.close()

    rows = session.scalars(select(ApiCall)).all()
    assert len(rows) == 1
    assert rows[0].method == "GET"
    assert rows[0].path == "/offers"
    assert rows[0].ok is True
    assert rows[0].status_code == 200


def test_audit_log_records_errors(session: FakeTracker) -> None:
    client = KeitaroClient(
        "http://fake",
        "wrong-key",
        transport=mock_transport(FakeTracker()),
        audit=ApiCallRecorder(session),
    )
    with pytest.raises(KeitaroAPIError):
        client.list_offers()
    client.close()

    row = session.scalars(select(ApiCall)).one()
    assert row.ok is False
    assert row.status_code == 401
    assert row.error


def test_campaign_id_extracted_for_audit(session, tracker: FakeTracker) -> None:
    client = KeitaroClient(
        "http://fake",
        "demo-api-key",
        transport=mock_transport(tracker),
        audit=ApiCallRecorder(session),
    )
    client.list_streams(1)
    client.close()

    row = session.scalars(select(ApiCall)).one()
    assert row.campaign_id == 1


def test_report_falls_back_to_measures(tracker: FakeTracker) -> None:
    attempts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        attempts.append(payload)
        if "metrics" in payload:
            return httpx.Response(400, json={"error": "Unknown field metrics"})
        status, body = tracker.handle(
            "POST", "/report/build", payload, dict(request.headers)
        )
        return httpx.Response(status, json=body)

    client = KeitaroClient("http://fake", "demo-api-key", transport=httpx.MockTransport(handler))
    report = client.build_report(dimensions=["campaign_id"], metrics=["clicks"])
    client.close()

    assert len(attempts) == 2
    assert "measures" in attempts[1]
    assert report.rows


def test_report_request_shape(tracker: FakeTracker) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        status, body = tracker.handle("POST", "/report/build", captured, dict(request.headers))
        return httpx.Response(status, json=body)

    client = KeitaroClient("http://fake", "demo-api-key", transport=httpx.MockTransport(handler))
    client.build_report(
        dimensions=["offer_id"],
        metrics=["clicks", "leads"],
        filters=[{"name": "campaign_id", "operator": "EQUALS", "expression": 1}],
        range_={"interval": "today"},
    )
    client.close()

    assert captured["dimensions"] == ["offer_id"]
    assert captured["metrics"] == ["clicks", "leads"]
    assert captured["range"] == {"interval": "today"}
    assert captured["filters"][0]["name"] == "campaign_id"


def test_action_types_follow_real_keitaro(tracker: FakeTracker) -> None:
    client = KeitaroClient("http://fake", "demo-api-key", transport=mock_transport(tracker))
    assert client.redirect_action_type() == "http"
    # show_landing в /streams_actions реального Keitaro нет — используем http
    assert client.offers_action_type() == "http"
    client.close()


def test_offers_action_prefers_show_landing_when_available() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"key": "http", "name": "HttpRedirect", "type": "redirect"},
                {"key": "show_landing", "name": "ShowLanding", "type": "other"},
            ],
        )

    client = KeitaroClient("http://fake", "demo-api-key", transport=httpx.MockTransport(handler))
    assert client.offers_action_type() == "show_landing"
    client.close()


def test_groups_require_type_param(tracker: FakeTracker) -> None:
    client = KeitaroClient("http://fake", "demo-api-key", transport=mock_transport(tracker))
    groups = client.list_groups("campaigns")
    assert groups
    client.close()

    def handler(request: httpx.Request) -> httpx.Response:
        if "type=" not in str(request.url):
            return httpx.Response(400, json={"error": "Param 'type' is required"})
        return httpx.Response(200, json=[])

    broken = KeitaroClient("http://fake", "demo-api-key", transport=httpx.MockTransport(handler))
    assert broken.list_groups() == []
    with pytest.raises(KeitaroValidationError):
        broken.request("GET", "/groups")
    broken.close()
