"""Синхронизация локального зеркала кампаний с Keitaro."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.keitaro.entities import KeitaroCampaign, KeitaroStream
from app.models import Campaign, CampaignOffer


def get_local_campaign(session: Session, keitaro_id: int) -> Campaign | None:
    return session.scalar(select(Campaign).where(Campaign.keitaro_id == keitaro_id))


def upsert_campaign(
    session: Session,
    campaign: KeitaroCampaign,
    *,
    geos: list[str] | None = None,
    geo_mode: str | None = None,
    redirect_url: str | None = None,
    domain_id: int | None = None,
    domain_name: str | None = None,
    group_id: int | None = None,
    group_name: str | None = None,
    source_id: int | None = None,
    source_name: str | None = None,
    redirect_flow_id: int | None = None,
    offer_flow_id: int | None = None,
) -> Campaign:
    """Создать или обновить запись зеркала. Переданные поля перезаписываются."""
    local = get_local_campaign(session, campaign.id)
    if local is None:
        local = Campaign(keitaro_id=campaign.id, name=campaign.name, alias=campaign.alias)
        session.add(local)

    local.name = campaign.name or local.name
    local.alias = campaign.alias or local.alias
    if geos is not None:
        local.geos = geos
    if geo_mode is not None:
        local.geo_mode = geo_mode
    if redirect_url is not None:
        local.redirect_url = redirect_url
    if domain_id is not None:
        local.domain_id = domain_id
    if domain_name is not None:
        local.domain_name = domain_name
    if group_id is not None:
        local.group_id = group_id
    if group_name is not None:
        local.group_name = group_name
    if source_id is not None:
        local.source_id = source_id
    if source_name is not None:
        local.source_name = source_name
    if redirect_flow_id is not None:
        local.redirect_flow_id = redirect_flow_id
    if offer_flow_id is not None:
        local.offer_flow_id = offer_flow_id

    session.commit()
    return local


def sync_offers(
    session: Session,
    local: Campaign,
    stream: KeitaroStream,
    names: dict[int, str] | None = None,
) -> None:
    """Привести зеркало офферов кампании в соответствие с потоком Keitaro."""
    names = names or {}
    current = {offer.offer_id: offer for offer in local.offers}
    actual_ids = {offer.offer_id for offer in stream.offers}

    for offer_id, row in list(current.items()):
        if offer_id not in actual_ids:
            session.delete(row)

    for position, offer in enumerate(sorted(stream.offers, key=lambda item: item.offer_id)):
        row = current.get(offer.offer_id)
        if row is None:
            row = CampaignOffer(
                campaign_id=local.id,
                offer_id=offer.offer_id,
                offer_name=names.get(offer.offer_id, f"Offer #{offer.offer_id}"),
            )
            session.add(row)
        row.share = offer.share
        row.position = position
        if names.get(offer.offer_id):
            row.offer_name = names[offer.offer_id]

    session.commit()
    session.expire(local, ["offers"])
