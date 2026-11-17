"""Справочники Keitaro с кэшем в БД.

Домены/группы/источники/офферы меняются редко, а нужны на каждом форме.
Кэш ускоряет UI и позволяет показать список, даже если трекер временно лежит
(используются устаревшие данные).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from sqlalchemy.orm import Session

from app.config import get_settings
from app.keitaro.client import KeitaroClient
from app.keitaro.entities import APIModel, KeitaroDomain, KeitaroGroup, KeitaroOffer, KeitaroSource
from app.models import ReferenceCache

T = TypeVar("T", bound=APIModel)

class Catalog:
    def __init__(
        self,
        client: KeitaroClient,
        session: Session,
        *,
        ttl: timedelta | None = None,
    ) -> None:
        self._client = client
        self._session = session
        self._ttl = ttl or timedelta(seconds=get_settings().cache_ttl_seconds)

    def domains(self, *, refresh: bool = False) -> list[KeitaroDomain]:
        return self._load("domains", self._client.list_domains, KeitaroDomain, refresh)

    def groups(self, *, refresh: bool = False) -> list[KeitaroGroup]:
        return self._load("groups", self._client.list_groups, KeitaroGroup, refresh)

    def sources(self, *, refresh: bool = False) -> list[KeitaroSource]:
        return self._load("sources", self._client.list_sources, KeitaroSource, refresh)

    def offers(self, *, refresh: bool = False) -> list[KeitaroOffer]:
        return self._load("offers", self._client.list_offers, KeitaroOffer, refresh)

    def search_offers(self, query: str = "", *, limit: int = 25) -> list[KeitaroOffer]:
        offers = [offer for offer in self.offers() if offer.state != "deleted"]
        query = query.strip().lower()
        if query:
            offers = [
                offer
                for offer in offers
                if query in offer.name.lower() or query == str(offer.id)
            ]
        offers.sort(key=lambda offer: offer.name.lower())
        return offers[:limit]

    def offer_names(self) -> dict[int, str]:
        return {offer.id: offer.name for offer in self.offers()}

    def invalidate(self, *keys: str) -> None:
        for key in keys or ("domains", "groups", "sources", "offers"):
            row = self._session.get(ReferenceCache, key)
            if row is not None:
                self._session.delete(row)
        self._session.commit()

    # ------------------------------------------------------------------ infra

    def _load(
        self,
        key: str,
        loader: Callable[[], list[T]],
        model: type[T],
        refresh: bool,
    ) -> list[T]:
        row = self._session.get(ReferenceCache, key)
        cached = self._parse(row, model)
        if row is not None and not refresh and _is_fresh(row.fetched_at, self._ttl):
            return cached

        try:
            fetched = loader()
        except Exception:  # лучше отдать устаревший кэш, чем упасть
            if cached:
                return cached
            raise

        self._store(key, [item.model_dump() for item in fetched])
        return fetched

    def _parse(self, row: ReferenceCache | None, model: type[T]) -> list[T]:
        if row is None:
            return []
        try:
            return [model.model_validate(item) for item in row.payload]
        except Exception:
            return []

    def _store(self, key: str, payload: list[dict]) -> None:
        row = self._session.get(ReferenceCache, key)
        if row is None:
            row = ReferenceCache(key=key, payload=payload, fetched_at=_now())
            self._session.add(row)
        else:
            row.payload = payload
            row.fetched_at = _now()
        self._session.commit()


def _now() -> datetime:
    return datetime.now(UTC)


def _is_fresh(fetched_at: datetime, ttl: timedelta) -> bool:
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - fetched_at < ttl
