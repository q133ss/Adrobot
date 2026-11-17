from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import Settings
from app.keitaro.errors import KeitaroError
from app.models import Campaign
from app.services.campaigns import (
    CampaignDraft,
    build_campaign_payload,
    build_offer_stream_payload,
    build_redirect_stream_payload,
    create_campaign,
)
from app.services.catalog import Catalog
from tools.fake_keitaro.core import FakeTracker


def make_draft(**overrides) -> CampaignDraft:
    defaults = {
        "name": "MX — Test Campaign",
        "geos": ["MX", "AU"],
        "offer_id": 1,
        "offer_name": "Crypto Wallet — MX",
        "domain_id": 1,
        "group_id": 1,
        "source_id": 1,
        "geo_mode": "reject",
        "redirect_url": "https://www.google.com/",
    }
    defaults.update(overrides)
    return CampaignDraft(**defaults)


def test_campaign_payload_matches_api_schema() -> None:
    draft = make_draft()
    payload = build_campaign_payload(draft, "mx-test-campaign")
    assert payload["name"] == "MX — Test Campaign"
    assert payload["alias"] == "mx-test-campaign"
    assert payload["type"] == "position"
    assert payload["domain_id"] == 1
    assert payload["group_id"] == 1
    assert payload["traffic_source_id"] == 1


def test_redirect_stream_payload_uses_geo_filter() -> None:
    payload = build_redirect_stream_payload(
        make_draft(geo_mode="accept"), campaign_id=7, action_type="redirect"
    )
    assert payload["campaign_id"] == 7
    assert payload["schema"] == "redirect"
    assert payload["action_type"] == "redirect"
    assert payload["action_payload"] == "https://www.google.com/"
    assert payload["filters"] == [
        {"name": "country", "mode": "accept", "payload": ["MX", "AU"]}
    ]


def test_redirect_stream_payload_without_geos_has_no_filters() -> None:
    payload = build_redirect_stream_payload(
        make_draft(geos=[], geo_mode="reject"), campaign_id=7, action_type="redirect"
    )
    assert payload["filters"] == []


def test_offer_stream_payload_contains_offer_with_full_share() -> None:
    payload = build_offer_stream_payload(make_draft(), campaign_id=7, action_type="show_landing")
    assert payload["schema"] == "landings"
    assert payload["offers"] == [{"offer_id": 1, "share": 100, "state": "active"}]


def test_create_campaign_creates_campaign_and_two_flows(keitaro, session, tracker: FakeTracker) -> None:
    creation = create_campaign(
        keitaro,
        Catalog(keitaro, session),
        session,
        make_draft(),
        Settings(_env_file=None),
    )

    assert creation.campaign.id in tracker.campaigns
    assert creation.alias == "mx-test-campaign"
    assert creation.action_types == {"redirect": "redirect", "offers": "show_landing"}

    streams = [stream for stream in tracker.streams.values() if stream["campaign_id"] == creation.campaign.id]
    assert len(streams) == 2

    redirect = next(stream for stream in streams if stream["schema"] == "redirect")
    assert redirect["position"] == 1
    assert redirect["filters"] == [{"name": "country", "mode": "reject", "payload": ["MX", "AU"]}]
    assert redirect["action_payload"] == "https://www.google.com/"

    offers = next(stream for stream in streams if stream["schema"] == "landings")
    assert offers["position"] == 2
    assert offers["offers"] == [{"offer_id": 1, "share": 100, "state": "active"}]


def test_create_campaign_stores_local_mirror(keitaro, session, tracker: FakeTracker) -> None:
    creation = create_campaign(
        keitaro, Catalog(keitaro, session), session, make_draft(), Settings(_env_file=None)
    )
    mirrored = session.scalar(select(Campaign).where(Campaign.keitaro_id == creation.campaign.id))

    assert mirrored is not None
    assert mirrored.name == "MX — Test Campaign"
    assert mirrored.geos == ["MX", "AU"]
    assert mirrored.redirect_flow_id == creation.redirect_stream.id
    assert mirrored.offer_flow_id == creation.offer_stream.id
    assert mirrored.domain_name == "https://demo-tracker.xyz"
    assert mirrored.group_name == "FORTS"
    assert mirrored.source_name == "FORTS"
    assert [offer.offer_id for offer in mirrored.offers] == [1]
    assert mirrored.campaign_url == "https://demo-tracker.xyz/mx-test-campaign"


def test_alias_is_unique(keitaro, session, tracker: FakeTracker) -> None:
    draft = make_draft(name="MX — Crypto Wallet — search")
    creation = create_campaign(keitaro, Catalog(keitaro, session), session, draft, Settings(_env_file=None))
    assert creation.alias == "mx-crypto-wallet-search-2"


def test_failed_second_flow_rolls_back_campaign(keitaro, session, tracker: FakeTracker, monkeypatch) -> None:
    original = tracker._create_stream

    def broken(payload):
        if payload["schema"] == "landings":
            return 400, {"error": "Internal boom"}
        return original(payload)

    monkeypatch.setattr(tracker, "_create_stream", broken)

    with pytest.raises(KeitaroError) as exc_info:
        create_campaign(
            keitaro, Catalog(keitaro, session), session, make_draft(), Settings(_env_file=None)
        )

    assert "Кампания удалена" in exc_info.value.message
    assert len(tracker.campaigns) == 2  # только сидовые
    assert all(stream["campaign_id"] in {1, 2} for stream in tracker.streams.values())
    assert session.scalar(select(Campaign)) is None


def test_create_campaign_accept_mode(keitaro, session, tracker: FakeTracker) -> None:
    creation = create_campaign(
        keitaro,
        Catalog(keitaro, session),
        session,
        make_draft(geo_mode="accept"),
        Settings(_env_file=None),
    )
    redirect = next(
        stream
        for stream in tracker.streams.values()
        if stream["campaign_id"] == creation.campaign.id and stream["schema"] == "redirect"
    )
    assert redirect["filters"][0]["mode"] == "accept"
