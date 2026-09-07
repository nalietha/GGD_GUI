"""FastAPI application factory.

Built as a factory over an explicit :class:`WebContext` rather than module
globals, so tests construct the app around an in-memory database and the
composition root constructs it around the live subsystems — same code path,
no patching.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from fastapi import FastAPI, Request

from livepulse import __version__
from livepulse.core.bus import EventBus
from livepulse.ingest.scheduler import IngestScheduler
from livepulse.ingest.supervisor import PushSupervisor
from livepulse.leds.renderer import Renderer
from livepulse.providers import Provider
from livepulse.storage.repositories import Repositories
from livepulse.storage.runtime import RuntimeSettingsStore

__all__ = ["WebContext", "create_app", "get_context"]


@dataclass(slots=True)
class WebContext:
    """Everything the web layer is allowed to touch.

    Notably absent: the database connection (only repositories), the driver
    (only the renderer's read-side), and any secret. What is not here cannot
    be leaked by a handler.
    """

    repos: Repositories
    store: RuntimeSettingsStore
    bus: EventBus
    scheduler: IngestScheduler
    supervisor: PushSupervisor
    renderer: Renderer

    providers: Mapping[str, Provider] = field(default_factory=dict)
    """Running provider instances, for handle resolution with warm auth."""

    request_timeout: float = 10.0
    """For transient providers built to resolve a platform not yet in use."""


def get_context(request: Request) -> WebContext:
    """FastAPI dependency: the context the app was created with."""
    context: WebContext = request.app.state.context
    return context


def create_app(context: WebContext) -> FastAPI:
    """Build the application around one context."""
    # Imported here to avoid a cycle: the API module depends on get_context.
    from livepulse.web.api import router as api_router

    app = FastAPI(
        title="LivePulse",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.context = context
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        """Liveness plus a summary of every subsystem, for `curl` and monitors.

        Always 200 while the process is serving: a degraded provider is a
        badge in the payload, not a dead service.
        """
        stats = context.renderer.stats
        return {
            "status": "ok",
            "version": __version__,
            "renderer": {
                "running": context.renderer.is_running,
                "frames": stats.frames,
                "late_frames": stats.late_frames,
                "driver_errors": stats.driver_errors,
            },
            "providers": context.scheduler.health(),
            "push_connected": context.supervisor.connected_count,
            "bus": context.bus.drain_report(),
        }

    return app
