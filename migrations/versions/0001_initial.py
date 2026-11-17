"""initial schema: campaigns, offers mirror, api log, reference cache

Revision ID: 0001
Revises:
Create Date: 2026-11-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("keitaro_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("geos", sa.JSON(), nullable=False),
        sa.Column("geo_mode", sa.String(length=16), nullable=False),
        sa.Column("redirect_url", sa.String(length=2048), nullable=False),
        sa.Column("domain_id", sa.Integer(), nullable=True),
        sa.Column("domain_name", sa.String(length=255), nullable=True),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("group_name", sa.String(length=255), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("source_name", sa.String(length=255), nullable=True),
        sa.Column("redirect_flow_id", sa.Integer(), nullable=True),
        sa.Column("offer_flow_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_campaigns_keitaro_id", "campaigns", ["keitaro_id"], unique=True)
    op.create_index("ix_campaigns_alias", "campaigns", ["alias"], unique=False)

    op.create_table(
        "campaign_offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("offer_name", sa.String(length=255), nullable=False),
        sa.Column("share", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("campaign_id", "offer_id", name="uq_campaign_offer"),
    )
    op.create_index("ix_campaign_offers_campaign_id", "campaign_offers", ["campaign_id"])

    op.create_table(
        "api_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=True),
        sa.Column("request_body", sa.Text(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_api_calls_campaign_id", "api_calls", ["campaign_id"])
    op.create_index("ix_api_calls_created_at", "api_calls", ["created_at"])

    op.create_table(
        "reference_cache",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("reference_cache")
    op.drop_index("ix_api_calls_created_at", table_name="api_calls")
    op.drop_index("ix_api_calls_campaign_id", table_name="api_calls")
    op.drop_table("api_calls")
    op.drop_index("ix_campaign_offers_campaign_id", table_name="campaign_offers")
    op.drop_table("campaign_offers")
    op.drop_index("ix_campaigns_alias", table_name="campaigns")
    op.drop_index("ix_campaigns_keitaro_id", table_name="campaigns")
    op.drop_table("campaigns")
