from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from app.config import get_settings
from app.db import create_all
from app.keitaro.errors import KeitaroError
from app.web import api, routes
from app.web.templating import STATIC_DIR, render

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.auto_create_schema:
        create_all()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description="Создание кампаний и редактор офферов для Keitaro",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=False,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(routes.router)
    app.include_router(api.router)

    @app.exception_handler(KeitaroError)
    async def keitaro_error_handler(request: Request, exc: KeitaroError):
        return render(
            request,
            "error.html",
            {
                "title": "Ошибка при обращении к Keitaro",
                "message": exc.user_message,
                "keitaro_url": settings.keitaro_base_url,
            },
            status_code=502,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        if not request.url.path.startswith("/api/"):
            return render(
                request,
                "error.html",
                {
                    "title": f"Ошибка {exc.status_code}",
                    "message": str(exc.detail),
                    "keitaro_url": settings.keitaro_base_url,
                },
                status_code=exc.status_code,
            )
        return await _json_http_error(request, exc)

    return app


async def _json_http_error(request: Request, exc: StarletteHTTPException):
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


app = create_app()
