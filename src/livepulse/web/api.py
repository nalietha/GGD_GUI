"""The REST API, version 1.

Everything the UI can do is here first — the HTML pages are a client of this
API, not a second write path. That ordering is deliberate: V1's pages each
edited the JSON file their own way, and the fourth page disagreed with the
other three.

Writes follow one pattern: validate, persist through the repository or store,
publish the change on the bus. The coordinator, the SSE stream, and any future
subscriber all learn about the edit the same way, so the strip and the browser
cannot drift apart.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from livepulse.config.settings import provider_secrets
from livepulse.core.errors import (
    ChannelNotFoundError,
    LivePulseError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimited,
    StorageError,
)
from livepulse.core.events import ChannelConfigChanged, NodeConfigChanged
from livepulse.core.models import (
    AuthKind,
    Channel,
    ChannelRef,
    DisplayMode,
    HexColor,
    LiveState,
    Node,
)
from livepulse.leds.color import to_hex
from livepulse.leds.effects import available_effects, describe_effects
from livepulse.providers import (
    ProviderConfig,
    capabilities,
    create_provider,
    describe_providers,
    resolve_input,
)
from livepulse.storage.runtime import RuntimeSettings
from livepulse.web.app import WebContext, get_context
from livepulse.web.events import event_stream

__all__ = ["router"]

router = APIRouter()


def _provider_http_error(exc: LivePulseError) -> HTTPException:
    """Map a provider failure onto the status the client can act on.

    The distinction that matters: 404 is the user's typo, 429/502/503 are the
    platform's or the operator's problem — the UI words them differently.
    """
    if isinstance(exc, ChannelNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, ProviderRateLimited):
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc))
    if isinstance(exc, ProviderAuthError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    if isinstance(exc, ProviderError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #


class NodePatch(BaseModel):
    """A partial node edit. Absent fields stay as they are."""

    model_config = ConfigDict(extra="forbid")

    channel_id: UUID | None = None
    """A channel to bind, or explicit ``null`` to unbind."""

    effect: str | None = None
    color: HexColor | None = None
    params: dict[str, Any] | None = None


@router.get("/nodes")
async def list_nodes(context: WebContext = Depends(get_context)) -> list[Node]:
    return await context.repos.nodes.list()


@router.patch("/nodes/{node_id}")
async def patch_node(
    node_id: int, patch: NodePatch, context: WebContext = Depends(get_context)
) -> Node:
    """Edit a node's binding, effect, colour, or parameters."""
    node = await context.repos.nodes.get(node_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no node {node_id}")

    updates: dict[str, Any] = {}

    # channel_id distinguishes "absent" from "null": null unbinds.
    if "channel_id" in patch.model_fields_set:
        if (
            patch.channel_id is not None
            and await context.repos.channels.get(patch.channel_id) is None
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no channel {patch.channel_id}")
        updates["channel_id"] = patch.channel_id

    if patch.effect is not None:
        # Rejected here rather than rendered dark: the compositor tolerates an
        # unknown effect, but the user deserves the error at edit time.
        if patch.effect not in available_effects():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"unknown effect {patch.effect!r}; one of {', '.join(available_effects())}",
            )
        updates["effect"] = patch.effect

    if patch.color is not None:
        updates["color"] = patch.color
    if patch.params is not None:
        updates["params"] = patch.params

    updated = node.model_copy(update=updates)
    await context.repos.nodes.update(updated)
    context.bus.publish(NodeConfigChanged(node_id=node_id))
    return updated


# --------------------------------------------------------------------------- #
# Channels
# --------------------------------------------------------------------------- #


class ChannelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str
    """A handle or a pasted URL; the platform is detected from URLs."""

    provider: str | None = None
    """Platform for bare handles. URLs carry their own."""

    color: HexColor | None = None
    alt_color: HexColor | None = None
    display_name: str | None = None


class ChannelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color: HexColor | None = None
    alt_color: HexColor | None = None
    display_name: str | None = None
    enabled: bool | None = None


class ChannelOut(BaseModel):
    """A channel plus its latest observation — what every page needs together."""

    channel: Channel
    state: LiveState | None


async def _resolve(context: WebContext, provider_id: str, handle: str) -> ChannelRef:
    """Resolve through the running provider, or a transient one.

    The running instance is preferred for its warm auth token; a platform not
    yet in use gets a short-lived provider that is closed immediately.
    """
    provider = context.providers.get(provider_id)
    if provider is not None:
        return await provider.resolve(handle)

    transient = create_provider(
        provider_id,
        ProviderConfig(
            provider_id=provider_id,
            secrets=provider_secrets(provider_id),
            timeout=context.request_timeout,
        ),
    )
    try:
        return await transient.resolve(handle)
    finally:
        await transient.aclose()


@router.get("/channels")
async def list_channels(context: WebContext = Depends(get_context)) -> list[ChannelOut]:
    channels = await context.repos.channels.list()
    states = await context.repos.live_state.all()
    return [ChannelOut(channel=channel, state=states.get(channel.id)) for channel in channels]


@router.get("/channels/{channel_id}")
async def get_channel(channel_id: UUID, context: WebContext = Depends(get_context)) -> ChannelOut:
    channel = await context.repos.channels.get(channel_id)
    if channel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no channel {channel_id}")
    return ChannelOut(channel=channel, state=await context.repos.live_state.get(channel_id))


@router.post("/channels", status_code=status.HTTP_201_CREATED)
async def add_channel(body: ChannelCreate, context: WebContext = Depends(get_context)) -> Channel:
    """Add a channel by handle or URL, resolving it against the platform.

    Resolution happens now, once — the platform's stable id is what gets
    stored, so a later rename on the platform does not orphan the channel.
    """
    try:
        provider_id, handle = resolve_input(body.input, body.provider)
    except LivePulseError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    try:
        ref = await _resolve(context, provider_id, handle)
    except LivePulseError as exc:
        raise _provider_http_error(exc) from exc

    if await context.repos.channels.find_by_ref(ref.provider, ref.platform_ref) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{ref.handle!r} is already configured on {ref.provider}"
        )

    fields: dict[str, Any] = {
        "provider": ref.provider,
        "platform_ref": ref.platform_ref,
        "handle": ref.handle,
        "display_name": body.display_name,
        "alt_color": body.alt_color,
    }
    if body.color is not None:
        fields["color"] = body.color

    channel = Channel(**fields)
    try:
        await context.repos.channels.add(channel)
    except StorageError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    context.bus.publish(ChannelConfigChanged(channel_id=channel.id))
    return channel


