"""Фейковый Keitaro Admin API для локальной демонстрации и тестов.

Реализует ровно тот срез API, который использует Adrobot:
кампании, потоки, офферы, справочники, /report/build.
Схемы повторяют https://admin-api.docs.keitaro.io
"""

from __future__ import annotations

import copy
import itertools
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_API_KEY = "demo-api-key"
# Как в реальном Keitaro: отдельного show_landing в /streams_actions нет,
# редирект — это ключ "http"; поток с офферами тоже принимает "http".
STREAM_ACTIONS = [
    {"key": "http", "name": "HttpRedirect", "type": "redirect"},
    {"key": "meta", "name": "Meta", "type": "redirect"},
    {"key": "show_text", "name": "ShowText", "type": "other"},
]

_CAMPAIGN_STREAMS_RE = re.compile(r"^/campaigns/(\d+)/streams$")
_CAMPAIGN_RE = re.compile(r"^/campaigns/(\d+)$")
_STREAM_RE = re.compile(r"^/streams/(\d+)$")


def _seed() -> dict[str, Any]:
    return {
        "domains": [
            {"id": 1, "name": "https://demo-tracker.xyz", "state": "active"},
            {"id": 2, "name": "https://go.demo-cdn.net", "state": "active"},
        ],
        "groups": [
            {"id": 1, "name": "FORTS", "type": "campaigns", "position": 1},
            {"id": 2, "name": "SEARCH", "type": "campaigns", "position": 2},
            {"id": 3, "name": "FORTS", "type": "offers", "position": 3},
        ],
        "sources": [
            {"id": 1, "name": "FORTS", "state": "active"},
            {"id": 2, "name": "Google Ads", "state": "active"},
        ],
        "offers": [
            {"id": 1, "name": "Crypto Wallet — MX", "country": ["MX"], "state": "active"},
            {"id": 2, "name": "Crypto Wallet — AU", "country": ["AU"], "state": "active"},
            {"id": 3, "name": "Antivirus 2026 — AU", "country": ["AU"], "state": "active"},
            {"id": 4, "name": "Antivirus 2026 — RO", "country": ["RO"], "state": "active"},
            {"id": 5, "name": "Dating Smartlink — Global", "country": [], "state": "active"},
            {"id": 6, "name": "Loan App — MX", "country": ["MX"], "state": "active"},
            {"id": 7, "name": "VPN Trial — DE", "country": ["DE"], "state": "active"},
        ],
    }


