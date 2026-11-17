from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.services.stats import PERIODS

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def money(value: float | None, currency: str = "$", *, signed: bool = False) -> str:
    amount = float(value or 0)
    formatted = f"{abs(amount):,.2f}".replace(",", " ")
    if amount < 0:
        sign = "−"
    elif signed and amount > 0:
        sign = "+"
    else:
        sign = ""
    return f"{sign}{formatted} {currency}"


def number(value: float | None) -> str:
    return f"{float(value or 0):,.0f}".replace(",", " ")


def percent(value: float | None, digits: int = 1) -> str:
    return f"{float(value or 0):.{digits}f}%"


def money_short(value: float | None) -> str:
    amount = float(value or 0)
    if abs(amount) >= 1_000_000:
        return f"{amount / 1_000_000:.1f}M"
    if abs(amount) >= 1_000:
        return f"{amount / 1_000:.1f}K"
    return f"{amount:.0f}"


def dt(value: datetime | str | None) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        return value.replace("T", " ")[:16]
    return value.strftime("%d.%m.%Y %H:%M")


templates.env.filters["money"] = money
templates.env.filters["money_short"] = money_short
templates.env.filters["number"] = number
templates.env.filters["percent"] = percent
templates.env.filters["dt"] = dt
templates.env.globals["periods"] = PERIODS


def render(request: Request, template: str, context: dict | None = None, status_code: int = 200):
    data = {
        "request": request,
        "app_name": get_settings().app_name,
        "flashes": pop_flashes(request),
    }
    if context:
        data.update(context)
    return templates.TemplateResponse(request, template, data, status_code=status_code)


def flash(request: Request, message: str, level: str = "info") -> None:
    request.session.setdefault("_flashes", []).append({"message": message, "level": level})


def pop_flashes(request: Request) -> list[dict]:
    return request.session.pop("_flashes", [])
