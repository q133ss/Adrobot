from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.deps import get_client
from app.keitaro.client import KeitaroClient
from app.keitaro.entities import KeitaroCampaign, KeitaroStream
from app.keitaro.errors import KeitaroError, KeitaroNotFound
from app.models import Campaign
from app.services.campaigns import (
    CampaignDraft,
    DraftError,
    create_campaign,
    parse_geos,
)
from app.services.catalog import Catalog
from app.services.editor import (
    EditorError,
    add_offer,
    load_view,
    refresh_local_from_view,
    remove_offer,
    update_share,
)
from app.services.mirror import get_local_campaign
from app.services.stats import (
    DEFAULT_PERIOD,
    PERIODS,
    Metrics,
    MetricsBook,
    StatsService,
    period_label,
)
from app.web.forms import repair_utf8
from app.web.templating import flash, render

router = APIRouter()


@dataclass(slots=True)
class CampaignRow:
    id: int
    name: str
    alias: str
    state: str
    geos: list[str] = field(default_factory=list)
    offers_count: int | None = None
    metrics: Metrics = field(default_factory=Metrics)
    url: str | None = None
    tracked: bool = False


@dataclass(slots=True)
class OfferRow:
    offer_id: int
    name: str
    share: int
    metrics: Metrics
    url: str | None = None


def parse_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def normalize_period(period: str | None) -> str:
    return period if period in PERIODS else DEFAULT_PERIOD


# ------------------------------------------------------------------- helpers


def _describe_stream(stream: KeitaroStream) -> dict:
    filters = []
    for stream_filter in stream.filters:
        mode = "в белом списке" if stream_filter.mode == "accept" else "в чёрном списке"
        name = "Страна" if stream_filter.name == "country" else stream_filter.name
        filters.append(f"{name} {mode}: {', '.join(stream_filter.payload)}")

    action = ""
    if isinstance(stream.action_payload, str):
        action = stream.action_payload
    elif isinstance(stream.action_payload, dict):
        action = str(
            stream.action_payload.get("url")
            or stream.action_payload.get("value")
            or stream.action_payload
        )

    return {
        "id": stream.id,
        "name": stream.name,
        "position": stream.position,
        "state": stream.state,
        "schema": stream.schema_,
        "is_offer_stream": stream.is_offer_stream,
        "filters": filters,
        "action": action,
        "offers": [offer.offer_id for offer in stream.offers],
        "action_type": stream.action_type,
    }


def _form_context(
    client: KeitaroClient,
    session: Session,
    settings: Settings,
    *,
    form: dict | None = None,
    errors: list[str] | None = None,
) -> dict:
    catalog = Catalog(client, session)
    catalog_error = None
    domains: list = []
    groups: list = []
    sources: list = []
    offers: list = []
    try:
        domains = catalog.domains()
        groups = catalog.groups()
        sources = catalog.sources()
        offers = catalog.search_offers("", limit=50)
    except KeitaroError as exc:
        catalog_error = exc.user_message

    return {
        "domains": domains,
        "groups": groups,
        "sources": sources,
        "offers": offers,
        "catalog_error": catalog_error,
        "errors": errors or [],
        "form": form or {},
        "default_geo_mode": settings.default_geo_filter_mode,
        "google_url": settings.google_redirect_url,
        "keitaro_url": settings.keitaro_base_url,
    }


# ----------------------------------------------------------------- dashboard


