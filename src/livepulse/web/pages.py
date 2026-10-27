"""The HTML pages: dashboard, channels, settings.

Server-rendered for the first paint, so a page is readable before — or
without — any JavaScript. Every *write* goes through ``/api/v1`` from the
small script in ``static/livepulse.js``; these routes only read. That keeps
the API the single write path, which is the point of having built it first:
V1's pages each edited the JSON file their own way.

Live updates arrive over the SSE stream. Rather than re-implement the
templates in JavaScript, the script re-fetches the page and swaps ``<main>``
— the same server-side rendering answers both questions, so the first paint
and the hundredth cannot disagree. (The plan named htmx for this; with a
JSON-only API it would have needed a second set of fragment endpoints, which
is a second writer by another name. The swap is a dozen lines.)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from livepulse import __version__
from livepulse.core.models import DisplayMode
from livepulse.leds.effects import describe_effects
from livepulse.providers import describe_providers
from livepulse.web.api import PreviewNode, build_preview
from livepulse.web.app import WebContext, get_context

__all__ = ["STATIC_DIR", "router"]

_HERE = Path(__file__).parent
STATIC_DIR = _HERE / "static"

templates = Jinja2Templates(directory=_HERE / "templates")

router = APIRouter(default_response_class=HTMLResponse)


def _render(request: Request, context: WebContext, name: str, **values: Any) -> HTMLResponse:
    """Render a page with what every page's header needs."""
    return templates.TemplateResponse(
        request,
        name,
        {
            "version": __version__,
            "mode": context.store.current.mode,
            "modes": list(DisplayMode),
            **values,
        },
    )


@router.get("/")
async def dashboard(request: Request, context: WebContext = Depends(get_context)) -> HTMLResponse:
    """Every node: what it shows now, and what it is bound to."""
    nodes = await context.repos.nodes.list()
    channels = await context.repos.channels.list()

    # The swatch mirrors the renderer's own snapshot, exactly as /preview does.
    # Before the first frame there is nothing to mirror, so swatches go blank
    # rather than guessing.
    state = context.renderer.state
    preview: dict[int, PreviewNode] = (
        {view.node_id: view for view in build_preview(state).nodes} if state else {}
    )

    return _render(
        request,
        context,
        "dashboard.html",
        page="dashboard",
        nodes=nodes,
        channels=channels,
        preview=preview,
        effects=describe_effects(),
        ambient_active=bool(state and state.ambient_active),
    )


@router.get("/channels")
async def channels(request: Request, context: WebContext = Depends(get_context)) -> HTMLResponse:
    """Every configured stream, its live state, and the add form."""
    configured = await context.repos.channels.list()
    states = await context.repos.live_state.all()
    bound = await context.repos.nodes.bound_channel_ids()

    return _render(
        request,
        context,
        "channels.html",
        page="channels",
        channels=configured,
        states=states,
        bound=bound,
        providers=describe_providers(),
    )


@router.get("/settings")
async def settings(request: Request, context: WebContext = Depends(get_context)) -> HTMLResponse:
    """Mode, brightness, and what the stand does when nobody is live."""
    return _render(
        request,
        context,
        "settings.html",
        page="settings",
        settings=context.store.current,
        effects=describe_effects(),
        health=context.scheduler.health(),
    )
