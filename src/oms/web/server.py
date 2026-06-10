"""oms.web.server — the ``make web-up`` entrypoint.

Serves :func:`oms.web.api.create_app` under uvicorn and mounts the static
viewer (``oms.web.app``) at ``/`` when it is present. Kept apart from
``api`` so importing the app for tests never pulls in uvicorn.

The bind identity defaults to ``public`` (anon, read-only, DB-enforced) — the
public website is structurally incapable of mutating the corpus (oms.web.md).
"""

from __future__ import annotations

import pathlib
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from oms.utils import config
from oms.web.api import create_app

# The viewer ships under web/viewer/ (SvelteKit + adapter-static; `make
# web-build` produces web/viewer/build/). web/app/index.html stays as the
# zero-toolchain fallback so a fresh clone of `make web-up` still renders
# something (oms.web.md 2026-05-20 entry). Mounted only if present — the
# viewer is optional / non-gating (oms.web.md Verification).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_VIEWER_BUILD = _REPO_ROOT / "web" / "viewer" / "build"
_LEGACY_APP = _REPO_ROOT / "web" / "app"
_APP_DIR = _VIEWER_BUILD if _VIEWER_BUILD.is_dir() else _LEGACY_APP


class _SPAStaticFiles(StaticFiles):
    """``StaticFiles`` that serves ``index.html`` for missing HTML routes
    (the SvelteKit SPA shell). adapter-static emits the fallback expecting
    the host to rewrite unmatched paths; FastAPI/Starlette would 404
    otherwise. Missing assets (``.js``, ``.css``, …) still 404 — we only
    rewrite requests whose last path segment has no extension.
    """

    async def get_response(self, path: str, scope: Any) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            fallback = pathlib.Path(self.directory or "") / "index.html"
            tail = path.rsplit("/", 1)[-1]
            if fallback.is_file() and "." not in tail:
                return FileResponse(fallback, media_type="text/html")
            raise


def _serve_spa_shell() -> FileResponse | None:
    shell = _APP_DIR / "index.html"
    return FileResponse(shell, media_type="text/html") if shell.is_file() else None


def _is_viewer_html_path(path: str) -> bool:
    """Identify viewer-owned paths that *also* collide with API routes.

    ``/s/{session}`` is documented as both an API JSON endpoint and the URL
    a browser visits (oms.web.md ``Read API`` table). We split by Accept:
    browsers (``text/html`` in Accept) get the SPA shell; programmatic
    callers (``*/*`` default for httpx/curl) get the API JSON.
    """
    # /s/X  but NOT /s/X/agents — agents is an API-only route.
    return (path.startswith("/s/") or path.startswith("/g/")) and path.count("/") == 2


def build_app(*, identity: str = "public") -> FastAPI:
    """The fully wired ASGI app: the read API + (if present) the static viewer.

    Tests for the read-API contract use :func:`create_app` directly so this
    function's SPA wiring stays off the frozen-test path (``test_web.py``).
    """
    app = create_app(identity=identity)
    if not _APP_DIR.is_dir():
        return app

    @app.middleware("http")
    async def spa_negotiation(request: Request, call_next: Any) -> Response:
        # Browsers always include `text/html` in Accept; httpx/curl and the
        # default `window.fetch()` send `*/*` which doesn't include it. So
        # this never affects programmatic callers (and never affects
        # test_web.py). Caveat: a future caller that explicitly sets
        # `Accept: text/html, application/json` on a fetch would be served
        # the SPA shell instead of JSON; if/when that becomes a real
        # use case, also require `application/json` to be *absent*.
        if (
            request.method == "GET"
            and _is_viewer_html_path(request.url.path)
            and "text/html" in request.headers.get("accept", "")
        ):
            resp = _serve_spa_shell()
            if resp is not None:
                return resp
        result: Response = await call_next(request)
        return result

    app.mount(
        "/",
        _SPAStaticFiles(directory=str(_APP_DIR), html=True),
        name="viewer",
    )
    return app


def serve(*, host: str | None = None, port: int | None = None, identity: str = "public") -> None:
    """Run the read-only API + viewer (``make web-up``)."""
    import uvicorn

    uvicorn.run(
        build_app(identity=identity),
        host=host or config.OMS_WEB_HOST,
        port=port if port is not None else config.OMS_WEB_PORT,
    )


if __name__ == "__main__":  # pragma: no cover — `python -m oms.web.server [PORT]`
    import sys

    _port = int(sys.argv[1]) if len(sys.argv) > 1 else None
    serve(port=_port)
