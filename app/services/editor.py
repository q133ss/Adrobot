"""Редактор кампании: добавление/удаление офферов и распределение трафика."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.keitaro.client import KeitaroClient
from app.keitaro.entities import KeitaroCampaign, KeitaroStream, StreamOffer
from app.models import Campaign
from app.services.mirror import sync_offers, upsert_campaign


class EditorError(Exception):
    """Понятная пользователю ошибка редактирования."""


@dataclass(slots=True)
class CampaignView:
    campaign: KeitaroCampaign
    streams: list[KeitaroStream]
    offer_stream: KeitaroStream | None = None
    redirect_stream: KeitaroStream | None = None
    offers: list[StreamOffer] = field(default_factory=list)

    def offer_ids(self) -> list[int]:
        return [offer.offer_id for offer in self.offers]


def find_offer_stream(streams: list[KeitaroStream]) -> KeitaroStream | None:
    for stream in streams:
        if stream.offers:
            return stream
    for stream in streams:
        if stream.schema_ == "landings":
            return stream
    return None


def find_redirect_stream(streams: list[KeitaroStream]) -> KeitaroStream | None:
    for stream in streams:
        if stream.schema_ == "redirect":
            return stream
    return None


def load_view(client: KeitaroClient, campaign_id: int) -> CampaignView:
    campaign = client.get_campaign(campaign_id)
    streams = sorted(client.list_streams(campaign_id), key=lambda item: (item.position, item.id))
    offer_stream = find_offer_stream(streams)
    offers = sorted(offer_stream.offers, key=lambda item: item.offer_id) if offer_stream else []
    return CampaignView(
        campaign=campaign,
        streams=streams,
        offer_stream=offer_stream,
        redirect_stream=find_redirect_stream(streams),
        offers=offers,
    )


# --------------------------------------------------------------- shares math


def clamp_share(value: int) -> int:
    return max(1, min(100, int(value)))


def equal_shares(count: int) -> list[int]:
    if count <= 0:
        return []
    base, remainder = divmod(100, count)
    return [base + (1 if index < remainder else 0) for index in range(count)]


def normalize_shares(values: list[int]) -> list[int]:
    """Привести доли к сумме 100, сохранив пропорции (минимум 1)."""
    if not values:
        return []
    shares = [clamp_share(value) for value in values]
    total = sum(shares)
    if total == 100:
        return shares
    if len(shares) == 1:
        return [100]

    raw = [share * 100 / total for share in shares]
    result = [int(value) for value in raw]
    deficit = 100 - sum(result)
    order = sorted(range(len(raw)), key=lambda index: raw[index] - result[index], reverse=True)
    for index in order[:deficit]:
        result[index] += 1
    # страховка от отрицательного остатка из-за округления
    while sum(result) > 100:
        index = max(range(len(result)), key=lambda position: result[position])
        result[index] -= 1
    return result


def rebalance_with_target(values: list[int], index: int, target: int) -> list[int]:
    """Поставить одному офферу точную долю, остальным — пропорционально остаток."""
    if not values:
        return []
    if len(values) == 1:
        return [100]

    target = clamp_share(target)
    others = [position for position in range(len(values)) if position != index]
    remaining = 100 - target
    if remaining < len(others):
        remaining = len(others)
        target = 100 - remaining

    current_others = [max(clamp_share(values[position]), 1) for position in others]
    total_others = sum(current_others)
    raw = [value * remaining / total_others for value in current_others]
    distributed = [max(int(value), 1) for value in raw]
    deficit = remaining - sum(distributed)
    order = sorted(
        range(len(raw)), key=lambda position: raw[position] - distributed[position], reverse=True
    )
    position = 0
    while deficit > 0:
        distributed[order[position % len(order)]] += 1
        deficit -= 1
        position += 1
    while deficit < 0:
        position = max(range(len(distributed)), key=lambda item: distributed[item])
        distributed[position] -= 1
        deficit += 1

    result = [0] * len(values)
    result[index] = target
    for position, value in zip(others, distributed, strict=False):
        result[position] = value
    return result


# ------------------------------------------------------------------- actions


def _offers_payload(offers: list[tuple[int, int]]) -> list[dict]:
    return [
        {"offer_id": offer_id, "share": share, "state": "active"}
        for offer_id, share in offers
    ]


def _push_offers(
    client: KeitaroClient, stream: KeitaroStream, offers: list[tuple[int, int]]
) -> KeitaroStream:
    return client.update_stream(stream.id, {"offers": _offers_payload(offers)})


def add_offer(
    client: KeitaroClient,
    campaign_id: int,
    offer_id: int,
    *,
    share: int | None = None,
) -> KeitaroStream:
    view = load_view(client, campaign_id)
    stream = view.offer_stream
    if stream is None:
        raise EditorError("В кампании нет потока с офферами — добавьте его в Keitaro.")

    existing = [(offer.offer_id, offer.share) for offer in view.offers]
    if any(current_id == offer_id for current_id, _ in existing):
        raise EditorError(f"Оффер #{offer_id} уже есть в кампании.")

    if share is None:
        shares = equal_shares(len(existing) + 1)
        offers = [(current_id, shares[index]) for index, (current_id, _) in enumerate(existing)]
        offers.append((offer_id, shares[-1]))
    else:
        offers = [*existing, (offer_id, clamp_share(share))]
        normalized = normalize_shares([current_share for _, current_share in offers])
        offers = [
            (current_id, new_share)
            for (current_id, _), new_share in zip(offers, normalized, strict=False)
        ]

    return _push_offers(client, stream, offers)


def remove_offer(client: KeitaroClient, campaign_id: int, offer_id: int) -> KeitaroStream:
    view = load_view(client, campaign_id)
    stream = view.offer_stream
    if stream is None:
        raise EditorError("В кампании нет потока с офферами.")

    remaining = [offer.offer_id for offer in view.offers if offer.offer_id != offer_id]
    if len(remaining) == len(view.offers):
        raise EditorError(f"Оффер #{offer_id} не найден в кампании.")
    if not remaining:
        raise EditorError(
            "Нельзя удалить последний оффер: кампания останется без трафика. "
            "Сначала добавьте другой оффер."
        )

    shares = equal_shares(len(remaining))
    return _push_offers(client, stream, list(zip(remaining, shares, strict=False)))


def update_share(
    client: KeitaroClient, campaign_id: int, offer_id: int, share: int
) -> KeitaroStream:
    view = load_view(client, campaign_id)
    stream = view.offer_stream
    if stream is None:
        raise EditorError("В кампании нет потока с офферами.")

    ids = [offer.offer_id for offer in view.offers]
    if offer_id not in ids:
        raise EditorError(f"Оффер #{offer_id} не найден в кампании.")
    index = ids.index(offer_id)
    values = [offer.share for offer in view.offers]
    shares = rebalance_with_target(values, index, share)
    return _push_offers(client, stream, list(zip(ids, shares, strict=False)))


# ------------------------------------------------------------------ mirroring


def refresh_local_from_view(
    session: Session,
    view: CampaignView,
    *,
    names: dict[int, str] | None = None,
    geos: list[str] | None = None,
    geo_mode: str | None = None,
) -> Campaign:
    local = upsert_campaign(
        session,
        view.campaign,
        geos=geos,
        geo_mode=geo_mode,
        redirect_flow_id=view.redirect_stream.id if view.redirect_stream else None,
        offer_flow_id=view.offer_stream.id if view.offer_stream else None,
    )
    if view.offer_stream is not None:
        sync_offers(session, local, view.offer_stream, names)
    return local


def refresh_local(
    session: Session,
    client: KeitaroClient,
    campaign_id: int,
    *,
    names: dict[int, str] | None = None,
    geos: list[str] | None = None,
    geo_mode: str | None = None,
) -> Campaign:
    """Обновить локальное зеркало после изменения кампании в Keitaro."""
    view = load_view(client, campaign_id)
    return refresh_local_from_view(
        session, view, names=names, geos=geos, geo_mode=geo_mode
    )
