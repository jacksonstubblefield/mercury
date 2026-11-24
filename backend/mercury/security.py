"""Local-only request guard.

Mercury's API can start coding agents on your machine, so it must not be
callable from arbitrary web pages you visit. This ASGI middleware:

* rejects requests whose Host header isn't localhost (DNS-rebinding defence), and
* rejects requests/websockets whose Origin header isn't a known Mercury origin
  (cross-site request defence). Requests without an Origin (curl, tests,
  same-origin GETs) are allowed.
"""

from __future__ import annotations

import os

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "testserver"}


def allowed_origins(port: int) -> set[str]:
    origins = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        "http://127.0.0.1:5173",  # Vite dev server
        "http://localhost:5173",
    }
    extra = os.environ.get("MERCURY_ALLOWED_ORIGINS", "")
    origins.update(o.strip() for o in extra.split(",") if o.strip())
    return origins


class LocalOnlyMiddleware:
    def __init__(self, app: ASGIApp, port: int):
        self.app = app
        self.origins = allowed_origins(port)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = headers.get("host", "").rsplit(":", 1)[0] if not headers.get("host", "").startswith("[") else headers["host"].split("]")[0] + "]"
        origin = headers.get("origin")

        reason = None
        if host and host not in LOCAL_HOSTS:
            reason = "Host not allowed"
        elif origin is not None and origin not in self.origins:
            reason = "Origin not allowed"

        if reason is None:
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4403, "reason": reason})
            return
        await PlainTextResponse(reason, status_code=403)(scope, receive, send)
