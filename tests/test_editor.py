from __future__ import annotations

import pytest

from app.keitaro.errors import KeitaroValidationError
from app.services.editor import (
    EditorError,
    add_offer,
    find_offer_stream,
    load_view,
    remove_offer,
    update_share,
)
from app.services.stats import StatsService
from tools.fake_keitaro.core import FakeTracker


def offers_of(tracker: FakeTracker, campaign_id: int) -> dict[int, int]:
    stream = next(
        stream
        for stream in tracker.streams.values()
        if stream["campaign_id"] == campaign_id and stream["schema"] == "landings"
    )
    return {offer["offer_id"]: offer["share"] for offer in stream["offers"]}


def test_load_view_finds_offer_flow(keitaro) -> None:
    view = load_view(keitaro, 2)
    assert view.campaign.name == "AU — Antivirus — push"
    assert view.offer_stream is not None
    assert view.redirect_stream is not None
    assert view.offer_ids() == [3, 4]
    assert find_offer_stream(view.streams) is view.offer_stream


def test_add_offer_splits_shares_equally(keitaro, tracker: FakeTracker) -> None:
    add_offer(keitaro, 1, 5)
    assert offers_of(tracker, 1) == {1: 50, 5: 50}


def test_add_offer_with_explicit_share_normalizes(keitaro, tracker: FakeTracker) -> None:
    add_offer(keitaro, 2, 5, share=50)
    shares = offers_of(tracker, 2)
    assert sum(shares.values()) == 100
    assert set(shares) == {3, 4, 5}


def test_add_duplicate_offer_rejected(keitaro) -> None:
    with pytest.raises(EditorError, match="уже есть"):
        add_offer(keitaro, 1, 1)


def test_remove_offer_rebalances(keitaro, tracker: FakeTracker) -> None:
    remove_offer(keitaro, 2, 3)
    assert offers_of(tracker, 2) == {4: 100}


def test_remove_unknown_offer_rejected(keitaro) -> None:
    with pytest.raises(EditorError, match="не найден"):
        remove_offer(keitaro, 2, 999)


def test_remove_last_offer_rejected(keitaro, tracker: FakeTracker) -> None:
    with pytest.raises(EditorError, match="последний"):
        remove_offer(keitaro, 1, 1)
    assert offers_of(tracker, 1) == {1: 100}


def test_update_share_rebalances_others(keitaro, tracker: FakeTracker) -> None:
    update_share(keitaro, 2, 3, 80)
    assert offers_of(tracker, 2) == {3: 80, 4: 20}


def test_update_share_clamps(keitaro, tracker: FakeTracker) -> None:
    update_share(keitaro, 2, 3, 500)
    shares = offers_of(tracker, 2)
    assert shares[3] == 99 or shares[3] == 100
    assert sum(shares.values()) == 100


def test_updated_offers_still_valid_for_keitaro(keitaro, tracker: FakeTracker) -> None:
    """Фейковый трекер валидирует сумму долей как настоящий Keitaro."""
    add_offer(keitaro, 2, 5)
    update_share(keitaro, 2, 4, 33)
    remove_offer(keitaro, 2, 5)

    shares = offers_of(tracker, 2)
    assert sum(shares.values()) == 100
    assert set(shares) == {3, 4}


def test_stats_service_returns_zero_for_unknown_offer(keitaro) -> None:
    stats = StatsService(keitaro).offer_metrics(1, "today")
    metrics = stats.for_offer(999, "Unknown")
    assert metrics.clicks == 0


def test_missing_flow_error(keitaro, tracker: FakeTracker, monkeypatch) -> None:
    """Кампания без потока с офферами даёт понятную ошибку."""
    stream_id = next(
        stream["id"]
        for stream in tracker.streams.values()
        if stream["campaign_id"] == 1 and stream["schema"] == "landings"
    )
    tracker.streams[stream_id]["offers"] = []
    tracker.streams[stream_id]["schema"] = "action"

    with pytest.raises(EditorError):
        add_offer(keitaro, 1, 5)


def test_keitaro_share_validation_is_enforced(keitaro, tracker: FakeTracker) -> None:
    """Если отправить доли с суммой ≠ 100, Keitaro ответит ошибкой."""
    stream = next(
        stream
        for stream in tracker.streams.values()
        if stream["campaign_id"] == 1 and stream["schema"] == "landings"
    )
    with pytest.raises(KeitaroValidationError):
        keitaro.update_stream(stream["id"], {"offers": [{"offer_id": 1, "share": 60}]})
