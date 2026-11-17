from __future__ import annotations

import httpx
from sqlalchemy import select

from app.models import Campaign
from tools.fake_keitaro.core import FakeTracker


def offer_shares(tracker: FakeTracker, campaign_id: int) -> dict[int, int]:
    stream = next(
        stream
        for stream in tracker.streams.values()
        if stream["campaign_id"] == campaign_id and stream["schema"] == "landings"
    )
    return {offer["offer_id"]: offer["share"] for offer in stream["offers"]}


def test_dashboard_lists_campaigns(web) -> None:
    response = web.get("/")
    assert response.status_code == 200
    assert "MX — Crypto Wallet — search" in response.text
    assert "AU — Antivirus — push" in response.text
    assert "создана вне Adrobot" in response.text


def test_dashboard_has_today_stats(web) -> None:
    response = web.get("/")
    assert "Клики" in response.text
    assert "102" in response.text


def test_dashboard_survives_keitaro_outage(web, tracker: FakeTracker, monkeypatch) -> None:
    def broken(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(tracker, "handle", broken)
    response = web.get("/")
    assert response.status_code == 200
    assert "Keitaro недоступен" in response.text
    # кампании из локального зеркала тоже показываем
    assert "Кампании" in response.text


def test_new_campaign_page_has_dictionaries(web) -> None:
    response = web.get("/campaigns/new")
    assert response.status_code == 200
    assert "https://demo-tracker.xyz" in response.text
    assert "FORTS" in response.text
    assert "Google Ads" in response.text
    assert "Создать кампанию" in response.text


def test_create_campaign_from_form(web, tracker: FakeTracker, session_factory) -> None:
    response = web.post(
        "/campaigns",
        data={
            "name": "MX — Form Campaign",
            "geo": "mx, au",
            "offer_id": "2",
            "domain_id": "1",
            "group_id": "1",
            "source_id": "2",
            "geo_mode": "reject",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    campaign_id = max(tracker.campaigns)
    assert response.headers["location"] == f"/campaigns/{campaign_id}"

    streams = [stream for stream in tracker.streams.values() if stream["campaign_id"] == campaign_id]
    assert {stream["schema"] for stream in streams} == {"redirect", "landings"}
    redirect = next(stream for stream in streams if stream["schema"] == "redirect")
    assert redirect["filters"] == [{"name": "country", "mode": "reject", "payload": ["MX", "AU"]}]
    assert redirect["action_payload"] == "https://www.google.com/"

    with session_factory() as db_session:
        mirrored = db_session.scalar(select(Campaign).where(Campaign.keitaro_id == campaign_id))
        assert mirrored is not None
        assert mirrored.name == "MX — Form Campaign"


def test_create_campaign_accept_mode(web, tracker: FakeTracker) -> None:
    web.post(
        "/campaigns",
        data={
            "name": "AU — Accept",
            "geo": "AU",
            "offer_id": "3",
            "domain_id": "1",
            "geo_mode": "accept",
        },
        follow_redirects=False,
    )
    campaign_id = max(tracker.campaigns)
    redirect = next(
        stream
        for stream in tracker.streams.values()
        if stream["campaign_id"] == campaign_id and stream["schema"] == "redirect"
    )
    assert redirect["filters"][0]["mode"] == "accept"


def test_cyrillic_name_is_preserved(web, tracker: FakeTracker) -> None:
    response = web.post(
        "/campaigns",
        data={
            "name": "Кампания «Кириллица»",
            "geo": "kz",
            "offer_id": "1",
            "domain_id": "1",
            "geo_mode": "reject",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    campaign_id = max(tracker.campaigns)
    assert tracker.campaigns[campaign_id]["name"] == "Кампания «Кириллица»"

    page = web.get(f"/campaigns/{campaign_id}")
    assert "Кампания «Кириллица»" in page.text


def test_create_campaign_validation_error(web, tracker: FakeTracker) -> None:
    response = web.post(
        "/campaigns",
        data={"name": "", "geo": "BAD", "offer_id": "", "domain_id": "1", "geo_mode": "reject"},
    )
    assert response.status_code == 400
    assert "Укажите название кампании" in response.text
    assert "BAD" in response.text
    assert len(tracker.campaigns) == 2


def test_campaign_detail_shows_offers_and_stats(web) -> None:
    response = web.get("/campaigns/2")
    assert response.status_code == 200
    assert "AU — Antivirus — push" in response.text
    assert "Antivirus 2026 — AU" in response.text
    assert "Antivirus 2026 — RO" in response.text
    assert "Offers" in response.text


def test_campaign_detail_period_switch(web) -> None:
    week = web.get("/campaigns/1?period=7d")
    assert week.status_code == 200
    assert "7 дней" in week.text


def test_campaign_detail_unknown(web) -> None:
    response = web.get("/campaigns/999")
    assert response.status_code == 404
    assert "не найдена" in response.text.lower()


def test_add_offer_from_form(web, tracker: FakeTracker) -> None:
    response = web.post(
        "/campaigns/1/offers",
        data={"offer_id": "5", "period": "today"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Оффер #5 добавлен" in response.text
    assert offer_shares(tracker, 1) == {1: 50, 5: 50}

    detail = web.get("/campaigns/1")
    assert "Dating Smartlink" in detail.text


def test_add_offer_validation_error(web, tracker: FakeTracker) -> None:
    response = web.post(
        "/campaigns/1/offers", data={"offer_id": "", "period": "today"}, follow_redirects=True
    )
    assert "Выберите оффер" in response.text
    assert offer_shares(tracker, 1) == {1: 100}


def test_add_duplicate_offer_flashes_error(web) -> None:
    response = web.post(
        "/campaigns/1/offers", data={"offer_id": "1", "period": "today"}, follow_redirects=True
    )
    assert "уже есть" in response.text


def test_remove_offer_from_form(web, tracker: FakeTracker) -> None:
    response = web.post(
        "/campaigns/2/offers/3/delete", data={"period": "today"}, follow_redirects=True
    )
    assert response.status_code == 200
    assert "Оффер #3 удалён" in response.text
    assert offer_shares(tracker, 2) == {4: 100}


def test_remove_last_offer_flashes_error(web, tracker: FakeTracker) -> None:
    response = web.post(
        "/campaigns/1/offers/1/delete", data={"period": "today"}, follow_redirects=True
    )
    assert "последний оффер" in response.text.lower()
    assert offer_shares(tracker, 1) == {1: 100}


def test_update_share_from_form(web, tracker: FakeTracker) -> None:
    response = web.post(
        "/campaigns/2/offers/3/share",
        data={"share": "80", "period": "today"},
        follow_redirects=True,
    )
    assert "Доля оффера #3 обновлена" in response.text
    assert offer_shares(tracker, 2) == {3: 80, 4: 20}


def test_update_share_validation(web, tracker: FakeTracker) -> None:
    response = web.post(
        "/campaigns/2/offers/3/share",
        data={"share": "0", "period": "today"},
        follow_redirects=True,
    )
    assert "от 1 до 100" in response.text
    assert offer_shares(tracker, 2) == {3: 60, 4: 40}


def test_api_offers_search(web) -> None:
    response = web.get("/api/offers?q=crypto")
    assert response.status_code == 200
    payload = response.json()
    assert {offer["id"] for offer in payload["offers"]} == {1, 2}

    empty = web.get("/api/offers?q=zzz").json()
    assert empty["offers"] == []


def test_api_campaign_stats(web) -> None:
    response = web.get("/api/campaigns/1/stats?period=today")
    assert response.status_code == 200
    payload = response.json()
    assert payload["offers"]["1"]["clicks"] == 102
    assert payload["totals"]["clicks"] == 102


def test_api_health(web) -> None:
    response = web.get("/api/health")
    assert response.status_code == 200
    assert response.json()["keitaro"] == "ok"


def test_static_css_served(web) -> None:
    response = web.get("/static/css/app.css")
    assert response.status_code == 200
    assert "--accent" in response.text


def test_geos_preserved_after_reload(web, tracker: FakeTracker) -> None:
    web.post(
        "/campaigns",
        data={
            "name": "RO — Persist",
            "geo": "RO",
            "offer_id": "4",
            "domain_id": "1",
            "geo_mode": "reject",
        },
        follow_redirects=False,
    )
    campaign_id = max(tracker.campaigns)
    page = web.get(f"/campaigns/{campaign_id}")
    assert "RO" in page.text
    assert "demo-tracker.xyz" in page.text
