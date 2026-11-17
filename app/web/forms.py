from __future__ import annotations


def repair_utf8(value: str | None) -> str:
    """Починить кириллицу из urlencoded-форм.

    Starlette декодирует application/x-www-form-urlencoded как latin-1, поэтому
    UTF-8-текст приходит «кракозябрами». Пробуем прочитать байты как UTF-8;
    если не получается — значение уже корректное.
    """
    if not value:
        return value or ""
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