@dataclass
class FakeTracker:
    """In-memory трекер. Состояние живёт, пока жив объект."""

    api_key: str = DEFAULT_API_KEY
    data: dict[str, Any] = field(default_factory=_seed)
    campaigns: dict[int, dict] = field(default_factory=dict)
    streams: dict[int, dict] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    _counters: dict[str, itertools.count] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._counters = {
            "campaigns": itertools.count(1),
            "streams": itertools.count(1),
        }
        self._seed_campaigns()

    # ------------------------------------------------------------------ seed

    def _seed_campaigns(self) -> None:
        self.campaigns[1] = {
            "id": 1,
            "name": "MX — Crypto Wallet — search",
            "alias": "mx-crypto-wallet-search",
            "type": "position",
            "state": "active",
            "group_id": 1,
            "traffic_source_id": 1,
            "cost_type": "CPC",
            "cost_value": 0,
            "token": "tok_demo_1",
        }
        self.campaigns[2] = {
            "id": 2,
            "name": "AU — Antivirus — push",
            "alias": "au-antivirus-push",
            "type": "position",
            "state": "active",
            "group_id": 2,
            "traffic_source_id": 2,
            "cost_type": "CPC",
            "cost_value": 0,
            "token": "tok_demo_2",
        }
        next(self._counters["campaigns"])
        next(self._counters["campaigns"])

        self._add_stream(
            {
                "campaign_id": 1,
                "name": "Google (MX)",
                "type": "regular",
                "schema": "redirect",
                "action_type": "redirect",
                "action_payload": "https://www.google.com/",
                "position": 1,
                "state": "active",
                "collect_clicks": True,
                "filters": [{"name": "country", "mode": "reject", "payload": ["MX"]}],
            }
        )
        self._add_stream(
            {
                "campaign_id": 1,
                "name": "Offers",
                "type": "regular",
                "schema": "landings",
                "action_type": "show_landing",
                "position": 2,
                "state": "active",
                "collect_clicks": True,
                "offers": [{"offer_id": 1, "share": 100, "state": "active"}],
            }
        )
        self._add_stream(
            {
                "campaign_id": 2,
                "name": "Google (AU)",
                "type": "regular",
                "schema": "redirect",
                "action_type": "redirect",
                "action_payload": "https://www.google.com/",
                "position": 1,
                "state": "active",
                "collect_clicks": True,
                "filters": [{"name": "country", "mode": "reject", "payload": ["AU"]}],
            }
        )
        self._add_stream(
            {
                "campaign_id": 2,
                "name": "Offers",
                "type": "regular",
                "schema": "landings",
                "action_type": "show_landing",
                "position": 2,
                "state": "active",
                "collect_clicks": True,
                "offers": [
                    {"offer_id": 3, "share": 60, "state": "active"},
                    {"offer_id": 4, "share": 40, "state": "active"},
                ],
            }
        )

    # ---------------------------------------------------------------- routing

    def handle(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        headers = headers or {}
        self.calls.append((method, path))

        normalized = {key.lower(): value for key, value in headers.items()}
        api_key = normalized.get("api-key") or _bearer(normalized.get("authorization"))
        if api_key != self.api_key:
            return 401, {"error": "Invalid API key"}

        path, _, query = path.partition("?")

        if path == "/streams_actions":
            return 200, copy.deepcopy(STREAM_ACTIONS)
        if path == "/offers" and method == "GET":
            return 200, copy.deepcopy(self.data["offers"])
        if path == "/domains" and method == "GET":
            return 200, copy.deepcopy(self.data["domains"])
        if path == "/groups" and method == "GET":
            if "type=" not in query:
                return 400, {"error": "Param 'type' is required"}
            return 200, copy.deepcopy(self.data["groups"])
        if path == "/traffic_sources" and method == "GET":
            return 200, copy.deepcopy(self.data["sources"])

        if path == "/campaigns":
            return self._campaigns_collection(method, body)

        match = _CAMPAIGN_STREAMS_RE.match(path)
        if match:
            campaign_id = int(match.group(1))
            if campaign_id not in self.campaigns:
                return 404, {"error": "Campaign not found"}
            return 200, copy.deepcopy(
                [stream for stream in self.streams.values() if stream["campaign_id"] == campaign_id]
            )

        match = _CAMPAIGN_RE.match(path)
        if match:
            return self._campaign_item(method, int(match.group(1)), body)

        match = _STREAM_RE.match(path)
        if match:
            return self._stream_item(method, int(match.group(1)), body)

        if path == "/streams" and method == "POST":
            return self._create_stream(body or {})

        if path == "/report/build" and method == "POST":
            return 200, self._report(body or {})

        return 404, {"error": f"Unknown endpoint {method} {path}"}

    # -------------------------------------------------------------- campaigns

    def _campaigns_collection(self, method: str, body: Any) -> tuple[int, Any]:
        if method == "GET":
            return 200, copy.deepcopy(sorted(self.campaigns.values(), key=lambda item: item["id"]))
        if method == "POST":
            payload = body or {}
            for field_name in ("name", "alias"):
                if not payload.get(field_name):
                    return 400, {"error": f"Field '{field_name}' is required"}
            if any(item["alias"] == payload["alias"] for item in self.campaigns.values()):
                return 400, {"error": f"Campaign with alias '{payload['alias']}' already exists"}
            if any(item["name"] == payload["name"] for item in self.campaigns.values()):
                return 422, {"name": ["Name has already used"]}
            campaign = {
                "id": next(self._counters["campaigns"]),
                "name": payload["name"],
                "alias": payload["alias"],
                "type": payload.get("type", "position"),
                "state": payload.get("state", "active"),
                "group_id": payload.get("group_id"),
                "traffic_source_id": payload.get("traffic_source_id"),
                "cost_type": payload.get("cost_type", "CPC"),
                "cost_value": payload.get("cost_value", 0),
                "token": f"tok_{payload['alias']}",
            }
            self.campaigns[campaign["id"]] = campaign
            return 200, copy.deepcopy(campaign)
        return 405, {"error": "Method not allowed"}

    def _campaign_item(self, method: str, campaign_id: int, body: Any) -> tuple[int, Any]:
        campaign = self.campaigns.get(campaign_id)
        if campaign is None:
            return 404, {"error": "Campaign not found"}
        if method == "GET":
            return 200, copy.deepcopy(campaign)
        if method == "PUT":
            campaign.update(body or {})
            return 200, copy.deepcopy(campaign)
        if method == "DELETE":
            del self.campaigns[campaign_id]
            for stream_id in [sid for sid, s in self.streams.items() if s["campaign_id"] == campaign_id]:
                del self.streams[stream_id]
            return 200, {}
        return 405, {"error": "Method not allowed"}

    # ---------------------------------------------------------------- streams

    def _add_stream(self, payload: dict) -> dict:
        stream = {
            "id": next(self._counters["streams"]),
            "state": "active",
            "weight": 0,
            "filters": [],
            "offers": [],
            "collect_clicks": False,
            "filter_or": False,
            **copy.deepcopy(payload),
        }
        self.streams[stream["id"]] = stream
        return stream

    def _validate_stream(self, payload: dict) -> str | None:
        for field_name in ("campaign_id", "schema", "type", "name", "action_type"):
            if field_name not in payload or payload[field_name] in (None, ""):
                return f"Field '{field_name}' is required"
        if int(payload["campaign_id"]) not in self.campaigns:
            return "Campaign not found"
        if payload["schema"] == "landings":
            offers = payload.get("offers") or []
            if not offers:
                return "Flow with schema 'landings' requires at least one offer"
            if sum(int(offer.get("share", 0)) for offer in offers) != 100:
                return "Offers shares must sum up to 100"
        return None

    def _create_stream(self, payload: dict) -> tuple[int, Any]:
        error = self._validate_stream(payload)
        if error:
            return 400, {"error": error}
        stream = self._add_stream(payload)
        return 200, copy.deepcopy(stream)

    def _stream_item(self, method: str, stream_id: int, body: Any) -> tuple[int, Any]:
        stream = self.streams.get(stream_id)
        if stream is None:
            return 404, {"error": "Flow not found"}
        if method == "GET":
            return 200, copy.deepcopy(stream)
        if method == "PUT":
            merged = {**stream, **(body or {})}
            error = self._validate_stream(merged)
            if error:
                return 400, {"error": error}
            stream.update(body or {})
            return 200, copy.deepcopy(stream)
        if method == "DELETE":
            del self.streams[stream_id]
            return 200, {}
        return 405, {"error": "Method not allowed"}

    # ---------------------------------------------------------------- reports

    def _report(self, payload: dict) -> dict:
        dimensions: list[str] = payload.get("dimensions") or []
        metrics: list[str] = payload.get("metrics") or payload.get("measures") or []
        filters = _normalize_filters(payload.get("filters") or [])
        range_ = payload.get("range") or {}
        days = _range_days(range_)

        records: list[dict[str, Any]] = []
        for stream in self.streams.values():
            campaign_id = stream["campaign_id"]
            if not _matches(filters, {"campaign_id": campaign_id, "stream_id": stream["id"]}):
                continue
            for offer in stream.get("offers") or []:
                values = {
                    **_numbers(campaign_id, stream["id"], int(offer["offer_id"]), days),
                    "campaign_id": campaign_id,
                    "stream_id": stream["id"],
                    "offer_id": int(offer["offer_id"]),
                    "offer": self._offer_name(int(offer["offer_id"])),
                    "stream": stream["name"],
                    "campaign": self.campaigns[campaign_id]["name"],
                }
                if not _matches(filters, values):
                    continue
                records.append(values)

        meta = [{"name": name, "title": name} for name in dimensions + metrics]
        rows = []
        for record in records:
            row = [record.get(name) for name in dimensions]
            row.extend(record.get(name, 0) for name in metrics)
            rows.append(row)
        return {"rows": rows, "total": len(rows), "meta": meta}

    def _offer_name(self, offer_id: int) -> str:
        for offer in self.data["offers"]:
            if offer["id"] == offer_id:
                return offer["name"]
        return f"Offer #{offer_id}"


def _numbers(campaign_id: int, stream_id: int, offer_id: int, days: int) -> dict[str, float]:
    base = 40 + (campaign_id * 17 + stream_id * 11 + offer_id * 23) % 260
    clicks = base * days
    leads = clicks // 11
    revenue = round(leads * 2.5 + offer_id * 0.13, 2)
    cost = round(clicks * 0.16, 2)
    return {
        "clicks": clicks,
        "leads": leads,
        "conversions": leads,
        "revenue": revenue,
        "cost": cost,
        "profit": round(revenue - cost, 2),
    }


def _range_days(range_: dict) -> int:
    interval = range_.get("interval")
    if interval == "today":
        return 1
    if interval == "yesterday":
        return 1
    if interval == "7_days_ago":
        return 7
    if interval == "1_month_ago":
        return 30
    if interval == "all_time":
        return 90
    return 1


def _normalize_filters(filters: Any) -> list[dict[str, Any]]:
    """Упрощённая поддержка фильтров без OR/AND-вложенности."""
    if isinstance(filters, dict):
        filters = filters.get("AND") or filters.get("OR") or []
    if not isinstance(filters, list):
        return []
    return [item for item in filters if isinstance(item, dict) and item.get("name")]


def _matches(filters: list[dict[str, Any]], values: dict[str, Any]) -> bool:
    for item in filters:
        name = item.get("name")
        operator = str(item.get("operator", "EQUALS")).upper()
        expression = item.get("expression")
        actual = values.get(name)
        if operator == "EQUALS" and actual != expression:
            return False
        if operator == "NOT_EQUAL" and actual == expression:
            return False
        if operator in {"IN_LIST", "IN"} and actual not in (expression or []):
            return False
    return True


def _bearer(header: str | None) -> str | None:
    if not header:
        return None
    parts = header.split(None, 1)
    return parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else None


def mock_transport(tracker: FakeTracker) -> httpx.BaseTransport:
    """httpx-транспорт, который ходит не в сеть, а в FakeTracker."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        prefix = "/admin_api/v1"
        if path.startswith(prefix):
            path = path[len(prefix):] or "/"
        body = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = None
        if request.url.query:
            path = f"{path}?{request.url.query}"
        status, payload = tracker.handle(request.method, path, body, dict(request.headers))
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)
