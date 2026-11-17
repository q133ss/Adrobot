from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class APIModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class StreamOffer(APIModel):
    offer_id: int
    share: int = 0
    state: str = "active"


class StreamFilter(APIModel):
    id: int | None = None
    name: str = ""
    mode: str = "accept"
    payload: list[str] = []

    @field_validator("payload", mode="before")
    @classmethod
    def _as_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return [str(item) for item in value]


class KeitaroStream(APIModel):
    id: int
    campaign_id: int | None = None
    name: str = ""
    type: str = "regular"
    schema_: str = Field("", alias="schema")
    position: int = 0
    weight: float = 0
    state: str = "active"
    action_type: str = ""
    action_payload: Any = None
    collect_clicks: bool = False
    filter_or: bool = False
    filters: list[StreamFilter] = []
    offers: list[StreamOffer] = []

    @property
    def is_offer_stream(self) -> bool:
        return bool(self.offers) or self.schema_ == "landings"

    @property
    def is_redirect_stream(self) -> bool:
        return self.schema_ == "redirect"


class KeitaroCampaign(APIModel):
    id: int
    name: str = ""
    alias: str = ""
    state: str = "active"
    type: str = "position"
    group_id: int | None = None
    traffic_source_id: int | None = None
    cost_type: str = "CPC"
    cost_value: float = 0
    token: str = ""
    created_at: str | None = None

    @property
    def is_active(self) -> bool:
        return self.state == "active"


class KeitaroOffer(APIModel):
    id: int
    name: str = ""
    group_id: int | None = None
    state: str = "active"
    country: list[str] = []
    action_payload: Any = None

    @field_validator("country", mode="before")
    @classmethod
    def _as_country_list(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return [str(item) for item in value]


class KeitaroDomain(APIModel):
    id: int
    name: str = ""
    state: str = "active"

    @property
    def url(self) -> str:
        if self.name.startswith(("http://", "https://")):
            return self.name.rstrip("/")
        return f"https://{self.name.rstrip('/')}"


class KeitaroGroup(APIModel):
    id: int
    name: str = ""
    type: str = "campaigns"


class KeitaroSource(APIModel):
    id: int
    name: str = ""
    state: str = "active"


class StreamAction(APIModel):
    key: str = ""
    name: str = ""
    type: str = ""
