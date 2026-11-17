from __future__ import annotations

import pytest

from app.services.campaigns import (
    CampaignDraft,
    DraftError,
    parse_geos,
    slugify,
    unique_alias,
)
from app.services.editor import (
    clamp_share,
    equal_shares,
    normalize_shares,
    rebalance_with_target,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mx, au", ["MX", "AU"]),
        (" MX ; AU ,RO ", ["MX", "AU", "RO"]),
        ("mx, MX", ["MX"]),
        ("", []),
    ],
)
def test_parse_geos(raw: str, expected: list[str]) -> None:
    assert parse_geos(raw) == expected


def test_slugify_transliterates_and_cleans() -> None:
    assert slugify("MX — Crypto Wallet — search") == "mx-crypto-wallet-search"
    assert slugify("Кампания №1") == "kampaniya-1"
    assert slugify("!!!") == "campaign"


def test_unique_alias_adds_suffix() -> None:
    assert unique_alias("mx-test", set()) == "mx-test"
    assert unique_alias("mx-test", {"mx-test"}) == "mx-test-2"
    assert unique_alias("mx-test", {"mx-test", "mx-test-2"}) == "mx-test-3"


def test_draft_validation_accumulates_errors() -> None:
    draft = CampaignDraft(
        name=" ",
        geos=["MEX", "AU"],
        offer_id=0,
        domain_id=None,
        geo_mode="whatever",
        redirect_url="google.com",
    )
    with pytest.raises(DraftError) as exc_info:
        draft.validate()
    messages = " ".join(exc_info.value.errors)
    assert "название" in messages.lower()
    assert "MEX" in messages
    assert "оффер" in messages.lower()
    assert "домен" in messages.lower()
    assert "http" in messages


def test_equal_shares_always_sum_100() -> None:
    assert equal_shares(1) == [100]
    assert equal_shares(3) == [34, 33, 33]
    assert sum(equal_shares(7)) == 100


def test_normalize_shares_keeps_ratio() -> None:
    assert normalize_shares([60, 40]) == [60, 40]
    assert normalize_shares([1]) == [100]
    assert normalize_shares([3, 1]) == [75, 25]
    assert sum(normalize_shares([5, 5, 5])) == 100
    assert normalize_shares([]) == []


def test_normalize_shares_clamps_and_rounds() -> None:
    result = normalize_shares([1000, 0, -5])
    assert sum(result) == 100
    assert all(share >= 1 for share in result)


def test_rebalance_with_target_keeps_target_and_sums_100() -> None:
    result = rebalance_with_target([50, 50], 0, 80)
    assert result[0] == 80
    assert sum(result) == 100

    result = rebalance_with_target([10, 20, 70], 1, 30)
    assert result[1] == 30
    assert sum(result) == 100

    result = rebalance_with_target([100], 0, 35)
    assert result == [100]


def test_rebalance_never_gives_zero_to_other_offers() -> None:
    result = rebalance_with_target([10, 10, 10], 0, 100)
    assert sum(result) == 100
    assert all(share >= 1 for share in result)


def test_clamp_share() -> None:
    assert clamp_share(0) == 1
    assert clamp_share(101) == 100
    assert clamp_share(42) == 42
