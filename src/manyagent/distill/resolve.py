"""Hybrid curator resolution (manyagent.distill.md "Hybrid curator", settled).

``MANYAGENT_CURATOR_MODE`` ∈ ``local`` | ``server`` | ``auto``:

* **local** — the curator prompt runs on the user's own LLM (an installed
  adapter's headless ``distill_model()``, or the ``MANYAGENT_LLM_*``
  OpenAI-compatible fallback). ManyAgent ships no inference for the user; the user
  pays; works with no server.
* **server** — the hosted corpus curator (``manyagent.distill.server``).
* **auto** — server if reachable, else local. The corpus is usable degraded
  (local-only) and better with the server.

C4 corollary (Design Principles §6/§11): a hosted curator over the *public
corpus* is corpus-curation, not the user's *task* inference provider.

The local model is **synchronous** (an adapter shells out a headless CLI;
``manyagent.adapters.builtin._HeadlessModel.complete`` is blocking). The curator
state machine is async (the Bank is async), so ``LocalCurator`` wraps the
blocking call in ``asyncio.to_thread`` — calling a sync ``.complete`` from the
async curate() without this would block the event loop (the M5 async-wrapper
hazard, realized here).
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from manyagent.distill.server import ServerCurator, ServerUnavailable
from manyagent.utils import config


@runtime_checkable
class Curator(Protocol):
    mode: str

    async def complete(self, system: str, user: str) -> str: ...


class NoLocalCurator(RuntimeError):
    """No local LLM available (no installed adapter with a headless model and
    no ``MANYAGENT_LLM_*`` fallback configured)."""


class LocalCurator:
    """Runs the curator prompt on the user's own LLM. Wraps a possibly-sync
    ``model.complete`` so it never blocks the curate() event loop."""

    mode = "local"

    def __init__(self, model: Any) -> None:
        if model is None:
            raise NoLocalCurator(
                "no local curator LLM: install an adapter (claude/codex/gemini) "
                "or set MANYAGENT_LLM_BASE_URL/MANYAGENT_LLM_API_KEY/MANYAGENT_LLM_MODEL"
            )
        self._model = model

    async def complete(self, system: str, user: str) -> str:
        # Cache-split contract: stable system prefix FIRST, variable posts
        # after (a CLI shell-out has no API cache, but ordering is the
        # documented contract and the server curator benefits).
        prompt = f"{system}\n\n{user}"
        fn = self._model.complete
        if asyncio.iscoroutinefunction(fn):
            return str(await fn(prompt))
        return str(await asyncio.to_thread(fn, prompt))


class AutoCurator:
    """Server if reachable, else local. ``mode`` is ``auto`` until a call
    resolves it to the concrete executor used (recorded on the distill
    packet's ``curator`` field for provenance)."""

    def __init__(self, server: Curator, local: Curator) -> None:
        self._server = server
        self._local = local
        self.mode = "auto"

    async def complete(self, system: str, user: str) -> str:
        try:
            out = await self._server.complete(system, user)
            self.mode = "server"
            return out
        except ServerUnavailable:
            out = await self._local.complete(system, user)
            self.mode = "local"
            return out


def _discover_local_model() -> Any | None:
    """First installed adapter's headless model, else the ``MANYAGENT_LLM_*``
    OpenAI-compatible fallback, else None. Lazy import (no adapter cost on the
    server path / in offline tests that inject a model)."""
    from manyagent.adapters import available
    from manyagent.adapters import resolve as resolve_adapter

    for name in available():
        try:
            adapter = resolve_adapter(name)(session_id="", agent_id="")
            model = adapter.distill_model()
        except Exception:  # noqa: S112 — discovery: try each installed adapter; a failure here means "not available", not an error to log
            continue
        if model is not None:
            return model
    if config.MANYAGENT_LLM_BASE_URL and config.MANYAGENT_LLM_API_KEY and config.MANYAGENT_LLM_MODEL:
        return _OpenAICompatModel(
            base_url=config.MANYAGENT_LLM_BASE_URL,
            api_key=config.MANYAGENT_LLM_API_KEY,
            model=config.MANYAGENT_LLM_MODEL,
        )
    return None


class _OpenAICompatModel:
    """Minimal OpenAI-compatible chat client (the ``MANYAGENT_LLM_*`` local
    fallback). Synchronous on purpose — ``LocalCurator`` threads it."""

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._model = model

    def complete(self, prompt: str, *, max_tokens: int | None = None) -> str:
        import json as _json

        import httpx

        resp = httpx.post(
            f"{self._base}/chat/completions",
            headers={"Authorization": f"Bearer {self._key}"},
            json={"model": self._model, "messages": [{"role": "user", "content": prompt}]},
            timeout=float(config.MANYAGENT_DISTILL_TIMEOUT_S),
        )
        resp.raise_for_status()
        try:
            body = resp.json()
        except _json.JSONDecodeError as exc:
            raise ValueError(
                f"OpenAI-compat endpoint returned non-JSON response (status {resp.status_code}): {exc}"
            ) from exc
        choices = body.get("choices") or [{}]
        return str(choices[0].get("message", {}).get("content", "")).strip()


def resolve(
    mode: str | None = None,
    *,
    model: Any | None = None,
    server_url: str | None = None,
) -> Curator:
    """Select a curator. ``mode=None`` reads ``MANYAGENT_CURATOR_MODE``. ``model``
    (an object with ``.complete``) overrides local discovery (tests inject a
    fake here so the offline suite never shells out)."""
    selected = (mode or config.resolve("MANYAGENT_CURATOR_MODE", "auto")).strip().lower()
    url = server_url if server_url is not None else config.MANYAGENT_CURATOR_SERVER_URL

    if selected == "server":
        return ServerCurator(url)
    if selected == "local":
        return LocalCurator(model if model is not None else _discover_local_model())
    if selected == "auto":
        local = LocalCurator(model if model is not None else _discover_local_model())
        return AutoCurator(ServerCurator(url), local)
    raise ValueError(f"bad MANYAGENT_CURATOR_MODE {selected!r}; expected local|server|auto")