@router.get("/")
def dashboard(
    request: Request,
    client: KeitaroClient = Depends(get_client),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    local_campaigns = session.scalars(
        select(Campaign).order_by(Campaign.keitaro_id.desc())
    ).all()
    mirror = {campaign.keitaro_id: campaign for campaign in local_campaigns}

    api_error = None
    try:
        campaigns = client.list_campaigns()
    except KeitaroError as exc:
        api_error = exc.user_message
        campaigns = [
            KeitaroCampaign(
                id=item.keitaro_id,
                name=item.name,
                alias=item.alias,
                state="unknown",
                type="position",
            )
            for item in local_campaigns
        ]

    stats_error = None
    totals: dict[int, Metrics] = {}
    try:
        totals = StatsService(client).campaign_totals([campaign.id for campaign in campaigns])
    except KeitaroError as exc:
        stats_error = exc.user_message

    rows: list[CampaignRow] = []
    for campaign in sorted(campaigns, key=lambda item: item.id, reverse=True):
        local = mirror.get(campaign.id)
        rows.append(
            CampaignRow(
                id=campaign.id,
                name=campaign.name,
                alias=campaign.alias,
                state=campaign.state,
                geos=list(local.geos) if local else [],
                offers_count=len(local.offers) if local else None,
                metrics=totals.get(campaign.id, Metrics()),
                url=local.campaign_url if local else None,
                tracked=local is not None,
            )
        )

    summary = Metrics()
    for metrics in totals.values():
        summary = summary + metrics

    return render(
        request,
        "dashboard.html",
        {
            "rows": rows,
            "summary": summary,
            "api_error": api_error,
            "stats_error": stats_error,
            "keitaro_url": settings.keitaro_base_url,
            "active_page": "dashboard",
        },
    )


# --------------------------------------------------------------- create page


@router.get("/campaigns/new")
def new_campaign_page(
    request: Request,
    client: KeitaroClient = Depends(get_client),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    context = _form_context(client, session, settings)
    context["active_page"] = "new"
    return render(request, "campaign_new.html", context)


@router.post("/campaigns")
def create_campaign_action(
    request: Request,
    name: str = Form(""),
    geo: str = Form(""),
    offer_id: str = Form(""),
    domain_id: str = Form(""),
    group_id: str = Form(""),
    source_id: str = Form(""),
    geo_mode: str = Form("reject"),
    client: KeitaroClient = Depends(get_client),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    geos = parse_geos(repair_utf8(geo))
    name = repair_utf8(name)
    form = {
        "name": name,
        "geo": ", ".join(geos),
        "offer_id": parse_int(offer_id),
        "domain_id": parse_int(domain_id),
        "group_id": parse_int(group_id),
        "source_id": parse_int(source_id),
        "geo_mode": geo_mode,
    }
    draft = CampaignDraft(
        name=name,
        geos=geos,
        offer_id=parse_int(offer_id) or 0,
        domain_id=parse_int(domain_id),
        group_id=parse_int(group_id),
        source_id=parse_int(source_id),
        geo_mode=geo_mode,
        redirect_url=settings.google_redirect_url,
    )

    try:
        creation = create_campaign(
            client, Catalog(client, session), session, draft, settings
        )
    except DraftError as exc:
        context = _form_context(client, session, settings, form=form, errors=exc.errors)
        context["active_page"] = "new"
        return render(request, "campaign_new.html", context, status_code=400)
    except KeitaroError as exc:
        context = _form_context(
            client, session, settings, form=form, errors=[exc.user_message]
        )
        context["active_page"] = "new"
        return render(request, "campaign_new.html", context, status_code=502)

    flash(
        request,
        f"Кампания «{creation.campaign.name}» создана: "
        f"поток Google и поток с оффером #{draft.offer_id}.",
        "success",
    )
    return RedirectResponse(f"/campaigns/{creation.campaign.id}", status_code=303)


# --------------------------------------------------------------- editor page


@router.get("/campaigns/{campaign_id}")
def campaign_page(
    campaign_id: int,
    request: Request,
    period: str = DEFAULT_PERIOD,
    client: KeitaroClient = Depends(get_client),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    period = normalize_period(period)
    try:
        view = load_view(client, campaign_id)
    except KeitaroNotFound as exc:
        raise HTTPException(status_code=404, detail="Кампания не найдена в Keitaro") from exc
    except KeitaroError as exc:
        return render(
            request,
            "error.html",
            {
                "title": "Не удалось загрузить кампанию",
                "message": exc.user_message,
                "keitaro_url": settings.keitaro_base_url,
            },
            status_code=502,
        )

    catalog = Catalog(client, session)
    names: dict[int, str] = {}
    names_error = None
    try:
        names = catalog.offer_names()
    except KeitaroError as exc:
        names_error = exc.user_message

    stats = MetricsBook()
    stats_error = None
    try:
        stats = StatsService(client).offer_metrics(campaign_id, period)
    except KeitaroError as exc:
        stats_error = exc.user_message

    local = get_local_campaign(session, campaign_id)
    if local is not None:
        try:
            refresh_local_from_view(session, view, names=names)
            local = get_local_campaign(session, campaign_id)
        except Exception:
            session.rollback()

    rows: list[OfferRow] = []
    for offer in view.offers:
        offer_name = names.get(offer.offer_id, f"Offer #{offer.offer_id}")
        rows.append(
            OfferRow(
                offer_id=offer.offer_id,
                name=offer_name,
                share=offer.share,
                metrics=stats.for_offer(offer.offer_id, offer_name),
            )
        )

    total_share = sum(row.share for row in rows)
    return render(
        request,
        "campaign_detail.html",
        {
            "campaign": view.campaign,
            "streams": [_describe_stream(stream) for stream in view.streams],
            "rows": rows,
            "summary": stats.totals,
            "period": period,
            "period_label": period_label(period),
            "stats_error": stats_error,
            "names_error": names_error,
            "local": local,
            "campaign_url": local.campaign_url if local else None,
            "redirect_url": (
                local.redirect_url if local and local.redirect_url else settings.google_redirect_url
            ),
            "geos": list(local.geos) if local else [],
            "geo_mode": local.geo_mode if local else settings.default_geo_filter_mode,
            "total_share": total_share,
            "keitaro_url": settings.keitaro_base_url,
            "active_page": "dashboard",
        },
    )


@router.post("/campaigns/{campaign_id}/offers")
def add_offer_action(
    campaign_id: int,
    request: Request,
    offer_id: str = Form(""),
    share: str = Form(""),
    period: str = Form(DEFAULT_PERIOD),
    client: KeitaroClient = Depends(get_client),
    session: Session = Depends(get_session),
):
    period = normalize_period(period)
    offer_id_value = parse_int(offer_id)
    if not offer_id_value:
        flash(request, "Выберите оффер из списка.", "error")
        return RedirectResponse(f"/campaigns/{campaign_id}?period={period}", status_code=303)

    try:
        stream = add_offer(client, campaign_id, offer_id_value, share=parse_int(share))
    except (EditorError, KeitaroError) as exc:
        flash(request, _message(exc), "error")
    else:
        names = _safe_offer_names(client, session)
        _sync_quietly(session, client, campaign_id, names)
        flash(request, f"Оффер #{offer_id_value} добавлен в поток «{stream.name}».", "success")

    return RedirectResponse(f"/campaigns/{campaign_id}?period={period}", status_code=303)


@router.post("/campaigns/{campaign_id}/offers/{offer_id}/delete")
def remove_offer_action(
    campaign_id: int,
    offer_id: int,
    request: Request,
    period: str = Form(DEFAULT_PERIOD),
    client: KeitaroClient = Depends(get_client),
    session: Session = Depends(get_session),
):
    period = normalize_period(period)
    try:
        remove_offer(client, campaign_id, offer_id)
    except (EditorError, KeitaroError) as exc:
        flash(request, _message(exc), "error")
    else:
        names = _safe_offer_names(client, session)
        _sync_quietly(session, client, campaign_id, names)
        flash(request, f"Оффер #{offer_id} удалён из кампании.", "success")

    return RedirectResponse(f"/campaigns/{campaign_id}?period={period}", status_code=303)


@router.post("/campaigns/{campaign_id}/offers/{offer_id}/share")
def update_share_action(
    campaign_id: int,
    offer_id: int,
    request: Request,
    share: str = Form(""),
    period: str = Form(DEFAULT_PERIOD),
    client: KeitaroClient = Depends(get_client),
    session: Session = Depends(get_session),
):
    period = normalize_period(period)
    share_value = parse_int(share)
    if share_value is None or not 1 <= share_value <= 100:
        flash(request, "Доля должна быть целым числом от 1 до 100.", "error")
        return RedirectResponse(f"/campaigns/{campaign_id}?period={period}", status_code=303)

    try:
        update_share(client, campaign_id, offer_id, share_value)
    except (EditorError, KeitaroError) as exc:
        flash(request, _message(exc), "error")
    else:
        flash(request, f"Доля оффера #{offer_id} обновлена, остальные пересчитаны до 100%.", "success")

    return RedirectResponse(f"/campaigns/{campaign_id}?period={period}", status_code=303)


# ------------------------------------------------------------------- helpers


def _message(exc: Exception) -> str:
    if isinstance(exc, KeitaroError):
        return exc.user_message
    return str(exc)


def _safe_offer_names(client: KeitaroClient, session: Session) -> dict[int, str]:
    try:
        return Catalog(client, session).offer_names()
    except KeitaroError:
        return {}


def _sync_quietly(
    session: Session, client: KeitaroClient, campaign_id: int, names: dict[int, str]
) -> None:
    try:
        refresh_local_from_view(session, load_view(client, campaign_id), names=names)
    except Exception:
        session.rollback()
