from __future__ import annotations

from typing import Any


class KeitaroError(Exception):
    """Базовая ошибка интеграции с Keitaro."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.payload = payload

    @property
    def user_message(self) -> str:
        return self.message


class KeitaroUnavailable(KeitaroError):
    """Трекер недоступен: сеть, таймаут, DNS."""


class KeitaroAPIError(KeitaroError):
    """Трекер ответил ошибкой (4xx/5xx)."""


class KeitaroNotFound(KeitaroAPIError):
    pass


class KeitaroValidationError(KeitaroAPIError):
    pass