@router.patch("/channels/{channel_id}")
async def patch_channel(
    channel_id: UUID, patch: ChannelPatch, context: WebContext = Depends(get_context)
) -> Channel:
    channel = await context.repos.channels.get(channel_id)
    if channel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no channel {channel_id}")

    updates = {key: value for key, value in patch.model_dump().items() if value is not None}
    updated = channel.model_copy(update=updates)
    await context.repos.channels.update(updated)
    context.bus.publish(ChannelConfigChanged(channel_id=channel_id))
    return updated


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(channel_id: UUID, context: WebContext = Depends(get_context)) -> Response:
    """Remove a channel. Bound nodes are unbound and keep their colours.

    (V1 reset them to black — destroying a setting nobody asked to change.)
    """
    if not await context.repos.channels.delete(channel_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no channel {channel_id}")
    context.bus.publish(ChannelConfigChanged(channel_id=channel_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brightness: int | None = Field(default=None, ge=0, le=255)
    mode: DisplayMode | None = None
    ambient_effect: str | None = None
    ambient_enabled: bool | None = None


@router.get("/settings")
async def get_settings(context: WebContext = Depends(get_context)) -> RuntimeSettings:
    return context.store.current


@router.patch("/settings")
async def patch_settings(
    patch: SettingsPatch, context: WebContext = Depends(get_context)
) -> RuntimeSettings:
    """Edit runtime settings. ``{"mode": "off"}`` is the off button.

    The store persists, updates its cache, and announces on the bus — one
    write path shared with the CLI and the schedule, so nothing can disagree.
    """
    changes = patch.model_dump(exclude_none=True)
    if not changes:
        return context.store.current

    try:
        return await context.store.update(**changes)
    except StorageError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


# --------------------------------------------------------------------------- #
# Providers, effects, preview, events
# --------------------------------------------------------------------------- #


class ProviderOut(BaseModel):
    id: str
    description: str
    needs_auth: AuthKind
    credentials_configured: bool
    supports_push: bool
    running: bool
    healthy: bool | None
    """``None`` when the provider is not part of the running ingest set."""

    push: dict[str, Any] | None
    """Subscription statistics, when a push connection is being supervised."""


@router.get("/providers")
async def list_providers(context: WebContext = Depends(get_context)) -> list[ProviderOut]:
    """Every registered platform, its auth state, and its health.

    Credentials are reported only as a boolean — the API never returns a
    secret, which is what makes this safe to render on any page.
    """
    health = context.scheduler.health()
    push_stats = context.supervisor.stats()

    out = []
    for provider_id, description in describe_providers().items():
        caps = capabilities(provider_id)
        configured = caps.needs_auth is AuthKind.NONE or bool(provider_secrets(provider_id))
        stats = push_stats.get(provider_id)
        out.append(
            ProviderOut(
                id=provider_id,
                description=description,
                needs_auth=caps.needs_auth,
                credentials_configured=configured,
                supports_push=caps.supports_push,
                running=provider_id in context.providers,
                healthy=health.get(provider_id),
                push=asdict(stats) if stats is not None else None,
            )
        )
    return out


@router.get("/effects")
async def list_effects() -> dict[str, str]:
    """Effect names and descriptions, for pickers."""
    return describe_effects()


class PreviewNode(BaseModel):
    node_id: int
    effect: str
    color: str
    bound: bool
    live: bool


class PreviewOut(BaseModel):
    mode: DisplayMode
    brightness: int
    ambient_active: bool
    nodes: list[PreviewNode]


@router.get("/preview")
async def preview(context: WebContext = Depends(get_context)) -> PreviewOut:
    """What the strip is currently being asked to show.

    Read from the renderer's own snapshot, so the browser grid mirrors the
    physical stand instead of re-deriving it — V1's Razor preview re-implemented
    the resolution rules and drifted.
    """
    state = context.renderer.state
    if state is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "nothing rendered yet")

    return PreviewOut(
        mode=state.mode,
        brightness=state.brightness,
        ambient_active=state.ambient_active,
        nodes=[
            PreviewNode(
                node_id=view.node_id,
                effect=view.effect,
                color=to_hex(view.color),
                bound=view.is_bound,
                live=view.is_live,
            )
            for view in state.nodes
        ],
    )


@router.get("/events")
async def events(context: WebContext = Depends(get_context)) -> StreamingResponse:
    """The bus as server-sent events. ``new EventSource(...)`` and done."""
    return StreamingResponse(
        event_stream(context.bus),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Disables buffering in nginx-style reverse proxies, which would
            # otherwise hold events until the buffer fills.
            "X-Accel-Buffering": "no",
        },
    )
