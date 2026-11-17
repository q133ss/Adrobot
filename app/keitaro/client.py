from __future__ import annotations

import contextlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.keitaro.entities import (
    KeitaroCampaign,
    KeitaroDomain,
    KeitaroGroup,
    KeitaroOffer,
    KeitaroSource,
    KeitaroStream,
    StreamAction,
)
from app.keitaro.errors import (
    KeitaroAPIError,
    KeitaroError,
    KeitaroNotFound,
    KeitaroUnavailable,
    KeitaroValidationError,
)
from app.keitaro.report import ReportResult

_CAMPAIGN_PATH_RE = re.compile(r"^/campaigns/(\d+)")


@dataclass(slots=True)
class AuditEntry:
    method: str
    path: str
    status_code: int | None
    ok: bool
    duration_ms: int
    campaign_id: int | None = None
    request_body: str | None = None
    response_body: str | None = None
    error: str | None = None


class KeitaroClient:
    """Тонкий клиент Keitaro Admin API v1.

    Документация: https://admin-api.docs.keitaro.io
    Авторизация — заголовок Api-Key.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 20.0,
        verify_ssl: bool = True,
        transport: httpx.BaseTransport | None = None,
        audit: Callable[[AuditEntry], None] | None = None,
        max_log_body: int = 4000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_log_body = max_log_body
        self._audit = audit
        self._actions: list[StreamAction] | None = None
        self._http = httpx.Client(
            base_url=f"{self.base_url}/admin_api/v1",
            headers={"Api-Key": api_key, "Accept": "application/json"},
            timeout=timeout,
            verify=verify_ssl,
            transport=transport,
        )

    # ------------------------------------------------------------------ infra

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> KeitaroClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        started = time.perf_counter()
        try:
            response = self._http.request(method, path, json=json_body, params=params)
        except httpx.HTTPError as exc:
            duration = _elapsed_ms(started)
            self._record(
                AuditEntry(
                    method=method,
                    path=path,
                    status_code=None,
                    ok=False,
                    duration_ms=duration,
                    request_body=self._dump(json_body),
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            raise KeitaroUnavailable(
                f"Keitaro недоступен по адресу {self.base_url}: {exc}"
            ) from exc

        duration = _elapsed_ms(started)
        payload = _decode(response)
        match = _CAMPAIGN_PATH_RE.match(path)
        campaign_id = int(match.group(1)) if match else None

        self._record(
            AuditEntry(
                method=method,
                path=path,
                status_code=response.status_code,
                ok=response.is_success,
                duration_ms=duration,
                campaign_id=campaign_id,
                request_body=self._dump(json_body),
                response_body=self._dump(payload),
                error=None if response.is_success else _error_message(payload),
            )
        )

        if not response.is_success:
            message = _error_message(payload) or response.reason_phrase
            error_cls: type[KeitaroAPIError] = KeitaroAPIError
            if response.status_code == 404:
                error_cls = KeitaroNotFound
            elif response.status_code in (400, 406, 422):
                error_cls = KeitaroValidationError
            raise error_cls(
                f"{method} {path} → {response.status_code}: {message}",
                status=response.status_code,
                payload=payload,
            )

        return payload

    def _record(self, entry: AuditEntry) -> None:
        if self._audit is None:
            return
        # журнал не должен ломать основной поток
        with contextlib.suppress(Exception):
            self._audit(entry)

    def _dump(self, value: Any) -> str | None:
        if value is None:
            return None
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
        if len(text) > self.max_log_body:
            text = text[: self.max_log_body] + "…"
        return text

    # -------------------------------------------------------------- campaigns

    def list_campaigns(self) -> list[KeitaroCampaign]:
        data = _as_list(self.request("GET", "/campaigns"))
        return [KeitaroCampaign.model_validate(item) for item in data]

    def get_campaign(self, campaign_id: int) -> KeitaroCampaign:
        data = self.request("GET", f"/campaigns/{campaign_id}")
        return KeitaroCampaign.model_validate(_unwrap(data, "campaign"))

    def create_campaign(self, payload: dict[str, Any]) -> KeitaroCampaign:
        data = self.request("POST", "/campaigns", json_body=payload)
        return KeitaroCampaign.model_validate(_unwrap(data, "campaign"))

    def update_campaign(self, campaign_id: int, payload: dict[str, Any]) -> KeitaroCampaign:
        data = self.request("PUT", f"/campaigns/{campaign_id}", json_body=payload)
        return KeitaroCampaign.model_validate(_unwrap(data, "campaign"))

    def delete_campaign(self, campaign_id: int) -> None:
        self.request("DELETE", f"/campaigns/{campaign_id}")

    # ---------------------------------------------------------------- streams

    def list_streams(self, campaign_id: int) -> list[KeitaroStream]:
        data = _as_list(self.request("GET", f"/campaigns/{campaign_id}/streams"))
        return [KeitaroStream.model_validate(item) for item in data]

    def get_stream(self, stream_id: int) -> KeitaroStream:
        data = self.request("GET", f"/streams/{stream_id}")
        return KeitaroStream.model_validate(_unwrap(data, "stream"))

    def create_stream(self, payload: dict[str, Any]) -> KeitaroStream:
        data = self.request("POST", "/streams", json_body=payload)
        return KeitaroStream.model_validate(_unwrap(data, "stream"))

    def update_stream(self, stream_id: int, payload: dict[str, Any]) -> KeitaroStream:
        data = self.request("PUT", f"/streams/{stream_id}", json_body=payload)
        return KeitaroStream.model_validate(_unwrap(data, "stream"))

    def delete_stream(self, stream_id: int) -> None:
        self.request("DELETE", f"/streams/{stream_id}")

    def stream_actions(self, *, refresh: bool = False) -> list[StreamAction]:
        if self._actions is None or refresh:
            data = _as_list(self.request("GET", "/streams_actions"))
            self._actions = [StreamAction.model_validate(item) for item in data]
        return self._actions

    def redirect_action_type(self) -> str:
        """Тип действия для редиректа: в Keitaro это ключ `http` (HttpRedirect)."""
        try:
            actions = self.stream_actions()
        except KeitaroError:
            return "http"
        keys = [action.key for action in actions if action.key]
        if "http" in keys:
            return "http"
        for action in actions:
            if action.type == "redirect":
                return action.key
        return "http"

    def offers_action_type(self) -> str:
        """Тип действия для потока с офферами (schema=landings).

        В текущих версиях Keitaro отдельного `show_landing` в /streams_actions
        нет, поток с офферами принимает `http`. Если в другой версии есть
        `show_landing`/`show_offer` — используем его.
        """
        try:
            actions = self.stream_actions()
        except KeitaroError:
            return "http"
        keys = [action.key for action in actions if action.key]
        for candidate in ("show_landing", "show_offer", "show_offers"):
            if candidate in keys:
                return candidate
        return self.redirect_action_type()

    # ------------------------------------------------------- справочники

    def list_offers(self) -> list[KeitaroOffer]:
        data = _as_list(self.request("GET", "/offers"))
        return [KeitaroOffer.model_validate(item) for item in data]

    def list_domains(self) -> list[KeitaroDomain]:
        data = _as_list(self.request("GET", "/domains"))
        return [KeitaroDomain.model_validate(item) for item in data]

    def list_groups(self, group_type: str = "campaigns") -> list[KeitaroGroup]:
        """`type` обязателен: campaigns или offers (иначе Keitaro отвечает 400)."""
        data = _as_list(self.request("GET", "/groups", params={"type": group_type}))
        return [KeitaroGroup.model_validate(item) for item in data]

    def list_sources(self) -> list[KeitaroSource]:
        data = _as_list(self.request("GET", "/traffic_sources"))
        return [KeitaroSource.model_validate(item) for item in data]

    # ---------------------------------------------------------------- reports

    def build_report(
        self,
        *,
        dimensions: list[str],
        metrics: list[str],
        filters: list[dict[str, Any]] | None = None,
        range_: dict[str, Any] | None = None,
        sort: list[dict[str, str]] | None = None,
        limit: int | None = None,
    ) -> ReportResult:
        payload: dict[str, Any] = {
            "dimensions": dimensions,
            "metrics": metrics,
        }
        if filters:
            payload["filters"] = filters
        if range_:
            payload["range"] = range_
        if sort:
            payload["sort"] = sort
        if limit:
            payload["limit"] = limit

        try:
            data = self.request("POST", "/report/build", json_body=payload)
        except KeitaroValidationError as exc:
            # В разных версиях поле называется metrics/measures — пробуем второе.
            if "metrics" not in json.dumps(exc.payload or {}, ensure_ascii=False):
                raise
            payload["measures"] = payload.pop("metrics")
            data = self.request("POST", "/report/build", json_body=payload)

        return ReportResult.from_payload(data)

    # ------------------------------------------------------------------ misc

    def ping(self) -> bool:
        try:
            self.request("GET", "/domains")
        except KeitaroError:
            return False
        return True


def _elapsed_ms(started: float) -> int:
    return max(int((time.perf_counter() - started) * 1000), 0)


def _decode(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


def _as_list(payload: Any) -> list[Any]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _unwrap(payload: Any, key: str) -> Any:
    if isinstance(payload, dict) and key in payload and isinstance(payload[key], dict):
        return payload[key]
    return payload


def _error_message(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()[:1000]
    if isinstance(payload, dict):
        for key in ("error", "message", "detail", "errors"):
            value = payload.get(key)
            if not value:
                continue
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                parts = [_error_message(item) for item in value]
                joined = "; ".join(part for part in parts if part)
                if joined:
                    return joined
            if isinstance(value, dict):
                nested = _error_message(value)
                if nested:
                    return nested
    return json.dumps(payload, ensure_ascii=False)[:1000]
