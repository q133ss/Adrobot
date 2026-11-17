from __future__ import annotations

from sqlalchemy.orm import Session

from app.keitaro.client import AuditEntry
from app.models import ApiCall


class ApiCallRecorder:
    """Пишет журнал вызовов Keitaro в отдельной сессии.

    Отдельная сессия нужна, чтобы записи об ошибках сохранялись, даже если
    основной запрос откатится.
    """

    def __init__(self, session: Session, *, max_body: int = 4000) -> None:
        self._bind = session.get_bind()
        self._max_body = max_body

    def __call__(self, entry: AuditEntry) -> None:
        record = ApiCall(
            method=entry.method,
            path=entry.path,
            status_code=entry.status_code,
            ok=entry.ok,
            duration_ms=entry.duration_ms,
            campaign_id=entry.campaign_id,
            request_body=_truncate(entry.request_body, self._max_body),
            response_body=_truncate(entry.response_body, self._max_body),
            error=_truncate(entry.error, self._max_body),
        )
        session = Session(bind=self._bind, expire_on_commit=False)
        try:
            session.add(record)
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    if len(value) <= limit:
        return value
    return value[:limit] + "…"
