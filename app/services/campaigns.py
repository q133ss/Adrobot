"""Создание кампании в Keitaro: кампания + два потока (Google и оффер)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.keitaro.client import KeitaroClient
from app.keitaro.entities import KeitaroCampaign, KeitaroStream
from app.keitaro.errors import KeitaroError
from app.models import Campaign
from app.services.catalog import Catalog
from app.services.mirror import sync_offers, upsert_campaign

GEO_CODE_RE = re.compile(r"^[A-Z]{2}$")
_ALIAS_RE = re.compile(r"[^a-z0-9]+")

_TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


class DraftError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(slots=True)
class CampaignDraft:
    name: str
    geos: list[str]
    offer_id: int
    offer_name: str | None = None
    domain_id: int | None = None
    group_id: int | None = None
    source_id: int | None = None
    geo_mode: str = "reject"
    redirect_url: str = "https://www.google.com/"

    def validate(self) -> None:
        errors: list[str] = []
        if not self.name.strip():
            errors.append("Укажите название кампании")
        elif len(self.name.strip()) > 255:
            errors.append("Название кампании длиннее 255 символов")
        if not self.geos:
            errors.append("Укажите хотя бы один гео-код (например, MX)")
        invalid = [geo for geo in self.geos if not GEO_CODE_RE.match(geo)]
        if invalid:
            errors.append(f"Некорректные гео-коды: {', '.join(invalid)}")
        if not self.offer_id:
            errors.append("Выберите оффер")
        if not self.domain_id:
            errors.append("Выберите домен кампании")
        if self.geo_mode not in {"accept", "reject"}:
            errors.append("Некорректный режим гео-фильтра")
        if not self.redirect_url.startswith(("http://", "https://")):
            errors.append("URL для Google-потока должен начинаться с http:// или https://")
        if errors:
            raise DraftError(errors)


@dataclass(slots=True)
class CampaignCreation:
    campaign: KeitaroCampaign
    redirect_stream: KeitaroStream
    offer_stream: KeitaroStream
    local: Campaign
    alias: str
    action_types: dict[str, str] = field(default_factory=dict)


def parse_geos(raw: str | list[str]) -> list[str]:
    """'mx, au;RO' → ['MX', 'AU', 'RO'] без дублей, в порядке ввода."""
    parts = raw if isinstance(raw, list) else re.split(r"[,\s;]+", raw)
    result: list[str] = []
    for part in parts:
        code = part.strip().upper()
        if code and code not in result:
            result.append(code)
    return result


def slugify(value: str, *, max_length: int = 48) -> str:
    text = value.strip().lower()
    text = "".join(_TRANSLIT.get(char, char) for char in text)
    text = _ALIAS_RE.sub("-", text).strip("-")
    return (text[:max_length].strip("-") or "campaign")


def unique_alias(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    for index in range(2, 1000):
        candidate = f"{base}-{index}"
        if candidate not in taken:
            return candidate
    raise RuntimeError("Не удалось подобрать уникальный alias")


def build_campaign_payload(draft: CampaignDraft, alias: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": draft.name.strip(),
        "alias": alias,
        "type": "position",
        "state": "active",
        "cost_type": "CPC",
        "cost_value": 0,
        "cookies_ttl": 24,
    }
    if draft.domain_id:
        payload["domain_id"] = draft.domain_id
    if draft.group_id:
        payload["group_id"] = draft.group_id
    if draft.source_id:
        payload["traffic_source_id"] = draft.source_id
    return payload


def build_redirect_stream_payload(
    draft: CampaignDraft,
    *,
    campaign_id: int,
    action_type: str,
    position: int = 1,
) -> dict[str, Any]:
    filters: list[dict[str, Any]] = []
    if draft.geos:
        filters.append(
            {
                "name": "country",
                "mode": draft.geo_mode,
                "payload": draft.geos,
            }
        )
    label = ", ".join(draft.geos)
    return {
        "campaign_id": campaign_id,
        "name": f"Google ({label})",
        "type": "regular",
        "schema": "redirect",
        "action_type": action_type,
        "action_payload": draft.redirect_url,
        "position": position,
        "state": "active",
        "collect_clicks": True,
        "filter_or": False,
        "filters": filters,
    }


def build_offer_stream_payload(
    draft: CampaignDraft,
    *,
    campaign_id: int,
    action_type: str,
    position: int = 2,
) -> dict[str, Any]:
    return {
        "campaign_id": campaign_id,
        "name": "Offers",
        "type": "regular",
        "schema": "landings",
        "action_type": action_type,
        "position": position,
        "state": "active",
        "collect_clicks": True,
        "offers": [
            {
                "offer_id": draft.offer_id,
                "share": 100,
                "state": "active",
            }
        ],
    }


def create_campaign(
    client: KeitaroClient,
    catalog: Catalog,
    session: Session,
    draft: CampaignDraft,
    settings: Settings,
) -> CampaignCreation:
    """Создать кампанию и два потока.

    Если второй поток не создался, откатываем всё созданное: в Keitaro не
    должно оставаться полуфабрикатов.
    """
    draft.validate()

    existing_aliases = {campaign.alias for campaign in client.list_campaigns() if campaign.alias}
    alias = unique_alias(slugify(draft.name), existing_aliases)

    domain_name = None
    group_name = None
    source_name = None
    if draft.domain_id:
        domain = next((item for item in catalog.domains() if item.id == draft.domain_id), None)
        domain_name = domain.url if domain else None
    if draft.group_id:
        group = next((item for item in catalog.groups() if item.id == draft.group_id), None)
        group_name = group.name if group else None
    if draft.source_id:
        source = next((item for item in catalog.sources() if item.id == draft.source_id), None)
        source_name = source.name if source else None

    redirect_action = client.resolve_action_type(
        preferred="redirect", contains=("redirect",), fallback="redirect"
    )
    offers_action = client.resolve_action_type(
        preferred="show_landing",
        contains=("landing", "offer"),
        fallback="show_landing",
    )

    campaign = client.create_campaign(build_campaign_payload(draft, alias))

    created_streams: list[int] = []
    try:
        redirect_stream = client.create_stream(
            build_redirect_stream_payload(draft, campaign_id=campaign.id, action_type=redirect_action)
        )
        created_streams.append(redirect_stream.id)
        offer_stream = client.create_stream(
            build_offer_stream_payload(draft, campaign_id=campaign.id, action_type=offers_action)
        )
        created_streams.append(offer_stream.id)
    except KeitaroError as exc:
        client.delete_campaign(campaign.id)
        raise KeitaroError(
            f"Не удалось создать потоки кампании #{campaign.id}: {exc.message}. "
            "Кампания удалена, повторите попытку.",
            status=exc.status,
            payload=exc.payload,
        ) from exc

    local = upsert_campaign(
        session,
        campaign,
        geos=draft.geos,
        geo_mode=draft.geo_mode,
        redirect_url=draft.redirect_url,
        domain_id=draft.domain_id,
        domain_name=domain_name,
        group_id=draft.group_id,
        group_name=group_name,
        source_id=draft.source_id,
        source_name=source_name,
        redirect_flow_id=redirect_stream.id,
        offer_flow_id=offer_stream.id,
    )

    names: dict[int, str] = {}
    if draft.offer_name:
        names[draft.offer_id] = draft.offer_name
    else:
        offer = next((item for item in catalog.offers() if item.id == draft.offer_id), None)
        if offer is not None:
            names[offer.id] = offer.name
    sync_offers(session, local, offer_stream, names)

    return CampaignCreation(
        campaign=campaign,
        redirect_stream=redirect_stream,
        offer_stream=offer_stream,
        local=local,
        alias=alias,
        action_types={"redirect": redirect_action, "offers": offers_action},
    )
