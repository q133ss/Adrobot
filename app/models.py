from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Campaign(Base):
    """Локальное зеркало кампаний, созданных/изменённых через Adrobot.

    Источник правды — Keitaro; здесь храним то, что нужно для дашборда,
    истории и восстановления связей кампания ↔ потоки.
    """

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    keitaro_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    alias: Mapped[str] = mapped_column(String(255), index=True)
    geos: Mapped[list[str]] = mapped_column(JSON, default=list)
    geo_mode: Mapped[str] = mapped_column(String(16), default="reject")
    redirect_url: Mapped[str] = mapped_column(String(2048), default="")

    domain_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    domain_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    redirect_flow_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offer_flow_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    offers: Mapped[list[CampaignOffer]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="CampaignOffer.position",
    )

    @property
    def campaign_url(self) -> str | None:
        if not self.domain_name:
            return None
        return f"{self.domain_name.rstrip('/')}/{self.alias}"


class CampaignOffer(Base):
    """Зеркало офферов внутри кампании (для количества/состава офферов в UI)."""

    __tablename__ = "campaign_offers"
    __table_args__ = (UniqueConstraint("campaign_id", "offer_id", name="uq_campaign_offer"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    offer_id: Mapped[int] = mapped_column(Integer)
    offer_name: Mapped[str] = mapped_column(String(255), default="")
    share: Mapped[int] = mapped_column(Integer, default=100)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    campaign: Mapped[Campaign] = relationship(back_populates="offers")


class ApiCall(Base):
    """Журнал запросов в Keitaro — помогает разбирать инциденты."""

    __tablename__ = "api_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(512))
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    request_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class ReferenceCache(Base):
    """Кэш справочников Keitaro (домены, группы, источники, офферы)."""

    __tablename__ = "reference_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[list] = mapped_column(JSON, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
