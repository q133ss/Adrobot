from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from fastapi import Depends
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.keitaro.client import KeitaroClient
from app.services.audit import ApiCallRecorder


@lru_cache
def _demo_tracker():
    """Один in-process трекер на процесс, чтобы демо-кампании не терялись."""
    from tools.fake_keitaro.core import FakeTracker

    return FakeTracker()


def build_client(settings: Settings, session: Session) -> KeitaroClient:
    transport = None
    if settings.keitaro_demo:
        from tools.fake_keitaro.core import mock_transport

        transport = mock_transport(_demo_tracker())

    return KeitaroClient(
        settings.keitaro_base_url,
        settings.keitaro_api_key,
        timeout=settings.keitaro_timeout,
        verify_ssl=settings.keitaro_verify_ssl,
        transport=transport,
        audit=ApiCallRecorder(session, max_body=settings.max_api_log_body),
        max_log_body=settings.max_api_log_body,
    )


def get_client(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Iterator[KeitaroClient]:
    client = build_client(settings, session)
    try:
        yield client
    finally:
        client.close()
