from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.deps import get_client
from app.keitaro.client import KeitaroClient
from app.keitaro.errors import KeitaroError
from app.services.catalog import Catalog
from app.services.stats import StatsService
from app.web.routes import normalize_period

router = APIRouter(prefix="/api")


@router.get("/offers")
def search_offers(
    q: str = "",
    limit: int = Query(25, ge=1, le=100),
    client: KeitaroClient = Depends(get_client),
    session: Session = Depends(get_session),
):
    catalog = Catalog(client, session)
    try:
        offers = catalog.search_offers(q, limit=limit)
    except KeitaroError as exc:
        return JSONResponse(
            {"offers": [], "error": exc.user_message},
            status_code=502,
        )
    return {
        "offers": [
            {"id": offer.id, "name": offer.name, "country": offer.country}
            for offer in offers
        ]
    }


@router.get("/campaigns/{campaign_id}/stats")
def campaign_stats(
    campaign_id: int,
    period: str = "today",
    client: KeitaroClient = Depends(get_client),
):
    period = normalize_period(period)
    try:
        book = StatsService(client).offer_metrics(campaign_id, period)
    except KeitaroError as exc:
        return JSONResponse({"error": exc.user_message}, status_code=502)

    def dump(metrics) -> dict:
        return {
            "clicks": metrics.clicks,
            "leads": metrics.leads,
            "conversions": metrics.conversions,
            "revenue": round(metrics.revenue, 2),
            "cost": round(metrics.cost, 2),
            "profit": round(metrics.profit, 2),
            "cr": round(metrics.cr, 2),
            "roi": round(metrics.roi, 2),
            "epc": round(metrics.epc, 3),
        }

    return {
        "period": period,
        "offers": {str(offer_id): dump(metrics) for offer_id, metrics in book.by_id.items()},
        "by_name": {name: dump(metrics) for name, metrics in book.by_name.items()},
        "totals": dump(book.totals),
    }


@router.get("/health")
def health(
    client: KeitaroClient = Depends(get_client),
    settings: Settings = Depends(get_settings),
):
    ok = client.ping()
    return JSONResponse(
        {
            "keitaro": "ok" if ok else "unreachable",
            "base_url": settings.keitaro_base_url,
            "demo": settings.keitaro_demo,
        },
        status_code=200 if ok else 503,
    )
