from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

_is_sqlite = settings.database_url.startswith("sqlite")

engine = create_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)


class Base(DeclarativeBase):
    pass


def get_session() -> Iterator[Session]:
    """FastAPI-зависимость: сессия на запрос."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def create_all() -> None:
    """Создать схему напрямую (без Alembic). Для нулевого старта и тестов."""
    from app import models  # noqa: F401  (регистрирует таблицы в metadata)

    Base.metadata.create_all(engine)
