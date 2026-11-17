from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tools.fake_keitaro.core import DEFAULT_API_KEY, FakeTracker

tracker = FakeTracker()
app = FastAPI(title="Fake Keitaro Admin API", docs_url="/docs")


@app.get("/")
def index():
    return {
        "service": "fake keitaro",
        "api_key": DEFAULT_API_KEY,
        "entrypoint": "/admin_api/v1",
        "hint": "KEITARO_BASE_URL=http://127.0.0.1:8001 и KEITARO_API_KEY=" + DEFAULT_API_KEY,
    }


@app.api_route("/admin_api/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def admin_api(path: str, request: Request):
    body = None
    if request.headers.get("content-type", "").startswith("application/json") and request.method in {
        "POST",
        "PUT",
    }:
        try:
            body = await request.json()
        except ValueError:
            body = None

    status, payload = tracker.handle(
        request.method,
        "/" + path,
        body,
        dict(request.headers),
    )
    return JSONResponse(payload, status_code=status)
